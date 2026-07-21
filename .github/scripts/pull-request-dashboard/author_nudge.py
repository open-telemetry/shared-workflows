#!/usr/bin/env python3
"""Track and post one-time reminders for pull requests waiting on authors."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path
import sys
from typing import Any

from github_cli import detect_repo, gh_api, load_reviewer_set, normalize_repo, repo_state_key, run_gh
from pr_status_comment import (
    DASHBOARD_APP_SLUG,
    managed_status_comments,
    publish_pr_status,
)
from state import (
    author_nudge_state_path,
    load_author_nudges,
    load_dashboard_state_cache,
    save_author_nudges,
    set_state_dir,
    update_dashboard_state_for_pr,
)
import state_branch
from utils import format_ts, parse_ts, utc_now


NUDGE_AFTER = timedelta(weeks=1)
NUDGE_MARKER = "<!-- pull-request-dashboard-author-nudge -->"


def plan_nudge(
    result: dict[str, Any] | None,
    previous: dict[str, Any] | None,
    now: datetime,
) -> tuple[bool, dict[str, Any] | None]:
    entry = dict(previous or {})
    nudged_at = entry.get("nudged_at") or ""
    if result and (
        result.get("failed")
        or result.get("route") in ("transient-failure", "unknown")
    ):
        return False, entry or None
    if not result or result.get("route") != "author":
        # Reset an unnudged route clock, but retain a posted nudge for the
        # lifetime of the PR so leaving and returning cannot trigger another.
        return False, {"nudged_at": nudged_at} if nudged_at else None
    if nudged_at:
        return False, {"nudged_at": nudged_at}

    waiting_since = parse_ts(entry.get("waiting_since") or "")
    if waiting_since is None:
        return False, {
            "waiting_since": format_ts(now),
            "nudged_at": "",
        }
    return now - waiting_since >= NUDGE_AFTER, entry


def existing_nudge_comment(repo: str, pr_number: int) -> dict[str, Any] | None:
    comments = gh_api(
        f"/repos/{repo}/issues/{pr_number}/comments?per_page=100",
        paginate=True,
    )
    return next(
        (
            comment
            for comment in comments or []
            if (comment.get("performed_via_github_app") or {}).get("slug")
            == DASHBOARD_APP_SLUG
            and NUDGE_MARKER in (comment.get("body") or "")
        ),
        None,
    )


def render_nudge(author: str, status_url: str) -> str:
    return "\n".join([
        NUDGE_MARKER,
        f"@{author}, this pull request has been waiting on your follow-up for one week.",
        "",
        f"See the [dashboard status comment]({status_url}) for the remaining items.",
        "",
    ])


def ensure_nudge(
    repo: str,
    pr_number: int,
    result: dict[str, Any],
    dashboard_state: dict[str, Any],
    now: datetime,
) -> str | None:
    existing = existing_nudge_comment(repo, pr_number)
    if existing:
        return existing.get("created_at") or format_ts(now)

    pr = gh_api(f"/repos/{repo}/pulls/{pr_number}") or {}
    if pr.get("state") != "open" or pr.get("draft"):
        return None

    publish_pr_status(repo, pr_number, dashboard_state)
    status_comments = managed_status_comments(repo, pr_number)
    if not status_comments or not status_comments[0].get("html_url"):
        raise RuntimeError(f"dashboard status comment not found for PR #{pr_number}")
    author = str(((result.get("facts") or {}).get("author") or "")).strip()
    author = author or str((pr.get("user") or {}).get("login") or "").strip()
    if not author:
        raise RuntimeError(f"author not found for PR #{pr_number}")
    run_gh([
        "gh", "api", "--method", "POST",
        f"repos/{repo}/issues/{pr_number}/comments",
        "-f", f"body={render_nudge(author, status_comments[0]['html_url'])}",
    ])
    return format_ts(now)


def record_author_nudge_observation(
    pr_number: int,
    result: dict[str, Any] | None,
    now: datetime,
) -> None:
    updated = dict(load_author_nudges())
    key = str(pr_number)
    _due, entry = plan_nudge(result, updated.get(key), now)
    if entry is None:
        updated.pop(key, None)
    else:
        updated[key] = entry
    save_author_nudges(updated)


def refresh_author_nudge_result(
    repo: str,
    pr_number: int,
    dashboard_state: dict[str, Any],
    approver_teams: list[str],
    required_approvals: int,
    non_blocking_check_patterns: list[str],
    require_clean_copilot_review: bool,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    from dashboard import DEFAULT_MODEL, build_dashboard_update_for_pr

    owner, repo_name = repo.split("/", 1)
    reviewers = load_reviewer_set(owner, approver_teams)
    calculation = build_dashboard_update_for_pr(
        repo,
        owner,
        repo_name,
        {pr_number},
        reviewers,
        pr_number,
        DEFAULT_MODEL,
        required_approvals,
        non_blocking_check_patterns,
        dashboard_state,
        require_clean_copilot_review,
    )
    result = calculation.trigger_pr_result
    return result, update_dashboard_state_for_pr(dashboard_state, pr_number, result)


def prepare_author_nudges(
    repo: str,
    refreshed_pr_numbers: set[int],
    now: datetime,
    approver_teams: list[str],
    required_approvals: int,
    non_blocking_check_patterns: list[str],
    require_clean_copilot_review: bool,
) -> int:
    dashboard_state = load_dashboard_state_cache()
    if dashboard_state is None:
        print("dashboard state not found; skipping author nudges", file=sys.stderr)
        return 0
    updated = dict(load_author_nudges())
    dashboard_prs = dashboard_state.get("prs") or {}
    for pr_number in sorted(refreshed_pr_numbers):
        key = str(pr_number)
        result = dashboard_prs.get(key)
        entry = updated.get(key) or {}
        waiting_since = parse_ts(entry.get("waiting_since") or "")
        if (
            result is None
            or result.get("route") != "author"
            or entry.get("nudged_at")
            or waiting_since is None
            or now - waiting_since < NUDGE_AFTER
        ):
            continue
        fresh_result, _fresh_dashboard_state = refresh_author_nudge_result(
            repo,
            pr_number,
            dashboard_state,
            approver_teams,
            required_approvals,
            non_blocking_check_patterns,
            require_clean_copilot_review,
        )
        _due, fresh_entry = plan_nudge(fresh_result, updated.get(key), now)
        if fresh_entry is None:
            updated.pop(key, None)
        else:
            updated[key] = fresh_entry
        if (
            fresh_result is None
            or fresh_result.get("failed")
            or fresh_result.get("route") != "author"
        ):
            continue
        fresh_facts = fresh_result.get("facts") or {}
        updated[key] = {
            **(fresh_entry or {}),
            "pending_at": format_ts(now),
            "head_sha": fresh_facts.get("head_sha") or "",
        }
    save_author_nudges(updated)
    return 0


def deliver_prepared_author_nudges(
    repo: str,
    now: datetime,
    retry_snapshot_path: Path | None = None,
) -> list[str]:
    dashboard_state = load_dashboard_state_cache()
    if dashboard_state is None:
        print("dashboard state not found; skipping author nudges", file=sys.stderr)
        return []
    updated = dict(load_author_nudges(retry_snapshot_path))
    dashboard_prs = dashboard_state.get("prs") or {}
    errors: list[str] = []
    for key, entry in sorted(updated.items(), key=lambda item: int(item[0])):
        if not (entry or {}).get("pending_at"):
            continue
        pr_number = int(key)
        result = dashboard_prs.get(key)
        if not result or result.get("route") != "author":
            _due, reset_entry = plan_nudge(result, entry, now)
            if reset_entry is None:
                updated.pop(key, None)
            else:
                updated[key] = reset_entry
            continue
        try:
            pr = gh_api(f"/repos/{repo}/pulls/{pr_number}") or {}
            expected_head = entry.get("head_sha") or ""
            current_head = ((pr.get("head") or {}).get("sha") or "")
            if (
                pr.get("state") != "open"
                or pr.get("draft")
                or (expected_head and current_head != expected_head)
            ):
                updated[key] = {
                    name: value
                    for name, value in entry.items()
                    if name not in ("pending_at", "head_sha")
                }
                continue
            nudged_at = ensure_nudge(repo, pr_number, result, dashboard_state, now)
        except Exception as e:
            errors.append(f"PR #{pr_number}: {e}")
            continue
        if nudged_at:
            updated[key] = {"nudged_at": nudged_at}
    save_author_nudges(updated)
    return errors


def parse_pr_numbers(value: str) -> set[int]:
    if not value:
        return set()
    numbers = {int(part) for part in value.split(",")}
    if any(number <= 0 for number in numbers):
        raise ValueError("PR numbers must be positive")
    return numbers


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", help="target repository name")
    parser.add_argument("--state-branch", required=True, help="git branch used for workflow state")
    parser.add_argument("--pr-numbers", required=True, help="comma-separated refreshed PR numbers")
    parser.add_argument(
        "--approver-team",
        action="append",
        required=True,
        help="approver team slug for the target repository; repeat for multiple teams",
    )
    parser.add_argument(
        "--required-approvals",
        type=int,
        default=1,
        help="minimum non-bot approvals needed before a PR can route to maintainers",
    )
    parser.add_argument(
        "--non-blocking-check-pattern",
        action="append",
        default=[],
        help="glob matching a non-required check to mention when it fails; repeat as needed",
    )
    parser.add_argument(
        "--require-clean-copilot-review",
        action="store_true",
        help="apply the clean Copilot review gate during the fresh route check",
    )
    args = parser.parse_args()

    try:
        pr_numbers = parse_pr_numbers(args.pr_numbers)
    except ValueError as e:
        parser.error(str(e))
    if args.required_approvals < 1:
        parser.error("--required-approvals must be at least 1")
    if not pr_numbers:
        return 0

    repo = normalize_repo(args.repo) if args.repo else detect_repo()
    repo_key = repo_state_key(repo)
    now = utc_now()
    with state_branch.temporary_state_dir() as state_dir:
        set_state_dir(state_dir / repo_key)
        return state_branch.push_state_changes(
            state_dir,
            "Update author nudge state",
            lambda: prepare_author_nudges(
                repo,
                pr_numbers,
                now,
                args.approver_team,
                args.required_approvals,
                args.non_blocking_check_pattern,
                args.require_clean_copilot_review,
            ),
            state_branch=args.state_branch,
            add_paths=[f"{repo_key}/{author_nudge_state_path().name}"],
        )


if __name__ == "__main__":
    sys.exit(main())