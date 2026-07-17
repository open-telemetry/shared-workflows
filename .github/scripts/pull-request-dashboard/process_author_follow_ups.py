#!/usr/bin/env python3
"""Post author handoff nudges and optionally escalate inactive pull requests."""

from __future__ import annotations

import argparse
from datetime import datetime
import os
from pathlib import Path
import sys
from typing import Any
from urllib.parse import quote

from author_follow_up import plan_follow_up
from github_cli import (
    detect_repo,
    fetch_pr_review_data,
    fetch_review_threads,
    gh_api,
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
from utils import format_ts, parse_ts, utc_now


HANDOFF_NUDGE_MARKER_PREFIX = "<!-- pull-request-dashboard-author-handoff-nudge:"
GENERAL_NUDGE_MARKER_PREFIX = "<!-- pull-request-dashboard-author-general-nudge:"
CLOSE_MARKER_PREFIX = "<!-- pull-request-dashboard-author-close:"
STALE_LABEL = "Stale"
STALE_LABEL_COLOR = "ededed"
STALE_LABEL_DESCRIPTION = "Inactive pull request"


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
    kind: str,
) -> str:
    facts = result.get("facts") or {}
    author = (facts.get("author") or "").strip()
    mention = f"@{author}, " if author else ""
    if kind == "handoff-nudge":
        message = (
            f"{mention}this pull request is still waiting on your follow-up and has not been "
            "routed back to reviewers. Some review feedback still needs an explicit reply or "
            "resolution."
        )
        marker_prefix = HANDOFF_NUDGE_MARKER_PREFIX
    elif kind == "general-nudge":
        message = (
            f"{mention}this pull request has been waiting on your follow-up for one week. "
            "Some review feedback still needs an explicit reply or resolution."
        )
        marker_prefix = GENERAL_NUDGE_MARKER_PREFIX
    else:
        raise ValueError(f"unsupported author nudge kind: {kind}")
    return "\n".join([
        lifecycle_marker(marker_prefix, cycle_id),
        message,
        "",
        f"See the [dashboard status comment]({status_url}) for the remaining items.",
        "",
    ])


def render_close_comment(cycle_id: str) -> str:
    return "\n".join([
        lifecycle_marker(CLOSE_MARKER_PREFIX, cycle_id),
        (
            "Closing this pull request after the general and stale reminders, including a "
            "quiet week after stale labeling. "
            "It can be reopened when the remaining review feedback has an explicit outcome and "
            "the pull request is ready to return to review."
        ),
        "",
    ])


def issue_label_names(repo: str, pr_number: int) -> dict[str, str]:
    return label_names(issue_details(repo, pr_number))


def issue_details(repo: str, pr_number: int) -> dict[str, Any]:
    return gh_api(f"/repos/{repo}/issues/{pr_number}") or {}


def label_names(value: dict[str, Any]) -> dict[str, str]:
    return {
        str(label.get("name") or "").lower(): str(label.get("name") or "")
        for label in value.get("labels") or []
        if label.get("name")
    }


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
    review_data = fetch_pr_review_data(owner, repo_name, pr_number)
    if not all(isinstance(items, list) for items in (issue_comments, review_comments, commits)):
        raise RuntimeError(f"could not verify current activity for PR #{pr_number}")
    reviews = review_data.get("reviews") if isinstance(review_data, dict) else None
    if not isinstance(reviews, list):
        raise RuntimeError(f"could not verify current reviews for PR #{pr_number}")

    timestamps: list[datetime] = []
    for comment in issue_comments:
        if (
            is_human_actor(comment.get("user"))
            and not comment.get("performed_via_github_app")
        ):
            timestamp = parse_ts(comment.get("created_at") or "")
            if timestamp is not None:
                timestamps.append(timestamp)
    for comment in review_comments:
        if is_human_actor(comment.get("user")):
            timestamp = parse_ts(comment.get("created_at") or "")
            if timestamp is not None:
                timestamps.append(timestamp)
    for review in reviews:
        if is_human_actor(review.get("user")):
            timestamp = parse_ts(review.get("submitted_at") or "")
            if timestamp is not None:
                timestamps.append(timestamp)

    author = str((result.get("facts") or {}).get("author") or "").lower()
    for commit in commits:
        commit_data = commit.get("commit") or {}
        commit_author = commit_data.get("author") or {}
        commit_committer = commit_data.get("committer") or {}
        author_actor = commit.get("author") or {}
        committer_actor = commit.get("committer") or {}
        if str(committer_actor.get("login") or "").lower() == author:
            timestamp = parse_ts(
                commit_committer.get("date") or commit_author.get("date") or ""
            )
        elif str(author_actor.get("login") or "").lower() == author:
            timestamp = parse_ts(commit_author.get("date") or "")
        else:
            timestamp = None
        if timestamp is not None:
            timestamps.append(timestamp)
    return max(timestamps, default=None)


def current_author_route(repo: str, pr_number: int, result: dict[str, Any]) -> bool:
    facts = result.get("facts") or {}
    top_level_urls = set(facts.get("author_action_top_level_feedback_urls") or [])
    if top_level_urls:
        return True

    expected_urls = set(facts.get("author_action_review_thread_urls") or [])
    if not expected_urls:
        return False
    owner, repo_name = repo.split("/", 1)
    current_urls = {
        str(((thread.get("comments") or {}).get("nodes") or [{}])[0].get("url") or "")
        for thread in fetch_review_threads(owner, repo_name, pr_number)
        if not thread.get("isResolved") and not thread.get("isOutdated")
    }
    return bool(expected_urls & current_urls)


def repository_stale_label(repo: str) -> str:
    labels = gh_api(f"/repos/{repo}/labels?per_page=100", paginate=True) or []
    for label in labels:
        name = str(label.get("name") or "")
        if name.lower() == STALE_LABEL.lower():
            return name
    run_gh([
        "gh", "api", "--method", "POST",
        f"repos/{repo}/labels",
        "-f", f"name={STALE_LABEL}",
        "-f", f"color={STALE_LABEL_COLOR}",
        "-f", f"description={STALE_LABEL_DESCRIPTION}",
    ])
    return STALE_LABEL


def add_stale_label(repo: str, pr_number: int) -> bool:
    current_labels = issue_label_names(repo, pr_number)
    if STALE_LABEL.lower() in current_labels:
        return False
    label = repository_stale_label(repo)
    run_gh([
        "gh", "api", "--method", "POST",
        f"repos/{repo}/issues/{pr_number}/labels",
        "-f", f"labels[]={label}",
    ])
    return True


def remove_stale_label(repo: str, pr_number: int) -> None:
    label = issue_label_names(repo, pr_number).get(STALE_LABEL.lower())
    if not label:
        return
    run_gh([
        "gh", "api", "--method", "DELETE",
        f"repos/{repo}/issues/{pr_number}/labels/{quote(label, safe='')}",
    ])


def execute_action(
    action: str,
    repo: str,
    pr_number: int,
    result: dict[str, Any] | None,
    previous: dict[str, Any] | None,
    updated: dict[str, Any],
    now: datetime,
) -> dict[str, Any] | None:
    cycle_id = updated.get("cycle_id") or updated.get("waiting_on_author_since") or format_ts(now)
    if action in ("handoff-nudge", "general-nudge"):
        marker_prefix = (
            HANDOFF_NUDGE_MARKER_PREFIX
            if action == "handoff-nudge"
            else GENERAL_NUDGE_MARKER_PREFIX
        )
        timestamp_field = (
            "handoff_nudged_at"
            if action == "handoff-nudge"
            else "general_nudged_at"
        )
        marker = lifecycle_marker(marker_prefix, cycle_id)
        existing = matching_comment(lifecycle_comments(repo, pr_number), marker)
        if existing:
            updated[timestamp_field] = existing.get("created_at") or format_ts(now)
            return updated
        if result is None:
            raise RuntimeError(f"cannot nudge PR #{pr_number} without dashboard result")
        status_url = ensure_status_comment(repo, pr_number, result)
        post_comment(repo, pr_number, render_nudge(result, status_url, cycle_id, action))
        print(f"sent {action} on PR #{pr_number}", file=sys.stderr)
        return updated

    if action == "stale":
        updated["stale_label_owned"] = add_stale_label(repo, pr_number)
        if updated["stale_label_owned"]:
            updated["stale_applied_at"] = format_ts(now)
        print(f"marked PR #{pr_number} stale", file=sys.stderr)
        return updated

    if action == "remove-stale":
        if (previous or {}).get("stale_label_owned"):
            remove_stale_label(repo, pr_number)
            print(f"removed dashboard-managed stale label from PR #{pr_number}", file=sys.stderr)
        updated.pop("stale_label_owned", None)
        return updated

    if action == "close":
        issue = issue_details(repo, pr_number)
        current_labels = label_names(issue)
        stale_applied_at = parse_ts(updated.get("stale_applied_at") or "")
        if (
            issue.get("state") != "open"
            or not issue.get("pull_request")
        ):
            return None
        if (
            result is None
            or result.get("route") != "author"
            or not current_author_route(repo, pr_number, result)
            or STALE_LABEL.lower() not in current_labels
        ):
            if updated.get("stale_label_owned") and STALE_LABEL.lower() in current_labels:
                remove_stale_label(repo, pr_number)
            updated["stale_applied_at"] = ""
            updated["stale_reset_at"] = format_ts(now)
            updated.pop("stale_label_owned", None)
            print(
                f"deferred closure of PR #{pr_number} after current-state recheck",
                file=sys.stderr,
            )
            return updated
        human_activity = current_human_activity(repo, pr_number, result)
        if stale_applied_at is None or (
            human_activity is not None and human_activity > stale_applied_at
        ):
            if updated.get("stale_label_owned"):
                remove_stale_label(repo, pr_number)
            updated["stale_applied_at"] = ""
            updated["stale_reset_at"] = format_ts(human_activity or now)
            updated.pop("stale_label_owned", None)
            print(
                f"deferred closure of PR #{pr_number} after current-activity recheck",
                file=sys.stderr,
            )
            return updated
        marker = lifecycle_marker(CLOSE_MARKER_PREFIX, cycle_id)
        existing_close_comment = matching_comment(
            lifecycle_comments(repo, pr_number),
            marker,
        )
        if not existing_close_comment:
            post_comment(repo, pr_number, render_close_comment(cycle_id))
        run_gh([
            "gh", "api", "--method", "PATCH",
            f"repos/{repo}/pulls/{pr_number}",
            "-f", "state=closed",
        ])
        print(f"closed stale PR #{pr_number}", file=sys.stderr)
        return None

    raise ValueError(f"unsupported author follow-up action: {action}")


def next_author_follow_ups(
    repo: str,
    results: dict[int, dict[str, Any]],
    open_pr_numbers: set[int],
    previous_follow_ups: dict[str, Any],
    now: datetime,
    stale_enabled: bool,
    current_prs: dict[int, dict[str, Any]] | None = None,
    refreshed_pr_numbers: set[int] | None = None,
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
        current_pr = (current_prs or {}).get(number) or {}
        if (
            current_prs is not None
            and previous
            and previous.get("stale_label_owned")
            and previous.get("stale_applied_at")
            and STALE_LABEL.lower() not in label_names(current_pr)
        ):
            previous = dict(previous)
            previous["stale_applied_at"] = ""
            previous["stale_reset_at"] = format_ts(now)
            previous.pop("stale_label_owned", None)
        action, updated = plan_follow_up(result, previous, now, stale_enabled)
        if action and updated is not None:
            updated = execute_action(action, repo, number, result, previous, updated, now)
        if (
            not result.get("failed")
            and result.get("route") not in ("author", "transient-failure", "unknown")
        ):
            updated = None
        if updated is not None:
            updated_follow_ups[key] = updated

    for key, previous in previous_follow_ups.items():
        if key in updated_follow_ups or key in result_keys:
            continue
        try:
            number = int(key)
        except ValueError:
            continue
        if number in open_pr_numbers and previous.get("stale_label_owned"):
            remove_stale_label(repo, number)
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
    stale_enabled: bool,
    now: datetime,
    refreshed_pr_numbers: set[int],
    retry_snapshot_path: Path | None = None,
) -> int:
    dashboard_state = load_dashboard_state_cache()
    if dashboard_state is None:
        print("dashboard state not found; skipping author follow-ups", file=sys.stderr)
        return 0
    prs = list_open_prs(repo)
    current_prs = {pr["number"]: pr for pr in prs}
    open_pr_numbers = {pr["number"] for pr in prs}
    open_non_draft_numbers = {
        pr["number"] for pr in prs if not pr.get("isDraft")
    }
    results = results_from_dashboard_state(dashboard_state, open_non_draft_numbers)
    previous = previous_author_follow_ups(retry_snapshot_path)
    updated = next_author_follow_ups(
        repo,
        results,
        open_pr_numbers,
        previous,
        now,
        stale_enabled,
        current_prs,
        refreshed_pr_numbers,
    )
    if updated == previous and author_follow_up_state_path().exists():
        print("author follow-up state unchanged", file=sys.stderr)
        return 0
    save_author_follow_ups(updated)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", help="target repository name, e.g. opentelemetry-java-instrumentation")
    parser.add_argument("--state-branch", required=True, help="git branch used for workflow state")
    parser.add_argument("--stale", action="store_true", help="enable stale labeling and closure")
    parser.add_argument(
        "--pr-numbers",
        required=True,
        help="comma-separated PR numbers refreshed by the current dashboard run",
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
                args.stale,
                utc_now(),
                {int(number) for number in args.pr_numbers.split(",") if number},
                retry_snapshot_path,
            ),
            state_branch=args.state_branch,
            add_paths=[f"{repo_key}/author-follow-up-state.json"],
            retry_snapshots=[(author_follow_up_state_path(), retry_snapshot_path)],
        )


if __name__ == "__main__":
    sys.exit(main())