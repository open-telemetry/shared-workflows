#!/usr/bin/env python3
"""Track and post one-time reminders for pull requests waiting on authors."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import sys
from typing import Any

from github_cli import detect_repo, gh_api, normalize_repo, repo_state_key, run_gh
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


def update_author_nudges(
    repo: str,
    refreshed_pr_numbers: set[int],
    post_due: bool,
    now: datetime,
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
        due, entry = plan_nudge(result, updated.get(key), now)
        if due and post_due and result is not None:
            nudged_at = ensure_nudge(repo, pr_number, result, dashboard_state, now)
            entry = {"nudged_at": nudged_at} if nudged_at else None
        if entry is None:
            updated.pop(key, None)
        else:
            updated[key] = entry
    save_author_nudges(updated)
    return 0


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
    parser.add_argument("--post-due", action="store_true", help="post due nudges")
    args = parser.parse_args()

    try:
        pr_numbers = parse_pr_numbers(args.pr_numbers)
    except ValueError as e:
        parser.error(str(e))
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
            lambda: update_author_nudges(repo, pr_numbers, args.post_due, now),
            state_branch=args.state_branch,
            add_paths=[f"{repo_key}/{author_nudge_state_path().name}"],
        )


if __name__ == "__main__":
    sys.exit(main())