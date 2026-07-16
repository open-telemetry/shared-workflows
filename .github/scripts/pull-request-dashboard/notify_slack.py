#!/usr/bin/env python3
"""Send due Slack notifications from accepted PR dashboard state."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from github_cli import detect_repo, list_open_prs, normalize_repo, repo_state_key
from notifications import next_notifications
from state import (
    load_dashboard_state_cache,
    load_notification_state_file,
    load_notifications,
    notification_state_path,
    results_from_dashboard_state,
    save_notifications,
    set_state_dir,
    union_merge_notifications,
)
import state_branch
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

    if not open_pr_numbers.issubset(results):
        print("dashboard backfill incomplete; skipping Slack notifications", file=sys.stderr)
        return []

    saved_notifications = load_notifications()

    updated_notifications, delivery_errors = next_notifications(
        repo,
        results,
        last_notifications(saved_notifications, retry_snapshot_path),
        utc_now(),
        notification_numbers=notification_numbers,
        notification_kinds=notification_kinds,
    )
    notifications_changed = updated_notifications != (saved_notifications or {})
    if not notifications_changed and saved_notifications is not None:
        print("notifications unchanged", file=sys.stderr)
        return delivery_errors

    save_notifications(updated_notifications)
    return delivery_errors


def notification_retry_snapshot_path() -> Path:
    return Path(os.environ.get("RUNNER_TEMP", ".")) / "prior-notification-state.json"


def delivery_errors_path() -> Path:
    return Path(os.environ.get("RUNNER_TEMP", ".")) / "notification-errors.txt"


def notify_slack(
    repo: str,
    retry_snapshot_path: Path,
    delivery_errors_file: Path,
    notification_numbers: set[int] | None,
    notification_kinds: set[str] | None,
) -> int:
    errors = notify_slack_from_state(repo, retry_snapshot_path, notification_numbers, notification_kinds)
    if errors:
        delivery_errors_file.write_text("\n".join(errors) + "\n", encoding="utf-8")
    else:
        delivery_errors_file.unlink(missing_ok=True)
    return 0


def notify_slack_with_state(args: argparse.Namespace, state_dir: Path) -> int:
    repo_key = repo_state_key(args.repo) if args.repo else repo_state_key(detect_repo())
    retry_snapshot_path = notification_retry_snapshot_path()
    delivery_errors_file = delivery_errors_path()
    delivery_errors_file.unlink(missing_ok=True)
    notification_numbers = {args.pr_number} if args.pr_number is not None else None
    notification_kinds = {"initial"} if args.pr_number is not None else {"follow-up"}
    status = state_branch.push_state_changes(
        state_dir,
        "Update dashboard notification state",
        lambda: notify_slack(
            normalize_repo(args.repo) if args.repo else detect_repo(),
            retry_snapshot_path,
            delivery_errors_file,
            notification_numbers,
            notification_kinds,
        ),
        state_branch=args.state_branch,
        add_paths=[f"{repo_key}/notification-state.json"],
        retry_snapshots=[(notification_state_path(), retry_snapshot_path)],
    )
    if status != 0:
        return status
    if not delivery_errors_file.exists():
        return 0
    print("Slack notification delivery failed:", file=sys.stderr)
    print(delivery_errors_file.read_text(encoding="utf-8").rstrip(), file=sys.stderr)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", help="target repository name, e.g. opentelemetry-java-instrumentation")
    parser.add_argument(
        "--state-branch",
        required=True,
        help="git branch used for workflow state",
    )
    parser.add_argument(
        "--pr-number",
        type=int,
        help="send initial notifications for one pull request; omit to send due follow-up notifications only",
    )
    args = parser.parse_args()
    with state_branch.temporary_state_dir() as state_dir:
        set_state_dir(state_dir / (repo_state_key(args.repo) if args.repo else repo_state_key(detect_repo())))
        return notify_slack_with_state(args, state_dir)


if __name__ == "__main__":
    sys.exit(main())
