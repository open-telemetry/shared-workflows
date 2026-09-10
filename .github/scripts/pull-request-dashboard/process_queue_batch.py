from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
import tempfile
import threading
from collections import defaultdict
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from queue_worker_client import QueueWorkerClient, acknowledge_all
import state_branch

SCRIPT_DIR = Path(__file__).resolve().parent
OWNER = "open-telemetry"
STATE_BRANCH_PREFIX = "otelbot/pull-request-dashboard-state"
MAX_ATTEMPTS = 3
PUBLISHER_LOCK_RETRY_AFTER_MS = 5 * 60 * 1000


class LeaseMonitor:
    def __init__(
        self,
        client: QueueWorkerClient,
        generation: int,
        worker_id: str,
        *,
        interval_seconds: int = 240,
    ) -> None:
        self.client = client
        self.generation = generation
        self.worker_id = worker_id
        self.interval_seconds = interval_seconds
        self.stop_event = threading.Event()
        self.lost_event = threading.Event()
        self.error: Exception | None = None
        self.thread = threading.Thread(target=self._heartbeat, daemon=True)

    def start(self) -> None:
        self._send_heartbeat()
        self.thread.start()

    def close(self) -> None:
        self.stop_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout=5)

    def assert_valid(self) -> None:
        if self.lost_event.is_set():
            raise RuntimeError(f"queue lease heartbeat failed: {self.error}")

    def _heartbeat(self) -> None:
        while not self.stop_event.wait(self.interval_seconds):
            try:
                self._send_heartbeat()
            except Exception as error:
                self.error = error
                self.lost_event.set()
                return

    def _send_heartbeat(self) -> None:
        result = self.client.call(
            "heartbeat",
            generation=self.generation,
            workerId=self.worker_id,
        )
        if result.get("dispatcher") is not True:
            raise RuntimeError("queue dispatcher heartbeat was rejected")


@dataclass(frozen=True)
class Claim:
    item_key: str
    claim_generation: int
    repository: str
    pr_number: int | None
    head_sha: str
    attempts: int


@dataclass(frozen=True)
class WorkItem:
    repository: str
    pr_number: int
    claims: tuple[Claim, ...]


class CommandFailedError(RuntimeError):
    def __init__(self, command: list[str], returncode: int) -> None:
        super().__init__(f"command failed with exit code {returncode}: {' '.join(command)}")
        self.returncode = returncode


@contextmanager
def queue_publisher_lock(state_branch_name: str, owner: str) -> Iterator[None]:
    with state_branch.publisher_lock(state_branch_name, owner, wait_seconds=0):
        yield


def load_claims(path: Path) -> list[Claim]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("claims file must contain a JSON array")
    claims = []
    for value in raw:
        if not isinstance(value, dict):
            raise ValueError("claim must be a JSON object")
        claim = Claim(
            item_key=required_string(value, "itemKey"),
            claim_generation=required_positive_int(value, "claimGeneration"),
            repository=required_string(value, "repository"),
            pr_number=optional_positive_int(value.get("prNumber"), "prNumber"),
            head_sha=value.get("headSha") or "",
            attempts=non_negative_int(value.get("attempts", 0), "attempts"),
        )
        if (claim.pr_number is None) == (not claim.head_sha):
            raise ValueError(f"claim {claim.item_key} must identify one PR or head SHA")
        claims.append(claim)
    return claims


def resolve_work_items(
    claims: list[Claim],
    resolve_head: Callable[[str, str], int | None],
) -> tuple[list[WorkItem], list[dict[str, Any]]]:
    grouped: dict[tuple[str, int], list[Claim]] = defaultdict(list)
    completed: list[dict[str, Any]] = []
    for claim in claims:
        try:
            pr_number = claim.pr_number or resolve_head(claim.repository, claim.head_sha)
        except state_branch.PublisherLockTimeoutError as error:
            completed.extend(publisher_lock_acknowledgments((claim,), error))
            continue
        except Exception as error:
            completed.extend(failure_acknowledgments((claim,), error))
            continue
        if pr_number is None:
            completed.append(acknowledgment(claim, "success"))
            continue
        grouped[(claim.repository, pr_number)].append(claim)
    work = [
        WorkItem(repository, pr_number, tuple(item_claims))
        for (repository, pr_number), item_claims in sorted(grouped.items())
    ]
    return work, completed


def group_by_repository(work_items: list[WorkItem]) -> dict[str, list[WorkItem]]:
    grouped: dict[str, list[WorkItem]] = defaultdict(list)
    for item in work_items:
        grouped[item.repository].append(item)
    for items in grouped.values():
        items.sort(key=lambda item: item.pr_number)
    return dict(sorted(grouped.items()))


def process_batch(
    work_items: list[WorkItem],
    process_repository: Callable[[str, list[WorkItem]], list[dict[str, Any]]],
    *,
    max_repositories: int = 4,
    on_results: Callable[[list[dict[str, Any]]], None] | None = None,
) -> list[dict[str, Any]]:
    if max_repositories < 1:
        raise ValueError("max_repositories must be positive")
    repositories = group_by_repository(work_items)
    results: list[dict[str, Any]] = []
    callback_errors: list[Exception] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_repositories) as executor:
        futures = {
            executor.submit(process_repository, repository, items): repository
            for repository, items in repositories.items()
        }
        for future in concurrent.futures.as_completed(futures):
            repository = futures[future]
            try:
                repository_results = future.result()
            except Exception as error:
                repository_results = []
                for item in repositories[repository]:
                    repository_results.extend(failure_acknowledgments(item.claims, error))
            results.extend(repository_results)
            if on_results is not None:
                try:
                    on_results(repository_results)
                except Exception as error:
                    callback_errors.append(error)
    if callback_errors:
        raise RuntimeError(
            f"{len(callback_errors)} incremental acknowledgment(s) failed: "
            + "; ".join(str(error) for error in callback_errors)
        )
    return sorted(results, key=lambda result: result["itemKey"])


class DashboardBatchProcessor:
    def __init__(
        self,
        config_path: Path,
        *,
        script_dir: Path = SCRIPT_DIR,
        env: dict[str, str] | None = None,
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        lease_check: Callable[[], None] | None = None,
        publisher_lock: Callable[[str, str], AbstractContextManager[None]] = (
            state_branch.publisher_lock
        ),
        publisher_lock_owner: str = "queue-worker",
    ) -> None:
        self.script_dir = script_dir
        self.base_env = dict(env or os.environ)
        self.run = run
        self.lease_check = lease_check
        self.publisher_lock = publisher_lock
        self.publisher_lock_owner = publisher_lock_owner
        config = json.loads(config_path.read_text(encoding="utf-8"))
        self.config = {
            entry["name"]: entry
            for entry in config
            if isinstance(entry, dict) and isinstance(entry.get("name"), str)
        }

    def resolve_head(self, repository: str, head_sha: str) -> int | None:
        self._assert_publisher_unlocked(repository)
        result = self._run(
            [
                "gh",
                "api",
                f"repos/{OWNER}/{repository}/commits/{head_sha}/pulls",
            ],
            env=self.base_env,
        )
        pull_requests = json.loads(result.stdout)
        matches = sorted(
            pull_request["number"]
            for pull_request in pull_requests
            if pull_request.get("state") == "open"
            and (pull_request.get("head") or {}).get("sha") == head_sha
            and isinstance(pull_request.get("number"), int)
        )
        return matches[0] if matches else None

    def _assert_publisher_unlocked(self, repository: str) -> None:
        state_branch_name = f"{STATE_BRANCH_PREFIX}/{repository}"
        with state_branch.temporary_state_dir() as state_dir:
            state_branch.configure_git()
            state_branch.checkout_state(
                state_dir,
                state_branch_name,
                require_existing=False,
            )
            state_branch.wait_for_publisher_unlock(
                state_dir,
                state_branch_name,
                wait_seconds=0,
            )

    def process_repository(
        self,
        repository: str,
        items: list[WorkItem],
    ) -> list[dict[str, Any]]:
        config = self.config.get(repository)
        if config is None:
            return [
                acknowledgment(claim, "dead", f"repository is not configured: {repository}")
                for item in items
                for claim in item.claims
            ]
        cache_dir = self.script_dir / ".cache" / "classifications" / repository
        worker_temp = self.script_dir / ".cache" / "queue-workers" / repository
        worker_temp.mkdir(parents=True, exist_ok=True)
        env = {
            **self.base_env,
            "PR_DASHBOARD_CLASSIFICATION_CACHE_DIR": str(cache_dir),
            "RUNNER_TEMP": str(worker_temp),
            "REPO_NAME": repository,
            "REQUIRED_APPROVALS": str(config.get("required_approvals", 1)),
            "APPROVER_TEAMS_JSON": json.dumps(config.get("approver_teams", [])),
            "NON_BLOCKING_CHECK_PATTERNS_JSON": json.dumps(
                config.get("non_blocking_check_patterns", [])
            ),
            "REQUIRE_CLEAN_COPILOT_REVIEW_BRANCHES_JSON": json.dumps(
                config.get("require_clean_copilot_review_branches", [])
            ),
            "SLACK_CHANNEL": config.get("slack_channel", ""),
            "SLACK_USER_MAP_JSON": json.dumps(config.get("slack_user_mapping", {})),
        }
        state_branch_name = f"{STATE_BRANCH_PREFIX}/{repository}"
        results: list[dict[str, Any]] = []
        ready: list[WorkItem] = []

        try:
            initial_backfill_complete = self._initial_backfill_complete(
                repository, state_branch_name, env
            )
        except Exception as error:
            return [
                result
                for item in items
                for result in failure_acknowledgments(item.claims, error)
            ]
        if not initial_backfill_complete:
            return [
                acknowledgment(claim, "success")
                for item in items
                for claim in item.claims
            ]

        for index, item in enumerate(items):
            try:
                self._update_dashboard(
                    repository,
                    item.pr_number,
                    state_branch_name,
                    config,
                    env,
                )
                ready.append(item)
            except CommandFailedError as error:
                if error.returncode == state_branch.PUBLISHER_LOCK_BUSY_STATUS:
                    deferred = [*ready, *items[index:]]
                    for deferred_item in deferred:
                        results.extend(
                            publisher_lock_acknowledgments(
                                deferred_item.claims,
                                error,
                            )
                        )
                    return results
                results.extend(failure_acknowledgments(item.claims, error))
            except Exception as error:
                results.extend(failure_acknowledgments(item.claims, error))

        if not ready:
            return results

        locked_results: list[dict[str, Any]] = []
        successful: list[WorkItem] = []
        publish_active = False
        try:
            with self.publisher_lock(state_branch_name, self.publisher_lock_owner):
                for item in ready:
                    delivery_active, delivery_error = self._deliver(
                        repository,
                        item.pr_number,
                        state_branch_name,
                        env,
                    )
                    publish_active = delivery_active or publish_active
                    if delivery_error is not None:
                        locked_results.extend(
                            failure_acknowledgments(item.claims, delivery_error)
                        )
                        continue
                    successful.append(item)

                if publish_active:
                    try:
                        self._publish(repository, state_branch_name, config, env)
                    except Exception as error:
                        for item in successful:
                            locked_results.extend(failure_acknowledgments(item.claims, error))
                        successful = []

                for item in successful:
                    locked_results.extend(
                        acknowledgment(claim, "success") for claim in item.claims
                    )
        except state_branch.PublisherLockTimeoutError as error:
            for item in ready:
                results.extend(
                    publisher_lock_acknowledgments(
                        item.claims,
                        error,
                    )
                )
        except Exception as error:
            for item in ready:
                results.extend(failure_acknowledgments(item.claims, error))
        else:
            results.extend(locked_results)
        return results

    def _initial_backfill_complete(
        self,
        repository: str,
        state_branch: str,
        env: dict[str, str],
    ) -> bool:
        result = self._run(
            [
                sys.executable,
                str(self.script_dir / "state.py"),
                "--repo",
                repository,
                "--state-branch",
                state_branch,
            ],
            env=env,
        )
        value = result.stdout.strip().splitlines()[-1]
        if value not in {"true", "false"}:
            raise RuntimeError(f"unexpected initial backfill result: {value}")
        return value == "true"

    def _update_dashboard(
        self,
        repository: str,
        pr_number: int,
        state_branch: str,
        config: dict[str, Any],
        env: dict[str, str],
    ) -> None:
        with tempfile.NamedTemporaryFile() as github_output:
            command = [
                sys.executable,
                str(self.script_dir / "dashboard.py"),
                "--state-branch",
                state_branch,
                "--repo",
                repository,
                "--pr-number",
                str(pr_number),
                "--required-approvals",
                str(config.get("required_approvals", 1)),
                "--publisher-lock-wait-seconds",
                "0",
                "--github-output",
                github_output.name,
            ]
            for team in config.get("approver_teams", []):
                command.extend(["--approver-team", team])
            for pattern in config.get("non_blocking_check_patterns", []):
                command.extend(["--non-blocking-check-pattern", pattern])
            for branch in config.get("require_clean_copilot_review_branches", []):
                command.extend(["--require-clean-copilot-review-branch", branch])
            self._run(command, env=env)

    def _deliver(
        self,
        repository: str,
        pr_number: int,
        state_branch: str,
        env: dict[str, str],
    ) -> tuple[bool, Exception | None]:
        with tempfile.NamedTemporaryFile(delete=False) as github_output:
            output_path = Path(github_output.name)
        try:
            error = None
            try:
                self._run(
                    [
                        sys.executable,
                        str(self.script_dir / "delivery.py"),
                        "--state-branch",
                        state_branch,
                        "--repo",
                        repository,
                        "--pr-number",
                        str(pr_number),
                        "--github-output",
                        str(output_path),
                    ],
                    env=env,
                )
            except Exception as caught:
                error = caught
            output = output_path.read_text(encoding="utf-8")
            return "active=true" in output.splitlines(), error
        finally:
            output_path.unlink(missing_ok=True)

    def _publish(
        self,
        repository: str,
        state_branch: str,
        config: dict[str, Any],
        env: dict[str, str],
    ) -> None:
        command = [
            sys.executable,
            str(self.script_dir / "publish_dashboard.py"),
            "--state-branch",
            state_branch,
            "--repo",
            repository,
            "--labels-to-display-json",
            json.dumps(config.get("labels_to_display", [])),
        ]
        if config.get("large_repo", False):
            command.append("--large-repo")
        self._run(command, env=env)

    def _run(
        self,
        command: list[str],
        *,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        if self.lease_check is None:
            result = self.run(
                command,
                cwd=self.script_dir.parents[2],
                env=env,
                text=True,
                capture_output=True,
            )
        else:
            self.lease_check()
            process = subprocess.Popen(
                command,
                cwd=self.script_dir.parents[2],
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            while True:
                try:
                    stdout, stderr = process.communicate(timeout=1)
                    break
                except subprocess.TimeoutExpired:
                    try:
                        self.lease_check()
                    except Exception:
                        process.terminate()
                        process.communicate()
                        raise
            result = subprocess.CompletedProcess(
                command,
                process.returncode,
                stdout,
                stderr,
            )
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        if result.returncode != 0:
            raise CommandFailedError(command, result.returncode)
        return result


def acknowledgment(
    claim: Claim,
    outcome: str,
    error: str = "",
    retry_after_ms: int = 0,
) -> dict[str, Any]:
    return {
        "itemKey": claim.item_key,
        "claimGeneration": claim.claim_generation,
        "outcome": outcome,
        "error": error[:1000],
        "retryAfterMs": retry_after_ms,
    }


def failure_acknowledgments(
    claims: tuple[Claim, ...],
    error: Exception,
) -> list[dict[str, Any]]:
    message = str(error)
    return [
        acknowledgment(
            claim,
            "dead" if claim.attempts + 1 >= MAX_ATTEMPTS else "retry",
            message,
        )
        for claim in claims
    ]


def publisher_lock_acknowledgments(
    claims: tuple[Claim, ...],
    error: Exception,
) -> list[dict[str, Any]]:
    message = str(error)
    return [
        acknowledgment(
            claim,
            "retry",
            message,
            PUBLISHER_LOCK_RETRY_AFTER_MS,
        )
        for claim in claims
    ]


def required_string(value: dict[str, Any], name: str) -> str:
    result = value.get(name)
    if not isinstance(result, str) or not result:
        raise ValueError(f"{name} must be a non-empty string")
    return result


def required_positive_int(value: dict[str, Any], name: str) -> int:
    result = value.get(name)
    if not isinstance(result, int) or isinstance(result, bool) or result < 1:
        raise ValueError(f"{name} must be a positive integer")
    return result


def optional_positive_int(value: Any, name: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def non_negative_int(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Process a claimed dashboard queue batch.")
    parser.add_argument("--claims", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument(
        "--config",
        type=Path,
        default=SCRIPT_DIR / "repositories.json",
    )
    parser.add_argument("--max-repositories", type=int, default=4)
    parser.add_argument("--queue-endpoint", required=True)
    parser.add_argument("--dispatcher-generation", type=int, required=True)
    parser.add_argument("--worker-id", required=True)
    args = parser.parse_args()

    claims = load_claims(args.claims)
    client = QueueWorkerClient(args.queue_endpoint)
    monitor = LeaseMonitor(
        client,
        args.dispatcher_generation,
        args.worker_id,
    )
    work_items: list[WorkItem] = []
    results: list[dict[str, Any]] = []
    common = {
        "generation": args.dispatcher_generation,
        "workerId": args.worker_id,
    }

    def record_and_acknowledge(completed: list[dict[str, Any]]) -> None:
        if not completed:
            return
        results.extend(completed)
        args.results.write_text(json.dumps(completed, indent=2) + "\n", encoding="utf-8")
        acknowledge_all(client, args.results, common)

    try:
        monitor.start()
        processor = DashboardBatchProcessor(
            args.config,
            lease_check=monitor.assert_valid,
            publisher_lock=queue_publisher_lock,
            publisher_lock_owner=args.worker_id,
        )
        work_items, resolved = resolve_work_items(claims, processor.resolve_head)
        record_and_acknowledge(resolved)
        process_batch(
            work_items,
            processor.process_repository,
            max_repositories=args.max_repositories,
            on_results=record_and_acknowledge,
        )
    finally:
        args.results.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
        monitor.close()
    dead_letters = sum(result["outcome"] == "dead" for result in results)
    retries = sum(result["outcome"] == "retry" for result in results)
    print(
        json.dumps(
            {
                "claims": len(claims),
                "work_items": len(work_items),
                "successes": len(results) - retries - dead_letters,
                "retries": retries,
                "dead_letters": dead_letters,
            },
            sort_keys=True,
        )
    )
    return 1 if dead_letters else 0


if __name__ == "__main__":
    sys.exit(main())
