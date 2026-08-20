"""Track Copilot re-review requests for delivery by the publisher job."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import sys
from typing import Any

from author_nudge import (
    copilot_request_component_digests,
    copilot_request_fingerprint,
    fetch_current_pr_routing_inputs,
)
from github_cli import (
    fetch_pr_reviews,
    fetch_review_requests,
    request_copilot_review,
    sleep_for_retry,
)
from state import load_copilot_review_requests, save_copilot_review_requests
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


def record_copilot_review_observation(
    pr_number: int,
    result: dict[str, Any] | None,
    observed_at: datetime,
) -> None:
    requests = dict(load_copilot_review_requests())
    key = str(pr_number)
    facts = (result or {}).get("facts") or {}
    head_sha = str(facts.get("head_sha") or "")
    request_fingerprint = str(facts.get("copilot_request_fingerprint") or "")
    if (
        not result
        or result.get("failed")
        or not facts.get("copilot_review_request_needed")
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


def named_checks(checks: list[dict[str, Any]]) -> str:
    names = sorted(check.get("name") or "" for check in checks)
    if len(names) > 3:
        return f"{', '.join(names[:3])} and {len(names) - 3} more"
    return ", ".join(names)


def stale_request_reason(
    entry: dict[str, Any],
    pr: dict[str, Any],
    current_head: str,
    current_request_fingerprint: str,
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
    # The fingerprint below only detects change, so checks that were failing
    # when the request was recorded and still are would otherwise pass through.
    checks = raw.get("checks")
    if checks is None:
        return "required check results are unavailable"
    failing = [check for check in checks if check.get("bucket") in ("fail", "cancel")]
    if failing:
        return f"required checks are failing: {named_checks(failing)}"
    if not entry.get("copilot_request_fingerprint"):
        return "no routing fingerprint was observed"
    if current_request_fingerprint != entry["copilot_request_fingerprint"]:
        digests = copilot_request_component_digests(raw)
        return (
            f"routing fingerprint is {current_request_fingerprint} "
            f"but {entry['copilot_request_fingerprint']} was observed; "
            f"components {digests}"
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
            current_request_fingerprint = copilot_request_fingerprint(raw)
            current_head = pr.get("headRefOid") or ""
            stale_reason = stale_request_reason(
                entry,
                pr,
                current_head,
                current_request_fingerprint,
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
            # A missing review is a reason to request, not to discard: the
            # request was recorded precisely because the automatic first review
            # never arrived. Only a review that already covers the current head
            # makes the request pointless, which is what happens when one lands
            # between the observation and this delivery.
            if review_exists and not review_stale:
                print(
                    f"discarding Copilot review request for PR #{pr_number}: "
                    f"Copilot review already covers head {current_head}",
                    file=sys.stderr,
                )
                requests.pop(key, None)
                continue
            pull_request_id = pr.get("id") or ""
            if not pull_request_id:
                raise RuntimeError(f"GitHub did not return a node ID for PR #{pr_number}")
            request_copilot_review(pull_request_id)
            landed = copilot_review_request_landed(
                owner,
                repo_name,
                pr_number,
                current_head,
            )
        except Exception as e:
            errors.append(f"PR #{pr_number}: {e}")
            continue
        if landed:
            requests[key] = {**entry, "requested_at": format_ts(now)}
            continue
        # Leaving the request undelivered keeps the next pass trying. Nothing
        # escalates from here: a request that keeps going missing leaves the
        # pull request held, and the hold is what reports the stall.
        print(
            f"GitHub did not record the Copilot review request for "
            f"PR #{pr_number} on head {current_head}",
            file=sys.stderr,
        )
    save_copilot_review_requests(requests)
    return errors