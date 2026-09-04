"""Prepare reviewer state and resolve dashboard reviewer rows."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from dashboard_contracts import ReviewerSummary
from pull_request_source import Actor, ReviewRequest
from pull_request_activity import reviewer_actor_login


@dataclass(frozen=True)
class ReviewerInput:
    events: tuple[Mapping[str, Any], ...]
    review_requests: tuple[ReviewRequest, ...]
    assignees: tuple[Actor, ...]


@dataclass(frozen=True)
class _PreparedReviewerState:
    active_review_states: Mapping[str, str]
    approver_logins: frozenset[str]
    participating_approver_logins: frozenset[str]


@dataclass(frozen=True)
class PreparedReviewers:
    approval_count: int
    assignee_logins: tuple[str, ...]
    pending_human_reviewer_logins: frozenset[str]
    _state: _PreparedReviewerState = field(repr=False, compare=False)


@dataclass(frozen=True)
class ReviewerDiscussionInput:
    review_threads: tuple[dict[str, Any], ...]
    top_level_feedback: tuple[dict[str, Any], ...]
    pending_actions: Mapping[str, dict[str, Any]]


_OPEN_DISCUSSION_ACTIONS = {"author", "reviewer"}


def _latest_review_states(events: tuple[Mapping[str, Any], ...]) -> dict[str, str]:
    latest_by_reviewer: dict[str, tuple[str, str]] = {}
    for event in events:
        if event.get("kind") != "review-state":
            continue
        reviewer = event.get("actor") or ""
        submitted_at = event.get("timestamp") or ""
        state = event.get("state") or ""
        if not reviewer or not submitted_at or state == "COMMENTED":
            continue
        previous = latest_by_reviewer.get(reviewer)
        if previous is None or submitted_at >= previous[0]:
            latest_by_reviewer[reviewer] = (submitted_at, state)
    return {
        reviewer: state
        for reviewer, (_, state) in latest_by_reviewer.items()
    }


def _human_request_logins(
    review_requests: tuple[ReviewRequest, ...],
) -> set[str]:
    return {
        request.login
        for request in review_requests
        if request.is_human and request.login
    }


def _reviewing_logins(events: tuple[Mapping[str, Any], ...]) -> set[str]:
    return {
        event["actor"]
        for event in events
        if event.get("kind") == "review-state" and event.get("actor")
    }


def _approver_logins(events: tuple[Mapping[str, Any], ...]) -> set[str]:
    return {
        event["actor"]
        for event in events
        if event.get("actor_role") == "approver" and event.get("actor")
    }


def _participating_approver_logins(
    events: tuple[Mapping[str, Any], ...],
) -> set[str]:
    return {
        event["actor"]
        for event in events
        if event.get("actor_role") == "approver"
        and event.get("kind")
        in ("issue-comment", "review-comment", "review-state")
        and event.get("actor")
    }


def prepare_reviewers(source: ReviewerInput) -> PreparedReviewers:
    requested = _human_request_logins(source.review_requests)
    pending_reviews = requested & _reviewing_logins(source.events)
    active_states = {
        reviewer: state
        for reviewer, state in _latest_review_states(source.events).items()
        if state != "APPROVED" or reviewer not in requested
    }
    approvers = _approver_logins(source.events)
    approval_count = sum(
        1
        for reviewer, state in active_states.items()
        if state == "APPROVED" and reviewer in approvers
    )
    assignee_logins = tuple(
        login
        for assignee in source.assignees
        if (login := reviewer_actor_login(assignee))
    )
    return PreparedReviewers(
        approval_count=approval_count,
        assignee_logins=assignee_logins,
        pending_human_reviewer_logins=frozenset(pending_reviews),
        _state=_PreparedReviewerState(
            active_review_states=active_states,
            approver_logins=frozenset(approvers),
            participating_approver_logins=frozenset(
                _participating_approver_logins(source.events)
            ),
        ),
    )


def _reviewers_with_open_threads(
    source: ReviewerDiscussionInput,
) -> set[str]:
    logins: set[str] = set()
    for discussion in source.review_threads:
        entry = source.pending_actions.get(discussion["discussion_id"]) or {}
        if entry.get("action") not in _OPEN_DISCUSSION_ACTIONS:
            continue
        comments = discussion.get("comments") or []
        if entry.get("ignored_last_comment"):
            ignored_index = entry.get("ignored_comment_index")
            if (
                isinstance(ignored_index, int)
                and not isinstance(ignored_index, bool)
                and 0 <= ignored_index < len(comments)
            ):
                comments = [
                    comment
                    for index, comment in enumerate(comments)
                    if index != ignored_index
                ]
            else:
                comments = comments[:-1]
        for comment in comments:
            if (
                comment.get("actor_role") in ("approver", "outsider", "bot")
                and comment.get("actor")
            ):
                logins.add(comment["actor"])
    return logins


def _reviewers_with_top_level_feedback(
    source: ReviewerDiscussionInput,
) -> set[str]:
    logins: set[str] = set()
    for discussion in source.top_level_feedback:
        entry = source.pending_actions.get(discussion["discussion_id"]) or {}
        if entry.get("action") == "author" and discussion.get("requester"):
            logins.add(discussion["requester"])
    return logins


def resolve_reviewers(
    prepared: PreparedReviewers,
    source: ReviewerDiscussionInput,
) -> tuple[ReviewerSummary, ...]:
    states = prepared._state.active_review_states
    approvers = prepared._state.approver_logins
    approved = {
        reviewer
        for reviewer, state in states.items()
        if state == "APPROVED" and reviewer in approvers
    }
    approved_non_team = {
        reviewer
        for reviewer, state in states.items()
        if state == "APPROVED" and reviewer not in approvers
    }
    changes_requested = {
        reviewer
        for reviewer, state in states.items()
        if state == "CHANGES_REQUESTED"
    }
    with_open = _reviewers_with_open_threads(source)
    with_top_level = _reviewers_with_top_level_feedback(source)
    candidates = (
        approved
        | approved_non_team
        | set(prepared.pending_human_reviewer_logins)
        | changes_requested
        | with_open
        | with_top_level
        | set(prepared._state.participating_approver_logins)
        | set(prepared.assignee_logins)
    )
    candidates.discard("")
    return tuple(
        ReviewerSummary(
            login=login,
            approved=login in approved,
            approved_non_team=login in approved_non_team,
            pending_review=login in prepared.pending_human_reviewer_logins,
            changes_requested=login in changes_requested,
            open_thread=login in with_open,
            top_level_feedback=login in with_top_level,
        )
        for login in sorted(candidates, key=str.lower)
    )
