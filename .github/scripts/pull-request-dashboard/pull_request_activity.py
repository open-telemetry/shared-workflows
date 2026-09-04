from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any

from dashboard_override import dashboard_command_body_remainder
from pull_request_source import (
    Actor,
    Commit,
    IssueComment,
    PullRequestSource,
    Review,
    ReviewComment,
)
from utils import parse_ts


# `role_for` assigns the author role before it checks for bot-shaped logins, so
# a bot that opened the pull request counts as a participant here.
_PARTICIPANT_ACTOR_ROLES = {"author", "approver", "outsider"}


@dataclass(frozen=True)
class ActivityInput:
    source: PullRequestSource
    author: str
    approver_logins: frozenset[str]


@dataclass(frozen=True)
class PullRequestActivity:
    events: tuple[Mapping[str, Any], ...]
    latest_participant_activity_at: datetime | None
    latest_author_activity_at: datetime | None
    latest_approver_activity_at: datetime | None


def role_for(login: str, author: str, reviewers: set[str]) -> str:
    if not login:
        return "outsider"
    low = login.lower()
    if low == author.lower():
        return "author"
    if low in reviewers:
        return "approver"
    if low.startswith("app/") or low.endswith("[bot]"):
        return "bot"
    return "outsider"


def reviewer_actor_login(actor: Actor) -> str:
    return actor.reviewer_login


def is_substantive_activity(event: Mapping[str, Any]) -> bool:
    if event.get("is_merge_from_base_by_non_author"):
        return False
    # Bot events never count as substantive: merge-bot pings, CI status
    # comments, and the like must not refresh the waiting clock. Bot PR
    # authors are remapped to their human delegator in `effective_author`,
    # so a real human's activity still shows up here under that login.
    if event.get("actor_role") == "bot":
        return False
    if event["kind"] == "review-state" and event.get("state") != "COMMENTED":
        return True
    return bool((event.get("body") or "").strip())


def _commit_event(
    commit: Commit,
    author: str,
    approver_logins: set[str],
) -> dict[str, Any]:
    author_login = commit.author.login
    committer_login = commit.committer.login
    if committer_login.lower() == author.lower():
        login = committer_login
        timestamp = commit.committed_at or commit.authored_at
    elif author_login.lower() == author.lower():
        login = author_login
        timestamp = commit.authored_at
    elif committer_login:
        login = committer_login
        timestamp = commit.committed_at
    else:
        login = author_login or commit.author_name
        timestamp = commit.authored_at
    return {
        "kind": "commit",
        "timestamp": timestamp,
        "actor": login,
        "actor_role": role_for(login, author, approver_logins),
        "body": commit.message,
        "state": None,
        "path": None,
        "sha": commit.sha[:7],
        "is_merge_from_base_by_non_author": (
            commit.parent_count >= 2 and login.lower() != author.lower()
        ),
    }


def _issue_comment_event(
    comment: IssueComment,
    author: str,
    approver_logins: set[str],
) -> dict[str, Any] | None:
    if comment.minimized:
        return None
    command_remainder = dashboard_command_body_remainder(comment)
    if command_remainder is not None and not command_remainder:
        return None
    body = (
        command_remainder
        if command_remainder is not None
        else comment.body
    )
    login = reviewer_actor_login(comment.actor)
    timestamp = comment.effective_content_timestamp
    return {
    "source_id": comment.database_id or None,
    "discussion_url": comment.url,
        "kind": "issue-comment",
        "timestamp": timestamp,
        "created_timestamp": comment.created_at or timestamp,
        "actor": login,
        "actor_role": role_for(login, author, approver_logins),
        "body": body,
        "state": None,
        "path": None,
        "sha": None,
        "is_merge_from_base_by_non_author": False,
    }


def _review_comment_event(
    comment: ReviewComment,
    author: str,
    approver_logins: set[str],
) -> dict[str, Any]:
    login = reviewer_actor_login(comment.actor)
    timestamp = comment.updated_at or comment.created_at
    return {
        "source_id": comment.database_id or None,
        "kind": "review-comment",
        "timestamp": timestamp,
        "created_timestamp": comment.created_at or timestamp,
        "actor": login,
        "actor_role": role_for(login, author, approver_logins),
        "body": comment.body,
        "state": None,
        "path": comment.path or None,
        "sha": None,
        "is_merge_from_base_by_non_author": False,
    }


def _review_event(
    review: Review,
    author: str,
    approver_logins: set[str],
) -> dict[str, Any]:
    login = reviewer_actor_login(review.actor)
    state = review.state
    return {
        "source_id": review.database_id or None,
        "discussion_url": review.url,
        "kind": "review-state",
        "timestamp": review.submitted_at,
        "content_timestamp": review.effective_content_timestamp,
        "created_timestamp": review.submitted_at,
        "actor": login,
        "actor_role": role_for(login, author, approver_logins),
        "body": review.body,
        "state": state,
        "path": None,
        "sha": None,
        "is_merge_from_base_by_non_author": False,
    }


def _ordered_events(source: ActivityInput) -> tuple[Mapping[str, Any], ...]:
    pr_source = source.source
    approver_logins = set(source.approver_logins)
    events = [
        _commit_event(commit, source.author, approver_logins)
        for commit in pr_source.commits
    ]
    events.extend(
        event
        for comment in pr_source.issue_comments
        if (
            event := _issue_comment_event(
                comment,
                source.author,
                approver_logins,
            )
        )
        is not None
    )
    events.extend(
        _review_comment_event(comment, source.author, approver_logins)
        for comment in pr_source.review_comments
    )
    events.extend(
        _review_event(review, source.author, approver_logins)
        for review in pr_source.reviews
    )
    events = [event for event in events if event["timestamp"]]
    events.sort(
        key=lambda event: event.get("created_timestamp") or event["timestamp"]
    )
    return tuple(MappingProxyType(event) for event in events)


def _latest_substantive_activity(
    events: tuple[Mapping[str, Any], ...],
    actor_roles: set[str],
) -> datetime | None:
    timestamps = [
        parse_ts(event.get("content_timestamp") or event["timestamp"])
        for event in events
        if event.get("actor_role") in actor_roles
        and is_substantive_activity(event)
    ]
    timestamps = [timestamp for timestamp in timestamps if timestamp is not None]
    return max(timestamps) if timestamps else None


def build_activity_timeline(source: ActivityInput) -> PullRequestActivity:
    events = _ordered_events(source)
    return PullRequestActivity(
        events=events,
        latest_participant_activity_at=_latest_substantive_activity(
            events,
            _PARTICIPANT_ACTOR_ROLES,
        ),
        latest_author_activity_at=_latest_substantive_activity(
            events,
            {"author"},
        ),
        latest_approver_activity_at=_latest_substantive_activity(
            events,
            {"approver"},
        ),
    )
