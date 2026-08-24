"""Recognize dashboard-managed status comments."""

from __future__ import annotations

import re
from typing import Any


STATUS_MARKER = "<!-- pull-request-dashboard-status -->"
AUTHOR_NUDGE_EPISODE_MARKER_PREFIX = (
    "<!-- pull-request-dashboard-author-nudge-episode:"
)
_AUTHOR_NUDGE_EPISODE_MARKER_RE = re.compile(
    r"<!-- pull-request-dashboard-author-nudge-episode:([a-f0-9]+) -->"
)
DASHBOARD_APP_SLUG = "opentelemetry-pr-dashboard"


def author_nudge_episode_marker(episode_id: str) -> str:
    return f"{AUTHOR_NUDGE_EPISODE_MARKER_PREFIX}{episode_id} -->"


def is_dashboard_app_comment(comment: dict[str, Any]) -> bool:
    app_slug = (
        (comment.get("performed_via_github_app") or {}).get("slug") or ""
    )
    author_login = (comment.get("user") or {}).get("login") or ""
    return (
        app_slug == DASHBOARD_APP_SLUG
        or author_login == f"{DASHBOARD_APP_SLUG}[bot]"
    )


def status_author_nudge_episode_id(comments: Any) -> str:
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
