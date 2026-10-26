from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from process_queue_batch import (
    SCRIPT_DIR,
    Claim,
    LeaseMonitor,
    failure_acknowledgments,
    parse_claims,
    process_claims,
)
from queue_worker_client import QueueWorkerClient, acknowledge_results
from report_rate_limits import report_rate_limits
from execution_code import ExecutionCode, ExecutionCodeLoader, stable_code_ref
from execution_process import subprocess_options

CLAIM_LIMIT = 16
ADMISSION_MARGIN_SECONDS = 10 * 60
MAXIMUM_EXCLUSIONS = 500
MAXIMUM_EXCLUSION_BYTES = 60 * 1024
MAXIMUM_REPOSITORIES = 4
TOKEN_HELPER = SCRIPT_DIR / "github_app_token.mjs"
CANARY_REPOSITORIES_EXAMPLE = '["opentelemetry-java-instrumentation"]'


@dataclass(frozen=True)
class JobResult:
    dead_letters: int
    retry_item_keys: tuple[str, ...]


@dataclass(frozen=True)
class DrainResult:
    claims: int
    dead_letters: int
    reason: str
    jobs: int


def drain_queue(
    processing_deadline: float,
    claim: Callable[[int, list[str], list[str]], list[Claim]],
    process_job: Callable[[list[Claim], int], JobResult],
    *,
    return_unstarted: Callable[[list[Claim]], None] | None = None,
    now: Callable[[], float] = time.time,
    admission_margin_seconds: float = ADMISSION_MARGIN_SECONDS,
    maximum_repositories: int = MAXIMUM_REPOSITORIES,
    maximum_exclusions: int = MAXIMUM_EXCLUSIONS,
    maximum_exclusion_bytes: int = MAXIMUM_EXCLUSION_BYTES,
) -> DrainResult:
    """Keep up to ``maximum_repositories`` repository jobs running.

    ``claim(free_slots, excluded_item_keys, active_repositories)`` returns
    claims for at most ``free_slots`` repositories, none of them active.
    Each repository's claims become one job; finishing any job frees its slot
    for newly claimed work while the other jobs keep running.
    """
    active: dict[concurrent.futures.Future[JobResult], str] = {}
    failures: dict[str, Exception] = {}
    retry_item_keys: set[str] = set()
    return_errors: list[Exception] = []
    jobs = 0
    claim_count = 0
    dead_letters = 0
    reason: str | None = None

    def collect_completed() -> None:
        nonlocal dead_letters
        done, _pending = concurrent.futures.wait(
            active, timeout=0, return_when=concurrent.futures.FIRST_COMPLETED
        )
        for future in done:
            repository = active.pop(future)
            try:
                result = future.result()
            except Exception as error:
                failures[repository] = error
                continue
            dead_letters += result.dead_letters
            retry_item_keys.update(result.retry_item_keys)

    def admission_stop_reason() -> str | None:
        serialized_exclusions = json.dumps(
            sorted(retry_item_keys),
            separators=(",", ":"),
        ).encode()
        if (
            len(retry_item_keys) >= maximum_exclusions
            or len(serialized_exclusions) >= maximum_exclusion_bytes
        ):
            return "exclusion_limit"
        if now() + admission_margin_seconds >= processing_deadline:
            return "deadline"
        return None

    def return_claims(claims: list[Claim]) -> None:
        if not claims:
            return
        try:
            if return_unstarted is None:
                raise RuntimeError(
                    "no callback is configured to return unstarted queue claims"
                )
            return_unstarted(claims)
        except Exception as error:
            return_errors.append(error)

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=maximum_repositories
    ) as executor:
        while True:
            collect_completed()
            while reason is None and not failures and len(active) < maximum_repositories:
                collect_completed()
                if failures:
                    break
                reason = admission_stop_reason()
                if reason is not None:
                    break

                free_slots = maximum_repositories - len(active)
                active_repositories = set(active.values())
                claims = claim(
                    free_slots, sorted(retry_item_keys), sorted(active_repositories)
                )
                collect_completed()
                if not claims:
                    if set(active.values()) != set(active_repositories):
                        continue
                    break
                claim_count += len(claims)

                if not failures:
                    reason = admission_stop_reason()
                if failures or reason is not None:
                    return_claims(claims)
                    break

                groups = list(
                    group_claims(
                        claims, set(active.values()), free_slots
                    ).items()
                )
                for index, (repository, repository_claims) in enumerate(groups):
                    collect_completed()
                    if not failures:
                        reason = admission_stop_reason()
                    if failures or reason is not None:
                        return_claims([
                            item
                            for _repository, unstarted in groups[index:]
                            for item in unstarted
                        ])
                        break
                    jobs += 1
                    future = executor.submit(process_job, repository_claims, jobs)
                    active[future] = repository

            if not active:
                break
            concurrent.futures.wait(active, return_when=concurrent.futures.FIRST_COMPLETED)
            collect_completed()

    if failures:
        details = "; ".join(
            f"{repository}: {failures[repository]}" for repository in sorted(failures)
        )
        if return_errors:
            details += "; unstarted claims could not be returned: " + "; ".join(
                str(error) for error in return_errors
            )
        raise RuntimeError(
            f"{len(failures)} repository job(s) failed: {details}"
        ) from failures[sorted(failures)[0]]
    if return_errors:
        details = "; ".join(str(error) for error in return_errors)
        raise RuntimeError(
            f"unstarted claims could not be returned: {details}"
        ) from return_errors[0]
    return DrainResult(claim_count, dead_letters, reason or "queue_empty", jobs)


def group_claims(
    claims: list[Claim],
    active_repositories: set[str],
    free_slots: int,
) -> dict[str, list[Claim]]:
    grouped: dict[str, list[Claim]] = {}
    for claim in claims:
        grouped.setdefault(claim.repository, []).append(claim)
    overlapping = sorted(active_repositories.intersection(grouped))
    if overlapping:
        raise RuntimeError(
            f"queue claimed work for active repositories: {', '.join(overlapping)}"
        )
    if len(grouped) > free_slots:
        raise RuntimeError(
            f"queue claimed {len(grouped)} repositories for {free_slots} free slot(s)"
        )
    return grouped


def job_result(results: list[dict[str, Any]]) -> JobResult:
    return JobResult(
        dead_letters=sum(result["outcome"] == "dead" for result in results),
        retry_item_keys=tuple(
            sorted(result["itemKey"] for result in results if result["outcome"] == "retry")
        ),
    )


def requeue_acknowledgments(claims: list[Claim]) -> list[dict[str, Any]]:
    return [
        {
            "itemKey": item.item_key,
            "claimGeneration": item.claim_generation,
            "outcome": "requeue",
        }
        for item in claims
    ]


class GitHubAppTokenClient:
    def __init__(
        self,
        client_id: str,
        private_key: str,
        *,
        helper: Path = TOKEN_HELPER,
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self.client_id = client_id
        self.private_key = private_key
        self.helper = helper
        self.run = run

    def mint(self, repositories: list[str]) -> str:
        result = self._call(
            {
                "operation": "mint",
                "clientId": self.client_id,
                "privateKey": self.private_key,
                "repositories": repositories,
            }
        )
        token = result.get("token")
        if not isinstance(token, str) or not token:
            raise RuntimeError("GitHub App token helper did not return a token")
        return token

    def revoke(self, token: str) -> None:
        result = self._call({"operation": "revoke", "token": token})
        if result.get("revoked") is not True:
            raise RuntimeError("GitHub App token helper did not confirm revocation")

    def _call(self, payload: dict[str, Any]) -> dict[str, Any]:
        completed = self.run(
            ["node", str(self.helper)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=False,
            env=child_process_environment(),
            **subprocess_options(),
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"GitHub App token helper failed with exit code {completed.returncode}: "
                f"{completed.stderr.strip()}"
            )
        result = json.loads(completed.stdout)
        if not isinstance(result, dict):
            raise RuntimeError("GitHub App token helper returned invalid JSON")
        return result


PREPARATION_SECRET_NAMES = frozenset({
    "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
    "ACTIONS_ID_TOKEN_REQUEST_URL",
    "ACTIONS_RUNTIME_TOKEN",
    "COPILOT_GITHUB_TOKEN",
    "COPILOT_REVIEW_FALLBACK_TOKEN",
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "PR_DASHBOARD_CLIENT_ID",
    "PR_DASHBOARD_PRIVATE_KEY",
    "PR_DASHBOARD_TOKEN",
    "SLACK_WEBHOOK_URL",
})


def code_preparation_environment() -> dict[str, str]:
    return {
        name: value
        for name, value in os.environ.items()
        if name not in PREPARATION_SECRET_NAMES
    }


def child_process_environment() -> dict[str, str]:
    return {
        name: value
        for name, value in os.environ.items()
        if name not in {"PR_DASHBOARD_CLIENT_ID", "PR_DASHBOARD_PRIVATE_KEY"}
    }


def take_github_app_credentials() -> tuple[str, str]:
    return (
        os.environ.pop("PR_DASHBOARD_CLIENT_ID", ""),
        os.environ.pop("PR_DASHBOARD_PRIVATE_KEY", ""),
    )


def unresolved_acknowledgments(
    claims: list[Claim],
    results: list[dict[str, Any]],
    error: Exception,
) -> list[dict[str, Any]]:
    resolved_keys = {
        result.get("itemKey")
        for result in results
        if isinstance(result, dict) and isinstance(result.get("itemKey"), str)
    }
    unresolved = [claim for claim in claims if claim.item_key not in resolved_keys]
    return [
        result
        for claim in unresolved
        for result in failure_acknowledgments((claim,), error)
    ]


def read_results(path: Path) -> list[dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError("results file must contain a JSON array of objects")
    return value


def process_repository_claims(
    claims: list[Claim],
    results_path: Path,
    client: QueueWorkerClient,
    generation: int,
    worker_id: str,
    token_client: GitHubAppTokenClient,
    *,
    lease_monitor: LeaseMonitor,
    execution_code: ExecutionCode,
    repository_root: Path,
    report_limits: Callable[..., None] = report_rate_limits,
) -> list[dict[str, Any]]:
    token: str | None = None
    common = {"generation": generation, "workerId": worker_id}
    try:
        lease_monitor.assert_valid()
        token = token_client.mint([claims[0].repository])
        print(f"::add-mask::{token}")
        processor_env = child_process_environment()
        processor_env.update({"GH_TOKEN": token, "PR_DASHBOARD_TOKEN": token})
        print(f"{claims[0].repository}: processing with {execution_code.ref}")
        _summary, results = process_claims(
            claims,
            results_path,
            client,
            generation,
            worker_id,
            processor_env=processor_env,
            lease_monitor=lease_monitor,
            repository_root=repository_root,
            script_dir=execution_code.script_dir,
            python_executable=execution_code.python,
        )
    except Exception as error:
        results = read_results(results_path)
        unresolved = unresolved_acknowledgments(
            claims,
            results,
            error,
        )
        if unresolved:
            try:
                acknowledge_results(client, unresolved, common)
            except Exception as acknowledgment_error:
                raise RuntimeError(
                    "repository job failed and unresolved claims could not be acknowledged"
                ) from acknowledgment_error
        raise
    finally:
        if token is not None:
            try:
                report_limits(20, token=token)
            except Exception as error:
                print(f"::warning::GitHub App rate-limit reporting failed: {error}")
            try:
                token_client.revoke(token)
            except Exception as error:
                print(f"::warning::GitHub App token revocation failed: {error}")

    return results


def parse_canary_repositories(value: str) -> frozenset[str]:
    message = (
        "expected a JSON array of repository names, for example "
        f"{CANARY_REPOSITORIES_EXAMPLE}"
    )
    try:
        repositories = json.loads(value)
    except json.JSONDecodeError as error:
        raise argparse.ArgumentTypeError(message) from error
    if not isinstance(repositories, list) or not all(
        isinstance(repository, str) and repository for repository in repositories
    ):
        raise argparse.ArgumentTypeError(message)
    return frozenset(repositories)


def run_owned_drain(args: argparse.Namespace) -> int:
    client = QueueWorkerClient(args.endpoint, lane=args.lane)
    activation = client.call("activate", generation=args.generation, workerId=args.worker)
    if activation.get("activated") is not True:
        print("Dispatcher activation was rejected; this run owns no work.")
        return 0
    client_id, private_key = take_github_app_credentials()
    token_client = GitHubAppTokenClient(client_id, private_key)
    monitor = LeaseMonitor(
        client, args.generation, args.worker, processing_deadline=args.deadline
    )
    try:
        monitor.start()
        with tempfile.TemporaryDirectory(prefix="dashboard-execution-") as directory:
            root = Path(directory)
            loader = ExecutionCodeLoader(root / "code", monitor, code_preparation_environment())
            stable_ref = stable_code_ref()

            common = {"generation": args.generation, "workerId": args.worker}

            def claim(
                free_slots: int, excluded: list[str], active_repositories: list[str]
            ) -> list[Claim]:
                monitor.assert_valid()
                response = client.call(
                    "claim", generation=args.generation, workerId=args.worker,
                    limit=CLAIM_LIMIT, excludeItemKeys=excluded,
                    excludeRepositories=active_repositories, maxRepositories=free_slots,
                )
                return parse_claims(response.get("claims"))

            def process_job(claims: list[Claim], job: int) -> JobResult:
                repository = claims[0].repository
                try:
                    code = loader.load(
                        args.canary_code_ref if repository in args.canary_repositories else stable_ref
                    )
                    repository_root = loader.checkout(repository)
                except Exception as error:
                    failures = [
                        result for claim in claims
                        for result in failure_acknowledgments((claim,), error)
                    ]
                    acknowledge_results(client, failures, common)
                    return job_result(failures)
                return job_result(process_repository_claims(
                    claims, root / f"results-{job}.json", client, args.generation,
                    args.worker, token_client,
                    lease_monitor=monitor,
                    execution_code=code,
                    repository_root=repository_root,
                ))

            def return_unstarted(claims: list[Claim]) -> None:
                acknowledge_results(client, requeue_acknowledgments(claims), common)

            result = drain_queue(
                args.deadline, claim, process_job, return_unstarted=return_unstarted
            )
            print(json.dumps(asdict(result), sort_keys=True))
            return 1 if result.dead_letters else 0
    finally:
        monitor.close()
        if not monitor.lost_event.is_set() and monitor.now() < monitor.valid_until:
            client.call("finish", generation=args.generation, workerId=args.worker)


def main() -> int:
    parser = argparse.ArgumentParser(description="Drain dashboard queue work.")
    parser.add_argument("--canary-code-ref", required=True)
    parser.add_argument("--deadline", type=int, required=True)
    parser.add_argument("--generation", type=int, required=True)
    parser.add_argument("--worker", required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument(
        "--lane", choices=("live", "maintenance"),
        default=os.environ.get("PR_DASHBOARD_QUEUE_LANE", "live"),
    )
    parser.add_argument(
        "--canary-repositories-json",
        dest="canary_repositories",
        type=parse_canary_repositories,
        required=True,
    )
    args = parser.parse_args()

    return run_owned_drain(args)


if __name__ == "__main__":
    raise SystemExit(main())
