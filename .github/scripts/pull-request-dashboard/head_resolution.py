from __future__ import annotations

from typing import Any


def matching_open_pr_numbers(pull_requests: Any, head_sha: str) -> tuple[int, ...]:
    if not isinstance(pull_requests, list):
        raise RuntimeError("head pull request lookup returned invalid JSON")
    return tuple(
        sorted(
            {
                pull_request["number"]
                for pull_request in pull_requests
                if isinstance(pull_request, dict)
                and pull_request.get("state") == "open"
                and isinstance(pull_request.get("head"), dict)
                and pull_request["head"].get("sha") == head_sha
                and isinstance(pull_request.get("number"), int)
                and not isinstance(pull_request["number"], bool)
                and pull_request["number"] > 0
            }
        )
    )
