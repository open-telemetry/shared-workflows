from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from dashboard_override import dashboard_command_body_remainder
from utils import actor_login, is_copilot_reviewer_login, parse_ts


_PARTICIPANT_ACTOR_ROLES = {"author", "approver", "outsider"}


@dataclass(frozen=True)
class ActivityInput:
    raw: dict[str, Any]
    author: str
    approver_logins: frozenset[str]


@dataclass(frozen=True)
class PullRequestActivity:
    events: tuple[dict[str, Any], ...]
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


def reviewer_actor_login(obj: dict[str, Any] | None) -> str:
    login = actor_login(obj)
    if is_copilot_reviewer_login(login):
        return "copilot-pull-request-reviewer[bot]"
    return login


def is_substantive_activity(event: dict[str, Any]) -> bool:
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


def _is_merge_commit(commit: dict[str, Any]) -> bool:
    return len(commit.get("parents") or []) >= 2


def _commit_event(
    commit: dict[str, Any],
    author: str,
    approver_logins: set[str],
) -> dict[str, Any]:
    commit_obj = commit.get("commit") or {}
    commit_author = commit_obj.get("author") or {}
    commit_committer = commit_obj.get("committer") or {}
    author_login = actor_login(commit.get("author") or {})
    committer_login = actor_login(commit.get("committer") or {})
    if committer_login.lower() == author.lower():
        login = committer_login
        timestamp = commit_committer.get("date") or commit_author.get("date") or ""
    elif author_login.lower() == author.lower():
        login = author_login
        timestamp = commit_author.get("date") or ""
    elif committer_login:
        login = committer_login
        timestamp = commit_committer.get("date") or ""
    else:
        login = author_login or commit_author.get("name") or ""
        timestamp = commit_author.get("date") or ""
    sha = commit.get("sha") or ""
    return {
        "kind": "commit",
        "timestamp": timestamp,
        "actor": login,
        "actor_role": role_for(login, author, approver_logins),
        "body": commit_obj.get("message") or "",
        "state": None,
        "path": None,
        "sha": sha[:7],
        "is_merge_from_base_by_non_author": (
            _is_merge_commit(commit) and login.lower() != author.lower()
        ),
    }


def _issue_comment_event(
    comment: dict[str, Any],
    author: str,
    approver_logins: set[str],
) -> dict[str, Any] | None:
    if comment.get("minimized"):
        return None
    command_remainder = dashboard_command_body_remainder(comment)
    if command_remainder is not None and not command_remainder:
        return None
    body = (
        command_remainder
        if command_remainder is not None
        else (comment.get("body") or "")
    )
    login = reviewer_actor_login(comment.get("user") or {})
    timestamp = (
        comment.get("content_updated_at")
        or comment.get("created_at")
        or comment.get("updated_at")
        or ""
    )
    return {
        "source_id": comment.get("id"),
        "discussion_url": comment.get("html_url") or "",
        "kind": "issue-comment",
        "timestamp": timestamp,
        "created_timestamp": comment.get("created_at") or timestamp,
        "actor": login,
        "actor_role": role_for(login, author, approver_logins),
        "body": body,
        "state": None,
        "path": None,
        "sha": None,
        "is_merge_from_base_by_non_author": False,
    }


def _review_comment_event(
    comment: dict[str, Any],
    author: str,
    approver_logins: set[str],
) -> dict[str, Any]:
    login = reviewer_actor_login(comment.get("user") or {})
    timestamp = comment.get("updated_at") or comment.get("created_at") or ""
    return {
        "source_id": comment.get("id"),
        "kind": "review-comment",
        "timestamp": timestamp,
        "created_timestamp": comment.get("created_at") or timestamp,
        "actor": login,
        "actor_role": role_for(login, author, approver_logins),
        "body": comment.get("body") or "",
        "state": None,
        "path": comment.get("path"),
        "sha": None,
        "is_merge_from_base_by_non_author": False,
    }


def _review_event(
    review: dict[str, Any],
    author: str,
    approver_logins: set[str],
) -> dict[str, Any]:
    login = reviewer_actor_login(review.get("user") or {})
    state = review.get("state") or ""
    return {
        "source_id": review.get("id"),
        "discussion_url": review.get("url") or "",
        "kind": "review-state",
        "timestamp": review.get("submitted_at") or "",
        "created_timestamp": review.get("submitted_at") or "",
        "actor": login,
        "actor_role": role_for(login, author, approver_logins),
        "body": review.get("body") or "",
        "state": state,
        "path": None,
        "sha": None,
        "is_merge_from_base_by_non_author": False,
    }


def _ordered_events(source: ActivityInput) -> tuple[dict[str, Any], ...]:
    raw = source.raw
    approver_logins = set(source.approver_logins)
    events = [
        _commit_event(commit, source.author, approver_logins)
        for commit in raw["commits"]
    ]
    events.extend(
        event
        for comment in raw["issue_comments"]
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
        for comment in raw["review_comments"]
    )
    events.extend(
        _review_event(review, source.author, approver_logins)
        for review in raw["reviews"]
    )
    events = [event for event in events if event["timestamp"]]
    events.sort(
        key=lambda event: event.get("created_timestamp") or event["timestamp"]
    )
    return tuple(events)


def _latest_substantive_activity(
    events: tuple[dict[str, Any], ...],
    actor_roles: set[str],
) -> datetime | None:
    timestamps = [
        parse_ts(event["timestamp"])
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
