#!/usr/bin/env python3
"""Send due Slack notifications from accepted PR dashboard state."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from datetime import datetime

from notifications import next_notifications
from state import (
    load_dashboard_state_cache,
    load_notification_state_file,
    load_notifications,
    results_from_dashboard_state,
    save_notifications,
    union_merge_notifications,
)


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
    open_prs: list[dict[str, Any]],
    now: datetime,
    target_pr_numbers: set[int] | None = None,
) -> list[str]:
    dashboard_state = load_dashboard_state_cache()
    if dashboard_state is None:
        print("dashboard state not found; skipping Slack notifications", file=sys.stderr)
        return []

    open_pr_numbers = {p["number"] for p in open_prs if not p.get("isDraft")}
    if target_pr_numbers is not None:
        open_pr_numbers &= target_pr_numbers
    results = results_from_dashboard_state(dashboard_state, open_pr_numbers)
    current_prs = {p["number"]: p for p in open_prs}
    for number, result in results.items():
        result["pr_title"] = current_prs.get(number, {}).get("title") or ""

    saved_notifications = load_notifications()
    last_notification_state = last_notifications(saved_notifications, retry_snapshot_path)
    if target_pr_numbers is not None and last_notification_state is not None:
        target_pr_keys = {str(number) for number in target_pr_numbers}
        last_notification_state = {
            str(number): notification
            for number, notification in last_notification_state.items()
            if str(number) in target_pr_keys
        }

    updated_notifications, delivery_errors = next_notifications(
        repo,
        results,
        last_notification_state,
        now,
    )
    if target_pr_numbers is not None:
        merged_notifications = {
            number: notification
            for number, notification in (saved_notifications or {}).items()
            if str(number) not in target_pr_keys
        }
        merged_notifications.update(updated_notifications)
        updated_notifications = merged_notifications
    notifications_changed = updated_notifications != (saved_notifications or {})
    if not notifications_changed and saved_notifications is not None:
        print("notifications unchanged", file=sys.stderr)
        return delivery_errors

    save_notifications(updated_notifications)
    return delivery_errors
