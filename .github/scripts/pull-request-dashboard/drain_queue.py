from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import subprocess
import tempfile
import time
from collections import defaultdict
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
from telemetry import session as telemetry_session

WAVE_LIMIT = 16
MINIMUM_WAVE_SECONDS = 10 * 60
WAVE_DURATION_MULTIPLIER = 1.5
MAXIMUM_EXCLUSIONS = 500
MAXIMUM_EXCLUSION_BYTES = 60 * 1024
MAXIMUM_REPOSITORIES = 4
TOKEN_HELPER = SCRIPT_DIR / "github_app_token.mjs"
CANARY_REPOSITORIES_EXAMPLE = '["opentelemetry-java-instrumentation"]'


@dataclass(frozen=True)
class WaveResult:
    dead_letters: int
    retry_item_keys: tuple[str, ...]


@dataclass(frozen=True)
class DrainResult:
    claims: int
    dead_letters: int
    reason: str
    waves: int


def drain_queue(
    initial_claims: list[Claim],
    processing_deadline: float,
    claim_wave: Callable[[int, list[str]], list[Claim]],
    process_wave: Callable[[list[Claim], int], WaveResult],
    *,
    now: Callable[[], float] = time.time,
    minimum_wave_seconds: float = MINIMUM_WAVE_SECONDS,
    wave_duration_multiplier: float = WAVE_DURATION_MULTIPLIER,
    maximum_exclusions: int = MAXIMUM_EXCLUSIONS,
    maximum_exclusion_bytes: int = MAXIMUM_EXCLUSION_BYTES,
) -> DrainResult:
    claims = initial_claims
    waves = 0
    claim_count = 0
    dead_letters = 0
    maximum_wave_seconds = 0.0
    retry_item_keys: set[str] = set()

    while claims:
        started_at = now()
        wave_result = process_wave(claims, waves + 1)
        finished_at = now()
        waves += 1
        claim_count += len(claims)
        dead_letters += wave_result.dead_letters
        retry_item_keys.update(wave_result.retry_item_keys)
        serialized_exclusions = json.dumps(
            sorted(retry_item_keys),
            separators=(",", ":"),
        ).encode()
        if (
            len(retry_item_keys) >= maximum_exclusions
            or len(serialized_exclusions) >= maximum_exclusion_bytes
        ):
            return DrainResult(claim_count, dead_letters, "exclusion_limit", waves)

        maximum_wave_seconds = max(maximum_wave_seconds, finished_at - started_at)
        next_wave_seconds = max(
            minimum_wave_seconds,
            maximum_wave_seconds * wave_duration_multiplier,
        )
        if finished_at + next_wave_seconds >= processing_deadline:
            return DrainResult(claim_count, dead_letters, "deadline", waves)
        claims = claim_wave(waves + 1, sorted(retry_item_keys))

    return DrainResult(claim_count, dead_letters, "queue_empty", waves)


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


def process_claim_wave(
    claims: list[Claim],
    results_path: Path,
    client: QueueWorkerClient,
    generation: int,
    worker_id: str,
    token_client: GitHubAppTokenClient,
    *,
    execution_code: dict[str, ExecutionCode],
    repository_roots: dict[str, Path],
    report_limits: Callable[..., None] = report_rate_limits,
    lease_monitor: LeaseMonitor | None = None,
) -> WaveResult:
    if (
        execution_code is None
        or repository_roots is None
        or any(
            claim.repository not in repository_roots or claim.repository not in execution_code
            for claim in claims
        )
    ):
        raise RuntimeError("owned dashboard execution requires isolated Git checkouts")
    claims_by_repository: dict[str, list[Claim]] = defaultdict(list)
    for claim in claims:
        claims_by_repository[claim.repository].append(claim)

    monitor = lease_monitor or LeaseMonitor(client, generation, worker_id)
    owns_monitor = lease_monitor is None
    try:
        if owns_monitor:
            monitor.start()
    except Exception as error:
        monitor.close()
        unresolved = unresolved_acknowledgments(claims, [], error)
        try:
            acknowledge_results(
                client,
                unresolved,
                {"generation": generation, "workerId": worker_id},
            )
        except Exception as acknowledgment_error:
            raise RuntimeError(
                "queue wave failed and unresolved claims could not be acknowledged"
            ) from acknowledgment_error
        raise

    results: list[dict[str, Any]] = []
    failures: dict[str, Exception] = {}
    try:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=MAXIMUM_REPOSITORIES
        ) as executor:
            futures = {
                executor.submit(
                    process_repository_claims,
                    repository_claims,
                    results_path.with_name(
                        f"{results_path.stem}-{index}{results_path.suffix}"
                    ),
                    client,
                    generation,
                    worker_id,
                    token_client,
                    lease_monitor=monitor,
                    report_limits=report_limits,
                    execution_code=execution_code[repository],
                    repository_root=repository_roots[repository],
                ): repository
                for index, (repository, repository_claims) in enumerate(
                    sorted(claims_by_repository.items())
                )
            }
            for future in concurrent.futures.as_completed(futures):
                repository = futures[future]
                try:
                    results.extend(future.result())
                except Exception as error:
                    failures[repository] = error
    finally:
        if owns_monitor:
            monitor.close()

    if failures:
        details = "; ".join(
            f"{repository}: {failures[repository]}" for repository in sorted(failures)
        )
        raise RuntimeError(
            f"{len(failures)} repository group(s) failed: {details}"
        ) from failures[sorted(failures)[0]]

    results.sort(key=lambda result: result["itemKey"])
    return WaveResult(
        dead_letters=sum(result["outcome"] == "dead" for result in results),
        retry_item_keys=tuple(
            result["itemKey"] for result in results if result["outcome"] == "retry"
        ),
    )


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
                    "queue wave failed and unresolved claims could not be acknowledged"
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

            def claim_wave(_wave: int, excluded: list[str]) -> list[Claim]:
                monitor.assert_valid()
                response = client.call(
                    "claim", generation=args.generation, workerId=args.worker,
                    limit=WAVE_LIMIT, excludeItemKeys=excluded,
                )
                return parse_claims(response.get("claims"))

            def process_wave(claims: list[Claim], wave: int) -> WaveResult:
                try:
                    codes = {
                        repository: loader.load(
                            args.canary_code_ref if repository in args.canary_repositories else stable_ref
                        )
                        for repository in sorted({claim.repository for claim in claims})
                    }
                    repository_roots = {
                        repository: loader.checkout(repository) for repository in codes
                    }
                except Exception as error:
                    failures = [
                        result for claim in claims
                        for result in failure_acknowledgments((claim,), error)
                    ]
                    acknowledge_results(
                        client, failures, {"generation": args.generation, "workerId": args.worker}
                    )
                    return WaveResult(
                        sum(result["outcome"] == "dead" for result in failures),
                        tuple(result["itemKey"] for result in failures if result["outcome"] == "retry"),
                    )
                return process_claim_wave(
                    claims, root / f"results-{wave}.json", client, args.generation,
                    args.worker, token_client,
                    execution_code=codes,
                    repository_roots=repository_roots,
                    lease_monitor=monitor,
                )

            result = drain_queue(
                claim_wave(1, []), args.deadline, claim_wave, process_wave,
            )
            print(json.dumps(asdict(result), sort_keys=True))
            return 1 if result.dead_letters else 0
    finally:
        monitor.close()
        if not monitor.lost_event.is_set() and monitor.now() < monitor.valid_until:
            client.call("finish", generation=args.generation, workerId=args.worker)


def main() -> int:
    parser = argparse.ArgumentParser(description="Drain dashboard queue waves.")
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
    with telemetry_session():
        raise SystemExit(main())
