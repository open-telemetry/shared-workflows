"""Track Copilot re-review requests for delivery by the publisher job."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from github_cli import (
    fetch_pr_review_data,
    gh_api,
    gh_pr_view,
    request_copilot_review,
)
from state import load_copilot_review_requests, save_copilot_review_requests
from utils import format_ts


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
        previous = requests.get(key) or {}
        requests[key] = (
            previous
            if previous.get("head_sha") == head_sha
            else {"head_sha": head_sha, "requested_at": ""}
        )
    save_copilot_review_requests(requests)


def deliver_copilot_review_requests(
    repo: str,
    now: datetime,
    retry_snapshot_path: Path | None = None,
) -> list[str]:
    from dashboard import copilot_review_needed, has_copilot_review, is_copilot_reviewer

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
            pr_view = gh_pr_view(repo, pr_number)
            if any(
                is_copilot_reviewer(request)
                for request in (pr_view.get("reviewRequests") or [])
            ):
                requests[key] = {**entry, "requested_at": format_ts(now)}
                continue
            review_data = fetch_pr_review_data(owner, repo_name, pr_number) or {}
            raw = {
                "reviews": review_data.get("reviews") or [],
                "commits": gh_api(
                    f"/repos/{repo}/pulls/{pr_number}/commits?per_page=100",
                    paginate=True,
                ) or [],
                "review_comments": gh_api(
                    f"/repos/{repo}/pulls/{pr_number}/comments?per_page=100",
                    paginate=True,
                ) or [],
            }
            if not has_copilot_review(raw) or not copilot_review_needed(raw):
                requests.pop(key, None)
                continue
            request_copilot_review(repo, pr_number)
        except Exception as e:
            errors.append(f"PR #{pr_number}: {e}")
            continue
        requests[key] = {**entry, "requested_at": format_ts(now)}
    save_copilot_review_requests(requests)
    return errors