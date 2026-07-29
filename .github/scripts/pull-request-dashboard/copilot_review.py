"""Track Copilot re-review requests for delivery by the publisher job."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys
from typing import Any

from author_nudge import (
    fetch_current_pr_routing_inputs,
    routing_input_component_digests,
    routing_input_fingerprint,
)
from github_cli import (
    fetch_pr_reviews,
    request_copilot_review,
)
from state import load_copilot_review_requests, save_copilot_review_requests
from utils import actor_login, format_ts


_COPILOT_REVIEWER_LOGINS = {
    "copilot",
    "copilot-pull-request-reviewer",
    "copilot-pull-request-reviewer[bot]",
}


def is_copilot_reviewer(obj: dict[str, Any] | None) -> bool:
    return actor_login(obj).lower() in _COPILOT_REVIEWER_LOGINS


def copilot_review_status(
    reviews: list[dict[str, Any]],
    head_sha: str,
) -> tuple[bool, bool]:
    copilot_reviews = [
        review
        for review in reviews
        if is_copilot_reviewer(review.get("user"))
    ]
    if not copilot_reviews:
        return False, False
    if not head_sha:
        return True, False
    current_head_reviews = [
        review
        for review in copilot_reviews
        if (review.get("commit_id") or "") == head_sha
    ]
    if not current_head_reviews:
        return True, True
    latest_review = max(
        current_head_reviews,
        key=lambda review: review.get("submitted_at") or "",
    )
    return True, bool(latest_review.get("finding_count"))


def apply_copilot_review_gate(
    facts: dict[str, Any],
    route: str,
    *,
    enabled: bool,
) -> str:
    facts["copilot_review_request_needed"] = False
    if not enabled or route not in ("approver", "maintainer"):
        return route
    if not facts.get("copilot_review_exists"):
        return "copilot"
    if not facts.get("copilot_review_needed"):
        return route
    if not facts.get("copilot_review_requested"):
        facts["copilot_review_request_needed"] = True
    return "copilot"


def record_copilot_review_observation(
    pr_number: int,
    result: dict[str, Any] | None,
    observed_at: datetime,
) -> None:
    requests = dict(load_copilot_review_requests())
    key = str(pr_number)
    facts = (result or {}).get("facts") or {}
    head_sha = str(facts.get("head_sha") or "")
    routing_fingerprint = str(facts.get("routing_input_fingerprint") or "")
    if (
        not result
        or result.get("failed")
        or result.get("route") != "copilot"
        or not facts.get("copilot_review_request_needed")
        or not head_sha
        or not routing_fingerprint
    ):
        requests.pop(key, None)
    else:
        requests[key] = {
            "head_sha": head_sha,
            "observed_at": format_ts(observed_at),
            "requested_at": "",
            "routing_input_fingerprint": routing_fingerprint,
        }
    save_copilot_review_requests(requests)


def stale_request_reason(
    entry: dict[str, Any],
    pr: dict[str, Any],
    current_head: str,
    current_routing_fingerprint: str,
    raw: dict[str, Any],
) -> str:
    if pr.get("state") != "OPEN":
        return f"pull request state is {pr.get('state')!r}"
    if pr.get("isDraft"):
        return "pull request is a draft"
    if current_head != entry.get("head_sha"):
        return (
            f"head is {current_head or '(missing)'} "
            f"but {entry.get('head_sha') or '(missing)'} was observed"
        )
    if not entry.get("routing_input_fingerprint"):
        return "no routing fingerprint was observed"
    if current_routing_fingerprint != entry["routing_input_fingerprint"]:
        digests = routing_input_component_digests(raw)
        return (
            f"routing fingerprint is {current_routing_fingerprint} "
            f"but {entry['routing_input_fingerprint']} was observed; "
            f"components {digests}"
        )
    return ""


def deliver_copilot_review_requests(
    repo: str,
    now: datetime,
    retry_snapshot_path: Path | None = None,
) -> list[str]:
    requests = dict(load_copilot_review_requests(retry_snapshot_path))
    owner, repo_name = repo.split("/", 1)
    errors: list[str] = []
    for key, entry in sorted(requests.items(), key=lambda item: int(item[0])):
        if (entry or {}).get("requested_at"):
            continue
        pr_number = int(key)
        try:
            pr, raw = fetch_current_pr_routing_inputs(
                repo,
                pr_number,
            )
            current_routing_fingerprint = routing_input_fingerprint(raw)
            current_head = pr.get("headRefOid") or ""
            stale_reason = stale_request_reason(
                entry,
                pr,
                current_head,
                current_routing_fingerprint,
                raw,
            )
            if stale_reason:
                print(
                    f"discarding Copilot review request for PR #{pr_number}: "
                    f"{stale_reason}",
                    file=sys.stderr,
                )
                requests.pop(key, None)
                continue
            if any(
                is_copilot_reviewer(request)
                for request in (raw.get("review_requests") or [])
            ):
                requests[key] = {**entry, "requested_at": format_ts(now)}
                continue
            reviews = fetch_pr_reviews(owner, repo_name, pr_number) or []
            review_exists, review_needed = copilot_review_status(
                reviews,
                current_head,
            )
            if not review_exists or not review_needed:
                print(
                    f"discarding Copilot review request for PR #{pr_number}: "
                    f"Copilot review exists={review_exists} "
                    f"needed={review_needed} for head {current_head}",
                    file=sys.stderr,
                )
                requests.pop(key, None)
                continue
            pull_request_id = pr.get("id") or ""
            if not pull_request_id:
                raise RuntimeError(f"GitHub did not return a node ID for PR #{pr_number}")
            request_copilot_review(pull_request_id)
        except Exception as e:
            errors.append(f"PR #{pr_number}: {e}")
            continue
        requests[key] = {**entry, "requested_at": format_ts(now)}
    save_copilot_review_requests(requests)
    return errors