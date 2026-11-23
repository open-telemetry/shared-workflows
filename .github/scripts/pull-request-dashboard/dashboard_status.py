"""Recognize dashboard-managed status comments."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

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
