from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from head_resolution import matching_open_pr_numbers
from process_queue_batch import (
    SCRIPT_DIR,
    Claim,
    LeaseMonitor,
    acknowledgment,
    coalesce_acknowledgments,
    failure_acknowledgments,
    load_claims,
    parse_claims,
    process_claims,
    resolve_work_items,
)
from queue_worker_client import QueueWorkerClient, acknowledge_results
from report_rate_limits import report_rate_limits

WAVE_LIMIT = 16
MINIMUM_WAVE_SECONDS = 10 * 60
WAVE_DURATION_MULTIPLIER = 1.5
MAXIMUM_EXCLUSIONS = 500
MAXIMUM_EXCLUSION_BYTES = 60 * 1024
MAXIMUM_REPOSITORIES = 4
TOKEN_HELPER = SCRIPT_DIR / "github_app_token.mjs"
DASHBOARD_WORKFLOW_DISPATCH_URL = (
    "https://api.github.com/repos/open-telemetry/shared-workflows/"
    "actions/workflows/pull-request-dashboard.yml/dispatches"
)
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


class DashboardWorkflowDispatcher:
    def __init__(
        self,
        token: str,
        *,
        opener: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        if not token:
            raise ValueError("GITHUB_TOKEN is required to dispatch stable dashboard work")
        self.token = token
        self.opener = opener

    def _fetch_head_page(
        self,
        url: str,
        token: str,
    ) -> tuple[list[dict[str, Any]], str]:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "User-Agent": "pull-request-dashboard-queue-drain",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with self.opener(request, timeout=30) as response:
                if response.status != 200:
                    raise RuntimeError(
                        "head pull request lookup returned "
                        f"unexpected HTTP status {response.status}"
                    )
                pull_requests = json.load(response)
                link = response.headers.get("Link", "")
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")[:1000]
            raise RuntimeError(
                f"head pull request lookup failed with HTTP {error.code}: {body}"
            ) from error
        if not isinstance(pull_requests, list):
            raise RuntimeError("head pull request lookup returned invalid JSON")
        next_page = re.search(r'<([^>]+)>;\s*rel="next"', link)
        return pull_requests, next_page.group(1) if next_page else ""

    def resolve_head(
        self,
        repository: str,
        head_sha: str,
        token: str,
    ) -> tuple[int, ...]:
        base = f"https://api.github.com/repos/open-telemetry/{repository}"

        def fetch(url: str) -> list[dict[str, Any]]:
            pull_requests: list[dict[str, Any]] = []
            while url:
                page, next_page = self._fetch_head_page(url, token)
                pull_requests.extend(page)
                if next_page:
                    parsed = urllib.parse.urlsplit(next_page)
                    if (
                        parsed.scheme != "https"
                        or parsed.netloc != "api.github.com"
                        or (
                            parsed.path != f"/repos/open-telemetry/{repository}/pulls"
                            and re.fullmatch(
                                r"/repositories/[1-9]\d*/pulls", parsed.path
                            )
                            is None
                        )
                    ):
                        raise RuntimeError(
                            "head pull request lookup returned invalid next page"
                        )
                url = next_page
            return pull_requests

        return matching_open_pr_numbers(
            fetch(f"{base}/pulls?state=open&per_page=100"),
            head_sha,
        )

    def dispatch(self, claim: Claim) -> None:
        payload = json.dumps(
            {
                "ref": "main",
                "inputs": {
                    "repository": claim.repository,
                    "pr_number": str(claim.pr_number or ""),
                    "head_sha": claim.head_sha,
                    "trigger_event": (
                        claim.trigger_events[0]
                        if claim.trigger_events
                        else "pull_request"
                    ),
                },
            }
        ).encode()
        request = urllib.request.Request(
            DASHBOARD_WORKFLOW_DISPATCH_URL,
            data=payload,
            method="POST",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "User-Agent": "pull-request-dashboard-queue-drain",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with self.opener(request, timeout=30) as response:
                if response.status != 204:
                    raise RuntimeError(
                        "dashboard workflow dispatch returned "
                        f"unexpected HTTP status {response.status}"
                    )
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")[:1000]
            raise RuntimeError(
                f"dashboard workflow dispatch failed with HTTP {error.code}: {body}"
            ) from error


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
    canary_repositories: frozenset[str],
    configured_repositories: frozenset[str],
    resolve_stable_head: Callable[[str, str, str], tuple[int, ...]],
    dispatch_stable: Callable[[Claim], None],
    report_limits: Callable[..., None] = report_rate_limits,
) -> WaveResult:
    claims_by_repository: dict[str, list[Claim]] = defaultdict(list)
    stable_claims: list[Claim] = []
    for claim in claims:
        if claim.repository in canary_repositories:
            claims_by_repository[claim.repository].append(claim)
        else:
            stable_claims.append(claim)

    monitor = LeaseMonitor(client, generation, worker_id)
    try:
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
        stable_results = [
            acknowledgment(
                claim,
                "dead",
                f"repository is not configured: {claim.repository}",
            )
            for claim in stable_claims
            if claim.repository not in configured_repositories
        ]
        configured_stable_claims = [
            claim
            for claim in stable_claims
            if claim.repository in configured_repositories
        ]

        resolution_tokens: dict[str, str] = {}
        try:
            def resolve_head(repository: str, head_sha: str) -> tuple[int, ...]:
                monitor.assert_valid()
                token = resolution_tokens.get(repository)
                if token is None:
                    token = token_client.mint([repository])
                    resolution_tokens[repository] = token
                    print(f"::add-mask::{token}")
                return resolve_stable_head(repository, head_sha, token)

            stable_work, resolved_stable = resolve_work_items(
                configured_stable_claims,
                resolve_head,
            )
        finally:
            for token in resolution_tokens.values():
                try:
                    report_limits(20, token=token)
                except Exception as error:
                    print(
                        "::warning::GitHub App rate-limit reporting failed: "
                        f"{error}"
                    )
                try:
                    token_client.revoke(token)
                except Exception as error:
                    print(f"::warning::GitHub App token revocation failed: {error}")
        stable_results.extend(resolved_stable)
        for item in stable_work:
            try:
                monitor.assert_valid()
                trigger_events = tuple(
                    dict.fromkeys(
                        event
                        for claim in item.claims
                        for event in claim.trigger_events
                    )
                )
                dispatch_stable(
                    replace(
                        item.claims[0],
                        pr_number=item.pr_number,
                        head_sha="",
                        trigger_events=trigger_events,
                    )
                )
            except Exception as error:
                stable_results.extend(failure_acknowledgments(item.claims, error))
            else:
                stable_results.extend(
                    acknowledgment(claim, "success") for claim in item.claims
                )
        stable_results = coalesce_acknowledgments(stable_results)
        acknowledge_results(
            client,
            stable_results,
            {"generation": generation, "workerId": worker_id},
        )
        results.extend(stable_results)

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
    report_limits: Callable[..., None] = report_rate_limits,
) -> list[dict[str, Any]]:
    token: str | None = None
    common = {"generation": generation, "workerId": worker_id}
    try:
        token = token_client.mint([claims[0].repository])
        print(f"::add-mask::{token}")
        processor_env = child_process_environment()
        processor_env.update({"GH_TOKEN": token, "PR_DASHBOARD_TOKEN": token})
        _summary, results = process_claims(
            claims,
            results_path,
            client,
            generation,
            worker_id,
            processor_env=processor_env,
            lease_monitor=lease_monitor,
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


def load_configured_repositories(
    path: Path = SCRIPT_DIR / "repositories.json",
) -> frozenset[str]:
    config = json.loads(path.read_text(encoding="utf-8"))
    return frozenset(
        entry["name"]
        for entry in config
        if isinstance(entry, dict) and isinstance(entry.get("name"), str)
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Drain dashboard queue waves.")
    parser.add_argument("--claims", type=Path, required=True)
    parser.add_argument("--deadline", type=int, required=True)
    parser.add_argument("--generation", type=int, required=True)
    parser.add_argument("--worker", required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument(
        "--canary-repositories-json",
        dest="canary_repositories",
        type=parse_canary_repositories,
        required=True,
    )
    args = parser.parse_args()

    initial_claims = load_claims(args.claims)
    configured_repositories = load_configured_repositories()
    client = QueueWorkerClient(args.endpoint)
    client_id, private_key = take_github_app_credentials()
    token_client = GitHubAppTokenClient(client_id, private_key)
    workflow_dispatcher = DashboardWorkflowDispatcher(
        os.environ.get("GITHUB_TOKEN", "")
    )
    with tempfile.TemporaryDirectory(prefix="dashboard-waves-") as directory:
        temporary_directory = Path(directory)

        def claim_wave(_wave: int, excluded: list[str]) -> list[Claim]:
            response = client.call(
                "claim",
                generation=args.generation,
                workerId=args.worker,
                limit=WAVE_LIMIT,
                excludeItemKeys=excluded,
            )
            return parse_claims(response.get("claims"))

        def process_wave(claims: list[Claim], wave: int) -> WaveResult:
            return process_claim_wave(
                claims,
                temporary_directory / f"results-{wave}.json",
                client,
                args.generation,
                args.worker,
                token_client,
                canary_repositories=args.canary_repositories,
                configured_repositories=configured_repositories,
                resolve_stable_head=workflow_dispatcher.resolve_head,
                dispatch_stable=workflow_dispatcher.dispatch,
            )

        result = drain_queue(
            initial_claims,
            args.deadline,
            claim_wave,
            process_wave,
        )
    print(json.dumps(asdict(result), sort_keys=True))
    return 1 if result.dead_letters else 0


if __name__ == "__main__":
    raise SystemExit(main())
