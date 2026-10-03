#!/usr/bin/env python3
"""Manage Git-backed dashboard state and Actions telemetry snapshots."""

from __future__ import annotations

import argparse
import base64
from collections.abc import Callable
from collections.abc import Iterator
from contextlib import contextmanager
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path


DEFAULT_MAX_ATTEMPTS = 8
RETRY_BACKOFF_BASE_SECONDS = 0.5
RETRY_BACKOFF_MAX_SECONDS = 8.0
CONFIG_LOCK_ATTEMPTS = 5
FETCH_ATTEMPTS = 4
STATE_BRANCH_PREFIX = "otelbot/pull-request-dashboard-state"
DELIVERY_STATE_BRANCH_PREFIX = "otelbot/pull-request-dashboard-delivery"
SNAPSHOT_GENERATION_TRAILER = "Data-snapshot-generation"
SUBPROCESS_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def snapshot_mode() -> bool:
    value = os.environ.get("DATA_BRANCH_SNAPSHOTS", "")
    if value not in ("", "false", "true"):
        raise ValueError("DATA_BRANCH_SNAPSHOTS must be true, false, or empty")
    return value == "true"


def delivery_state_branch(state_branch: str) -> str:
    accepted_prefix = f"{STATE_BRANCH_PREFIX}/"
    if not state_branch.startswith(accepted_prefix):
        raise ValueError(
            f"accepted state branch must start with {accepted_prefix}: {state_branch}"
        )
    repository = state_branch.removeprefix(accepted_prefix)
    if not repository:
        raise ValueError(f"accepted state branch is missing a repository: {state_branch}")
    return f"{DELIVERY_STATE_BRANCH_PREFIX}/{repository}"


@contextmanager
def temporary_state_dir() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="pull-request-dashboard-") as temp_root:
        state_dir = Path(temp_root) / "state"
        try:
            yield state_dir
        finally:
            remove_existing_state_dir(state_dir)


@contextmanager
def accepted_state_dir(state_branch: str, required: bool) -> Iterator[Path | None]:
    with temporary_state_dir() as checkout_dir:
        if not fetch_state_branch(state_branch, required=required):
            yield None
            return
        try:
            run([
                "git", "worktree", "add", "--quiet", "--detach", str(checkout_dir),
                remote_ref(state_branch),
            ])
            yield checkout_dir
        finally:
            remove_existing_state_dir(checkout_dir)


def run(cmd: list[str], check: bool = True, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd, check=check, cwd=cwd, text=True, creationflags=SUBPROCESS_FLAGS,
    )


def remote_ref(state_branch: str) -> str:
    return f"refs/remotes/origin/{state_branch}"


def is_missing_remote_ref(stderr: str) -> bool:
    return "couldn't find remote ref" in stderr.lower()


def is_transient_fetch_error(output: str) -> bool:
    message = output.lower()
    if re.search(r"(?:http(?:/\d(?:\.\d)?)?|returned error:)\s*5\d\d\b", message):
        return True
    return any(
        text in message
        for text in (
            "bad gateway",
            "broken pipe",
            "connection closed by remote host",
            "connection refused",
            "connection reset",
            "connection timed out",
            "connection was reset",
            "could not resolve host",
            "early eof",
            "empty reply from server",
            "failed to connect",
            "failure when receiving data from the peer",
            "gateway timeout",
            "gnutls_handshake() failed",
            "internal server error",
            "network is unreachable",
            "operation timed out",
            "recv failure",
            "remote end hung up unexpectedly",
            "schannel: failed to receive handshake",
            "send failure",
            "service unavailable",
            "ssl_error_syscall",
            "temporary failure in name resolution",
            "tls connection was non-properly terminated",
            "transfer closed with outstanding read data remaining",
            "unexpected disconnect",
            "unexpected eof while reading",
            "was not closed cleanly",
        )
    )


def temporary_fetch_ref() -> str:
    return f"refs/pull-request-dashboard-fetch/{uuid.uuid4()}"


def ref_is_ancestor(ancestor: str, descendant: str) -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        capture_output=True,
        text=True,
        check=False,
        creationflags=SUBPROCESS_FLAGS,
    )
    if result.returncode not in (0, 1):
        raise RuntimeError(f"failed to compare Git refs: {result.stderr.strip()}")
    return result.returncode == 0


def ref_oid(ref: str, cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", "rev-parse", ref],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        creationflags=SUBPROCESS_FLAGS,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
        raise RuntimeError(f"failed to resolve Git ref {ref}: {message}")
    return result.stdout.strip()


def remote_is_behind_local(state_branch: str, fetched_ref: str) -> bool:
    if not has_state_branch(state_branch):
        return False
    return ref_is_ancestor(fetched_ref, remote_ref(state_branch))


def snapshot_generation(ref: str, cwd: Path | None = None) -> int:
    result = subprocess.run(
        ["git", "cat-file", "commit", ref],
        cwd=cwd, capture_output=True, text=True, check=True,
        creationflags=SUBPROCESS_FLAGS,
    )
    headers, _, message = result.stdout.partition("\n\n")
    has_parents = any(line.startswith("parent ") for line in headers.splitlines())
    trailers = [
        line for line in message.splitlines()
        if line.startswith(f"{SNAPSHOT_GENERATION_TRAILER}:")
    ]
    if not trailers:
        return 0
    if (
        has_parents
        or len(trailers) != 1
        or re.fullmatch(rf"{SNAPSHOT_GENERATION_TRAILER}: [1-9][0-9]*", trailers[0]) is None
    ):
        raise RuntimeError(f"invalid data snapshot commit: {ref}")
    return int(trailers[0].split(": ", 1)[1])


def has_snapshot_ancestor(oid: str) -> bool:
    result = subprocess.run(
        [
            "git", "log", "-n1", "--format=%H", "-E",
            f"--grep=^{SNAPSHOT_GENERATION_TRAILER}:", oid,
        ],
        capture_output=True, text=True, check=False,
        creationflags=SUBPROCESS_FLAGS,
    )
    if result.returncode != 0:
        raise RuntimeError(f"failed to inspect Git history for {oid}: {result.stderr.strip()}")
    return bool(result.stdout.strip())


def update_snapshot_ref(state_branch: str, fetched_ref: str) -> None:
    destination = remote_ref(state_branch)
    fetched_oid = ref_oid(fetched_ref)
    fetched_generation = snapshot_generation(fetched_oid)
    # A fresh checkout has no local ref, so detect history appended to a snapshot from the commits themselves.
    if not fetched_generation and has_snapshot_ancestor(fetched_oid):
        raise RuntimeError(f"history restored after data snapshot for {state_branch}")
    for _ in range(FETCH_ATTEMPTS):
        local_oid = ref_oid(destination) if has_state_branch(state_branch) else ""
        if local_oid == fetched_oid:
            return
        if local_oid:
            local_generation = snapshot_generation(local_oid)
            if local_generation and fetched_generation:
                if local_generation == fetched_generation:
                    raise RuntimeError(f"conflicting data snapshot generation for {state_branch}")
                newer = fetched_generation > local_generation
            elif fetched_generation:
                if ref_is_ancestor(fetched_oid, local_oid):
                    raise RuntimeError(f"history restored after data snapshot for {state_branch}")
                newer = True
            elif local_generation:
                if ref_is_ancestor(local_oid, fetched_oid):
                    raise RuntimeError(f"history restored after data snapshot for {state_branch}")
                newer = False
            else:
                newer = ref_is_ancestor(local_oid, fetched_oid)
                if not newer and not ref_is_ancestor(fetched_oid, local_oid):
                    raise RuntimeError(f"fetched state branch {state_branch} diverged from the local ref")
            if not newer:
                print(f"remote {state_branch} is behind the local ref; keeping the local ref", file=sys.stderr)
                return
        # A concurrent fetch must not replace a newer local snapshot.
        result = run(
            ["git", "update-ref", destination, fetched_oid, local_oid],
            check=False,
        )
        if result.returncode == 0:
            return
    raise RuntimeError(f"could not update local snapshot ref for {state_branch}")


def fetch_state_branch(state_branch: str, required: bool) -> bool:
    fetched_ref = temporary_fetch_ref()
    try:
        for attempt in range(1, FETCH_ATTEMPTS + 1):
            proc = subprocess.run(
                [
                    "git",
                    "fetch",
                    "--no-write-fetch-head",
                    "origin",
                    f"{state_branch}:{fetched_ref}",
                ],
                capture_output=True,
                text=True,
                check=False,
                creationflags=SUBPROCESS_FLAGS,
            )
            if proc.returncode == 0:
                break
            if not required and is_missing_remote_ref(proc.stderr):
                return False
            message = proc.stderr.strip() or proc.stdout.strip() or f"exit code {proc.returncode}"
            if (
                not is_transient_fetch_error(f"{proc.stderr}\n{proc.stdout}")
                or attempt == FETCH_ATTEMPTS
            ):
                kind = "required" if required else "optional"
                raise RuntimeError(
                    f"failed to fetch {kind} state branch {state_branch}: {message}"
                )
            time.sleep(retry_delay_seconds(attempt))

        if snapshot_mode():
            update_snapshot_ref(state_branch, fetched_ref)
            return True

        destination = remote_ref(state_branch)
        if not has_state_branch(state_branch) or ref_is_ancestor(destination, fetched_ref):
            run(["git", "update-ref", destination, fetched_ref])
            return True
        if remote_is_behind_local(state_branch, fetched_ref):
            # GitHub fetches can briefly lag the copy that accepted the push.
            print(
                f"remote {state_branch} is behind the local ref; keeping the local ref",
                file=sys.stderr,
            )
            return True
        raise RuntimeError(f"fetched state branch {state_branch} diverged from the local ref")
    finally:
        run(["git", "update-ref", "-d", fetched_ref], check=False)


def has_state_branch(state_branch: str) -> bool:
    proc = run(["git", "show-ref", "--verify", "--quiet", remote_ref(state_branch)], check=False)
    return proc.returncode == 0


def remove_existing_state_dir(state_dir: Path) -> None:
    if not state_dir.exists():
        return
    run(["git", "worktree", "remove", "--force", str(state_dir)], check=False)
    if not state_dir.exists():
        return
    if state_dir.is_dir():
        shutil.rmtree(state_dir)
    else:
        state_dir.unlink()


def checkout_state(state_dir: Path, state_branch: str, require_existing: bool) -> str:
    remove_existing_state_dir(state_dir)
    fetch_state_branch(state_branch, required=require_existing)
    if has_state_branch(state_branch):
        run(["git", "worktree", "add", "-B", state_branch, str(state_dir), f"origin/{state_branch}"])
        return ref_oid("HEAD", cwd=state_dir)
    run(["git", "worktree", "add", "--detach", str(state_dir), "HEAD"])
    run(["git", "switch", "--orphan", state_branch], cwd=state_dir)
    run(["git", "rm", "-rf", "."], cwd=state_dir, check=False)
    return ""


def reset_state(state_dir: Path, state_branch: str) -> bool:
    if not fetch_state_branch(state_branch, required=False):
        return False
    run(["git", "reset", "--hard", f"origin/{state_branch}"], cwd=state_dir)
    return True


def push_state(state_dir: Path, state_branch: str, expected_sha: str) -> bool:
    env = dict(os.environ)
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        credential = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = "http.https://github.com/.extraheader"
        env["GIT_CONFIG_VALUE_0"] = f"AUTHORIZATION: basic {credential}"
    cmd = [
        "git", "push", f"--force-with-lease=refs/heads/{state_branch}:{expected_sha}",
        "origin", f"HEAD:refs/heads/{state_branch}",
    ]
    return subprocess.run(
        cmd, cwd=state_dir, check=False, text=True, env=env,
        creationflags=SUBPROCESS_FLAGS,
    ).returncode == 0


def commit_staged_state(state_dir: Path, message: str, expected_sha: str) -> bool:
    enabled = snapshot_mode()
    generation = snapshot_generation(expected_sha, cwd=state_dir) if expected_sha else 0
    if generation and not enabled:
        raise RuntimeError("DATA_BRANCH_SNAPSHOTS must remain true for snapshot branches")
    diff = run(
        ["git", "diff", "--cached", "--quiet"], cwd=state_dir, check=False,
    )
    if diff.returncode not in (0, 1):
        raise RuntimeError(f"failed to check staged data: exit code {diff.returncode}")
    unchanged = diff.returncode == 0
    if unchanged and (not enabled or generation):
        print("no state changes to push", file=sys.stderr)
        return False
    if not enabled:
        run(["git", "commit", "-m", message], cwd=state_dir)
        return True

    tree = subprocess.run(
        ["git", "write-tree"], cwd=state_dir, capture_output=True,
        text=True, check=True, creationflags=SUBPROCESS_FLAGS,
    ).stdout.strip()
    snapshot = subprocess.run(
        ["git", "commit-tree", tree],
        input=f"{message}\n\n{SNAPSHOT_GENERATION_TRAILER}: {generation + 1}\n",
        cwd=state_dir, capture_output=True, text=True, check=True,
        creationflags=SUBPROCESS_FLAGS,
    ).stdout.strip()
    run(["git", "update-ref", "HEAD", snapshot, expected_sha], cwd=state_dir)
    return True


def configure_git() -> None:
    set_git_config("user.email", "otelbot@users.noreply.github.com")
    set_git_config("user.name", "otelbot")


def is_config_lock_contention(stderr: str) -> bool:
    message = stderr.lower()
    return "could not lock config file" in message and "file exists" in message


def set_git_config(name: str, value: str) -> None:
    """Set one repository config value, tolerating a contended config lock.

    A queue drain processes several repositories concurrently against a single
    checkout, so two writers can reach `.git/config.lock` at the same time.
    """
    for attempt in range(1, CONFIG_LOCK_ATTEMPTS + 1):
        proc = subprocess.run(
            ["git", "config", name, value],
            check=False,
            text=True,
            capture_output=True,
            creationflags=SUBPROCESS_FLAGS,
        )
        if proc.returncode == 0:
            return
        if not is_config_lock_contention(proc.stderr) or attempt == CONFIG_LOCK_ATTEMPTS:
            raise subprocess.CalledProcessError(
                proc.returncode,
                proc.args,
                output=proc.stdout,
                stderr=proc.stderr,
            )
        time.sleep(retry_delay_seconds(attempt))


def copy_snapshots(snapshots: list[tuple[Path, Path]]) -> None:
    for source, destination in snapshots:
        if source.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)


def retry_delay_seconds(attempt: int) -> float:
    # Full jitter so concurrent writers de-synchronize instead of colliding again.
    ceiling = min(RETRY_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)), RETRY_BACKOFF_MAX_SECONDS)
    return random.uniform(0, ceiling)


def push_state_changes(
    state_dir: Path,
    commit_message: str,
    update_state: Callable[[], int],
    *,
    state_branch: str,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    add_paths: list[str] | None = None,
    retry_snapshots: list[tuple[Path, Path]] | None = None,
) -> int:
    configure_git()
    expected_sha = checkout_state(state_dir, state_branch, require_existing=False)
    paths_to_add = add_paths or ["."]
    snapshots = retry_snapshots or []

    for attempt in range(1, max_attempts + 1):
        (state_dir / ".publisher-lock.json").unlink(missing_ok=True)
        status = update_state()
        if status != 0:
            return status

        run(["git", "add", "--", *paths_to_add], cwd=state_dir)
        if run(
            ["git", "diff", "--quiet", "--", ".publisher-lock.json"],
            cwd=state_dir,
            check=False,
        ).returncode != 0:
            run(
                ["git", "add", "--update", "--", ".publisher-lock.json"],
                cwd=state_dir,
            )
        if not commit_staged_state(state_dir, commit_message, expected_sha):
            return 0

        copy_snapshots(snapshots)

        if push_state(state_dir, state_branch, expected_sha):
            print(f"state pushed on attempt {attempt}", file=sys.stderr)
            return 0

        if attempt >= max_attempts:
            print(f"CAS retry exhausted after {attempt} attempt(s)", file=sys.stderr)
            return 1

        delay = retry_delay_seconds(attempt)
        print(
            f"push rejected (attempt {attempt}); refetching and retrying in {delay:.2f}s",
            file=sys.stderr,
        )
        time.sleep(delay)
        if not reset_state(state_dir, state_branch):
            return 1
        expected_sha = ref_oid("HEAD", cwd=state_dir)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    checkout = subparsers.add_parser("checkout", help="check out the accepted state branch")
    checkout.add_argument("--state-branch", required=True)
    checkout.add_argument("--state-dir", type=Path, required=True)
    commit = subparsers.add_parser("commit-data", help="commit and push a collected data tree")
    commit.add_argument("--state-branch", required=True)
    commit.add_argument("--state-dir", type=Path, required=True)
    commit.add_argument("--expected-sha", required=True)
    commit.add_argument("--message", required=True)
    args = parser.parse_args()

    if args.command == "checkout":
        configure_git()
        checkout_state(args.state_dir, args.state_branch, require_existing=True)
        return 0
    if args.command == "commit-data":
        run(["git", "add", "--all"], cwd=args.state_dir)
        if not commit_staged_state(args.state_dir, args.message, args.expected_sha):
            return 0
        if not push_state(args.state_dir, args.state_branch, args.expected_sha):
            print("data push rejected; collection will resume from the accepted checkpoint", file=sys.stderr)
            return 1
        return 0
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
