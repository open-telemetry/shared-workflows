from __future__ import annotations

import argparse
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
    acknowledgment,
    failure_acknowledgments,
    load_claims,
    parse_claims,
    process_claims,
)
from queue_worker_client import QueueWorkerClient, acknowledge_results
from report_rate_limits import report_rate_limits

WAVE_LIMIT = 16
MINIMUM_WAVE_SECONDS = 10 * 60
WAVE_DURATION_MULTIPLIER = 1.5
MAXIMUM_EXCLUSIONS = 500
MAXIMUM_EXCLUSION_BYTES = 60 * 1024
TOKEN_HELPER = SCRIPT_DIR / "github_app_token.mjs"


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
    *,
    allow_dead_letters: bool = True,
) -> list[dict[str, Any]]:
    resolved_keys = {
        result.get("itemKey")
        for result in results
        if isinstance(result, dict) and isinstance(result.get("itemKey"), str)
    }
    unresolved = [claim for claim in claims if claim.item_key not in resolved_keys]
    if allow_dead_letters:
        return [
            result
            for claim in unresolved
            for result in failure_acknowledgments((claim,), error)
        ]
    return [acknowledgment(claim, "retry", str(error)) for claim in unresolved]


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
    report_limits: Callable[..., None] = report_rate_limits,
) -> WaveResult:
    token: str | None = None
    common = {"generation": generation, "workerId": worker_id}
    try:
        token = token_client.mint(sorted({claim.repository for claim in claims}))
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
        )
    except Exception as error:
        results = read_results(results_path)
        unresolved = unresolved_acknowledgments(
            claims,
            results,
            error,
            allow_dead_letters=token is not None,
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

    return WaveResult(
        dead_letters=sum(result["outcome"] == "dead" for result in results),
        retry_item_keys=tuple(
            result["itemKey"] for result in results if result["outcome"] == "retry"
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Drain dashboard queue waves.")
    parser.add_argument("--claims", type=Path, required=True)
    parser.add_argument("--deadline", type=int, required=True)
    parser.add_argument("--generation", type=int, required=True)
    parser.add_argument("--worker", required=True)
    parser.add_argument("--endpoint", required=True)
    args = parser.parse_args()

    initial_claims = load_claims(args.claims)
    client = QueueWorkerClient(args.endpoint)
    client_id, private_key = take_github_app_credentials()
    token_client = GitHubAppTokenClient(client_id, private_key)
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
