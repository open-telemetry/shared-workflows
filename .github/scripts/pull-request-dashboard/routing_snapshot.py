"""Build the shared pull request routing snapshot."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from github_cli import fetch_pr_routing_raw
from pr_status_comment import DASHBOARD_APP_SLUG
from utils import compute_conflicts


@dataclass(frozen=True)
class RoutingSnapshot:
    state: str
    is_draft: bool
    node_id: str
    head_sha: str
    checks: list[dict[str, Any]] | None
    review_requests: list[dict[str, Any]]
    review_threads: list[dict[str, Any]]
    routing_input_fingerprint: str
    copilot_request_fingerprint: str
    copilot_request_component_digests: dict[str, str]


def _routing_inputs(raw: dict[str, Any]) -> dict[str, Any]:
    dashboard_login = f"{DASHBOARD_APP_SLUG}[bot]"
    pr = raw.get("pr") or {}
    issue_comments = [
        comment
        for comment in raw.get("issue_comments") or []
        if (comment.get("user") or {}).get("login") != dashboard_login
    ]
    return {
        "base_branch": str(pr.get("baseRefName") or ""),
        "checks": raw.get("checks"),
        # Hash the derived state so equivalent mergeability values do not
        # invalidate prepared delivery.
        "conflicts": compute_conflicts(pr),
        "issue_comments": issue_comments,
        "pr_text": {
            "body": str(pr.get("body") or "").replace("\r\n", "\n"),
            "title": str(pr.get("title") or ""),
        },
        "review_comments": raw.get("review_comments") or [],
        "review_requests": raw.get("review_requests") or [],
        "reviews": raw.get("reviews") or [],
        "review_threads": raw.get("review_threads") or [],
    }


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _copilot_request_inputs(raw: dict[str, Any]) -> dict[str, Any]:
    # Review requests are sent while checks are running, so including checks in
    # the fingerprint would invalidate queued requests as their status changes.
    # Delivery separately rejects requests when check results are unavailable or
    # a required check is failing or canceled.
    inputs = _routing_inputs(raw)
    inputs.pop("checks", None)
    return inputs


def _component_digests(inputs: dict[str, Any]) -> dict[str, str]:
    return {name: _digest(value)[:16] for name, value in inputs.items()}


def build_routing_snapshot(raw: dict[str, Any]) -> RoutingSnapshot:
    pr = raw.get("pr") or {}
    routing_inputs = _routing_inputs(raw)
    copilot_inputs = _copilot_request_inputs(raw)
    return RoutingSnapshot(
        state=str(pr.get("state") or ""),
        is_draft=bool(pr.get("isDraft")),
        node_id=str(pr.get("id") or ""),
        head_sha=str(pr.get("headRefOid") or ""),
        checks=raw.get("checks"),
        review_requests=raw.get("review_requests") or [],
        review_threads=raw.get("review_threads") or [],
        routing_input_fingerprint=_digest(routing_inputs),
        copilot_request_fingerprint=_digest(copilot_inputs),
        copilot_request_component_digests=_component_digests(copilot_inputs),
    )


def fetch_routing_snapshot(repo: str, pr_number: int) -> RoutingSnapshot:
    owner, repo_name = repo.split("/", 1)
    return build_routing_snapshot(
        fetch_pr_routing_raw(repo, owner, repo_name, pr_number)
    )
