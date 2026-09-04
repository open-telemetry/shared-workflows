from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from dashboard_contracts import DashboardFacts


DEFAULT_TRUNCATE_CHARS = 1200


def markdown_escape(s: str) -> str:
    return (
        (s or "")
        .replace("\\", "\\\\")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("|", "\\|")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace("@", "&#64;")
        .replace("\n", " ")
        .strip()
    )


def parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def seconds_since(ts: datetime | None) -> int | None:
    if ts is None:
        return None
    return max(0, int((datetime.now(timezone.utc) - ts).total_seconds()))


def activity_age(ts: datetime | None) -> str:
    seconds = seconds_since(ts)
    if seconds is None:
        return "?"
    minutes = seconds // 60
    if minutes < 1:
        return "<1m"
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h"
    return f"{hours // 24}d"


def truncate(s: str, n: int = DEFAULT_TRUNCATE_CHARS) -> str:
    s = (s or "").strip()
    if len(s) <= n:
        return s
    return s[:n] + " ...[truncated]"


def actor_login(obj: dict[str, Any] | None) -> str:
    return ((obj or {}).get("login") or "").strip()


def normalize_author_identity(login: str) -> str:
    normalized = (login or "").strip().casefold()
    if normalized.startswith("app/"):
        return normalized.removeprefix("app/")
    if normalized.endswith("[bot]"):
        return normalized.removesuffix("[bot]")
    return normalized


def is_unattended_author_login(login: str) -> bool:
    normalized = (login or "").strip().casefold()
    return (
        normalized == "opentelemetrybot"
        or normalized.startswith("app/")
        or normalized.endswith("[bot]")
    )


# Every login GitHub has used for the Copilot reviewer, lowercased.
COPILOT_REVIEWER_LOGINS = frozenset({
    "copilot",
    "copilot[bot]",
    "copilot-pull-request-reviewer",
    "copilot-pull-request-reviewer[bot]",
})


def is_copilot_reviewer_login(login: str) -> bool:
    return (login or "").strip().lower() in COPILOT_REVIEWER_LOGINS


def required_checks_settled(facts: DashboardFacts) -> bool:
    if facts.ci_pending_count is None:
        return False
    return not (
        facts.ci_pending_count
        or facts.ci_maintainer_action_required_count
    )


def required_checks_unreported(facts: DashboardFacts) -> bool:
    # A route computed while checks are still running is provisional because a
    # failure becomes visible only after the check completes.
    if facts.required_checks_settled:
        return False
    return (
        facts.ci_pending_count is None
        or facts.ci_pending_count > 0
    )


def format_ts(ts: datetime | None) -> str:
    return ts.isoformat() if ts else ""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
