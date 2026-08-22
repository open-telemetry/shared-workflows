"""Track Copilot re-review requests for delivery by the publisher job."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from github_cli import (
    fetch_pr_reviews,
    fetch_review_requests,
    request_copilot_review,
    sleep_for_retry,
)
from routing_snapshot import RoutingSnapshot
from utils import (
    actor_login,
    format_ts,
    is_copilot_reviewer_login,
    parse_ts,
    utc_now,
)


# How long the automatic first review is given to arrive before the dashboard
# requests one itself. Measured against observed first reviews, which normally
# land within twenty minutes and have been seen as late as forty; an hour
# clears that without waiting through the whole first review cycle again.
FIRST_REVIEW_GRACE = timedelta(hours=1)


# How many times the pull request is read back before a request counts as
# missing. GitHub takes a moment to record a reviewer it did accept, so a
# single empty read proves nothing.
REQUEST_CONFIRMATION_ATTEMPTS = 3


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


def copilot_review_unreported(facts: dict[str, Any], *, enabled: bool) -> bool:
    # Whether the gate is still waiting for Copilot to say anything about the
    # current head. Findings are an answer, not a silence: the threads they
    # leave are the author's to clear, and the dashboard already routes the
    # pull request to the author for them. Only a review that is missing or
    # that covers older code is a report that has not arrived.
    if not enabled:
        return False
    return not facts.get("copilot_review_exists") or bool(
        facts.get("copilot_review_stale")
    )


def set_copilot_first_review_missing_since(
    facts: dict[str, Any],
    previous_result: dict[str, Any] | None,
    *,
    enabled: bool,
    now: datetime,
) -> None:
    # How long this pull request has been waiting on a first review that GitHub
    # was expected to start automatically. The clock runs only while the wait is
    # real: the gate applies, the pull request is out of draft, and Copilot has
    # never reviewed it. Draft resets it because GitHub starts the automatic
    # review when a pull request becomes ready, not when it is opened. A push
    # deliberately does not reset it, because GitHub does not automatically
    # review a pull request it has never reviewed, so restarting the wait on
    # every push would leave an actively developed pull request waiting forever.
    previous_facts = (previous_result or {}).get("facts") or {}
    if not enabled or facts.get("is_draft") or facts.get("copilot_review_exists"):
        facts.pop("copilot_first_review_missing_since", None)
        return
    facts["copilot_first_review_missing_since"] = str(
        previous_facts.get("copilot_first_review_missing_since") or format_ts(now)
    )


def copilot_first_review_overdue(facts: dict[str, Any], now: datetime) -> bool:
    missing_since = parse_ts(facts.get("copilot_first_review_missing_since"))
    if missing_since is None:
        return False
    return now - missing_since >= FIRST_REVIEW_GRACE


def set_copilot_review_request_needed(
    facts: dict[str, Any],
    route: str,
    *,
    enabled: bool,
    now: datetime | None = None,
) -> None:
    # Only two states are worth a request. A stale review means the author
    # pushed, which is the one change a re-review can respond to; findings on
    # the current head sit on unchanged code, so re-reviewing would reach the
    # same verdict and be requested again on every pass. A review GitHub should
    # have started automatically and never did is the other: the gate would
    # otherwise hold the pull request on its author indefinitely, waiting for a
    # review nobody has asked for.
    #
    # Pending checks do not hold the request back, so the review and the checks
    # run at once. Failing checks still do, because they route the pull request
    # to its author and only a reviewer route reaches here.
    now = now or utc_now()
    review_missing = not facts.get("copilot_review_exists")
    facts["copilot_review_request_needed"] = (
        enabled
        and route in ("approver", "maintainer")
        and (
            (
                bool(facts.get("copilot_review_exists"))
                and bool(facts.get("copilot_review_stale"))
            )
            or (review_missing and copilot_first_review_overdue(facts, now))
        )
        and not facts.get("copilot_review_requested")
    )


def named_checks(checks: list[dict[str, Any]]) -> str:
    names = sorted(check.get("name") or "" for check in checks)
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
    failing = [check for check in checks if check.get("bucket") in ("fail", "cancel")]
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


def copilot_review_request_landed(
    owner: str,
    repo_name: str,
    pr_number: int,
    head_sha: str,
) -> bool:
    """Report whether GitHub recorded the Copilot review request just sent."""
    for attempt in range(REQUEST_CONFIRMATION_ATTEMPTS):
        if attempt:
            sleep_for_retry(attempt - 1)
        if any(
            is_copilot_reviewer(request)
            for request in fetch_review_requests(owner, repo_name, pr_number) or []
        ):
            return True
    # Copilot can finish a short review before the last read, which takes it
    # back out of the pending requests. A review of the current head proves the
    # request landed just as well as a pending one does.
    review_exists, review_stale, _findings = copilot_review_status(
        fetch_pr_reviews(owner, repo_name, pr_number) or [],
        head_sha,
        [],
    )
    return review_exists and not review_stale
