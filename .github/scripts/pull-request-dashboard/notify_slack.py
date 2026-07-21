#!/usr/bin/env python3
"""Send due Slack notifications from accepted PR dashboard state."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from datetime import datetime

from github_cli import list_open_prs
from notifications import next_notifications
from state import (
    load_dashboard_state_cache,
    load_notification_state_file,
    load_notifications,
    results_from_dashboard_state,
    save_notifications,
    union_merge_notifications,
)
from utils import utc_now


def last_notifications(
    saved_notifications: dict[str, Any] | None,
    retry_snapshot_path: Path | None,
) -> dict[str, Any] | None:
    if saved_notifications is None:
        return None
    merged_notifications = saved_notifications
    if retry_snapshot_path and retry_snapshot_path.exists():
        retry_snapshot_state = load_notification_state_file(retry_snapshot_path)
        if retry_snapshot_state is not None:
            merged_notifications = union_merge_notifications(
                merged_notifications,
                retry_snapshot_state["prs"],
            )
    return merged_notifications


def notify_slack_from_state(
    repo: str,
    retry_snapshot_path: Path | None,
    notification_numbers: set[int] | None,
    notification_kinds: set[str] | None,
    now: datetime | None = None,
) -> list[str]:
    dashboard_state = load_dashboard_state_cache()
    if dashboard_state is None:
        print("dashboard state not found; skipping Slack notifications", file=sys.stderr)
        return []

    prs = list_open_prs(repo)
    open_pr_numbers = {p["number"] for p in prs if not p.get("isDraft")}
    results = results_from_dashboard_state(dashboard_state, open_pr_numbers)
    current_prs = {p["number"]: p for p in prs}
    for number, result in results.items():
        result["pr_title"] = current_prs.get(number, {}).get("title") or ""

    saved_notifications = load_notifications()

    updated_notifications, delivery_errors = next_notifications(
        repo,
        results,
        last_notifications(saved_notifications, retry_snapshot_path),
        now or utc_now(),
        notification_numbers=notification_numbers,
        notification_kinds=notification_kinds,
    )
    notifications_changed = updated_notifications != (saved_notifications or {})
    if not notifications_changed and saved_notifications is not None:
        print("notifications unchanged", file=sys.stderr)
        return delivery_errors

    save_notifications(updated_notifications)
    return delivery_errors
