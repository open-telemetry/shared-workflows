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
REVIEWER_HANDOFF_CLEARED_MARKER_PREFIX = (
    "<!-- pull-request-dashboard-reviewer-handoff-cleared:"
)
_REVIEWER_HANDOFF_CLEARED_MARKER_RE = re.compile(
    r"<!-- pull-request-dashboard-reviewer-handoff-cleared:(\d+):([^\s>]+) -->"
)
DASHBOARD_APP_SLUG = "opentelemetry-pr-dashboard"


def author_nudge_episode_marker(episode_id: str) -> str:
    return f"{AUTHOR_NUDGE_EPISODE_MARKER_PREFIX}{episode_id} -->"


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
    comments: list[dict[str, Any]] | None,
) -> tuple[int, str]:
    best_id = 0
    best_head = ""
    for comment in comments or []:
        body = comment.get("body") or ""
        if STATUS_MARKER not in body or not is_dashboard_app_comment(comment):
            continue
        for match in _REVIEWER_HANDOFF_CLEARED_MARKER_RE.finditer(body):
            command_id = int(match.group(1))
            if command_id >= best_id:
                best_id, best_head = command_id, match.group(2)
    return best_id, best_head
