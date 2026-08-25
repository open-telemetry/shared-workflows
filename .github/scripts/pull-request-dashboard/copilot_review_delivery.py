"""Record and deliver Copilot review requests from accepted state."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys

from copilot_review import (
    copilot_review_request_landed,
    copilot_review_status,
    is_copilot_reviewer,
    stale_request_reason,
)
from dashboard_contracts import StoredDashboardResult
from github_cli import fetch_pr_reviews, request_copilot_review
from routing_snapshot import fetch_routing_snapshot
from state import load_copilot_review_requests, save_copilot_review_requests
from utils import format_ts


def record_copilot_review_observation(
    pr_number: int,
    result: StoredDashboardResult | None,
    observed_at: datetime,
) -> None:
    requests = dict(load_copilot_review_requests())
    key = str(pr_number)
    facts = result.facts if result is not None else None
    head_sha = facts.head_sha if facts is not None else ""
    request_fingerprint = (
        facts.copilot_request_fingerprint
        if facts is not None
        else ""
    )
    if (
        not result
        or facts is None
        or not facts.copilot_review_request_needed
        or not head_sha
        or not request_fingerprint
    ):
        requests.pop(key, None)
    else:
        requests[key] = {
            "head_sha": head_sha,
            "observed_at": format_ts(observed_at),
            "requested_at": "",
            "copilot_request_fingerprint": request_fingerprint,
        }
    save_copilot_review_requests(requests)


def deliver_copilot_review_requests(
    repo: str,
    now: datetime,
    retry_snapshot_path: Path | None = None,
) -> list[str]:
    requests = dict(load_copilot_review_requests(retry_snapshot_path))
    owner, repo_name = repo.split("/", 1)
    errors: list[str] = []
    for key, entry in sorted(
        requests.items(),
        key=lambda item: int(item[0]),
    ):
        if (entry or {}).get("requested_at"):
            continue
        pr_number = int(key)
        try:
            snapshot = fetch_routing_snapshot(repo, pr_number)
            stale_reason = stale_request_reason(entry, snapshot)
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
                for request in snapshot.review_requests
            ):
                requests[key] = {
                    **entry,
                    "requested_at": format_ts(now),
                }
                continue
            reviews = fetch_pr_reviews(owner, repo_name, pr_number) or []
            review_exists, review_stale, _review_findings = (
                copilot_review_status(
                    reviews,
                    snapshot.head_sha,
                    snapshot.review_threads,
                )
            )
            if review_exists and not review_stale:
                print(
                    f"discarding Copilot review request for PR #{pr_number}: "
                    f"Copilot review already covers head {snapshot.head_sha}",
                    file=sys.stderr,
                )
                requests.pop(key, None)
                continue
            pull_request_id = snapshot.node_id
            if not pull_request_id:
                raise RuntimeError(
                    f"GitHub did not return a node ID for PR #{pr_number}"
                )
            request_copilot_review(pull_request_id)
            landed = copilot_review_request_landed(
                owner,
                repo_name,
                pr_number,
                snapshot.head_sha,
            )
        except Exception as error:
            errors.append(f"PR #{pr_number}: {error}")
            continue
        if landed:
            requests[key] = {
                **entry,
                "requested_at": format_ts(now),
            }
            continue
        # Leaving the request undelivered keeps the next pass trying. Nothing
        # escalates from here: a request that keeps going missing leaves the
        # pull request held, and the hold is what reports the stall.
        print(
            f"GitHub did not record the Copilot review request for "
            f"PR #{pr_number} on head {snapshot.head_sha}",
            file=sys.stderr,
        )
    save_copilot_review_requests(requests)
    return errors
