"""Track Copilot re-review requests for delivery by the publisher job."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from github_cli import (
    fetch_pr_review_data,
    gh_api,
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
) -> None:
    requests = dict(load_copilot_review_requests())
    key = str(pr_number)
    facts = (result or {}).get("facts") or {}
    head_sha = str(facts.get("head_sha") or "")
    if (
        not result
        or result.get("failed")
        or result.get("route") != "copilot"
        or not facts.get("copilot_review_request_needed")
        or not head_sha
    ):
        requests.pop(key, None)
    else:
        requests[key] = {"head_sha": head_sha, "requested_at": ""}
    save_copilot_review_requests(requests)


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
            pr = gh_api(f"/repos/{repo}/pulls/{pr_number}") or {}
            current_head = ((pr.get("head") or {}).get("sha") or "")
            if (
                pr.get("state") != "open"
                or pr.get("draft")
                or current_head != entry.get("head_sha")
            ):
                requests.pop(key, None)
                continue
            if any(
                is_copilot_reviewer(request)
                for request in (pr.get("requested_reviewers") or [])
            ):
                requests[key] = {**entry, "requested_at": format_ts(now)}
                continue
            review_data = fetch_pr_review_data(owner, repo_name, pr_number) or {}
            review_exists, review_needed = copilot_review_status(
                review_data.get("reviews") or [],
                current_head,
            )
            if not review_exists or not review_needed:
                requests.pop(key, None)
                continue
            pull_request_id = pr.get("node_id") or ""
            if not pull_request_id:
                raise RuntimeError(f"GitHub did not return a node ID for PR #{pr_number}")
            request_copilot_review(pull_request_id)
        except Exception as e:
            errors.append(f"PR #{pr_number}: {e}")
            continue
        requests[key] = {**entry, "requested_at": format_ts(now)}
    save_copilot_review_requests(requests)
    return errors