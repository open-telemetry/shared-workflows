"""Recognize dashboard-managed status comments."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from utils import parse_ts

if TYPE_CHECKING:
    from pull_request_source import IssueComment


STATUS_MARKER = "<!-- pull-request-dashboard-status -->"
AUTHOR_NUDGE_EPISODE_MARKER_PREFIX = (
    "<!-- pull-request-dashboard-author-nudge-episode:"
)
_AUTHOR_NUDGE_EPISODE_MARKER_RE = re.compile(
    r"<!-- pull-request-dashboard-author-nudge-episode:([a-f0-9]+) -->"
)
STALE_CLOSE_MARKER_PREFIX = "<!-- pull-request-dashboard-stale-close:"
STALE_CLOSE_PENDING_MARKER_PREFIX = (
    "<!-- pull-request-dashboard-stale-close-pending:"
)
REVIEWER_HANDOFF_CLEARED_MARKER_PREFIX = (
    "<!-- pull-request-dashboard-reviewer-handoff-cleared:"
)
_REVIEWER_HANDOFF_CLEARED_MARKER_RE = re.compile(
    r"<!-- pull-request-dashboard-reviewer-handoff-cleared:(\d+):([^\s>]+) -->"
)
DASHBOARD_APP_SLUG = "opentelemetry-pr-dashboard"


def author_nudge_episode_marker(episode_id: str) -> str:
    return f"{AUTHOR_NUDGE_EPISODE_MARKER_PREFIX}{episode_id} -->"


def stale_close_marker(episode_id: str) -> str:
    return f"{STALE_CLOSE_MARKER_PREFIX}{episode_id} -->"


def stale_close_pending_marker(episode_id: str) -> str:
    return f"{STALE_CLOSE_PENDING_MARKER_PREFIX}{episode_id} -->"


def stale_close_pending_comment(
    comments: Sequence[dict[str, Any]] | None,
    episode_id: str,
) -> dict[str, Any] | None:
    """The explanation posted for a close that has not been confirmed yet."""
    marker = stale_close_pending_marker(episode_id)
    for comment in comments or []:
        if is_dashboard_app_comment(comment) and marker in (
            comment.get("body") or ""
        ):
            return comment
    return None


def stale_close_recorded(
    comments: Sequence[dict[str, Any] | IssueComment] | None,
    episode_id: str,
) -> bool:
    """Whether the dashboard confirmed closing the PR for this reminder episode.

    A pending marker, written before the close request, does not count.
    """
    marker = stale_close_marker(episode_id)
    for comment in comments or []:
        if isinstance(comment, dict):
            body = comment.get("body") or ""
            from_dashboard_app = is_dashboard_app_comment(comment)
        else:
            body = comment.body
            from_dashboard_app = comment.is_from_app(DASHBOARD_APP_SLUG)
        if from_dashboard_app and marker in body:
            return True
    return False


def reviewer_handoff_cleared_marker(command_id: int, head_sha: str) -> str:
    return (
        f"{REVIEWER_HANDOFF_CLEARED_MARKER_PREFIX}"
        f"{command_id}:{head_sha} -->"
    )


def is_dashboard_app_comment(comment: dict[str, Any]) -> bool:
    app_slug = (
        (comment.get("performed_via_github_app") or {}).get("slug") or ""
    )
    author_login = (comment.get("user") or {}).get("login") or ""
    return (
        app_slug == DASHBOARD_APP_SLUG
        or author_login == f"{DASHBOARD_APP_SLUG}[bot]"
    )


def status_author_nudge_episode_id(
    comments: Sequence[dict[str, Any] | IssueComment] | None,
) -> str:
    for comment in comments or []:
        if isinstance(comment, dict):
            body = comment.get("body") or ""
            from_dashboard_app = is_dashboard_app_comment(comment)
        else:
            body = comment.body
            from_dashboard_app = comment.is_from_app(DASHBOARD_APP_SLUG)
        match = _AUTHOR_NUDGE_EPISODE_MARKER_RE.search(body)
        if (
            match
            and STATUS_MARKER in body
            and from_dashboard_app
        ):
            return match.group(1)
    return ""


def status_reviewer_handoff_clearance(
    comments: Sequence[dict[str, Any] | IssueComment] | None,
) -> tuple[int, str]:
    best_key = (0, float("-inf"), -1)
    best_head = ""
    for position, comment in enumerate(comments or []):
        if isinstance(comment, dict):
            body = comment.get("body") or ""
            from_dashboard_app = is_dashboard_app_comment(comment)
            updated_at = (
                comment.get("content_updated_at")
                or comment.get("updated_at")
                or comment.get("lastEditedAt")
                or comment.get("updatedAt")
                or comment.get("created_at")
                or comment.get("createdAt")
                or ""
            )
        else:
            body = comment.body
            from_dashboard_app = comment.is_from_app(DASHBOARD_APP_SLUG)
            updated_at = (
                comment.content_updated_at
                or comment.updated_at
                or comment.created_at
            )
        if not from_dashboard_app:
            continue
        for match in _REVIEWER_HANDOFF_CLEARED_MARKER_RE.finditer(body):
            command_id = int(match.group(1))
            parsed_updated_at = parse_ts(str(updated_at))
            timestamp = (
                parsed_updated_at.timestamp()
                if parsed_updated_at is not None
                else float("-inf")
            )
            key = (command_id, timestamp, position)
            if key >= best_key:
                best_key, best_head = key, match.group(2)
    return best_key[0], best_head
