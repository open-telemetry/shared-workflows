#!/usr/bin/env python3
"""Deliver dashboard side effects from accepted state in one CAS transaction."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import traceback
from typing import Callable

from author_nudge import deliver_prepared_author_nudges
from copilot_review import deliver_copilot_review_requests
from dashboard_override import deliver_dashboard_command_replies
from github_cli import detect_repo, gh_api, list_open_prs, normalize_repo, repo_state_key
from notify_slack import notify_slack_from_state
from route_presentation import unreported_gate_phrase
from pr_status_comment import (
    update_status_comments_from_state,
    update_targeted_status_comment_from_state,
)
from state import (
    author_nudge_state_path,
    claim_delivery_versions,
    copilot_review_request_state_path,
    load_dashboard_state_cache,
    notification_state_path,
    set_state_dir,
)
import state_branch
from utils import utc_now


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


def report_stalled_gates(open_pr_numbers: set[int]) -> list[str]:
    # The gates are the one place where the dashboard waits on someone else.
    # When a wait outlasts its limit the dashboard has already routed the pull
    # request, so nothing on the pull request itself is broken and nobody would
    # notice. Reporting it here is what turns a silent stall into a failure a
    # person sees, whatever the gate was and whatever went missing.
    state = load_dashboard_state_cache()
    if state is None:
        return []
    stalled: list[str] = []
    for key, result in (state.get("prs") or {}).items():
        facts = (result or {}).get("facts") or {}
        # GitHub may not start checks or reviews for an unmergeable head, so a
        # conflict is not evidence that dashboard delivery stalled.
        if facts.get("conflicts") == "yes":
            continue
        if not facts.get("route_hold_expired"):
            continue
        try:
            number = int(key)
        except ValueError:
            continue
        if number not in open_pr_numbers:
            continue
        gates = unreported_gate_phrase(facts)
        if not gates:
            continue
        stalled.append(f"PR #{number}: {gates} never reported on head {facts.get('head_sha') or 'unknown'}")
    return sorted(stalled)


def deliver_from_state(
    repo: str,
    author_retry_snapshot_path: Path,
    copilot_retry_snapshot_path: Path,
    notification_retry_snapshot_path: Path,
    pr_number: int | None = None,
) -> list[str]:
    now = utc_now()
    errors: list[str] = []
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
    except Exception as e:
        errors.append(f"open pull requests: {e}")
        open_prs = None
    run_delivery_action(
        "dashboard command replies",
        lambda: deliver_dashboard_command_replies(repo),
        errors,
    )
    run_delivery_action(
        "author nudges",
        lambda: deliver_prepared_author_nudges(
            repo,
            now,
            author_retry_snapshot_path,
        ),
        errors,
    )
    if pr_number is not None:
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
    if pr_number is None and open_prs is not None:
        # Last, so a stalled gate is reported but never keeps the real work
        # from being delivered. Only whole-repository passes report it: a
        # single pull request refresh has no business failing over another
        # pull request's stall.
        run_delivery_action(
            "stalled gates",
            lambda: report_stalled_gates({pr["number"] for pr in open_prs}),
            errors,
        )
    return errors


def deliver_with_state(
    repo: str,
    state_branch_name: str,
    state_dir: Path,
    pr_number: int | None = None,
    github_output: Path | None = None,
) -> int:
    repo_key = repo_state_key(repo)
    author_retry = runner_temp_path("prior-author-nudge-state.json")
    copilot_retry = runner_temp_path("prior-copilot-review-request-state.json")
    notification_retry = runner_temp_path("prior-notification-state.json")
    errors: list[str] = []
    active_versions = False

    def deliver() -> int:
        nonlocal active_versions
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
    parser.add_argument("--github-output", type=Path, help="append the active versions result")
    args = parser.parse_args()
    repo = normalize_repo(args.repo) if args.repo else detect_repo()
    with state_branch.temporary_state_dir() as state_dir:
        set_state_dir(state_dir / repo_state_key(repo))
        return deliver_with_state(
            repo,
            args.state_branch,
            state_dir,
            pr_number=args.pr_number,
            github_output=args.github_output,
        )


if __name__ == "__main__":
    sys.exit(main())