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
    is_copilot_quota_failure,
    is_copilot_reviewer,
    refresh_copilot_review_cycle,
    stale_request_reason,
)
from dashboard_contracts import CopilotReviewCycle, StoredDashboardResult
from github_cli import CopilotReviewRequestRejected, fetch_pr_reviews, request_copilot_review
from pull_request_source import normalize_reviews
from routing_snapshot import fetch_routing_snapshot
from state import (
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
        if entry.get("requested_at"):
            continue
        pr_number = int(key)
        reservation: CopilotReviewCycle | None = None
        rollback_cycle: CopilotReviewCycle | None = None
        request_started = False

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

        def finish(
            cycle: CopilotReviewCycle | None,
            requested_at: str | None = None,
            *,
            discard: bool = False,
        ) -> None:
            def record() -> int:
                cycles = load_copilot_review_cycles()
                current = cycles.get(key)
                if current != reservation and current != cycle:
                    raise RuntimeError("Copilot dispatch changed; refusing to overwrite its state")
                if cycle is None:
                    cycles.pop(key, None)
                else:
                    cycles[key] = cycle
                save_copilot_review_cycles(cycles)
                update_receipt(requested_at, discard=discard)
                return 0

            commit_state(record)

        try:
            snapshot = fetch_routing_snapshot(repo, pr_number)
            stale_reason = stale_request_reason(entry, snapshot)
            if stale_reason:
                print(
                    f"discarding Copilot review request for PR #{pr_number}: {stale_reason}",
                    file=sys.stderr,
                )
                commit_state(discard_receipt)
                continue
            reviews = normalize_reviews(fetch_pr_reviews(owner, repo_name, pr_number))
            error_count = copilot_review_error_count(reviews, snapshot.head_sha)
            if error_count >= COPILOT_REVIEW_ERROR_LIMIT:
                print(
                    f"discarding Copilot review request for PR #{pr_number}: "
                    f"Copilot reported {error_count} errors on head {snapshot.head_sha}",
                    file=sys.stderr,
                )
                commit_state(discard_receipt)
                continue
            exists, stale = copilot_review_status(reviews, snapshot.head_sha)
            if exists and not stale:
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
            dispatch_id = str(uuid.uuid4())

            def reserve() -> int:
                nonlocal reservation, rollback_cycle
                reservation = None
                cycles = load_copilot_review_cycles()
                prior = cycles.get(key)
                if prior is not None and prior.dispatch_id == dispatch_id:
                    if prior.phase != "sending":
                        raise RuntimeError("Copilot dispatch was already processed")
                    reservation = prior
                    return 0
                cycle = refresh_copilot_review_cycle(
                    prior, reviews, snapshot.head_sha, prepared_at,
                    fallback_available=bool(token),
                )
                if cycle is not None:
                    cycles[key] = cycle
                if (
                    copilot_review_in_flight(cycle)
                    or (cycle is not None and cycle.phase in ("limited", "complete"))
                ):
                    save_copilot_review_cycles(cycles)
                    return 0
                if (
                    copilot_quota_issue(reviews, snapshot.head_sha)
                    or entry.get("quota_exhausted")
                ) and not token:
                    save_copilot_review_cycles(cycles)
                    update_receipt(discard=True)
                    return 0
                rollback_cycle = cycle
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
                    dispatch_id=dispatch_id,
                    started_at=(cycle.started_at if cycle is not None else "") or format_ts(prepared_at),
                    sent_at="",
                    baseline_review_ids=baseline_ids,
                    limit_reason="",
                )
                cycles[key] = reservation
                save_copilot_review_cycles(cycles)
                return 0

            commit_state(reserve)
            if reservation is None:
                continue
            snapshot = fetch_routing_snapshot(repo, pr_number)
            stale_reason = stale_request_reason(entry, snapshot)
            if stale_reason:
                print(
                    f"discarding reserved Copilot request for PR #{pr_number}: {stale_reason}",
                    file=sys.stderr,
                )
                finish(rollback_cycle, discard=True)
                continue
            reviews = normalize_reviews(fetch_pr_reviews(owner, repo_name, pr_number))
            exists, stale = copilot_review_status(reviews, snapshot.head_sha)
            if exists and not stale:
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
            refreshed = refresh_copilot_review_cycle(
                reservation, reviews, snapshot.head_sha, sending_at,
                fallback_available=bool(token),
            )
            if refreshed is not None and refreshed.phase == "limited":
                print(
                    f"discarding reserved Copilot request for PR #{pr_number}: "
                    "the review cycle expired before dispatch",
                    file=sys.stderr,
                )
                finish(refreshed, discard=True)
                continue
            sent_at = format_ts(sending_at)
            try:
                request_started = True
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
            if head_changed:
                print(
                    f"Copilot request for PR #{pr_number} returned head {requested_head}; "
                    f"not acknowledging the old head {entry['head_sha']}",
                    file=sys.stderr,
                )
            finish(
                replace(
                    reservation, phase="accepted", head_sha=requested_head,
                    request_count=1 if head_changed else reservation.request_count,
                    started_at=sent_at if head_changed else reservation.started_at,
                    sent_at=sent_at, baseline_review_ids=baseline_ids,
                ),
                sent_at,
                discard=head_changed,
            )
        except Exception as error:
            if reservation is not None and not request_started:
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
