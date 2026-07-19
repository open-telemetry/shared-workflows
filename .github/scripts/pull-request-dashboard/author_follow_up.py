"""Lifecycle policy for author follow-up nudges."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from utils import format_ts, parse_ts


NUDGE_AFTER = timedelta(weeks=1)


def latest_human_activity(facts: dict[str, Any]) -> datetime | None:
    timestamps = [
        parse_ts(facts.get(field) or "")
        for field in (
            "last_author_activity_at",
            "last_approver_activity_at",
            "last_external_activity_at",
            "human_head_observed_at",
        )
    ]
    return max((timestamp for timestamp in timestamps if timestamp is not None), default=None)


def plan_follow_up(
    result: dict[str, Any] | None,
    previous: dict[str, Any] | None,
    now: datetime,
) -> tuple[str | None, dict[str, Any] | None]:
    entry = dict(previous or {})
    already_nudged = bool(entry.get("general_nudged_at"))
    if result and (
        result.get("failed")
        or result.get("route") in ("transient-failure", "unknown")
    ):
        return None, entry or None
    if not result or result.get("route") != "author":
        # The PR is no longer waiting on the author. Preserve the one-time nudge
        # marker so the same PR is never nudged twice, but drop the waiting clock
        # so a later return to the author route does not schedule another nudge.
        if already_nudged:
            return None, {"general_nudged_at": entry["general_nudged_at"]}
        return None, None
    if already_nudged:
        return None, {"general_nudged_at": entry["general_nudged_at"]}

    waiting_since = parse_ts(entry.get("waiting_on_author_since") or "")
    if waiting_since is None:
        waiting_since_value = format_ts(now)
        entry = {
            "waiting_on_author_since": waiting_since_value,
            "general_nudged_at": "",
        }
        waiting_since = now
    if now - waiting_since >= NUDGE_AFTER:
        entry["general_nudged_at"] = format_ts(now)
        return "general-nudge", entry
    return None, entry
