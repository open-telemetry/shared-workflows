#!/usr/bin/env python3
"""Deliver dashboard side effects from accepted state in one CAS transaction."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
from typing import Callable

from author_nudge import deliver_prepared_author_nudges
from copilot_review import deliver_copilot_review_requests
from github_cli import detect_repo, list_all_open_pr_numbers, normalize_repo, repo_state_key
from notify_slack import notify_slack_from_state
from pr_status_comment import update_status_comments_from_state
from state import (
    author_nudge_state_path,
    copilot_review_request_state_path,
    notification_state_path,
    set_state_dir,
)
import state_branch
from utils import utc_now


def runner_temp_path(name: str) -> Path:
    return Path(os.environ.get("RUNNER_TEMP", ".")) / name


def delivery_errors_path() -> Path:
    return runner_temp_path("dashboard-delivery-errors.txt")


def run_delivery_action(
    label: str,
    action: Callable[[], list[str]],
    errors: list[str],
) -> None:
    try:
        errors.extend(f"{label}: {error}" for error in action())
    except Exception as e:
        errors.append(f"{label}: {e}")


def deliver_from_state(
    repo: str,
    pr_number: int | None,
    notification_kind: str,
    non_blocking_check_patterns: list[str],
    author_retry_snapshot_path: Path,
    copilot_retry_snapshot_path: Path,
    notification_retry_snapshot_path: Path,
    errors_file: Path,
) -> int:
    now = utc_now()
    errors: list[str] = []
    run_delivery_action(
        "author nudges",
        lambda: deliver_prepared_author_nudges(
            repo,
            now,
            non_blocking_check_patterns,
            author_retry_snapshot_path,
        ),
        errors,
    )
    run_delivery_action(
        "status comments",
        lambda: update_status_comments_from_state(
            repo,
            pr_number,
            list_all_open_pr_numbers(repo),
        ),
        errors,
    )
    run_delivery_action(
        "Copilot reviews",
        lambda: deliver_copilot_review_requests(repo, now, copilot_retry_snapshot_path),
        errors,
    )
    if notification_kind:
        run_delivery_action(
            "Slack notifications",
            lambda: notify_slack_from_state(
                repo,
                notification_retry_snapshot_path,
                {pr_number} if pr_number is not None else None,
                {notification_kind},
                now,
            ),
            errors,
        )
    if errors:
        errors_file.write_text("\n".join(errors) + "\n", encoding="utf-8")
    else:
        errors_file.unlink(missing_ok=True)
    return 0


def deliver_with_state(
    repo: str,
    state_branch_name: str,
    state_dir: Path,
    pr_number: int | None,
    notification_kind: str,
    non_blocking_check_patterns: list[str],
) -> int:
    repo_key = repo_state_key(repo)
    errors_file = delivery_errors_path()
    errors_file.unlink(missing_ok=True)
    author_retry = runner_temp_path("prior-author-nudge-state.json")
    copilot_retry = runner_temp_path("prior-copilot-review-request-state.json")
    notification_retry = runner_temp_path("prior-notification-state.json")
    status = state_branch.push_state_changes(
        state_dir,
        "Deliver pull request dashboard updates",
        lambda: deliver_from_state(
            repo,
            pr_number,
            notification_kind,
            non_blocking_check_patterns,
            author_retry,
            copilot_retry,
            notification_retry,
            errors_file,
        ),
        state_branch=state_branch_name,
        add_paths=[repo_key],
        retry_snapshots=[
            (author_nudge_state_path(), author_retry),
            (copilot_review_request_state_path(), copilot_retry),
            (notification_state_path(), notification_retry),
        ],
    )
    if status != 0:
        return status
    if not errors_file.exists():
        return 0
    print("Dashboard delivery failed:", file=sys.stderr)
    print(errors_file.read_text(encoding="utf-8").rstrip(), file=sys.stderr)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", help="target repository name")
    parser.add_argument("--state-branch", required=True, help="git branch used for workflow state")
    parser.add_argument("--pr-number", type=int, help="targeted pull request")
    parser.add_argument(
        "--notification-kind",
        choices=("", "initial", "follow-up"),
        default="",
        help="Slack notification kind allowed for this run",
    )
    parser.add_argument(
        "--non-blocking-check-pattern",
        action="append",
        default=[],
        help="glob matching a non-required check; repeat as needed",
    )
    args = parser.parse_args()
    repo = normalize_repo(args.repo) if args.repo else detect_repo()
    with state_branch.temporary_state_dir() as state_dir:
        set_state_dir(state_dir / repo_state_key(repo))
        return deliver_with_state(
            repo,
            args.state_branch,
            state_dir,
            args.pr_number,
            args.notification_kind,
            args.non_blocking_check_pattern,
        )


if __name__ == "__main__":
    sys.exit(main())