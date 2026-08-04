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
from utils import (
    actor_login,
    format_ts,
    is_copilot_reviewer_login,
    required_checks_settled,
)


def is_copilot_reviewer(obj: dict[str, Any] | None) -> bool:
    return is_copilot_reviewer_login(actor_login(obj))


def open_copilot_finding_count(review_threads: list[dict[str, Any]] | None) -> int:
    # A review's own comment count never shrinks, so it still counts findings
    # the author has since addressed. Unresolved threads are the live ones:
    # GitHub marks a thread outdated once its anchor lines change, which is how
    # the rest of the dashboard already recognises a pushed fix.
    count = 0
    for thread in review_threads or []:
        if thread.get("isResolved") or thread.get("isOutdated"):
            continue
        comments = (thread.get("comments") or {}).get("nodes") or []
        if comments and is_copilot_reviewer(comments[0].get("author") or {}):
            count += 1
    return count


def copilot_review_status(
    reviews: list[dict[str, Any]],
    head_sha: str,
    review_threads: list[dict[str, Any]],
) -> tuple[bool, bool, bool]:
    """Return (review exists, review is stale, review left open findings)."""
    copilot_reviews = [
        review
        for review in reviews
        if is_copilot_reviewer(review.get("user"))
    ]
    if not copilot_reviews:
        return False, False, False
    if not head_sha:
        # Without a head to compare against, staleness is unknowable, and
        # acting on incomplete data would hold the pull request on a guess.
        return True, False, False
    stale = not any(
        (review.get("commit_id") or "") == head_sha
        for review in copilot_reviews
    )
    return True, stale, open_copilot_finding_count(review_threads) > 0


def copilot_review_outstanding(facts: dict[str, Any], *, enabled: bool) -> bool:
    if not enabled:
        return False
    return not facts.get("copilot_review_exists") or bool(
        facts.get("copilot_review_needed")
    )


def set_copilot_review_request_needed(
    facts: dict[str, Any],
    route: str,
    *,
    enabled: bool,
) -> None:
    # Requesting a re-review before the checks report would spend it on code CI
    # is about to reject, and a PR GitHub has never reviewed is already queued
    # for the automatic first review. Only a stale review is worth re-running:
    # findings on the current head are unchanged code, so a re-review cannot
    # clear them and would be requested again on every pass.
    facts["copilot_review_request_needed"] = (
        enabled
        and route in ("approver", "maintainer")
        and bool(facts.get("copilot_review_exists"))
        and bool(facts.get("copilot_review_stale"))
        and not facts.get("copilot_review_requested")
        and required_checks_settled(facts)
    )


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


def named_checks(checks: list[dict[str, Any]]) -> str:
    names = sorted(check.get("name") or "" for check in checks)
    if len(names) > 3:
        return f"{', '.join(names[:3])} and {len(names) - 3} more"
    return ", ".join(names)


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
    # The fingerprint below only detects change, so checks that were unsettled
    # when the request was recorded and still are would otherwise pass through.
    checks = raw.get("checks")
    if checks is None:
        return "required check results are unavailable"
    failing = [check for check in checks if check.get("bucket") in ("fail", "cancel")]
    if failing:
        return f"required checks are failing: {named_checks(failing)}"
    pending = [check for check in checks if check.get("bucket") == "pending"]
    if pending:
        return f"required checks have not completed: {named_checks(pending)}"
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
            review_exists, review_stale, _review_findings = copilot_review_status(
                reviews,
                current_head,
                raw.get("review_threads") or [],
            )
            if not review_exists or not review_stale:
                print(
                    f"discarding Copilot review request for PR #{pr_number}: "
                    f"Copilot review exists={review_exists} "
                    f"stale={review_stale} for head {current_head}",
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