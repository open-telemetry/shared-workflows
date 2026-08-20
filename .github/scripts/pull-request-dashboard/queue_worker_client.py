from __future__ import annotations

import argparse
import http.client
import json
import os
import random
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

OIDC_AUDIENCE = "otel-pr-dashboard-queue"
RETRYABLE_ACTIONS = frozenset({"acknowledge", "finish", "heartbeat", "stats"})
RETRYABLE_HTTP_STATUSES = frozenset({502, 503, 504})
MAX_ATTEMPTS = 4
INITIAL_BACKOFF_SECONDS = 1.0
MAX_BACKOFF_SECONDS = 4.0


class QueueWorkerClient:
    def __init__(
        self,
        endpoint: str,
        *,
        oidc_request_url: str | None = None,
        oidc_request_token: str | None = None,
        opener: Any = urllib.request.urlopen,
        sleeper: Any = time.sleep,
        jitter: Any = random.uniform,
        operation_id_factory: Any = lambda: uuid.uuid4().hex,
    ) -> None:
        self.endpoint = endpoint
        self.oidc_request_url = oidc_request_url or os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL", "")
        self.oidc_request_token = oidc_request_token or os.environ.get(
            "ACTIONS_ID_TOKEN_REQUEST_TOKEN", ""
        )
        self.opener = opener
        self.sleeper = sleeper
        self.jitter = jitter
        self.operation_id_factory = operation_id_factory
        if not self.endpoint.startswith("https://"):
            raise ValueError("queue endpoint must use HTTPS")
        if not self.oidc_request_url or not self.oidc_request_token:
            raise ValueError("GitHub OIDC request configuration is missing")

    def call(self, action: str, **payload: Any) -> dict[str, Any]:
        request_payload = {"action": action, **payload}
        if action == "acknowledge":
            if "operationId" not in request_payload:
                request_payload["operationId"] = self.operation_id_factory()
        body = json.dumps(request_payload, separators=(",", ":")).encode()
        for attempt in range(MAX_ATTEMPTS):
            try:
                token = self._request_oidc_token()
                request = urllib.request.Request(
                    self.endpoint,
                    data=body,
                    method="POST",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                        "User-Agent": "pull-request-dashboard-queue-worker",
                    },
                )
                with self.opener(request, timeout=30) as response:
                    result = json.load(response)
                if not isinstance(result, dict):
                    raise RuntimeError("queue worker response must be a JSON object")
                return result
            except Exception as error:
                if (
                    action not in RETRYABLE_ACTIONS
                    or attempt + 1 >= MAX_ATTEMPTS
                    or not is_transient_error(error)
                ):
                    raise
                maximum = min(
                    INITIAL_BACKOFF_SECONDS * (2**attempt),
                    MAX_BACKOFF_SECONDS,
                )
                self.sleeper(self.jitter(0, maximum))
        raise AssertionError("unreachable")

    def _request_oidc_token(self) -> str:
        separator = "&" if "?" in self.oidc_request_url else "?"
        url = (
            f"{self.oidc_request_url}{separator}audience="
            f"{urllib.parse.quote(OIDC_AUDIENCE, safe='')}"
        )
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self.oidc_request_token}",
                "User-Agent": "pull-request-dashboard-queue-worker",
            },
        )
        with self.opener(request, timeout=30) as response:
            result = json.load(response)
        token = result.get("value") if isinstance(result, dict) else None
        if not isinstance(token, str) or not token:
            raise RuntimeError("GitHub OIDC response did not include a token")
        return token


def is_transient_error(error: BaseException) -> bool:
    if isinstance(error, urllib.error.HTTPError):
        return error.code in RETRYABLE_HTTP_STATUSES
    if isinstance(error, urllib.error.URLError):
        return is_transient_error(error.reason)
    return isinstance(
        error,
        (
            TimeoutError,
            socket.timeout,
            ConnectionError,
            http.client.RemoteDisconnected,
            http.client.IncompleteRead,
        ),
    )


def write_github_output(path: Path, values: dict[str, str]) -> None:
    with path.open("a", encoding="utf-8") as output:
        for name, value in values.items():
            output.write(f"{name}={value}\n")


def acknowledge_all(
    client: QueueWorkerClient,
    results_path: Path,
    common: dict[str, Any],
) -> dict[str, Any]:
    """Acknowledge every claimed item, reporting failures only at the end.

    A rejected acknowledgment leaves its item leased until recovery, so the
    remaining items are still worth acknowledging before the caller fails.
    """
    results = json.loads(results_path.read_text(encoding="utf-8"))
    if not isinstance(results, list):
        raise ValueError("results file must contain a JSON array")
    acknowledged = 0
    failures: list[str] = []
    for item in results:
        if not isinstance(item, dict):
            raise ValueError("each acknowledgment result must be an object")
        try:
            client.call(
                "acknowledge",
                itemKey=item["itemKey"],
                claimGeneration=item["claimGeneration"],
                outcome=item["outcome"],
                error=item.get("error", ""),
                retryAfterMs=item.get("retryAfterMs", 0),
                **common,
            )
        except Exception as error:  # noqa: BLE001 - reported after the loop
            failures.append(f"{item.get('itemKey')}: {error}")
        else:
            acknowledged += 1
    if failures:
        raise RuntimeError(
            "queue acknowledgment failed for "
            f"{len(failures)} of {len(results)} items: {'; '.join(failures)}"
        )
    return {"acknowledged": acknowledged}


def main() -> int:
    parser = argparse.ArgumentParser(description="Call the Netlify dashboard queue worker API.")
    parser.add_argument("--endpoint", default=os.environ.get("PR_DASHBOARD_QUEUE_ENDPOINT", ""))
    parser.add_argument("--generation", type=int, required=True)
    parser.add_argument("--worker-id", required=True)
    subparsers = parser.add_subparsers(dest="action", required=True)

    subparsers.add_parser("activate")

    claim = subparsers.add_parser("claim")
    claim.add_argument("--limit", type=int, default=32)
    claim.add_argument("--output", type=Path, required=True)
    claim.add_argument("--github-output", type=Path)

    subparsers.add_parser("heartbeat")

    acknowledge = subparsers.add_parser("acknowledge")
    acknowledge.add_argument("--results", type=Path, required=True)

    subparsers.add_parser("finish")

    stats = subparsers.add_parser("stats")
    stats.add_argument("--output", type=Path)

    args = parser.parse_args()
    client = QueueWorkerClient(args.endpoint)
    common = {
        "generation": args.generation,
        "workerId": args.worker_id,
    }

    if args.action == "activate":
        result = client.call("activate", **common)
        if result.get("activated") is not True:
            raise RuntimeError("dispatcher activation was rejected")
    elif args.action == "claim":
        result = client.call("claim", limit=args.limit, **common)
        claims = result.get("claims")
        if not isinstance(claims, list):
            raise RuntimeError("queue claim response did not include a claims array")
        args.output.write_text(json.dumps(claims, indent=2) + "\n", encoding="utf-8")
        if args.github_output:
            repositories = sorted(
                {
                    claim.get("repository")
                    for claim in claims
                    if isinstance(claim, dict) and isinstance(claim.get("repository"), str)
                }
            )
            write_github_output(
                args.github_output,
                {
                    "count": str(len(claims)),
                    "repositories": ",".join(repositories),
                },
            )
    elif args.action == "heartbeat":
        result = client.call("heartbeat", **common)
        if result.get("dispatcher") is not True:
            raise RuntimeError("dispatcher heartbeat was rejected")
    elif args.action == "acknowledge":
        result = acknowledge_all(client, args.results, common)
    elif args.action == "finish":
        result = client.call("finish", **common)
    else:
        result = client.call("stats", **common)
        if args.output:
            args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
