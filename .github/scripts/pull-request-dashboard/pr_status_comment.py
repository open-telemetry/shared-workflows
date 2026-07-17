#!/usr/bin/env python3
"""Create or update the dashboard-managed status comment on a pull request."""

from __future__ import annotations

import argparse
import sys
from typing import Any

from github_cli import detect_repo, gh_api, normalize_repo, run_gh
from route_presentation import route_status
from state import load_accepted_dashboard_state


STATUS_MARKER = "<!-- pull-request-dashboard-status -->"
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
        mention = author_mention(effective_author)
        failing_count = facts.get("ci_failing_count", 0)
        if route == "author" and failing_count > 0:
            check_action = (
                "fix the failing required status check"
                if failing_count == 1
                else "fix failing required status checks"
            )
            has_review_feedback = bool(
                facts.get("author_action_review_thread_urls")
                or facts.get("author_action_top_level_feedback_urls")
            )
            if has_review_feedback:
                check_action += " and address or respond to review feedback"
            status = f"Waiting on {mention} to {check_action}."
        else:
            status = route_status(route, mention)
            if failing_count > 0:
                check_subject = (
                    "A required status check is"
                    if failing_count == 1
                    else "Required status checks are"
                )
                status += f" {check_subject} also failing."

    lines = [
        STATUS_MARKER,
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", help="target repository name, e.g. opentelemetry-java-instrumentation")
    parser.add_argument("--state-branch", required=True, help="git branch used for workflow state")
    parser.add_argument("--pr-number", type=int, required=True, help="pull request to update")
    args = parser.parse_args()

    repo = normalize_repo(args.repo) if args.repo else detect_repo()
    dashboard_state = load_accepted_dashboard_state(repo, args.state_branch)
    if dashboard_state is None:
        print("dashboard result state not found; skipping PR status comment", file=sys.stderr)
        return 0
    publish_pr_status(repo, args.pr_number, dashboard_state)
    return 0


if __name__ == "__main__":
    sys.exit(main())