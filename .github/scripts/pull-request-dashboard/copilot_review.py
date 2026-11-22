"""Track Copilot review requests for delivery by the publisher job."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from typing import Any

from dashboard_contracts import CopilotReviewCycle, DashboardFacts
from routing_snapshot import RoutingSnapshot
from pull_request_source import (
    Actor,
    Check,
    Review,
    ReviewRequest,
)
from utils import GATE_HOLD_LIMIT, format_ts, parse_ts


COPILOT_QUOTA_FAILURE_PREFIXES = (
    "Copilot was unable to review this pull request because the user who "
    "requested the review has reached their quota limit.",
    "Copilot was unable to review this pull request because the user who "
    "requested the review has insufficient remaining quota.",
)
COPILOT_ERROR_BODY = (
    "Copilot encountered an error and was unable to review this pull request. "
    "You can try again by re-requesting a review."
)
COPILOT_REVIEW_ERROR_LIMIT = 2
COPILOT_REVIEW_REQUEST_LIMIT = COPILOT_REVIEW_ERROR_LIMIT + 1


def is_copilot_reviewer(
    value: Actor | ReviewRequest | Review,
) -> bool:
    if isinstance(value, ReviewRequest):
        return value.is_copilot_reviewer
    actor = value.actor if isinstance(value, Review) else value
    return actor.is_copilot_reviewer


def is_copilot_quota_failure(review: Review) -> bool:
    return (
        is_copilot_reviewer(review)
        and review.body.lstrip().startswith(COPILOT_QUOTA_FAILURE_PREFIXES)
    )


def is_copilot_error(review: Review) -> bool:
    return (
        is_copilot_reviewer(review)
        and review.body.strip() == COPILOT_ERROR_BODY
    )


def is_completed_copilot_review(review: Review) -> bool:
    return (
        is_copilot_reviewer(review)
        and review.state != "PENDING"
        and not is_copilot_quota_failure(review)
        and not is_copilot_error(review)
    )


def copilot_review_error_count(reviews: Sequence[Review], head_sha: str) -> int:
    if not head_sha:
        return 0
    return sum(
        is_copilot_error(review) and review.commit_id == head_sha
        for review in reviews
    )


def copilot_review_ids(reviews: Sequence[Review]) -> tuple[int, ...]:
    ids = {
        review.database_id for review in reviews
        if is_copilot_reviewer(review) and review.state != "PENDING"
    }
    if any(review_id <= 0 for review_id in ids):
        raise RuntimeError("GitHub returned a Copilot review without a review ID")
    return tuple(sorted(ids))


def copilot_quota_issue(reviews: Sequence[Review], head_sha: str) -> bool:
    return bool(head_sha) and any(
        review.commit_id == head_sha
        and review.state != "PENDING"
        and is_copilot_quota_failure(review)
        for review in reviews
    )


def copilot_review_in_flight(cycle: CopilotReviewCycle | None) -> bool:
    return cycle is not None and cycle.phase in ("sending", "accepted", "unknown")


def _new_copilot_notices(
    cycle: CopilotReviewCycle,
    reviews: Sequence[Review],
) -> tuple[bool, bool]:
    sent_at = parse_ts(cycle.sent_at)
    if sent_at is None or sent_at.tzinfo is None:
        raise RuntimeError("Copilot review cycle has an invalid send timestamp")
    # GitHub reports review submissions with second precision.
    sent_at = sent_at.replace(microsecond=0)
    baseline_ids = set(cycle.baseline_review_ids)
    quota = error = False
    for review in reviews:
        if (
            not is_copilot_reviewer(review)
            or review.database_id in baseline_ids
            or review.state == "PENDING"
            or review.commit_id != cycle.head_sha
        ):
            continue
        quota_notice = is_copilot_quota_failure(review)
        error_notice = is_copilot_error(review)
        if not quota_notice and not error_notice:
            continue
        if review.database_id <= 0:
            raise RuntimeError("GitHub returned a Copilot review without a review ID")
        submitted_at = parse_ts(review.submitted_at)
        if submitted_at is None or submitted_at.tzinfo is None:
            raise RuntimeError("GitHub returned a Copilot notice without a valid timestamp")
        if submitted_at >= sent_at:
            quota = quota or quota_notice
            error = error or error_notice
    return quota, error


def refresh_copilot_review_cycle(
    cycle: CopilotReviewCycle | None,
    reviews: Sequence[Review],
    head_sha: str,
    now: datetime,
    *,
    fallback_available: bool,
) -> CopilotReviewCycle | None:
    if now.tzinfo is None:
        raise ValueError("Copilot review cycle requires a timezone-aware clock")
    if cycle is None:
        return None
    if not head_sha:
        raise ValueError("Copilot review cycle requires a current head")
    exists, stale = copilot_review_status(reviews, head_sha)
    if exists and not stale:
        return replace(
            cycle if cycle.head_sha == head_sha else CopilotReviewCycle(
                head_sha, requester=cycle.requester,
            ),
            phase="complete",
            limit_reason="",
        )
    if cycle.phase == "complete":
        cycle = replace(cycle, phase="ready")
    quota_pause_needed = (
        cycle.head_sha == head_sha
        and not fallback_available and copilot_quota_issue(reviews, head_sha)
    )
    if cycle.phase == "ready" and quota_pause_needed:
        cycle = replace(cycle, phase="limited", limit_reason="quota")
    if (
        cycle.phase == "limited" and cycle.limit_reason == "quota"
        and fallback_available
    ):
        cycle = replace(
            cycle, requester="fallback", phase="ready", limit_reason="",
            started_at=format_ts(now),
        )
    if cycle.request_count and cycle.phase in ("sending", "accepted", "unknown", "ready"):
        started_at = parse_ts(cycle.started_at)
        if started_at is None or started_at.tzinfo is None:
            raise RuntimeError("Copilot review cycle has an invalid start timestamp")
        if now - started_at >= GATE_HOLD_LIMIT:
            cycle = replace(cycle, phase="limited", limit_reason="timeout")
        elif cycle.phase == "accepted":
            exists, stale = copilot_review_status(reviews, cycle.head_sha)
            if exists and not stale:
                cycle = replace(cycle, phase="complete", limit_reason="")
            elif copilot_review_error_count(reviews, cycle.head_sha) >= COPILOT_REVIEW_ERROR_LIMIT:
                cycle = replace(cycle, phase="limited", limit_reason="errors")
            else:
                quota, error = _new_copilot_notices(cycle, reviews)
                if quota and cycle.requester == "app":
                    cycle = (
                        replace(cycle, requester="fallback", phase="ready")
                        if fallback_available
                        else replace(cycle, phase="limited", limit_reason="quota")
                    )
                elif error:
                    cycle = replace(
                        cycle, phase="limited" if quota_pause_needed else "ready",
                        limit_reason="quota" if quota_pause_needed else "",
                    )
                # A quota notice cannot identify whose quota failed. Once the
                # fallback is selected, it is not a reason for another request.
    if not copilot_review_in_flight(cycle) and cycle.head_sha != head_sha:
        cycle = CopilotReviewCycle(head_sha, requester=cycle.requester)
    if cycle.phase == "ready" and cycle.request_count >= COPILOT_REVIEW_REQUEST_LIMIT:
        cycle = replace(cycle, phase="limited", limit_reason="requests")
    return cycle


def copilot_review_gate_disabled(facts: DashboardFacts) -> bool:
    if facts.copilot_review_exists and not facts.copilot_review_stale:
        return False
    if (
        facts.copilot_review_error_count >= COPILOT_REVIEW_ERROR_LIMIT
        or facts.copilot_review_request_limit_reason == "errors"
    ):
        return True
    if facts.copilot_review_request_limit_reason == "timeout":
        return False
    return (
        (
            facts.copilot_review_quota_exhausted
            or facts.copilot_review_request_limit_reason == "quota"
        )
        and not facts.copilot_review_fallback_available
        and not facts.copilot_review_request_in_flight
    )


def copilot_review_status(
    reviews: Sequence[Review],
    head_sha: str,
) -> tuple[bool, bool]:
    """Return (review exists, review is stale)."""
    copilot_reviews = [
        review
        for review in reviews
        if is_completed_copilot_review(review)
    ]
    if not copilot_reviews:
        return False, False
    if not head_sha:
        # Without a head to compare against, staleness is unknowable, and
        # acting on incomplete data would hold the pull request on a guess.
        return True, False
    stale = not any(
        review.commit_id == head_sha
        for review in copilot_reviews
    )
    return True, stale


def copilot_review_outstanding(facts: DashboardFacts, *, enabled: bool) -> bool:
    if not enabled or copilot_review_gate_disabled(facts):
        return False
    return not facts.copilot_review_exists or facts.copilot_review_needed


def copilot_review_unreported(facts: DashboardFacts, *, enabled: bool) -> bool:
    # Whether the gate is still waiting for Copilot to say anything about the
    # current head. Findings are an answer, not a silence: actionable threads
    # belong to discussion routing. Only a review that is missing or that
    # covers older code is a report that has not arrived.
    if not enabled or copilot_review_gate_disabled(facts):
        return False
    return not facts.copilot_review_exists or facts.copilot_review_stale


def set_copilot_review_request_needed(
    facts: DashboardFacts,
    route: str,
    *,
    enabled: bool,
) -> DashboardFacts:
    # Missing and stale reviews need a request. Findings on the current head
    # belong to the author; reviewing unchanged code again cannot resolve them.
    #
    # Pending checks do not hold the request back, so the review and the checks
    # run at once. Failing checks still do, because they route the pull request
    # to its author and only a reviewer route reaches here.
    return facts.with_changes(copilot_review_request_needed=(
        enabled
        and route in ("approver", "maintainer")
        and (
            not facts.copilot_review_exists
            or facts.copilot_review_stale
        )
        and not facts.copilot_review_requested
        and not facts.copilot_review_request_in_flight
        and not facts.copilot_review_request_limit_reason
        and not copilot_review_gate_disabled(facts)
    ))


def named_checks(checks: Sequence[Check]) -> str:
    names = sorted(check.name for check in checks)
    if len(names) > 3:
        return f"{', '.join(names[:3])} and {len(names) - 3} more"
    return ", ".join(names)


def stale_request_reason(
    entry: dict[str, Any],
    snapshot: RoutingSnapshot,
) -> str:
    if snapshot.state != "OPEN":
        return f"pull request state is {snapshot.state!r}"
    if snapshot.is_draft:
        return "pull request is a draft"
    if snapshot.has_conflicts:
        return "pull request has merge conflicts"
    if snapshot.head_sha != entry.get("head_sha"):
        return (
            f"head is {snapshot.head_sha or '(missing)'} "
            f"but {entry.get('head_sha') or '(missing)'} was observed"
        )
    # The fingerprint below only detects change, so checks that were failing
    # when the request was recorded and still are would otherwise pass through.
    checks = snapshot.checks
    if checks is None:
        return "required check results are unavailable"
    failing = [
        check
        for check in checks
        if check.bucket in ("fail", "cancel", "action_required")
    ]
    if failing:
        return f"required checks are failing: {named_checks(failing)}"
    if not entry.get("copilot_request_fingerprint"):
        return "no routing fingerprint was observed"
    if (
        snapshot.copilot_request_fingerprint
        != entry["copilot_request_fingerprint"]
    ):
        return (
            f"routing fingerprint is {snapshot.copilot_request_fingerprint} "
            f"but {entry['copilot_request_fingerprint']} was observed; "
            f"components {snapshot.copilot_request_component_digests}"
        )
    return ""
