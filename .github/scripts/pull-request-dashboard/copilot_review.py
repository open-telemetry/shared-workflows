"""Track Copilot re-review requests for delivery by the publisher job."""

from __future__ import annotations

from datetime import datetime, timedelta
from collections.abc import Sequence
from typing import Any

from dashboard_contracts import DashboardFacts
from github_cli import (
    fetch_pr_reviews,
    fetch_review_requests,
    sleep_for_retry,
)
from routing_snapshot import RoutingSnapshot
from pull_request_source import (
    Actor,
    Check,
    Review,
    ReviewRequest,
    ReviewThread,
    normalize_review_requests,
    normalize_reviews,
)
from utils import (
    format_ts,
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


def is_copilot_reviewer(
    value: Actor | ReviewRequest | Review,
) -> bool:
    if isinstance(value, ReviewRequest):
        return value.is_copilot_reviewer
    actor = value.actor if isinstance(value, Review) else value
    return actor.is_copilot_reviewer


def open_copilot_findings(
    review_threads: Sequence[ReviewThread],
) -> tuple[ReviewThread, ...]:
    # A review's own comment count never shrinks, so it still counts findings
    # the author has since addressed. Unresolved threads are the live ones:
    # GitHub marks a thread outdated once its anchor lines change, which is how
    # the rest of the dashboard already recognises a pushed fix.
    return tuple(
        thread
        for thread in review_threads
        if (
            not thread.is_resolved
            and not thread.is_outdated
            and thread.comments
            and is_copilot_reviewer(thread.comments[0].actor)
        )
    )


def open_copilot_finding_count(
    review_threads: Sequence[ReviewThread],
) -> int:
    return len(open_copilot_findings(review_threads))


def open_copilot_finding_urls(
    review_threads: Sequence[ReviewThread],
) -> tuple[str, ...]:
    urls: list[str] = []
    for thread in open_copilot_findings(review_threads):
        url = thread.comments[0].url
        if url and url not in urls:
            urls.append(url)
    return tuple(urls)


def copilot_review_status(
    reviews: Sequence[Review],
    head_sha: str,
    review_threads: Sequence[ReviewThread],
) -> tuple[bool, bool, bool]:
    """Return (review exists, review is stale, review left open findings)."""
    copilot_reviews = [
        review
        for review in reviews
        if is_copilot_reviewer(review)
    ]
    if not copilot_reviews:
        return False, False, False
    if not head_sha:
        # Without a head to compare against, staleness is unknowable, and
        # acting on incomplete data would hold the pull request on a guess.
        return True, False, False
    stale = not any(
        review.commit_id == head_sha
        for review in copilot_reviews
    )
    return True, stale, open_copilot_finding_count(review_threads) > 0


def copilot_review_outstanding(facts: DashboardFacts, *, enabled: bool) -> bool:
    if not enabled:
        return False
    return not facts.copilot_review_exists or facts.copilot_review_needed


def copilot_review_unreported(facts: DashboardFacts, *, enabled: bool) -> bool:
    # Whether the gate is still waiting for Copilot to say anything about the
    # current head. Findings are an answer, not a silence: the threads they
    # leave are the author's to clear, and the dashboard already routes the
    # pull request to the author for them. Only a review that is missing or
    # that covers older code is a report that has not arrived.
    if not enabled:
        return False
    return not facts.copilot_review_exists or facts.copilot_review_stale


def set_copilot_first_review_missing_since(
    facts: DashboardFacts,
    previous_facts: DashboardFacts,
    *,
    enabled: bool,
    now: datetime,
) -> DashboardFacts:
    # How long this pull request has been waiting on a first review that GitHub
    # was expected to start automatically. The clock runs only while the wait is
    # real: the gate applies, the pull request is out of draft, and Copilot has
    # never reviewed it. Draft resets it because GitHub starts the automatic
    # review when a pull request becomes ready, not when it is opened. A push
    # deliberately does not reset it, because GitHub does not automatically
    # review a pull request it has never reviewed, so restarting the wait on
    # every push would leave an actively developed pull request waiting forever.
    if not enabled or facts.is_draft or facts.copilot_review_exists:
        return facts.with_changes(copilot_first_review_missing_since=None)
    return facts.with_changes(
        copilot_first_review_missing_since=(
            previous_facts.copilot_first_review_missing_since
            or format_ts(now)
        )
    )


def copilot_first_review_overdue(facts: DashboardFacts, now: datetime) -> bool:
    missing_since = parse_ts(facts.copilot_first_review_missing_since)
    if missing_since is None:
        return False
    return now - missing_since >= FIRST_REVIEW_GRACE


def set_copilot_review_request_needed(
    facts: DashboardFacts,
    route: str,
    *,
    enabled: bool,
    now: datetime | None = None,
) -> DashboardFacts:
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
    review_missing = not facts.copilot_review_exists
    return facts.with_changes(copilot_review_request_needed=(
        enabled
        and route in ("approver", "maintainer")
        and (
            (
                facts.copilot_review_exists
                and facts.copilot_review_stale
            )
            or (review_missing and copilot_first_review_overdue(facts, now))
        )
        and not facts.copilot_review_requested
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
            for request in normalize_review_requests(
                fetch_review_requests(owner, repo_name, pr_number)
            )
        ):
            return True
    # Copilot can finish a short review before the last read, which takes it
    # back out of the pending requests. A review of the current head proves the
    # request landed just as well as a pending one does.
    review_exists, review_stale, _findings = copilot_review_status(
        normalize_reviews(fetch_pr_reviews(owner, repo_name, pr_number)),
        head_sha,
        [],
    )
    return review_exists and not review_stale
