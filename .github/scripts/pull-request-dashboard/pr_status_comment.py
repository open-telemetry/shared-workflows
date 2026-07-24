#!/usr/bin/env python3
"""Create or update dashboard-managed status comments and rollout state."""

from __future__ import annotations

import re
import sys
from typing import Any
from urllib.parse import urlencode

from github_cli import (
    gh_api,
    run_gh,
)
from dashboard_override import PRE_REVIEW_ROUTES, author_override_guidance
from route_presentation import route_status_summary
from state import (
    STATUS_COMMENT_REVISION,
    load_dashboard_state_cache,
    load_status_comment_rollout_state,
    save_status_comment_rollout_state,
)
from utils import markdown_escape, truncate
from utils import utc_now


STATUS_MARKER = "<!-- pull-request-dashboard-status -->"
AUTHOR_NUDGE_EPISODE_MARKER_PREFIX = (
    "<!-- pull-request-dashboard-author-nudge-episode:"
)
_AUTHOR_NUDGE_EPISODE_MARKER_RE = re.compile(
    r"<!-- pull-request-dashboard-author-nudge-episode:([a-f0-9]+) -->"
)
STATUS_COMMENT_ROLLOUT_BATCH_SIZE = 50
AUTHOR_ACTION_FEEDBACK_LINK_LIMIT = 20
NON_BLOCKING_CHECK_FAILURE_LIMIT = 20
NON_BLOCKING_CHECK_FAILURE_NAME_LIMIT = 200
STATUS_REPORT_ISSUE_URL = "https://github.com/open-telemetry/shared-workflows/issues/new"
STATUS_REPORT_ISSUE_TEMPLATE = "incorrect-pr-dashboard-result.md"
STATUS_REPORT_URL_MAX_CHARS = 4096
STATUS_REPORT_TRUNCATION_NOTICE = (
    "[Status comment truncated to keep this report link usable.]"
)
AUTHOR_GUIDANCE = (
    "For each item, reply to move the discussion forward, e.g. link to the commit "
    "that addresses it, explain why no change is needed, or ask a follow-up question."
)
DASHBOARD_APP_SLUG = "opentelemetry-pr-dashboard"
# Remove after migrating open PRs as described by the post-rollout
# compatibility cleanup in WEBHOOK_SETUP.md.
LEGACY_MARKERS = (
    "<!-- review-guidance -->",
    "<!-- copilot-review-guidance -->",
)


def author_nudge_episode_marker(episode_id: str) -> str:
    return f"{AUTHOR_NUDGE_EPISODE_MARKER_PREFIX}{episode_id} -->"


def is_dashboard_app_comment(comment: dict[str, Any]) -> bool:
    app_slug = (comment.get("performed_via_github_app") or {}).get("slug") or ""
    author_login = (comment.get("user") or {}).get("login") or ""
    return app_slug == DASHBOARD_APP_SLUG or author_login == f"{DASHBOARD_APP_SLUG}[bot]"


def status_author_nudge_episode_id(
    comments: list[dict[str, Any]] | None,
) -> str:
    for comment in comments or []:
        body = comment.get("body") or ""
        match = _AUTHOR_NUDGE_EPISODE_MARKER_RE.search(body)
        if (
            match
            and STATUS_MARKER in body
            and is_dashboard_app_comment(comment)
        ):
            return match.group(1)
    return ""


def status_report_url(pr: dict[str, Any], status_comment: str) -> str:
    quoted_status_comment = "\n".join(
        f"> {line}" for line in status_comment.splitlines()
    )
    query = urlencode({
        "template": STATUS_REPORT_ISSUE_TEMPLATE,
        "title": "PR dashboard result looks incorrect",
        "body": (
            f"PR: {pr.get('html_url') or ''}\n\n"
            f"Current live status comment:\n{quoted_status_comment}\n\n"
            "What looks incorrect:\n"
        ),
    })
    return f"{STATUS_REPORT_ISSUE_URL}?{query}"


def accuracy_note(
    pr: dict[str, Any],
    status_comment: str,
    override_route: str = "",
) -> str:
    report_url = status_report_url(pr, status_comment)
    if len(report_url) > STATUS_REPORT_URL_MAX_CHARS:
        lower_bound = 0
        upper_bound = len(status_comment)
        while lower_bound <= upper_bound:
            midpoint = (lower_bound + upper_bound) // 2
            truncated_status_comment = (
                f"{status_comment[:midpoint]}\n{STATUS_REPORT_TRUNCATION_NOTICE}"
            )
            candidate_url = status_report_url(pr, truncated_status_comment)
            if len(candidate_url) <= STATUS_REPORT_URL_MAX_CHARS:
                report_url = candidate_url
                lower_bound = midpoint + 1
            else:
                upper_bound = midpoint - 1
    note = (
        "This automated status or its linked feedback items may be incorrect. "
        f"If something looks wrong, please [report it]({report_url}) with the result you expected."
    )
    if override_route:
        note += " " + author_override_guidance(
            "If the last refreshed time above predates your latest reply or "
            "push, the dashboard hasn't processed it yet.",
            route=override_route,
        )
    return f"_{note}_"


def render_status_comment(
    pr: dict[str, Any],
    result: dict[str, Any] | None,
) -> str:
    last_updated = utc_now().strftime("%Y-%m-%d %H:%M:%S UTC")
    state = (pr.get("state") or "").lower()
    facts = (result or {}).get("facts") or {}
    review_thread_urls = facts.get("author_action_review_thread_urls") or []
    top_level_feedback_urls = facts.get("author_action_top_level_feedback_urls") or []
    feedback_count = len(review_thread_urls) + len(top_level_feedback_urls)
    failing_count = facts.get("ci_failing_count", 0)
    non_blocking_check_failures = facts.get("non_blocking_check_failures") or []
    non_blocking_failure_note = ""
    if non_blocking_check_failures:
        displayed_failures = non_blocking_check_failures[
            :NON_BLOCKING_CHECK_FAILURE_LIMIT
        ]
        names = format_list([
            markdown_escape(truncate(name, NON_BLOCKING_CHECK_FAILURE_NAME_LIMIT))
            for name in displayed_failures
        ])
        if len(non_blocking_check_failures) == 1:
            non_blocking_failure_note = (
                f"{names} is failing but is not a required check."
            )
        else:
            non_blocking_failure_note = (
                f"{names} are failing but are not required checks."
            )
        omitted_count = len(non_blocking_check_failures) - len(displayed_failures)
        if omitted_count:
            noun = "failure" if omitted_count == 1 else "failures"
            omitted_verb = "is" if omitted_count == 1 else "are"
            non_blocking_failure_note += (
                f" {omitted_count} additional non-blocking check {noun} "
                f"{omitted_verb} not shown."
            )

    feedback_indent: str | None = None
    override_route = ""

    if pr.get("merged"):
        summary = ["- **Status:** Merged."]
    elif state == "closed":
        summary = ["- **Status:** Closed."]
    elif pr.get("draft"):
        summary = [
            "- **Waiting on:** Author",
            "- **Next step:** Move out of draft to request review.",
        ]
    elif result is None:
        summary = [
            "- **Waiting on:** Pull request dashboard",
            "- **Next step:** Finish refreshing this pull request.",
        ]
    else:
        route = result.get("route") or "unknown"
        if route in PRE_REVIEW_ROUTES:
            override_route = route
        if route == "author":
            waiting_on, fallback_next_step = route_status_summary(route)
            check_action = None
            if failing_count:
                # One required aggregate check can represent multiple failing jobs.
                check_action = "Investigate required status check failures."
                if non_blocking_failure_note:
                    check_action += f" Note: {non_blocking_failure_note}"
            noun = "item" if feedback_count == 1 else "items"
            feedback_action = f"Address or respond to {feedback_count} review feedback {noun}:"
            if check_action and feedback_count:
                summary = [
                    f"- **Waiting on:** {waiting_on}",
                    "- **Next steps:**",
                    f"  - {check_action}",
                    f"  - {feedback_action}",
                ]
                feedback_indent = "    "
            elif feedback_count:
                summary = [
                    f"- **Waiting on:** {waiting_on}",
                    f"- **Next step:** {feedback_action}",
                ]
                feedback_indent = "  "
            elif check_action:
                summary = [
                    f"- **Waiting on:** {waiting_on}",
                    f"- **Next step:** {check_action}",
                ]
            else:
                summary = [
                    f"- **Waiting on:** {waiting_on}",
                    f"- **Next step:** {fallback_next_step}",
                ]
        else:
            waiting_on, next_step = route_status_summary(route)
            summary = [
                f"- **Waiting on:** {waiting_on}",
                f"- **Next step:** {next_step}",
            ]
            if failing_count:
                check_summary = (
                    "1 required status check is failing."
                    if failing_count == 1
                    else f"{failing_count} required status checks are failing."
                )
                summary.append(f"- **Also blocked by:** {check_summary}")
            if non_blocking_failure_note:
                label = (
                    "Non-blocking check failure"
                    if len(non_blocking_check_failures) == 1
                    else "Non-blocking check failures"
                )
                summary.append(f"- **{label}:** {non_blocking_failure_note}")

    lines = [
        STATUS_MARKER,
        f"<!-- pull-request-dashboard-status-revision:{STATUS_COMMENT_REVISION} -->",
        "## Pull request dashboard status",
        "",
        f"_Status last refreshed: {last_updated}._",
        "",
        *summary,
    ]
    episode_id = str(facts.get("author_nudge_episode_id") or "")
    if (result or {}).get("route") == "author" and episode_id:
        lines.insert(2, author_nudge_episode_marker(episode_id))

    if feedback_indent is not None and feedback_count:
        lines.extend(
            feedback_breakdown_lines(
                review_thread_urls,
                top_level_feedback_urls,
                feedback_indent,
            )
        )
    status_comment = "\n".join(lines)
    lines.append("")
    lines.append(accuracy_note(pr, status_comment, override_route))
    lines.append("")
    return "\n".join(lines)


def format_list(values: list[str]) -> str:
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        return f"{values[0]} and {values[1]}"
    return f"{', '.join(values[:-1])}, and {values[-1]}"


def feedback_breakdown_lines(
    review_thread_urls: list[str],
    top_level_feedback_urls: list[str],
    indent: str,
) -> list[str]:
    feedback_count = len(review_thread_urls) + len(top_level_feedback_urls)
    sections = (
        ("Inline threads", review_thread_urls),
        ("Top-level feedback", top_level_feedback_urls),
    )
    lines: list[str] = []
    remaining_limit = AUTHOR_ACTION_FEEDBACK_LINK_LIMIT
    shown = 0
    for label, urls in sections:
        displayed_urls = urls[:remaining_limit]
        if not displayed_urls:
            continue
        links = ", ".join(
            f"[{index}]({url})"
            for index, url in enumerate(displayed_urls, start=shown + 1)
        )
        lines.append(f"{indent}- **{label}:** {links}")
        shown += len(displayed_urls)
        remaining_limit -= len(displayed_urls)
    if shown < feedback_count:
        lines.append(
            f"{indent}- _Showing {shown} of {feedback_count} feedback links; "
            "resolve the remaining items from the pull request's conversation._"
        )
    lines.append(f"{indent}- _{AUTHOR_GUIDANCE}_")
    return lines


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
    open_pr_numbers: set[int],
) -> list[str]:
    dashboard_state = load_dashboard_state_cache()
    if dashboard_state is None:
        print("dashboard result state not found; skipping PR status comment", file=sys.stderr)
        return []

    saved_rollout_state = load_status_comment_rollout_state()
    queued_pr_numbers = set(saved_rollout_state.get("pending_pr_numbers") or [])
    rollout_state = prepare_rollout_state(saved_rollout_state, open_pr_numbers)
    pending_pr_numbers = set(rollout_state["pending_pr_numbers"]) | queued_pr_numbers
    rollout_pr_numbers = sorted(pending_pr_numbers)[:STATUS_COMMENT_ROLLOUT_BATCH_SIZE]
    successful_pr_numbers: set[int] = set()
    errors: list[str] = []
    for number in rollout_pr_numbers:
        try:
            publish_pr_status(repo, number, dashboard_state)
        except Exception as e:
            errors.append(f"PR #{number}: {e}")
        else:
            successful_pr_numbers.add(number)

    pending = pending_pr_numbers - successful_pr_numbers
    rollout_state["pending_pr_numbers"] = sorted(pending)
    if not pending:
        rollout_state["completed_revision"] = STATUS_COMMENT_REVISION
    save_status_comment_rollout_state(rollout_state)
    return errors
