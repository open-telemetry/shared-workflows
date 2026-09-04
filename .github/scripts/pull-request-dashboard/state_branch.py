#!/usr/bin/env python3
"""Manage the dashboard workflow's git-backed state branch."""

from __future__ import annotations

import argparse
import base64
from collections.abc import Callable
from collections.abc import Iterator
from contextlib import contextmanager
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from typing import TypedDict
import uuid
from pathlib import Path


DEFAULT_MAX_ATTEMPTS = 8
RETRY_BACKOFF_BASE_SECONDS = 0.5
RETRY_BACKOFF_MAX_SECONDS = 8.0
CONFIG_LOCK_ATTEMPTS = 5
PUBLISHER_LOCK_PATH = Path(".publisher-lock.json")
DEFAULT_PUBLISHER_LOCK_LEASE_SECONDS = 60 * 60
DEFAULT_PUBLISHER_LOCK_WAIT_SECONDS = 60 * 60
PUBLISHER_LOCK_POLL_SECONDS = 5
PUBLISHER_LOCK_BUSY_STATUS = 75


class PublisherLock(TypedDict):
    owner: str
    expiresAt: float


class PublisherLockTimeoutError(TimeoutError):
    pass


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
    return subprocess.run(cmd, check=check, cwd=cwd, text=True)


def remote_ref(state_branch: str) -> str:
    return f"refs/remotes/origin/{state_branch}"


def is_missing_remote_ref(stderr: str) -> bool:
    return "couldn't find remote ref" in stderr.lower()


def temporary_fetch_ref() -> str:
    return f"refs/pull-request-dashboard-fetch/{uuid.uuid4()}"


def ref_is_ancestor(ancestor: str, descendant: str) -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def ref_oid(ref: str, cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", "rev-parse", ref],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
        raise RuntimeError(f"failed to resolve Git ref {ref}: {message}")
    return result.stdout.strip()


def remote_is_behind_local(state_branch: str, fetched_ref: str) -> bool:
    if not has_state_branch(state_branch):
        return False
    return ref_is_ancestor(fetched_ref, remote_ref(state_branch))


def fetch_state_branch(state_branch: str, required: bool) -> bool:
    fetched_ref = temporary_fetch_ref()
    try:
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
        )
        if proc.returncode != 0:
            if not required and is_missing_remote_ref(proc.stderr):
                return False
            message = proc.stderr.strip() or proc.stdout.strip() or f"exit code {proc.returncode}"
            kind = "required" if required else "optional"
            raise RuntimeError(f"failed to fetch {kind} state branch {state_branch}: {message}")

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


def checkout_state(state_dir: Path, state_branch: str, require_existing: bool) -> None:
    remove_existing_state_dir(state_dir)
    fetch_state_branch(state_branch, required=require_existing)
    if has_state_branch(state_branch):
        run(["git", "worktree", "add", "-B", state_branch, str(state_dir), f"origin/{state_branch}"])
        return
    run(["git", "worktree", "add", "--detach", str(state_dir), "HEAD"])
    run(["git", "switch", "--orphan", state_branch], cwd=state_dir)
    run(["git", "rm", "-rf", "."], cwd=state_dir, check=False)


def reset_state(state_dir: Path, state_branch: str) -> bool:
    if not fetch_state_branch(state_branch, required=False):
        return False
    run(["git", "reset", "--hard", f"origin/{state_branch}"], cwd=state_dir)
    return True


def push_state(state_dir: Path, state_branch: str) -> bool:
    env = dict(os.environ)
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        credential = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = "http.https://github.com/.extraheader"
        env["GIT_CONFIG_VALUE_0"] = f"AUTHORIZATION: basic {credential}"
    cmd = ["git", "push", "--force-with-lease", "origin", state_branch]
    return subprocess.run(cmd, cwd=state_dir, check=False, text=True, env=env).returncode == 0


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


def load_publisher_lock(state_dir: Path) -> PublisherLock | None:
    path = state_dir / PUBLISHER_LOCK_PATH
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise RuntimeError(f"publisher lock is not valid JSON: {error}") from error
    if not isinstance(value, dict):
        raise RuntimeError("publisher lock has an invalid shape")
    owner = value.get("owner")
    expires_at = value.get("expiresAt")
    if (
        not isinstance(owner, str)
        or not owner
        or isinstance(expires_at, bool)
        or not isinstance(expires_at, (int, float))
    ):
        raise RuntimeError("publisher lock has an invalid shape")
    return {"owner": owner, "expiresAt": float(expires_at)}


def wait_for_publisher_unlock(
    state_dir: Path,
    state_branch: str,
    *,
    wait_seconds: int = DEFAULT_PUBLISHER_LOCK_WAIT_SECONDS,
    now: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    if wait_seconds < 0:
        raise ValueError("publisher lock wait must be non-negative")
    deadline = now() + wait_seconds
    announced_owner: str | None = None
    while True:
        current_time = now()
        lock = load_publisher_lock(state_dir)
        if lock is None or lock["expiresAt"] <= current_time:
            return
        if lock["owner"] != announced_owner:
            print(
                f"dashboard publisher lock {state_branch} is held by {lock['owner']}; waiting",
                file=sys.stderr,
            )
            announced_owner = lock["owner"]
        remaining = deadline - current_time
        if remaining <= 0:
            raise PublisherLockTimeoutError(
                f"timed out waiting for dashboard publisher lock {state_branch} "
                f"held by {lock['owner']}"
            )
        sleep(
            min(
                PUBLISHER_LOCK_POLL_SECONDS,
                remaining,
                lock["expiresAt"] - current_time,
            )
        )
        if not reset_state(state_dir, state_branch):
            raise RuntimeError(
                f"dashboard state branch {state_branch} disappeared while waiting "
                "for its publisher lock"
            )


def commit_publisher_lock(
    state_dir: Path,
    state_branch: str,
    message: str,
) -> bool:
    run(["git", "add", "--all", "--", str(PUBLISHER_LOCK_PATH)], cwd=state_dir)
    run(["git", "commit", "-m", message], cwd=state_dir)
    return push_state(state_dir, state_branch)


def acquire_publisher_lock(
    state_branch: str,
    owner: str,
    *,
    lease_seconds: int = DEFAULT_PUBLISHER_LOCK_LEASE_SECONDS,
    wait_seconds: int = DEFAULT_PUBLISHER_LOCK_WAIT_SECONDS,
    now: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    if not owner:
        raise ValueError("publisher lock owner must not be empty")
    if lease_seconds < 1 or wait_seconds < 0:
        raise ValueError("publisher lock lease must be positive and wait must be non-negative")
    deadline = now() + wait_seconds
    while True:
        current_time = now()
        with temporary_state_dir() as state_dir:
            configure_git()
            checkout_state(state_dir, state_branch, require_existing=True)
            lock = load_publisher_lock(state_dir)
            if (
                lock is not None
                and lock["owner"] == owner
                and lock["expiresAt"] > current_time
            ):
                return
            if lock is None or lock["expiresAt"] <= current_time:
                (state_dir / PUBLISHER_LOCK_PATH).write_text(
                    json.dumps(
                        {
                            "owner": owner,
                            "expiresAt": current_time + lease_seconds,
                        },
                        sort_keys=True,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                if commit_publisher_lock(
                    state_dir,
                    state_branch,
                    f"Acquire dashboard publisher lock for {owner}",
                ):
                    return
        remaining = deadline - now()
        if remaining <= 0:
            raise PublisherLockTimeoutError(
                f"timed out waiting for dashboard publisher lock {state_branch}"
            )
        sleep(min(PUBLISHER_LOCK_POLL_SECONDS, remaining))


def release_publisher_lock(state_branch: str, owner: str) -> None:
    for attempt in range(1, DEFAULT_MAX_ATTEMPTS + 1):
        with temporary_state_dir() as state_dir:
            configure_git()
            checkout_state(state_dir, state_branch, require_existing=True)
            lock = load_publisher_lock(state_dir)
            if lock is None:
                return
            if lock["owner"] != owner:
                raise RuntimeError(
                    f"dashboard publisher lock {state_branch} is owned by {lock['owner']}"
                )
            (state_dir / PUBLISHER_LOCK_PATH).unlink()
            if commit_publisher_lock(
                state_dir,
                state_branch,
                f"Release dashboard publisher lock for {owner}",
            ):
                return
        if attempt == DEFAULT_MAX_ATTEMPTS:
            break
        time.sleep(retry_delay_seconds(attempt))
    raise RuntimeError(f"failed to release dashboard publisher lock {state_branch}")


@contextmanager
def publisher_lock(
    state_branch: str,
    owner: str,
    *,
    wait_seconds: int = DEFAULT_PUBLISHER_LOCK_WAIT_SECONDS,
) -> Iterator[None]:
    acquire_publisher_lock(state_branch, owner, wait_seconds=wait_seconds)
    try:
        yield
    finally:
        release_publisher_lock(state_branch, owner)


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
    respect_publisher_lock: bool = False,
    publisher_lock_wait_seconds: int = DEFAULT_PUBLISHER_LOCK_WAIT_SECONDS,
) -> int:
    configure_git()
    checkout_state(state_dir, state_branch, require_existing=False)
    paths_to_add = add_paths or ["."]
    snapshots = retry_snapshots or []

    for attempt in range(1, max_attempts + 1):
        if respect_publisher_lock:
            wait_for_publisher_unlock(
                state_dir,
                state_branch,
                wait_seconds=publisher_lock_wait_seconds,
            )
        status = update_state()
        if status != 0:
            return status

        run(["git", "add", "--", *paths_to_add], cwd=state_dir)
        if run(["git", "diff", "--cached", "--quiet"], cwd=state_dir, check=False).returncode == 0:
            print("no state changes to push", file=sys.stderr)
            return 0

        run(["git", "commit", "-m", commit_message], cwd=state_dir)
        copy_snapshots(snapshots)

        if push_state(state_dir, state_branch):
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
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    checkout = subparsers.add_parser("checkout", help="check out the accepted state branch")
    checkout.add_argument("--state-branch", required=True)
    checkout.add_argument("--state-dir", type=Path, required=True)
    acquire_lock = subparsers.add_parser(
        "acquire-publisher-lock",
        help="acquire the repository publisher lock",
    )
    acquire_lock.add_argument("--state-branch", required=True)
    acquire_lock.add_argument("--owner", required=True)
    release_lock = subparsers.add_parser(
        "release-publisher-lock",
        help="release the repository publisher lock",
    )
    release_lock.add_argument("--state-branch", required=True)
    release_lock.add_argument("--owner", required=True)
    args = parser.parse_args()

    if args.command == "checkout":
        configure_git()
        checkout_state(args.state_dir, args.state_branch, require_existing=True)
        return 0
    if args.command == "acquire-publisher-lock":
        acquire_publisher_lock(args.state_branch, args.owner)
        return 0
    if args.command == "release-publisher-lock":
        release_publisher_lock(args.state_branch, args.owner)
        return 0
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
