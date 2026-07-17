#!/usr/bin/env python3
"""Create or update the dashboard-managed status comment on a pull request."""

from __future__ import annotations

import argparse
import sys
from typing import Any

from github_cli import detect_repo, gh_api, normalize_repo, repo_state_key, run_gh
from state import load_dashboard_state_cache, set_state_dir
import state_branch


STATUS_MARKER = "<!-- pull-request-dashboard-status -->"
AUTHOR_ACTION_DISCUSSION_LINK_LIMIT = 20
AUTHOR_GUIDANCE = (
    "Please give every review discussion a clear outcome: link to the commit that addresses it, "
    "explain why no change is needed, ask a follow-up question, or resolve the discussion."
)
DASHBOARD_APP_SLUG = "opentelemetry-pr-dashboard"
LEGACY_MARKERS = (
    "<!-- review-guidance -->",
    "<!-- copilot-review-guidance -->",
)


def load_accepted_dashboard_state(repo: str, state_branch_name: str) -> dict[str, Any] | None:
    if not state_branch.fetch_state_branch(state_branch_name, required=False):
        return None
    with state_branch.temporary_state_dir() as checkout_dir:
        try:
            state_branch.run([
                "git", "worktree", "add", "--quiet", "--detach", str(checkout_dir),
                state_branch.remote_ref(state_branch_name),
            ])
            set_state_dir(checkout_dir / repo_state_key(repo))
            return load_dashboard_state_cache()
        finally:
            state_branch.remove_existing_state_dir(checkout_dir)


def render_status_comment(
    pr: dict[str, Any],
    result: dict[str, Any] | None,
) -> str:
    author = ((pr.get("user") or {}).get("login") or "").strip() or "the author"
    state = (pr.get("state") or "").lower()
    terminal = bool(pr.get("merged")) or state == "closed"

    if pr.get("merged"):
        next_action = "No one"
        waiting_on = "This pull request has been merged."
    elif state == "closed":
        next_action = "No one"
        waiting_on = "This pull request has been closed."
    elif pr.get("draft"):
        next_action = f"@{author}" if author != "the author" else author
        waiting_on = "The author to mark this pull request ready for review."
    elif result is None:
        next_action = "Dashboard maintainers"
        waiting_on = "The dashboard to finish refreshing this pull request."
    else:
        facts = result.get("facts") or {}
        route = result.get("route") or "unknown"
        if route == "author":
            effective_author = (facts.get("author") or author).strip()
            next_action = f"@{effective_author}" if effective_author != "the author" else effective_author
            waiting_on = "The author to address or respond to unresolved review discussions."
        elif route == "approver":
            next_action = "Reviewers"
            waiting_on = "Reviewers to review the latest changes."
        elif route == "maintainer":
            next_action = "Maintainers"
            waiting_on = "A maintainer to merge the pull request."
        elif route == "external":
            next_action = "External"
            waiting_on = "An external dependency or decision."
        else:
            next_action = "Dashboard maintainers"
            waiting_on = "The dashboard to determine the next action."

    lines = [
        STATUS_MARKER,
        "## Pull request dashboard status",
        "",
        f"**Next action:** {next_action}",
        "",
        f"**Waiting on:** {waiting_on}",
    ]

    if not terminal and result and result.get("route") == "author":
        urls = (result.get("facts") or {}).get("author_action_discussion_urls") or []
        if urls:
            lines.extend(["", "Unresolved discussions waiting on the author:"])
            displayed_urls = urls[:AUTHOR_ACTION_DISCUSSION_LINK_LIMIT]
            lines.extend(f"- [Discussion {index}]({url})" for index, url in enumerate(displayed_urls, start=1))
            remaining_count = len(urls) - len(displayed_urls)
            if remaining_count:
                noun = "discussion" if remaining_count == 1 else "discussions"
                lines.append(f"- {remaining_count} more unresolved {noun} not shown")

    if not terminal:
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", help="target repository name, e.g. opentelemetry-java-instrumentation")
    parser.add_argument("--state-branch", required=True, help="git branch used for workflow state")
    parser.add_argument("--pr-number", type=int, help="pull request to update")
    parser.add_argument(
        "--check-state-exists",
        action="store_true",
        help='print "true" when accepted dashboard state exists, otherwise "false"',
    )
    args = parser.parse_args()

    repo = normalize_repo(args.repo) if args.repo else detect_repo()
    dashboard_state = load_accepted_dashboard_state(repo, args.state_branch)
    if args.check_state_exists:
        print("true" if dashboard_state is not None else "false")
        return 0
    if args.pr_number is None:
        parser.error("--pr-number is required unless --check-state-exists is set")
    if dashboard_state is None:
        print("dashboard result state not found; skipping PR status comment", file=sys.stderr)
        return 0
    publish_pr_status(repo, args.pr_number, dashboard_state)
    return 0


if __name__ == "__main__":
    sys.exit(main())