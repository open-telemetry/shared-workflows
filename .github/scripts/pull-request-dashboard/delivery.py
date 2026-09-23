#!/usr/bin/env python3
"""Deliver dashboard side effects from accepted state in one CAS transaction."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import traceback
from typing import Callable

from author_nudge import deliver_prepared_author_nudges
from copilot_review_delivery import deliver_copilot_review_requests
from dashboard_override_delivery import deliver_dashboard_command_replies
from github_cli import (
    GhNotFoundError,
    detect_repo,
    gh_api,
    list_open_prs,
    normalize_repo,
    repo_state_key,
)
from notify_slack import notify_slack_from_state
from pr_status_comment import (
    update_status_comments_from_state,
    update_targeted_status_comment_from_state,
)
from state import (
    AUTHOR_NUDGE_STATE_FILE,
    COPILOT_REVIEW_REQUEST_STATE_FILE,
    DELIVERY_STATE_FILE,
    DELIVERY_VERSIONS_FILE,
    STATUS_COMMENT_ROLLOUT_STATE_FILE,
    author_nudge_state_path,
    claim_delivery_versions,
    copilot_review_request_state_path,
    notification_state_path,
    set_delivery_state_dirs,
    state_dir as current_state_dir,
)
import state_branch
from utils import utc_now


DELIVERY_STATE_VERSION = 1
LEGACY_DELIVERY_FILES = (
    "notification-state.json",
    AUTHOR_NUDGE_STATE_FILE,
    COPILOT_REVIEW_REQUEST_STATE_FILE,
    STATUS_COMMENT_ROLLOUT_STATE_FILE,
    DELIVERY_VERSIONS_FILE,
)


def runner_temp_path(name: str) -> Path:
    return Path(os.environ.get("RUNNER_TEMP", ".")) / name


def run_delivery_action(
    label: str,
    action: Callable[[], list[str]],
    errors: list[str],
) -> None:
    try:
        errors.extend(f"{label}: {error}" for error in action())
    except Exception as e:
        # Keep the traceback in the job log so a failed stage is diagnosable;
        # the short message alone is rarely enough in production.
        print(f"{label} raised an exception:", file=sys.stderr)
        traceback.print_exc()
        errors.append(f"{label}: {e}")


def initialize_delivery_state(accepted_repo_dir: Path) -> None:
    delivery_repo_dir = current_state_dir()
    marker = delivery_repo_dir / DELIVERY_STATE_FILE
    if marker.exists():
        try:
            marker_state = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(f"delivery state marker is unreadable: {error}") from error
        if (
            not isinstance(marker_state, dict)
            or marker_state.get("version") != DELIVERY_STATE_VERSION
            or marker_state.get("migrated_from_accepted_state") is not True
        ):
            raise RuntimeError("delivery state marker has an incompatible shape")
        return
    delivery_repo_dir.mkdir(parents=True, exist_ok=True)
    for name in LEGACY_DELIVERY_FILES:
        source = accepted_repo_dir / name
        destination = delivery_repo_dir / name
        if source.exists() and not destination.exists():
            shutil.copyfile(source, destination)
    marker.write_text(
        json.dumps(
            {
                "version": DELIVERY_STATE_VERSION,
                "migrated_from_accepted_state": True,
            },
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def deliver_from_state(
    repo: str,
    author_retry_snapshot_path: Path,
    copilot_retry_snapshot_path: Path,
    notification_retry_snapshot_path: Path,
    pr_number: int | None = None,
) -> list[str]:
    now = utc_now()
    errors: list[str] = []
    targeted_pr_missing = False
    try:
        if pr_number is None:
            open_prs = list_open_prs(repo)
        else:
            pr = gh_api(f"/repos/{repo}/pulls/{pr_number}")
            open_prs = (
                [{"number": pr_number, "isDraft": bool(pr.get("draft")), "title": pr.get("title") or ""}]
                if pr.get("state") == "open"
                else []
            )
    except GhNotFoundError as e:
        if pr_number is None:
            errors.append(f"open pull requests: {e}")
            open_prs = None
        else:
            print(f"PR #{pr_number} does not exist", file=sys.stderr)
            open_prs = []
            targeted_pr_missing = True
    except Exception as e:
        errors.append(f"open pull requests: {e}")
        open_prs = None
    failed_command_reply_prs: set[int] = set()
    reply_error_count = len(errors)
    run_delivery_action(
        "dashboard command replies",
        lambda: deliver_dashboard_command_replies(
            repo,
            failed_command_reply_prs,
            pr_number,
        ),
        errors,
    )
    if len(errors) > reply_error_count and not failed_command_reply_prs:
        if pr_number is not None:
            failed_command_reply_prs.add(pr_number)
        elif open_prs is not None:
            failed_command_reply_prs.update(pr["number"] for pr in open_prs)
    reply_failure_scope_unknown = (
        len(errors) > reply_error_count
        and not failed_command_reply_prs
        and open_prs is None
    )
    if not reply_failure_scope_unknown:
        run_delivery_action(
            "author nudges",
            lambda: deliver_prepared_author_nudges(
                repo,
                now,
                author_retry_snapshot_path,
                failed_command_reply_prs,
            ),
            errors,
        )
    if (
        pr_number is not None
        and (
            targeted_pr_missing
            or pr_number not in failed_command_reply_prs
        )
    ):
        run_delivery_action(
            "status comments",
            lambda: update_targeted_status_comment_from_state(repo, pr_number),
            errors,
        )
    elif open_prs is not None:
        run_delivery_action(
            "status comments",
            lambda: update_status_comments_from_state(
                repo,
                {pr["number"] for pr in open_prs},
                failed_command_reply_prs,
                open_draft_pr_numbers={
                    pr["number"] for pr in open_prs if pr.get("isDraft")
                },
            ),
            errors,
        )
    run_delivery_action(
        "Copilot reviews",
        lambda: deliver_copilot_review_requests(repo, now, copilot_retry_snapshot_path),
        errors,
    )
    if open_prs is not None:
        run_delivery_action(
            "Slack notifications",
            lambda: (
                notify_slack_from_state(
                    repo,
                    notification_retry_snapshot_path,
                    open_prs,
                    now,
                    {pr_number},
                )
                if pr_number is not None
                else notify_slack_from_state(
                    repo,
                    notification_retry_snapshot_path,
                    open_prs,
                    now,
                )
            ),
            errors,
        )
    return errors


def deliver_with_state(
    repo: str,
    state_branch_name: str,
    state_dir: Path,
    pr_number: int | None = None,
    github_output: Path | None = None,
    *,
    delivery_state_branch_name: str | None = None,
    accepted_repo_dir: Path | None = None,
) -> int:
    repo_key = repo_state_key(repo)
    author_retry = runner_temp_path("prior-author-nudge-state.json")
    copilot_retry = runner_temp_path("prior-copilot-review-request-state.json")
    notification_retry = runner_temp_path("prior-notification-state.json")
    errors: list[str] = []
    active_versions = False

    def deliver() -> int:
        nonlocal active_versions
        if accepted_repo_dir is not None:
            initialize_delivery_state(accepted_repo_dir)
        active_versions = claim_delivery_versions()
        if not active_versions:
            errors.clear()
            print("newer dashboard delivery versions are active; skipping", file=sys.stderr)
            return 0
        errors[:] = deliver_from_state(
            repo,
            author_retry,
            copilot_retry,
            notification_retry,
            pr_number,
        )
        return 0

    status = state_branch.push_state_changes(
        state_dir,
        "Deliver pull request dashboard updates",
        deliver,
        state_branch=(
            delivery_state_branch_name
            or state_branch.delivery_state_branch(state_branch_name)
        ),
        add_paths=[repo_key],
        retry_snapshots=[
            (author_nudge_state_path(), author_retry),
            (copilot_review_request_state_path(), copilot_retry),
            (notification_state_path(), notification_retry),
        ],
    )
    if status != 0:
        return status
    if github_output is not None:
        with github_output.open("a", encoding="utf-8") as output:
            output.write(f"active={'true' if active_versions else 'false'}\n")
    if not errors:
        return 0
    print("Dashboard delivery failed:", file=sys.stderr)
    print("\n".join(errors), file=sys.stderr)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", help="target repository name")
    parser.add_argument("--pr-number", type=int, help="target pull request number")
    parser.add_argument("--state-branch", required=True, help="git branch used for workflow state")
    parser.add_argument(
        "--delivery-state-branch",
        required=True,
        help="git branch used for publisher receipts and rollout state",
    )
    parser.add_argument("--github-output", type=Path, help="append the active versions result")
    args = parser.parse_args()
    repo = normalize_repo(args.repo) if args.repo else detect_repo()
    with state_branch.accepted_state_dir(
        args.state_branch,
        required=True,
    ) as accepted_checkout:
        if accepted_checkout is None:
            raise RuntimeError(f"required state branch not found: {args.state_branch}")
        with state_branch.temporary_state_dir() as state_dir:
            accepted_repo_dir = accepted_checkout / repo_state_key(repo)
            set_delivery_state_dirs(
                state_dir / repo_state_key(repo),
                accepted_repo_dir,
            )
            return deliver_with_state(
                repo,
                args.state_branch,
                state_dir,
                pr_number=args.pr_number,
                github_output=args.github_output,
                delivery_state_branch_name=args.delivery_state_branch,
                accepted_repo_dir=accepted_repo_dir,
            )


if __name__ == "__main__":
    sys.exit(main())