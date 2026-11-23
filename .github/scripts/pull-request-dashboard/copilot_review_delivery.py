"""Deliver accepted Copilot review intents through durable dispatch reservations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
import os
import sys
import uuid

from copilot_review import (
    COPILOT_REVIEW_ERROR_LIMIT,
    copilot_quota_issue,
    copilot_review_error_count,
    copilot_review_ids,
    copilot_review_in_flight,
    copilot_review_status,
    copilot_review_window_expired,
    is_copilot_quota_failure,
    is_copilot_reviewer,
    refresh_copilot_review_cycle,
    stale_request_reason,
)
from dashboard_contracts import CopilotReviewCycle, CopilotReviewCycleUpdate, StoredDashboardResult
from github_cli import CopilotReviewRequestRejected, fetch_pr_reviews, request_copilot_review
from pull_request_source import Review, normalize_reviews
from routing_snapshot import RoutingSnapshot, fetch_routing_snapshot
from state import (
    apply_copilot_review_cycle_update,
    load_copilot_review_cycles,
    load_copilot_review_requests,
    save_copilot_review_cycles,
    save_copilot_review_requests,
)
from utils import format_ts, utc_now


def record_copilot_review_observation(
    pr_number: int,
    result: StoredDashboardResult | None,
    observed_at: datetime,
) -> None:
    requests = dict(load_copilot_review_requests())
    key = str(pr_number)
    facts = result.facts if result is not None else None
    head_sha = facts.head_sha if facts is not None else ""
    request_fingerprint = facts.copilot_request_fingerprint if facts is not None else ""
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
            **({"quota_exhausted": True} if facts.copilot_review_quota_exhausted else {}),
        }
    save_copilot_review_requests(requests)


def deliver_copilot_review_requests(
    repo: str,
    commit_state: Callable[[Callable[[], int]], None],
) -> list[str]:
    requests = dict(load_copilot_review_requests())
    token = os.environ.get("COPILOT_REVIEW_FALLBACK_TOKEN") or None
    owner, repo_name = repo.split("/", 1)
    errors: list[str] = []
    for key, entry in sorted(requests.items(), key=lambda item: int(item[0])):
        pr_number = int(key)
        reservation: CopilotReviewCycle | None = None
        rollback_cycle: CopilotReviewCycle | None = None
        request_invoked = False

        def update_receipt(requested_at: str | None = None, *, discard: bool = False) -> None:
            current = dict(load_copilot_review_requests())
            intent = current.get(key)
            if intent is not None and (
                intent.get("head_sha") == entry.get("head_sha")
                and intent.get("copilot_request_fingerprint") == entry.get("copilot_request_fingerprint")
            ):
                if discard:
                    current.pop(key, None)
                elif requested_at is not None:
                    current[key] = {**intent, "requested_at": requested_at}
                save_copilot_review_requests(current)

        def discard_receipt() -> int:
            update_receipt(discard=True)
            return 0

        def commit_cycle_update(
            update: CopilotReviewCycleUpdate,
            requested_at: str | None = None,
            *,
            discard: bool = False,
        ) -> bool:
            applied = False

            def record() -> int:
                nonlocal applied
                cycles = load_copilot_review_cycles()
                applied = apply_copilot_review_cycle_update(cycles, key, update)
                if applied:
                    save_copilot_review_cycles(cycles)
                    # Empty timestamps checkpoint pending receipts; None leaves receipts untouched.
                    if requested_at is not None or discard:
                        update_receipt(requested_at, discard=discard)
                return 0

            commit_state(record)
            if not applied:
                print(
                    f"Copilot cycle changed for PR #{pr_number}; conditional update skipped",
                    file=sys.stderr,
                )
            return applied

        def finish(
            cycle: CopilotReviewCycle | None,
            requested_at: str | None = None,
            *,
            discard: bool = False,
        ) -> None:
            if not commit_cycle_update(
                CopilotReviewCycleUpdate(reservation, cycle),
                requested_at if requested_at is not None else "",
                discard=discard,
            ):
                raise RuntimeError("Copilot dispatch changed; refusing to overwrite its state")

        def read_preflight() -> tuple[RoutingSnapshot, str, tuple[Review, ...]]:
            snapshot = fetch_routing_snapshot(repo, pr_number)
            stale_reason = stale_request_reason(entry, snapshot)
            # Dispatch needs a current intent; coverage needs only a current head.
            reviews = (
                snapshot.reviews if stale_reason
                else normalize_reviews(fetch_pr_reviews(owner, repo_name, pr_number))
            )
            return snapshot, stale_reason, reviews

        try:
            observed_cycle = load_copilot_review_cycles().get(key)
            snapshot, stale_reason, reviews = read_preflight()
            exists, stale = copilot_review_status(reviews, snapshot.head_sha)
            if snapshot.head_sha and exists and not stale:
                if observed_cycle is None or observed_cycle.phase != "sending":
                    complete = refresh_copilot_review_cycle(
                        observed_cycle, reviews, snapshot.head_sha, utc_now(),
                        fallback_available=bool(token),
                    )
                    commit_cycle_update(
                        CopilotReviewCycleUpdate(observed_cycle, complete), discard=True,
                    )
                continue
            if stale_reason:
                print(
                    f"discarding Copilot review request for PR #{pr_number}: {stale_reason}",
                    file=sys.stderr,
                )
                commit_state(discard_receipt)
                continue
            error_count = copilot_review_error_count(reviews, snapshot.head_sha)
            if error_count >= COPILOT_REVIEW_ERROR_LIMIT:
                print(
                    f"discarding Copilot review request for PR #{pr_number}: "
                    f"Copilot reported {error_count} errors on head {snapshot.head_sha}",
                    file=sys.stderr,
                )
                commit_state(discard_receipt)
                continue
            if any(is_copilot_reviewer(request) for request in snapshot.review_requests):
                observed_at = format_ts(utc_now())

                def acknowledge_pending() -> int:
                    update_receipt(observed_at)
                    return 0

                commit_state(acknowledge_pending)
                continue
            baseline_ids = copilot_review_ids(reviews)
            prepared_at = utc_now()
            cycle = refresh_copilot_review_cycle(
                observed_cycle, reviews, snapshot.head_sha, prepared_at,
                fallback_available=bool(token),
            )
            # Keep same-head settlement, but retain the prior budget for an unsent head reset.
            rollback_cycle = (
                observed_cycle
                if observed_cycle is not None and cycle is not None
                and observed_cycle.head_sha != cycle.head_sha
                else cycle
            )
            if (
                copilot_review_in_flight(cycle)
                or (cycle is not None and cycle.phase in ("limited", "complete"))
            ):
                commit_cycle_update(CopilotReviewCycleUpdate(observed_cycle, cycle))
                continue
            if (
                copilot_quota_issue(reviews, snapshot.head_sha)
                or entry.get("quota_exhausted")
            ) and not token:
                commit_cycle_update(CopilotReviewCycleUpdate(observed_cycle, rollback_cycle), discard=True)
                continue
            requester = (
                "fallback"
                if token and (
                    (cycle is not None and cycle.requester == "fallback")
                    or entry.get("quota_exhausted")
                    or any(is_copilot_quota_failure(review) for review in reviews)
                )
                else "app"
            )
            reservation = replace(
                cycle or CopilotReviewCycle(snapshot.head_sha),
                requester=requester,
                phase="sending",
                request_count=(cycle.request_count if cycle is not None else 0) + 1,
                dispatch_id=str(uuid.uuid4()),
                started_at=(cycle.started_at if cycle is not None else "") or format_ts(prepared_at),
                reserved_at=format_ts(prepared_at),
                sent_at="",
                baseline_review_ids=baseline_ids,
                limit_reason="",
            )
            if not commit_cycle_update(CopilotReviewCycleUpdate(observed_cycle, reservation)):
                reservation = None
                continue
            snapshot, stale_reason, reviews = read_preflight()
            exists, stale = copilot_review_status(reviews, snapshot.head_sha)
            if snapshot.head_sha and exists and not stale:
                finish(
                    refresh_copilot_review_cycle(
                        rollback_cycle, reviews, snapshot.head_sha, utc_now(),
                        fallback_available=bool(token),
                    ),
                    discard=True,
                )
                continue
            if stale_reason:
                print(
                    f"discarding reserved Copilot request for PR #{pr_number}: {stale_reason}",
                    file=sys.stderr,
                )
                finish(rollback_cycle, discard=True)
                continue
            if copilot_review_error_count(reviews, snapshot.head_sha) >= COPILOT_REVIEW_ERROR_LIMIT:
                print(
                    f"discarding reserved Copilot request for PR #{pr_number}: "
                    "Copilot's error limit was reached",
                    file=sys.stderr,
                )
                finish(rollback_cycle, discard=True)
                continue
            if any(is_copilot_reviewer(request) for request in snapshot.review_requests):
                finish(rollback_cycle, format_ts(utc_now()))
                continue
            if reservation.requester == "app" and copilot_quota_issue(reviews, snapshot.head_sha):
                print(
                    f"discarding reserved Copilot request for PR #{pr_number}: "
                    "a new quota notice requires a fresh routing observation",
                    file=sys.stderr,
                )
                finish(rollback_cycle, discard=True)
                continue
            pull_request_id = snapshot.node_id
            if not pull_request_id:
                finish(rollback_cycle)
                raise RuntimeError(f"GitHub did not return a node ID for PR #{pr_number}")
            baseline_ids = copilot_review_ids(reviews)

            def validate_reservation() -> int:
                if load_copilot_review_cycles().get(key) != reservation:
                    raise RuntimeError("Copilot reservation changed before dispatch")
                return 0

            commit_state(validate_reservation)
            sending_at = utc_now()
            if copilot_review_window_expired(reservation.started_at, sending_at):
                print(
                    f"discarding reserved Copilot request for PR #{pr_number}: "
                    "the review cycle expired before dispatch",
                    file=sys.stderr,
                )
                finish(replace(reservation, phase="limited", limit_reason="timeout"), discard=True)
                continue
            sent_at = format_ts(sending_at)
            try:
                request_invoked = True
                requested_head = (
                    request_copilot_review(pull_request_id, token=token)
                    if reservation.requester == "fallback"
                    else request_copilot_review(pull_request_id)
                )
            except Exception as request_error:
                outcome = (
                    rollback_cycle
                    if isinstance(request_error, CopilotReviewRequestRejected)
                    else replace(
                        reservation, phase="unknown", sent_at=sent_at, baseline_review_ids=baseline_ids,
                    )
                )
                try:
                    finish(outcome)
                except Exception as state_error:
                    raise RuntimeError(
                        f"Copilot request failed: {request_error}; "
                        f"its outcome could not be recorded: {state_error}"
                    ) from state_error
                raise
            head_changed = requested_head != reservation.head_sha
            request_count = reservation.request_count
            started_at = reservation.started_at
            if head_changed:
                if rollback_cycle is not None and rollback_cycle.head_sha == requested_head:
                    request_count = rollback_cycle.request_count + 1
                    started_at = rollback_cycle.started_at or sent_at
                else:
                    request_count = 1
                    started_at = sent_at
                print(
                    f"Copilot request for PR #{pr_number} returned head {requested_head}; "
                    f"not acknowledging the old head {entry['head_sha']}",
                    file=sys.stderr,
                )
            finish(
                replace(
                    reservation, phase="accepted", head_sha=requested_head,
                    request_count=request_count, started_at=started_at,
                    sent_at=sent_at, baseline_review_ids=baseline_ids,
                ),
                sent_at,
                discard=head_changed,
            )
        except Exception as error:
            if reservation is not None and not request_invoked:
                try:
                    finish(rollback_cycle)
                except Exception as state_error:
                    errors.append(
                        f"PR #{pr_number}: {error}; "
                        f"its unsent reservation could not be released: {state_error}"
                    )
                    continue
            errors.append(f"PR #{pr_number}: {error}")
    return errors
