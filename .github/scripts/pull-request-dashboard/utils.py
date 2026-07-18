from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal


DEFAULT_TRUNCATE_CHARS = 1200
NEUTRAL_COMMIT_ACTOR_LOGINS = {"copilot", "web-flow"}


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


def classify_commit_actor(
    actor: dict[str, Any] | None,
) -> Literal["human", "bot", "unknown"]:
    actor = actor or {}
    login = actor_login(actor).lower()
    if not login:
        return "unknown"
    actor_type = actor.get("type") or actor.get("__typename")
    if (
        actor_type == "Bot"
        or login.startswith("app/")
        or login.endswith("[bot]")
        or login in NEUTRAL_COMMIT_ACTOR_LOGINS
    ):
        return "bot"
    return "human"


def is_human_commit_actor(actor: dict[str, Any] | None) -> bool:
    return classify_commit_actor(actor) == "human"


def classify_commit(
    commit: dict[str, Any],
) -> Literal["human", "bot", "unknown"]:
    actor_kinds = {
        classify_commit_actor(commit.get(field))
        for field in ("committer", "author")
    }
    if "human" in actor_kinds:
        return "human"
    if "unknown" in actor_kinds:
        return "unknown"
    return "bot"


def commit_delta(
    commits: list[dict[str, Any]],
    previous_sha: str,
    current_sha: str,
) -> list[dict[str, Any]] | None:
    previous_index = next(
        (
            index
            for index, commit in enumerate(commits)
            if str(commit.get("sha") or "") == previous_sha
        ),
        None,
    )
    current_index = next(
        (
            index
            for index, commit in enumerate(commits)
            if str(commit.get("sha") or "") == current_sha
        ),
        None,
    )
    if (
        previous_index is None
        or current_index is None
        or previous_index >= current_index
    ):
        return None
    return commits[previous_index + 1 : current_index + 1]


def format_ts(ts: datetime | None) -> str:
    return ts.isoformat() if ts else ""


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)
