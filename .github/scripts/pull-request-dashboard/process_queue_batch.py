from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from collections import defaultdict
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from head_resolution import matching_open_pr_numbers
from execution_process import run_monitored, subprocess_options
from queue_worker_client import QueueWorkerClient, acknowledge_results
import state_branch as state_branch_git

SCRIPT_DIR = Path(__file__).resolve().parent
OWNER = "open-telemetry"
MAX_ATTEMPTS = 3


class LeaseMonitor:
    def __init__(
        self,
        client: QueueWorkerClient,
        generation: int,
        worker_id: str,
        *,
        interval_seconds: float = 240,
        now: Callable[[], float] = time.monotonic,
        processing_deadline: float | None = None,
    ) -> None:
        self.client = client
        self.generation = generation
        self.worker_id = worker_id
        self.interval_seconds = interval_seconds
        self.now = now
        self.valid_until = 0.0
        self.processing_deadline = processing_deadline
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
        if self.now() >= self.valid_until:
            raise RuntimeError("queue lease safety deadline expired")
        if self.processing_deadline is not None and time.time() >= self.processing_deadline:
            raise RuntimeError("queue processing deadline expired")

    def _heartbeat(self) -> None:
        while not self.stop_event.wait(self.interval_seconds):
            try:
                self._send_heartbeat()
            except Exception as error:
                self.error = error
                self.lost_event.set()
                return

    def _send_heartbeat(self) -> None:
        started_at = self.now()
        result = self.client.call(
            "heartbeat",
            generation=self.generation,
            workerId=self.worker_id,
        )
        if result.get("dispatcher") is not True:
            raise RuntimeError("queue dispatcher heartbeat was rejected")
        duration = result.get("leaseDurationMs", 15 * 60 * 1000)
        if not isinstance(duration, int) or duration <= 60_000:
            raise RuntimeError("queue heartbeat returned an invalid lease duration")
        # Stop before the server can hand ownership to a replacement. Network
        # time belongs to the lease, not to this worker's execution allowance.
        self.valid_until = started_at + duration / 1000 - 60

    @contextmanager
    def delivery_lease(self, repository: str) -> Iterator[None]:
        common = {
            "repository": repository,
            "generation": self.generation,
            "workerId": self.worker_id,
        }
        while True:
            self.assert_valid()
            result = self.client.call("acquire-delivery", **common)
            if result.get("acquired") is True:
                lease_generation = required_positive_int(result, "leaseGeneration")
                break
            if result.get("acquired") is not False:
                raise RuntimeError("queue did not confirm repository delivery ownership")
            if self.stop_event.wait(2):
                raise RuntimeError("queue lease monitor stopped while waiting for delivery")
        try:
            self.assert_valid()
            yield
        finally:
            result = self.client.call(
                "release-delivery", leaseGeneration=lease_generation, **common
            )
            if result.get("released") is not True:
                raise RuntimeError("repository delivery lease release was rejected")


@dataclass(frozen=True)
class Claim:
    item_key: str
    claim_generation: int
    repository: str
    pr_number: int | None
    head_sha: str
    attempts: int
    trigger_events: tuple[str, ...] = ()
    kind: str = "refresh"


@dataclass(frozen=True)
class WorkItem:
    repository: str
    pr_number: int | None
    claims: tuple[Claim, ...]
    kind: str = "refresh"


def load_claims(path: Path) -> list[Claim]:
    return parse_claims(json.loads(path.read_text(encoding="utf-8")))


def parse_claims(raw: Any) -> list[Claim]:
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
            trigger_events=string_tuple(value.get("triggerEvents"), "triggerEvents"),
            kind=value.get("kind", "refresh"),
        )
        if not isinstance(claim.kind, str) or claim.kind not in {"refresh", "backfill", "reminders"}:
            raise ValueError(f"claim {claim.item_key} has an invalid kind")
        if not isinstance(claim.head_sha, str):
            raise ValueError(f"claim {claim.item_key} has an invalid head SHA")
        if claim.kind != "refresh" and (claim.pr_number is not None or claim.head_sha):
            raise ValueError(f"claim {claim.item_key} must not identify a PR or head SHA")
        if claim.kind == "refresh" and (claim.pr_number is None) == (not claim.head_sha):
            raise ValueError(f"claim {claim.item_key} must identify one PR or head SHA")
        claims.append(claim)
    return claims


def process_claims(
    claims: list[Claim],
    results_path: Path,
    client: QueueWorkerClient,
    generation: int,
    worker_id: str,
    *,
    config_path: Path = SCRIPT_DIR / "repositories.json",
    max_repositories: int = 4,
    processor_env: dict[str, str] | None = None,
    lease_monitor: LeaseMonitor | None = None,
    script_dir: Path = SCRIPT_DIR,
    python_executable: str = sys.executable,
    repository_root: Path = SCRIPT_DIR.parents[2],
) -> tuple[dict[str, int], list[dict[str, Any]]]:
    monitor = lease_monitor or LeaseMonitor(client, generation, worker_id)
    owns_monitor = lease_monitor is None
    work_items: list[WorkItem] = []
    results: list[dict[str, Any]] = []
    common = {"generation": generation, "workerId": worker_id}

    def record_and_acknowledge(completed: list[dict[str, Any]]) -> None:
        if not completed:
            return

        def record_acknowledged(result: dict[str, Any]) -> None:
            results.append(result)
            results_path.write_text(
                json.dumps(results, indent=2) + "\n",
                encoding="utf-8",
            )

        acknowledge_results(
            client,
            completed,
            common,
            on_acknowledged=record_acknowledged,
        )

    try:
        if owns_monitor:
            monitor.start()
        processor = DashboardBatchProcessor(
            config_path,
            env=processor_env,
            lease_check=monitor.assert_valid,
            delivery_lease=monitor.delivery_lease,
            script_dir=script_dir,
            python_executable=python_executable,
            repository_root=repository_root,
        )
        work_items, resolved = resolve_work_items(claims, processor.resolve_head)
        record_and_acknowledge(resolved)
        process_batch(
            work_items,
            processor.process_repository,
            max_repositories=max_repositories,
            on_results=record_and_acknowledge,
        )
    finally:
        results_path.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
        if owns_monitor:
            monitor.close()
    dead_letters = sum(result["outcome"] == "dead" for result in results)
    retries = sum(result["outcome"] == "retry" for result in results)
    continues = sum(result["outcome"] == "continue" for result in results)
    return (
        {
            "claims": len(claims),
            "work_items": len(work_items),
            "successes": len(results) - retries - dead_letters - continues,
            "continues": continues,
            "retries": retries,
            "dead_letters": dead_letters,
        },
        results,
    )


def resolve_work_items(
    claims: list[Claim],
    resolve_head: Callable[[str, str], tuple[int, ...]],
) -> tuple[list[WorkItem], list[dict[str, Any]]]:
    grouped: dict[tuple[str, int | None, str], list[Claim]] = defaultdict(list)
    completed: list[dict[str, Any]] = []
    for claim in claims:
        if claim.kind != "refresh":
            grouped[(claim.repository, None, claim.kind)].append(claim)
            continue
        try:
            pr_numbers = (
                (claim.pr_number,)
                if claim.pr_number is not None
                else resolve_head(claim.repository, claim.head_sha)
            )
        except Exception as error:
            completed.extend(failure_acknowledgments((claim,), error))
            continue
        if not pr_numbers:
            completed.append(acknowledgment(claim, "success"))
            continue
        for pr_number in pr_numbers:
            grouped[(claim.repository, pr_number, "refresh")].append(claim)
    work = [
        WorkItem(repository, pr_number, tuple(item_claims), kind)
        for (repository, pr_number, kind), item_claims in sorted(
            grouped.items(), key=lambda entry: (entry[0][0], entry[0][2], entry[0][1] or 0)
        )
    ]
    return work, completed


def coalesce_acknowledgments(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    combined: dict[str, dict[str, Any]] = {}
    severity = {"success": 0, "continue": 1, "retry": 2, "dead": 3}
    for result in results:
        key = result["itemKey"]
        previous = combined.get(key)
        if previous is None or severity[result["outcome"]] > severity[previous["outcome"]]:
            combined[key] = result
    return list(combined.values())


def group_by_repository(work_items: list[WorkItem]) -> dict[str, list[WorkItem]]:
    grouped: dict[str, list[WorkItem]] = defaultdict(list)
    for item in work_items:
        grouped[item.repository].append(item)
    for items in grouped.values():
        items.sort(key=lambda item: (item.kind, item.pr_number or 0))
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
                repository_results = coalesce_acknowledgments(future.result())
            except Exception as error:
                repository_results = []
                for item in repositories[repository]:
                    repository_results.extend(failure_acknowledgments(item.claims, error))
                repository_results = coalesce_acknowledgments(repository_results)
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
        delivery_lease: Callable[[str], AbstractContextManager[None]] = nullcontext,
        python_executable: str = sys.executable,
        repository_root: Path = SCRIPT_DIR.parents[2],
    ) -> None:
        self.script_dir = script_dir
        self.base_env = dict(os.environ if env is None else env)
        self.run = run
        self.lease_check = lease_check
        self.delivery_lease = delivery_lease
        self.python_executable = python_executable
        self.repository_root = repository_root
        config = json.loads(config_path.read_text(encoding="utf-8"))
        self.config = {
            entry["name"]: entry
            for entry in config
            if isinstance(entry, dict) and isinstance(entry.get("name"), str)
        }

    def resolve_head(self, repository: str, head_sha: str) -> tuple[int, ...]:
        def fetch(path: str) -> list[dict[str, Any]]:
            result = self._run(
                ["gh", "api", "--paginate", "--slurp", path],
                env=self.base_env,
                print_stdout=False,
            )
            pages = json.loads(result.stdout)
            if not isinstance(pages, list) or not all(
                isinstance(page, list) for page in pages
            ):
                raise RuntimeError("head pull request lookup returned invalid JSON")
            return [pull_request for page in pages for pull_request in page]

        return matching_open_pr_numbers(
            fetch(f"repos/{OWNER}/{repository}/pulls?state=open&per_page=100"),
            head_sha,
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
        cache_dir = Path(self.base_env.get(
            "PR_DASHBOARD_CLASSIFICATION_CACHE_ROOT",
            str(self.script_dir / ".cache" / "classifications"),
        )) / repository
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
        state_branch = f"{state_branch_git.STATE_BRANCH_PREFIX}/{repository}"
        results: list[dict[str, Any]] = []
        ready: list[WorkItem] = []
        failed_updates: set[tuple[str, int | None]] = set()
        reminders = [item for item in items if item.kind == "reminders"]
        items = [item for item in items if item.kind != "reminders"]

        try:
            initial_backfill_complete = not items or self._initial_backfill_complete(
                repository, state_branch, env
            )
        except Exception as error:
            results.extend(
                result
                for item in items
                for result in failure_acknowledgments(item.claims, error)
            )
            items = []
        for item in items:
            try:
                if self.lease_check:
                    self.lease_check()
                if item.kind == "refresh" and not initial_backfill_complete:
                    results.extend(acknowledgment(claim, "success") for claim in item.claims)
                    continue
                item_env = dict(env)
                if item.kind == "backfill" and any(
                    "schedule" in claim.trigger_events for claim in item.claims
                ):
                    item_env["PREPARE_AUTHOR_NUDGES"] = "true"
                self._update_dashboard(repository, item.pr_number, state_branch, config, item_env)
                if item.kind == "backfill":
                    initial_backfill_complete = self._initial_backfill_complete(
                        repository, state_branch, env
                    )
                    if not initial_backfill_complete:
                        results.extend(acknowledgment(claim, "continue") for claim in item.claims)
                        continue
                ready.append(item)
            except Exception as error:
                results.extend(failure_acknowledgments(item.claims, error))
                failed_updates.add((item.kind, item.pr_number))
                if item.kind == "backfill":
                    try:
                        initial_backfill_complete = self._initial_backfill_complete(
                            repository, state_branch, env
                        )
                    except Exception as state_error:
                        results.extend(failure_acknowledgments(item.claims, state_error))
                    else:
                        if initial_backfill_complete:
                            ready.append(item)

        if ready or reminders:
            try:
                with self.delivery_lease(repository):
                    results.extend(self._deliver_repository(
                        repository, state_branch, config, env, ready, reminders, failed_updates
                    ))
            except Exception as error:
                results.extend(
                    result
                    for item in ready + reminders
                    for result in failure_acknowledgments(item.claims, error)
                )
        return results

    def _deliver_repository(
        self,
        repository: str,
        state_branch: str,
        config: dict[str, Any],
        env: dict[str, str],
        ready: list[WorkItem],
        reminders: list[WorkItem],
        failed_updates: set[tuple[str, int | None]],
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        successful: list[WorkItem] = []
        publish_needed = False
        for item in ready:
            delivery_active, delivery_error, full_generation = self._deliver(
                repository, item.pr_number, state_branch, env
            )
            publish_needed = delivery_active or publish_needed
            if delivery_error is not None:
                results.extend(failure_acknowledgments(item.claims, delivery_error))
                continue
            if not delivery_active:
                results.extend(failure_acknowledgments(
                    item.claims, RuntimeError("dashboard delivery rejected the execution's versions")
                ))
                continue
            if full_generation:
                try:
                    self._publish(repository, state_branch, config, env)
                    publish_needed = False
                    self._complete_full_publish(repository, state_branch, full_generation, env)
                except Exception as error:
                    results.extend(failure_acknowledgments(item.claims, error))
                    continue
            successful.append(item)

        if publish_needed:
            try:
                self._publish(repository, state_branch, config, env)
            except Exception as error:
                for item in successful:
                    results.extend(failure_acknowledgments(item.claims, error))
                successful = []

        for item in successful:
            if (item.kind, item.pr_number) not in failed_updates:
                results.extend(acknowledgment(claim, "success") for claim in item.claims)
        for item in reminders:
            try:
                self._run(
                    [self.python_executable, str(self.script_dir / "refresh_author_nudges.py"), "--repo", repository],
                    env=env,
                )
            except Exception as error:
                results.extend(failure_acknowledgments(item.claims, error))
            else:
                results.extend(acknowledgment(claim, "success") for claim in item.claims)
        return results

    def _initial_backfill_complete(
        self,
        repository: str,
        state_branch: str,
        env: dict[str, str],
    ) -> bool:
        result = self._run(
            [
                self.python_executable,
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
        pr_number: int | None,
        state_branch: str,
        config: dict[str, Any],
        env: dict[str, str],
    ) -> None:
        env = dict(env)
        env["COPILOT_REVIEW_FALLBACK_AVAILABLE"] = (
            "true" if env.pop("COPILOT_REVIEW_FALLBACK_TOKEN", "") else "false"
        )
        with tempfile.TemporaryDirectory() as directory:
            github_output = Path(directory) / "output"
            command = [
                self.python_executable,
                str(self.script_dir / "dashboard.py"),
                "--state-branch",
                state_branch,
                "--repo",
                repository,
                "--required-approvals",
                str(config.get("required_approvals", 1)),
                "--github-output",
                str(github_output),
            ]
            if pr_number is not None:
                command.extend(["--pr-number", str(pr_number)])
            if env.get("PREPARE_AUTHOR_NUDGES") == "true":
                command.append("--prepare-author-nudges")
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
        pr_number: int | None,
        state_branch: str,
        env: dict[str, str],
    ) -> tuple[bool, Exception | None, int]:
        with tempfile.NamedTemporaryFile(delete=False) as github_output:
            output_path = Path(github_output.name)
        try:
            error = None
            try:
                command = [
                    self.python_executable,
                    str(self.script_dir / "delivery.py"),
                    "--state-branch",
                    state_branch,
                    "--delivery-state-branch",
                    state_branch_git.delivery_state_branch(state_branch),
                    "--repo",
                    repository,
                    "--github-output",
                    str(output_path),
                ]
                if pr_number is not None:
                    command.extend(["--pr-number", str(pr_number)])
                self._run(command, env=env)
            except Exception as caught:
                error = caught
            output = dict(
                line.split("=", 1)
                for line in output_path.read_text(encoding="utf-8").splitlines()
                if "=" in line
            )
            generation = int(output.get("full_publish_generation", "0"))
            return output.get("active") == "true", error, generation if error is None else 0
        finally:
            output_path.unlink(missing_ok=True)

    def _complete_full_publish(
        self,
        repository: str,
        state_branch: str,
        generation: int,
        env: dict[str, str],
    ) -> None:
        self._run(
            [
                self.python_executable,
                str(self.script_dir / "delivery.py"),
                "--state-branch",
                state_branch,
                "--delivery-state-branch",
                state_branch_git.delivery_state_branch(state_branch),
                "--repo",
                repository,
                "--complete-full-publish-generation",
                str(generation),
            ],
            env=env,
        )

    def _publish(
        self,
        repository: str,
        state_branch: str,
        config: dict[str, Any],
        env: dict[str, str],
    ) -> None:
        command = [
            self.python_executable,
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
        print_stdout: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        if self.lease_check is None:
            result = self.run(
                command,
                cwd=self.repository_root,
                env=env,
                text=True,
                encoding="utf-8",
                capture_output=True,
                **subprocess_options(),
            )
        else:
            result = run_monitored(command, self.lease_check, cwd=self.repository_root, env=env)
        if result.stdout and print_stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        if result.returncode != 0:
            raise RuntimeError(
                f"command failed with exit code {result.returncode}: {' '.join(command)}"
            )
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
            min(60_000 * 2**claim.attempts, 15 * 60_000),
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


def string_tuple(value: Any, name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise ValueError(f"{name} must be an array of non-empty strings")
    return tuple(value)


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
    parser.add_argument(
        "--lane", choices=("live", "maintenance"),
        default=os.environ.get("PR_DASHBOARD_QUEUE_LANE", "live"),
    )
    args = parser.parse_args()

    claims = load_claims(args.claims)
    client = QueueWorkerClient(args.queue_endpoint, lane=args.lane)
    summary, _results = process_claims(
        claims,
        args.results,
        client,
        args.dispatcher_generation,
        args.worker_id,
        config_path=args.config,
        max_repositories=args.max_repositories,
    )
    print(json.dumps(summary, sort_keys=True))
    return 1 if summary["dead_letters"] else 0


if __name__ == "__main__":
    sys.exit(main())
