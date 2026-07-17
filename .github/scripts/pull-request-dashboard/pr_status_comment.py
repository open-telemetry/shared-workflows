#!/usr/bin/env python3
"""Create or update dashboard-managed status comments and rollout state."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from github_cli import detect_repo, gh_api, list_open_prs, normalize_repo, repo_state_key, run_gh
from route_presentation import route_status
from state import (
    load_dashboard_state_cache,
    load_status_comment_rollout_state,
    save_status_comment_rollout_state,
    set_state_dir,
    status_comment_rollout_state_path,
)
import state_branch


STATUS_MARKER = "<!-- pull-request-dashboard-status -->"
# Increment whenever render_status_comment changes in a way existing comments
# need to adopt. Hourly runs durably roll the revision out to all open PRs.
STATUS_COMMENT_REVISION = 1
STATUS_COMMENT_ROLLOUT_BATCH_SIZE = 50
AUTHOR_ACTION_FEEDBACK_LINK_LIMIT = 20
AUTHOR_GUIDANCE = (
    "Please give each review feedback item a clear outcome: link to the commit that addresses it, "
    "explain why no change is needed, or ask a follow-up question."
)
DASHBOARD_APP_SLUG = "opentelemetry-pr-dashboard"
# Remove after migrating open PRs as described by the post-rollout
# compatibility cleanup in WEBHOOK_SETUP.md.
LEGACY_MARKERS = (
    "<!-- review-guidance -->",
    "<!-- copilot-review-guidance -->",
)


def author_mention(author: str) -> str:
    return f"@{author}" if author != "the author" else author


def render_status_comment(
    pr: dict[str, Any],
    result: dict[str, Any] | None,
) -> str:
    author = ((pr.get("user") or {}).get("login") or "").strip() or "the author"
    state = (pr.get("state") or "").lower()
    terminal = bool(pr.get("merged")) or state == "closed"

    if pr.get("merged"):
        status = "This pull request has been merged."
    elif state == "closed":
        status = "This pull request has been closed."
    elif pr.get("draft"):
        status = f"Waiting on {author_mention(author)} to mark this pull request ready for review."
    elif result is None:
        status = "Waiting for the dashboard to finish refreshing this pull request."
    else:
        facts = result.get("facts") or {}
        route = result.get("route") or "unknown"
        effective_author = (facts.get("author") or author).strip()
        status = route_status(route, author_mention(effective_author))

    lines = [
        STATUS_MARKER,
        f"<!-- pull-request-dashboard-status-revision:{STATUS_COMMENT_REVISION} -->",
        "## Pull request dashboard status",
        "",
        f"**Status:** {status}",
    ]

    if not terminal and result and result.get("route") == "author":
        facts = result.get("facts") or {}
        feedback_sections = (
            (
                "Unresolved inline review threads waiting on the author:",
                "Thread",
                "inline review thread",
                "inline review threads",
                facts.get("author_action_review_thread_urls") or [],
            ),
            (
                "Top-level feedback waiting on the author:",
                "Feedback",
                "top-level feedback item",
                "top-level feedback items",
                facts.get("author_action_top_level_feedback_urls") or [],
            ),
        )
        remaining_limit = AUTHOR_ACTION_FEEDBACK_LINK_LIMIT
        for heading, label, singular, plural, urls in feedback_sections:
            if not urls:
                continue
            lines.extend(["", heading])
            displayed_urls = urls[:remaining_limit]
            lines.extend(
                f"- [{label} {index}]({url})"
                for index, url in enumerate(displayed_urls, start=1)
            )
            remaining_count = len(urls) - len(displayed_urls)
            if remaining_count:
                noun = singular if remaining_count == 1 else plural
                lines.append(f"- {remaining_count} more {noun} not shown")
            remaining_limit -= len(displayed_urls)

    if not terminal and result and result.get("route") == "author":
        lines.extend(["", AUTHOR_GUIDANCE])
    lines.append("")
    return "\n".join(lines)


def managed_status_comments(repo: str, pr_number: int) -> list[dict[str, Any]]:
    comments = gh_api(
        f"/repos/{repo}/issues/{pr_number}/comments?per_page=100",
        paginate=True,
    )
    markers = (STATUS_MARKER, *LEGACY_MARKERS)
    return [
        comment
        for comment in comments or []
        if (comment.get("performed_via_github_app") or {}).get("slug") == DASHBOARD_APP_SLUG
        and any(marker in (comment.get("body") or "") for marker in markers)
    ]


def upsert_status_comment(repo: str, pr_number: int, body: str) -> None:
    comments = managed_status_comments(repo, pr_number)
    if comments:
        comment = comments[0]
        comment_id = comment["id"]
        if comment.get("body") == body:
            print(f"PR #{pr_number} status comment is unchanged", file=sys.stderr)
        else:
            print(f"updating PR #{pr_number} status comment {comment_id}", file=sys.stderr)
            run_gh([
                "gh", "api", "--method", "PATCH",
                f"repos/{repo}/issues/comments/{comment_id}",
                "-f", f"body={body}",
            ])
        for duplicate in comments[1:]:
            duplicate_id = duplicate["id"]
            print(f"deleting duplicate PR #{pr_number} status comment {duplicate_id}", file=sys.stderr)
            run_gh([
                "gh", "api", "--method", "DELETE",
                f"repos/{repo}/issues/comments/{duplicate_id}",
            ])
        return

    print(f"creating PR #{pr_number} status comment", file=sys.stderr)
    run_gh([
        "gh", "api", "--method", "POST",
        f"repos/{repo}/issues/{pr_number}/comments",
        "-f", f"body={body}",
    ])


def publish_pr_status(repo: str, pr_number: int, dashboard_state: dict[str, Any]) -> None:
    pr = gh_api(f"/repos/{repo}/pulls/{pr_number}")
    result = (dashboard_state.get("prs") or {}).get(str(pr_number))
    upsert_status_comment(repo, pr_number, render_status_comment(pr, result))


def prepare_rollout_state(
    rollout_state: dict[str, Any],
    open_pr_numbers: set[int],
) -> dict[str, Any]:
    if rollout_state.get("target_revision") != STATUS_COMMENT_REVISION:
        return {
            "target_revision": STATUS_COMMENT_REVISION,
            "completed_revision": int(rollout_state.get("completed_revision") or 0),
            "pending_pr_numbers": sorted(open_pr_numbers),
        }
    pending = {
        number
        for number in rollout_state.get("pending_pr_numbers") or []
        if number in open_pr_numbers
    }
    return {
        "target_revision": STATUS_COMMENT_REVISION,
        "completed_revision": int(rollout_state.get("completed_revision") or 0),
        "pending_pr_numbers": sorted(pending),
    }


def update_status_comments_from_state(
    repo: str,
    pr_number: int | None,
    open_pr_numbers: set[int] | None,
) -> list[str]:
    dashboard_state = load_dashboard_state_cache()
    if dashboard_state is None:
        print("dashboard result state not found; skipping PR status comment", file=sys.stderr)
        return []

    rollout_state = load_status_comment_rollout_state()
    if pr_number is not None:
        publish_pr_status(repo, pr_number, dashboard_state)
        pending = set(rollout_state["pending_pr_numbers"])
        if pr_number in pending:
            pending.remove(pr_number)
            rollout_state["pending_pr_numbers"] = sorted(pending)
            if not pending and rollout_state["target_revision"] == STATUS_COMMENT_REVISION:
                rollout_state["completed_revision"] = STATUS_COMMENT_REVISION
            save_status_comment_rollout_state(rollout_state)
        return []

    if open_pr_numbers is None:
        raise RuntimeError("open PR numbers are required for a rollout update")
    rollout_state = prepare_rollout_state(rollout_state, open_pr_numbers)
    rollout_pr_numbers = rollout_state["pending_pr_numbers"][:STATUS_COMMENT_ROLLOUT_BATCH_SIZE]
    successful_pr_numbers: set[int] = set()
    errors: list[str] = []
    for number in rollout_pr_numbers:
        try:
            publish_pr_status(repo, number, dashboard_state)
        except Exception as e:
            errors.append(f"PR #{number}: {e}")
        else:
            successful_pr_numbers.add(number)

    pending = set(rollout_state["pending_pr_numbers"]) - successful_pr_numbers
    rollout_state["pending_pr_numbers"] = sorted(pending)
    if not pending:
        rollout_state["completed_revision"] = STATUS_COMMENT_REVISION
    save_status_comment_rollout_state(rollout_state)
    return errors


def rollout_errors_path() -> Path:
    return Path(os.environ.get("RUNNER_TEMP", ".")) / "status-comment-rollout-errors.txt"


def update_status_comments(
    repo: str,
    pr_number: int | None,
    open_pr_numbers: set[int] | None,
    errors_file: Path,
) -> int:
    errors = update_status_comments_from_state(repo, pr_number, open_pr_numbers)
    if errors:
        errors_file.write_text("\n".join(errors) + "\n", encoding="utf-8")
    else:
        errors_file.unlink(missing_ok=True)
    return 0


def update_status_comments_with_state(
    repo: str,
    state_branch_name: str,
    state_dir: Path,
    pr_number: int | None,
) -> int:
    open_pr_numbers = None
    if pr_number is None:
        open_pr_numbers = {pr["number"] for pr in list_open_prs(repo)}
    repo_key = repo_state_key(repo)
    errors_file = rollout_errors_path()
    errors_file.unlink(missing_ok=True)
    status = state_branch.push_state_changes(
        state_dir,
        "Update status comment rollout state",
        lambda: update_status_comments(
            repo,
            pr_number,
            open_pr_numbers,
            errors_file,
        ),
        state_branch=state_branch_name,
        add_paths=[f"{repo_key}/{status_comment_rollout_state_path().name}"],
    )
    if status != 0:
        return status
    if not errors_file.exists():
        return 0
    print("Status comment rollout failed:", file=sys.stderr)
    print(errors_file.read_text(encoding="utf-8").rstrip(), file=sys.stderr)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", help="target repository name, e.g. opentelemetry-java-instrumentation")
    parser.add_argument("--state-branch", required=True, help="git branch used for workflow state")
    parser.add_argument("--pr-number", type=int, help="targeted pull request to update")
    args = parser.parse_args()

    repo = normalize_repo(args.repo) if args.repo else detect_repo()

    with state_branch.temporary_state_dir() as state_dir:
        set_state_dir(state_dir / repo_state_key(repo))
        return update_status_comments_with_state(
            repo,
            args.state_branch,
            state_dir,
            args.pr_number,
        )


if __name__ == "__main__":
    sys.exit(main())