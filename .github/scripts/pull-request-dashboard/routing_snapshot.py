"""Build the shared pull request routing snapshot."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from pull_request_source import (
    Check,
    PullRequestSource,
    ReviewRequest,
    ReviewThread,
    fetch_pull_request_source,
)


@dataclass(frozen=True)
class RoutingSnapshot:
    state: str
    is_draft: bool
    node_id: str
    head_sha: str
    checks: tuple[Check, ...] | None
    review_requests: tuple[ReviewRequest, ...]
    review_threads: tuple[ReviewThread, ...]
    routing_input_fingerprint: str
    copilot_request_fingerprint: str
    copilot_request_component_digests: dict[str, str]


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _component_digests(inputs: dict[str, Any]) -> dict[str, str]:
    return {name: _digest(value)[:16] for name, value in inputs.items()}


def build_routing_snapshot(source: PullRequestSource) -> RoutingSnapshot:
    pr = source.pull_request
    fingerprint = source.fingerprint
    if fingerprint is None:
        raise ValueError("pull request source is missing its fingerprint projection")
    routing_inputs = fingerprint.routing_inputs()
    # Review requests are sent while checks are running, so including checks in
    # the fingerprint would invalidate queued requests as their status changes.
    # Delivery separately rejects requests when check results are unavailable or
    # a required check is failing or canceled.
    copilot_inputs = fingerprint.copilot_request_inputs()
    return RoutingSnapshot(
        state=pr.state,
        is_draft=pr.is_draft,
        node_id=pr.node_id,
        head_sha=pr.head_sha,
        checks=source.checks,
        review_requests=source.review_requests,
        review_threads=source.review_threads,
        routing_input_fingerprint=_digest(routing_inputs),
        copilot_request_fingerprint=_digest(copilot_inputs),
        copilot_request_component_digests=_component_digests(copilot_inputs),
    )


def fetch_routing_snapshot(repo: str, pr_number: int) -> RoutingSnapshot:
    owner, repo_name = repo.split("/", 1)
    return build_routing_snapshot(
        fetch_pull_request_source(
            repo,
            owner,
            repo_name,
            pr_number,
            include_commits=False,
        )
    )
