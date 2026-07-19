#!/usr/bin/env python3
"""Post author follow-up nudges."""

from __future__ import annotations

import argparse
from datetime import datetime
import os
from pathlib import Path
import sys
from typing import Any

from author_follow_up import latest_human_activity, plan_follow_up
from github_cli import (
    detect_repo,
    fetch_pr_review_data,
    fetch_review_threads,
    gh_api,
    gh_pr_checks,
    gh_pr_view,
    gh_required_check_contexts,
    include_missing_required_checks,
    list_open_prs,
    normalize_repo,
    repo_state_key,
    run_gh,
)
from pr_status_comment import DASHBOARD_APP_SLUG, ensure_status_comment
from state import (
    author_follow_up_state_path,
    load_author_follow_up_state_file,
    load_author_follow_ups,
    load_dashboard_state_cache,
    results_from_dashboard_state,
    save_author_follow_ups,
    set_state_dir,
)
import state_branch
from utils import (
    classify_commit,
    commit_delta,
    format_ts,
    parse_ts,
    utc_now,
)


GENERAL_NUDGE_MARKER_PREFIX = "<!-- pull-request-dashboard-author-general-nudge:"


def lifecycle_marker(prefix: str, cycle_id: str) -> str:
    return f"{prefix}{cycle_id} -->"


def lifecycle_comments(repo: str, pr_number: int) -> list[dict[str, Any]]:
    return [
        comment
        for comment in gh_api(
            f"/repos/{repo}/issues/{pr_number}/comments?per_page=100",
            paginate=True,
        ) or []
        if (comment.get("performed_via_github_app") or {}).get("slug") == DASHBOARD_APP_SLUG
    ]


def matching_comment(comments: list[dict[str, Any]], marker: str) -> dict[str, Any] | None:
    return next(
        (comment for comment in comments if marker in (comment.get("body") or "")),
        None,
    )


def post_comment(repo: str, pr_number: int, body: str) -> None:
    run_gh([
        "gh", "api", "--method", "POST",
        f"repos/{repo}/issues/{pr_number}/comments",
        "-f", f"body={body}",
    ])


def render_nudge(
    result: dict[str, Any],
    status_url: str,
    cycle_id: str,
) -> str:
    facts = result.get("facts") or {}
    author = (facts.get("author") or "").strip()
    mention = f"@{author}, " if author else ""
    message = f"{mention}this pull request has been waiting on your follow-up for one week."
    return "\n".join([
        lifecycle_marker(GENERAL_NUDGE_MARKER_PREFIX, cycle_id),
        message,
        "",
        f"See the [dashboard status comment]({status_url}) for the remaining items.",
        "",
    ])


def issue_details(repo: str, pr_number: int) -> dict[str, Any]:
    return gh_api(f"/repos/{repo}/issues/{pr_number}") or {}


def is_human_actor(actor: dict[str, Any] | None) -> bool:
    actor = actor or {}
    login = str(actor.get("login") or "").lower()
    actor_type = actor.get("type") or actor.get("__typename")
    return bool(
        login
        and actor_type != "Bot"
        and not login.startswith("app/")
        and not login.endswith("[bot]")
    )


def current_human_activity(
    repo: str,
    pr_number: int,
    result: dict[str, Any],
    now: datetime,
) -> datetime | None:
    owner, repo_name = repo.split("/", 1)
    issue_comments = gh_api(
        f"/repos/{repo}/issues/{pr_number}/comments?per_page=100",
        paginate=True,
    )
    review_comments = gh_api(
        f"/repos/{repo}/pulls/{pr_number}/comments?per_page=100",
        paginate=True,
    )
    commits = gh_api(
        f"/repos/{repo}/pulls/{pr_number}/commits?per_page=100",
        paginate=True,
    )
    current_pr = gh_pr_view(repo, pr_number)
    review_data = fetch_pr_review_data(owner, repo_name, pr_number)
    if not all(isinstance(items, list) for items in (issue_comments, review_comments, commits)):
        raise RuntimeError(f"could not verify current activity for PR #{pr_number}")
    reviews = review_data.get("reviews") if isinstance(review_data, dict) else None
    if not isinstance(reviews, list):
        raise RuntimeError(f"could not verify current reviews for PR #{pr_number}")

    activities: list[datetime] = []
    for comment in issue_comments:
        if (
            is_human_actor(comment.get("user"))
            and not comment.get("performed_via_github_app")
        ):
            timestamp = parse_ts(comment.get("created_at") or "")
            if timestamp is not None:
                activities.append(timestamp)
    for comment in review_comments:
        if is_human_actor(comment.get("user")):
            timestamp = parse_ts(comment.get("created_at") or "")
            if timestamp is not None:
                activities.append(timestamp)
    for review in reviews:
        if is_human_actor(review.get("user")):
            timestamp = parse_ts(review.get("submitted_at") or "")
            if timestamp is not None:
                activities.append(timestamp)

    accepted_head_sha = str((result.get("facts") or {}).get("head_sha") or "")
    current_head_sha = str(current_pr.get("headRefOid") or "")
    if accepted_head_sha and current_head_sha != accepted_head_sha:
        new_commits = commit_delta(commits, accepted_head_sha, current_head_sha)
        if new_commits is None:
            activities.append(now)
        else:
            for commit in reversed(new_commits):
                if classify_commit(commit) == "bot":
                    continue
                activities.append(now)
                break
    if not activities:
        return None
    return max(activities)


def current_author_route(repo: str, pr_number: int, result: dict[str, Any]) -> bool:
    facts = result.get("facts") or {}
    current_pr = gh_pr_view(repo, pr_number)
    if (
        str(current_pr.get("state") or "").upper() != "OPEN"
        or current_pr.get("isDraft")
    ):
        return False

    top_level_urls = set(facts.get("author_action_top_level_feedback_urls") or [])
    observed_at = parse_ts(facts.get("observed_at") or "")
    updated_at = parse_ts(current_pr.get("updatedAt") or "")
    if (
        top_level_urls
        and observed_at is not None
        and updated_at is not None
        and updated_at < observed_at
    ):
        return True

    expected_urls = set(facts.get("author_action_review_thread_urls") or [])
    if expected_urls:
        owner, repo_name = repo.split("/", 1)
        current_urls = {
            str(((thread.get("comments") or {}).get("nodes") or [{}])[0].get("url") or "")
            for thread in fetch_review_threads(owner, repo_name, pr_number)
            if not thread.get("isResolved") and not thread.get("isOutdated")
        }
        if expected_urls & current_urls:
            return True

    if facts.get("ci_failing_count", 0) <= 0 or facts.get("is_maintenance_bot"):
        return False
    pr_id = str(current_pr.get("id") or "")
    base_branch = str(current_pr.get("baseRefName") or "")
    if not pr_id or not base_branch:
        return False
    checks = include_missing_required_checks(
        gh_pr_checks(repo, pr_id),
        gh_required_check_contexts(repo, base_branch),
    )
    return bool(
        checks is not None
        and any(check.get("bucket") in ("fail", "cancel") for check in checks)
    )


def has_activity_since_snapshot(
    result: dict[str, Any],
    current_activity: datetime | None,
) -> bool:
    if current_activity is None:
        return False
    facts = result.get("facts") or {}
    observed_at = parse_ts(facts.get("observed_at") or "")
    if observed_at is not None:
        return current_activity >= observed_at
    accepted_activity = latest_human_activity(facts)
    return accepted_activity is None or current_activity > accepted_activity


def execute_action(
    action: str,
    repo: str,
    pr_number: int,
    result: dict[str, Any] | None,
    previous: dict[str, Any] | None,
    updated: dict[str, Any],
    now: datetime,
) -> dict[str, Any] | None:
    if action != "general-nudge":
        raise ValueError(f"unsupported author follow-up action: {action}")
    cycle_id = updated.get("waiting_on_author_since") or format_ts(now)
    marker = lifecycle_marker(GENERAL_NUDGE_MARKER_PREFIX, cycle_id)
    existing = matching_comment(lifecycle_comments(repo, pr_number), marker)
    if existing:
        updated["general_nudged_at"] = existing.get("created_at") or format_ts(now)
        return updated
    if result is None:
        raise RuntimeError(f"cannot nudge PR #{pr_number} without dashboard result")
    issue = issue_details(repo, pr_number)
    if issue.get("state") != "open" or not issue.get("pull_request"):
        print(
            f"cancelled {action} on closed PR #{pr_number} after live recheck",
            file=sys.stderr,
        )
        return None
    if not current_author_route(repo, pr_number, result):
        print(
            f"cancelled {action} on PR #{pr_number} after route recheck",
            file=sys.stderr,
        )
        return None
    current_activity = current_human_activity(repo, pr_number, result, now)
    if has_activity_since_snapshot(result, current_activity):
        print(
            f"deferred {action} on PR #{pr_number} after activity recheck",
            file=sys.stderr,
        )
        deferred = dict(previous) if previous is not None else dict(updated)
        deferred["general_nudged_at"] = ""
        return deferred
    status_url = ensure_status_comment(repo, pr_number, result)
    post_comment(repo, pr_number, render_nudge(result, status_url, cycle_id))
    print(f"sent {action} on PR #{pr_number}", file=sys.stderr)
    return updated


def next_author_follow_ups(
    repo: str,
    results: dict[int, dict[str, Any]],
    previous_follow_ups: dict[str, Any],
    now: datetime,
    refreshed_pr_numbers: set[int] | None = None,
    reset_only: bool = False,
) -> dict[str, Any]:
    updated_follow_ups: dict[str, Any] = {}
    result_keys = {str(number) for number in results}
    for number, result in sorted(results.items()):
        key = str(number)
        previous = previous_follow_ups.get(key)
        if refreshed_pr_numbers is not None and number not in refreshed_pr_numbers:
            if previous is not None:
                updated_follow_ups[key] = previous
            continue
        if reset_only and (
            result.get("failed")
            or result.get("route") in ("author", "transient-failure", "unknown")
        ):
            if previous is not None:
                updated_follow_ups[key] = previous
            continue
        action, updated = plan_follow_up(result, previous, now)
        if action and updated is not None:
            updated = execute_action(action, repo, number, result, previous, updated, now)
        if updated is not None:
            updated_follow_ups[key] = updated

    for key, previous in previous_follow_ups.items():
        if key in updated_follow_ups or key in result_keys:
            continue
        try:
            number = int(key)
        except ValueError:
            continue
        if (
            reset_only
            and refreshed_pr_numbers is not None
            and number not in refreshed_pr_numbers
        ):
            updated_follow_ups[key] = previous
            continue
    return updated_follow_ups


def previous_author_follow_ups(retry_snapshot_path: Path | None) -> dict[str, Any]:
    saved = load_author_follow_ups()
    if retry_snapshot_path and retry_snapshot_path.exists():
        retry_state = load_author_follow_up_state_file(retry_snapshot_path)
        if retry_state is not None:
            return retry_state["prs"]
    return saved


def process_author_follow_ups(
    repo: str,
    now: datetime,
    refreshed_pr_numbers: set[int],
    reset_only: bool = False,
    retry_snapshot_path: Path | None = None,
) -> int:
    dashboard_state = load_dashboard_state_cache()
    if dashboard_state is None:
        print("dashboard state not found; skipping author follow-ups", file=sys.stderr)
        return 0
    prs = list_open_prs(repo)
    open_non_draft_numbers = {
        pr["number"] for pr in prs if not pr.get("isDraft")
    }
    results = results_from_dashboard_state(dashboard_state, open_non_draft_numbers)
    checkout_previous = load_author_follow_ups()
    previous = previous_author_follow_ups(retry_snapshot_path)
    updated = next_author_follow_ups(
        repo,
        results,
        previous,
        now,
        refreshed_pr_numbers,
        reset_only,
    )
    if updated == checkout_previous and author_follow_up_state_path().exists():
        print("author follow-up state unchanged", file=sys.stderr)
        return 0
    save_author_follow_ups(updated)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", help="target repository name, e.g. opentelemetry-java-instrumentation")
    parser.add_argument("--state-branch", required=True, help="git branch used for workflow state")
    parser.add_argument(
        "--pr-numbers",
        required=True,
        help="comma-separated PR numbers refreshed by the current dashboard run",
    )
    parser.add_argument(
        "--reset-only",
        action="store_true",
        help="clear departed routes without evaluating due follow-up actions",
    )
    args = parser.parse_args()

    repo = normalize_repo(args.repo) if args.repo else detect_repo()
    repo_key = repo_state_key(repo)
    retry_snapshot_path = Path(
        os.environ.get("RUNNER_TEMP", ".")
    ) / "prior-author-follow-up-state.json"
    retry_snapshot_path.unlink(missing_ok=True)
    with state_branch.temporary_state_dir() as state_dir:
        set_state_dir(state_dir / repo_key)
        return state_branch.push_state_changes(
            state_dir,
            "Update author follow-up state",
            lambda: process_author_follow_ups(
                repo,
                utc_now(),
                {int(number) for number in args.pr_numbers.split(",") if number},
                args.reset_only,
                retry_snapshot_path,
            ),
            state_branch=args.state_branch,
            add_paths=[f"{repo_key}/author-follow-up-state.json"],
            retry_snapshots=[(author_follow_up_state_path(), retry_snapshot_path)],
        )


if __name__ == "__main__":
    sys.exit(main())