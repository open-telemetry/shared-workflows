"""Lifecycle policy for author follow-up nudges and stale escalation."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from utils import format_ts, parse_ts


FAST_NUDGE_AFTER = timedelta(days=1)
NUDGE_AFTER = timedelta(weeks=1)
STAGE_INTERVAL = timedelta(weeks=1)


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


def qualifying_author_activity(facts: dict[str, Any]) -> datetime | None:
    author_activity = parse_ts(facts.get("last_author_activity_at") or "")
    if author_activity is None:
        return None
    other_activity = [
        parse_ts(facts.get("last_approver_activity_at") or ""),
        parse_ts(facts.get("last_external_activity_at") or ""),
    ]
    if any(activity is not None and activity >= author_activity for activity in other_activity):
        return None
    return author_activity


def latest_other_human_activity(facts: dict[str, Any]) -> datetime | None:
    timestamps = [
        parse_ts(facts.get("last_approver_activity_at") or ""),
        parse_ts(facts.get("last_external_activity_at") or ""),
    ]
    return max((timestamp for timestamp in timestamps if timestamp is not None), default=None)


def plan_follow_up(
    result: dict[str, Any] | None,
    previous: dict[str, Any] | None,
    now: datetime,
    stale_enabled: bool,
) -> tuple[str | None, dict[str, Any] | None]:
    entry = dict(previous or {})
    if result and (
        result.get("failed")
        or result.get("route") in ("transient-failure", "unknown")
    ):
        return None, entry or None
    if not result or result.get("route") != "author":
        if entry.get("stale_applied_at"):
            entry["stale_applied_at"] = ""
            return "remove-stale", entry
        return None, None

    facts = result.get("facts") or {}
    if not entry:
        waiting_since_value = format_ts(now)
        entry = {
            "cycle_id": waiting_since_value,
            "waiting_on_author_since": waiting_since_value,
            "pending_handoff_since": "",
            "handoff_nudged_at": "",
            "general_nudged_at": "",
            "stale_applied_at": "",
            "stale_reset_at": "",
        }
    waiting_since = parse_ts(entry.get("waiting_on_author_since") or "")
    if waiting_since is None:
        return None, entry

    author_activity = qualifying_author_activity(facts)
    other_activity = latest_other_human_activity(facts)
    pending_handoff_since = parse_ts(entry.get("pending_handoff_since") or "")
    if (
        pending_handoff_since is not None
        and other_activity is not None
        and other_activity >= pending_handoff_since
    ):
        entry["pending_handoff_since"] = ""
        pending_handoff_since = None
    if (
        not entry.get("handoff_nudged_at")
        and author_activity is not None
        and author_activity >= waiting_since
        and pending_handoff_since is None
    ):
        entry["pending_handoff_since"] = format_ts(author_activity)
        pending_handoff_since = author_activity

    if (
        not entry.get("handoff_nudged_at")
        and pending_handoff_since is not None
        and now - pending_handoff_since >= FAST_NUDGE_AFTER
    ):
        entry["handoff_nudged_at"] = format_ts(now)
        entry["pending_handoff_since"] = ""
        return "handoff-nudge", entry

    handoff_nudged_at = parse_ts(entry.get("handoff_nudged_at") or "")
    general_baseline = handoff_nudged_at or waiting_since
    if (
        not entry.get("general_nudged_at")
        and pending_handoff_since is None
        and now - general_baseline >= NUDGE_AFTER
    ):
        entry["general_nudged_at"] = format_ts(now)
        return "general-nudge", entry

    if not stale_enabled:
        if entry.get("stale_applied_at"):
            entry["stale_applied_at"] = ""
            return "remove-stale", entry
        return None, entry

    stale_applied_at = parse_ts(entry.get("stale_applied_at") or "")
    if stale_applied_at is not None:
        human_activity = latest_human_activity(facts)
        if human_activity is not None and human_activity > stale_applied_at:
            entry["stale_applied_at"] = ""
            entry["stale_reset_at"] = format_ts(human_activity)
            return "remove-stale", entry
        if entry.get("stale_label_owned") and now - stale_applied_at >= STAGE_INTERVAL:
            return "close", entry
        return None, entry

    general_nudged_at = parse_ts(entry.get("general_nudged_at") or "")
    if general_nudged_at is None:
        return None, entry
    stale_baseline = general_nudged_at
    human_activity = latest_human_activity(facts)
    if human_activity is not None and human_activity > stale_baseline:
        stale_baseline = human_activity
    stale_reset_at = parse_ts(entry.get("stale_reset_at") or "")
    if stale_reset_at is not None and stale_reset_at > stale_baseline:
        stale_baseline = stale_reset_at
    if now - stale_baseline >= STAGE_INTERVAL:
        entry["stale_applied_at"] = format_ts(now)
        return "stale", entry
    return None, entry