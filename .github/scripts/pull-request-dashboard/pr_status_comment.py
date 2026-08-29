#!/usr/bin/env python3
"""Create or update dashboard-managed status comments and rollout state."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlencode

from github_cli import (
    gh_api,
    run_gh,
)
from dashboard_override import (
    PRE_REVIEW_ROUTES,
    acknowledged_override,
    override_ack_marker,
)
from dashboard_contracts import (
    DashboardFacts,
    DashboardRoute,
    DashboardState,
    EvaluationFailure,
    StoredDashboardResult,
)
from dashboard_status import (
    AUTHOR_NUDGE_EPISODE_MARKER_PREFIX,
    DASHBOARD_APP_SLUG,
    STATUS_MARKER,
    author_nudge_episode_marker,
    is_dashboard_app_comment,
    reviewer_handoff_cleared_marker,
    status_author_nudge_episode_id,
    status_reviewer_handoff_clearance,
)
from pull_request_source import normalize_issue_comments
from route_presentation import (
    abandoned_gate_note,
    outstanding_gate_phrase,
    route_status_summary,
    status_headline,
)
from state import (
    STATUS_COMMENT_REVISION,
    load_dashboard_state_cache,
    load_status_comment_rollout_state,
    save_status_comment_rollout_state,
)
from utils import markdown_escape, truncate
from utils import utc_now


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
RESPONSE_EXAMPLES = "(e.g. link a commit, explain why not, ask a follow-up)"


class StatusCommentDeferred(Exception):
    pass


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


def _bounded_report_url(pr: dict[str, Any], status_comment: str) -> str:
    report_url = status_report_url(pr, status_comment)
    if len(report_url) <= STATUS_REPORT_URL_MAX_CHARS:
        return report_url
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
    return report_url


def status_footer(
    pr: dict[str, Any],
    status_comment: str,
    *,
    override_route: str = "",
    terminal: bool = False,
) -> list[str]:
    report_url = _bounded_report_url(pr, status_comment)
    lines = [
        "<details>",
        "<summary>Status above doesn't look right?</summary>",
        "",
        "<br>",
        "",
    ]
    if not terminal:
        lines.append(
            "- **Just replied or pushed?** Anything around or after the refresh "
            "time above may not be picked up yet \u2014 give it a few minutes."
        )
    if override_route:
        lines.append(
            "- **Should this be with reviewers?** Comment "
            "`/dashboard route:reviewers` to route it to them."
        )
    report_lead = (
        "Anything wrong \u2014 including the routing?"
        if override_route
        else "Anything look wrong?"
    )
    lines.append(
        f"- **{report_lead}** [Report it]({report_url}) with what you expected; "
        "it helps us improve the dashboard."
    )
    lines.extend(["", "</details>"])
    return lines


def non_blocking_failure_summary(
    non_blocking_check_failures: Sequence[str], *, names_only: bool = False
) -> str:
    if not non_blocking_check_failures:
        return ""
    displayed_failures = non_blocking_check_failures[:NON_BLOCKING_CHECK_FAILURE_LIMIT]
    names = format_list([
        markdown_escape(truncate(name, NON_BLOCKING_CHECK_FAILURE_NAME_LIMIT))
        for name in displayed_failures
    ])
    if names_only:
        note = names
    elif len(non_blocking_check_failures) == 1:
        note = f"{names} is also failing but is not a required check."
    else:
        note = f"{names} are also failing but are not required checks."
    omitted_count = len(non_blocking_check_failures) - len(displayed_failures)
    if omitted_count:
        noun = "failure" if omitted_count == 1 else "failures"
        omitted_verb = "is" if omitted_count == 1 else "are"
        omitted = (
            f"{omitted_count} additional non-blocking check {noun} "
            f"{omitted_verb} not shown"
        )
        note = f"{note} ({omitted})" if names_only else f"{note} {omitted}."
    return note


def author_body(
    *,
    feedback_count: int,
    failing_count: int,
    non_blocking_failure_note: str,
    review_thread_urls: Sequence[str],
    top_level_feedback_urls: Sequence[str],
    held_gates: str = "",
) -> list[str]:
    noun = "item" if feedback_count == 1 else "items"
    if failing_count and feedback_count:
        checks_bullet = "- **Required checks are failing** \u2014 investigate the failures."
        if non_blocking_failure_note:
            checks_bullet += f" Note: {non_blocking_failure_note}"
        body = [
            "Two things need attention:",
            checks_bullet,
            f"- **{feedback_count} review {noun}** — respond to each {RESPONSE_EXAMPLES}:",
        ]
        body.extend(
            feedback_breakdown_lines(
                review_thread_urls, top_level_feedback_urls, indent="  "
            )
        )
        return body
    if feedback_count:
        body = [f"Respond to {feedback_count} review {noun} {RESPONSE_EXAMPLES}:"]
        body.extend(
            feedback_breakdown_lines(review_thread_urls, top_level_feedback_urls)
        )
        return body
    if failing_count:
        sentence = "Investigate required status check failures."
        if non_blocking_failure_note:
            sentence += f" Note: {non_blocking_failure_note}"
        return [sentence]
    if held_gates:
        return [
            f"Wait for {held_gates} to report; this pull request moves to "
            "reviewers once the results are clean."
        ]
    _, fallback_next_step = route_status_summary("author")
    return [fallback_next_step]


def is_terminal_pr(pr: dict[str, Any]) -> bool:
    return bool(pr.get("merged")) or (pr.get("state") or "").lower() == "closed"


def render_status_comment(
    pr: dict[str, Any],
    result: StoredDashboardResult | EvaluationFailure | None,
) -> str:
    last_updated = utc_now().strftime("%Y-%m-%d %H:%M UTC")
    state = (pr.get("state") or "").lower()
    facts = (
        result.facts
        if result is not None and result.facts is not None
        else DashboardFacts()
    )
    review_thread_urls = facts.author_action_review_thread_urls
    top_level_feedback_urls = facts.author_action_top_level_feedback_urls
    feedback_count = len(review_thread_urls) + len(top_level_feedback_urls)
    failing_count = facts.ci_failing_count or 0
    non_blocking_check_failures = facts.non_blocking_check_failures

    override_route = ""
    terminal = False
    body: list[str] = []

    if pr.get("merged"):
        headline = "Merged"
        terminal = True
    elif state == "closed":
        headline = "Closed"
        terminal = True
    elif pr.get("draft"):
        headline = status_headline("author")
        body = ["Move out of draft to request review."]
    elif result is None:
        headline = "Waiting on the pull request dashboard"
        body = ["Finish refreshing this pull request."]
    else:
        route = result.route
        conflicted = facts.conflicts == "yes"
        if route.value in PRE_REVIEW_ROUTES:
            override_route = route.value
        headline = status_headline(route)
        if route is DashboardRoute.AUTHOR:
            body = ["Resolve merge conflicts."] if conflicted else []
            if (
                not conflicted
                or feedback_count
                or failing_count
                or facts.route_held_for_gates
            ):
                author_actions = author_body(
                    feedback_count=feedback_count,
                    failing_count=failing_count,
                    non_blocking_failure_note=non_blocking_failure_summary(
                        non_blocking_check_failures
                    ),
                    review_thread_urls=review_thread_urls,
                    top_level_feedback_urls=top_level_feedback_urls,
                    held_gates=(
                        outstanding_gate_phrase(facts)
                        if facts.route_held_for_gates
                        else ""
                    ),
                )
                if body:
                    body.append("")
                body.extend(author_actions)
        else:
            _, next_step = route_status_summary(route)
            body = (
                ["Resolve merge conflicts, then merge when ready."]
                if conflicted and route is DashboardRoute.MAINTAINER
                else [next_step]
            )
            abandoned_gates = (
                abandoned_gate_note(facts)
                if facts.route_hold_expired
                else ""
            )
            if abandoned_gates:
                body.extend(["", abandoned_gates])
            if failing_count:
                check_summary = (
                    "1 required status check is failing."
                    if failing_count == 1
                    else f"{failing_count} required status checks are failing."
                )
                body.extend(["", f"**Also blocked by:** {check_summary}"])
            if non_blocking_check_failures:
                label = (
                    "Non-blocking check failure"
                    if len(non_blocking_check_failures) == 1
                    else "Non-blocking check failures"
                )
                names = non_blocking_failure_summary(
                    non_blocking_check_failures, names_only=True
                )
                body.extend(["", f"**{label}:** {names}"])
            if conflicted and route is not DashboardRoute.MAINTAINER:
                body.extend(["", "**Also blocked by:** Merge conflicts."])

    lines = [
        STATUS_MARKER,
        f"<!-- pull-request-dashboard-status-revision:{STATUS_COMMENT_REVISION} -->",
        "## Pull request dashboard status",
        "",
        f"**{headline}** \u00b7 refreshed {last_updated}",
    ]
    optional_markers: list[str] = []
    episode_id = facts.author_nudge_episode_id or ""
    if (
        result is not None
        and result.route is DashboardRoute.AUTHOR
        and episode_id
    ):
        optional_markers.append(author_nudge_episode_marker(episode_id))
    bound_command_id = facts.dashboard_override_bound_command_id
    bound_head = facts.dashboard_override_head_sha
    if (
        facts.dashboard_override_cleared_by_feedback
        and bound_command_id
        and bound_head
    ):
        optional_markers.append(
            override_ack_marker(
                bound_command_id,
                bound_head,
                facts.dashboard_override_since,
            ),
        )
        optional_markers.append(
            reviewer_handoff_cleared_marker(bound_command_id, bound_head),
        )
    lines[2:2] = optional_markers

    if body:
        lines.append("")
        lines.extend(body)

    status_comment = "\n".join(lines)
    lines.append("")
    lines.extend(
        status_footer(
            pr,
            status_comment,
            override_route=override_route,
            terminal=terminal,
        )
    )
    lines.append("")
    return "\n".join(lines)


def format_list(values: list[str]) -> str:
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        return f"{values[0]} and {values[1]}"
    return f"{', '.join(values[:-1])}, and {values[-1]}"


def feedback_breakdown_lines(
    review_thread_urls: Sequence[str],
    top_level_feedback_urls: Sequence[str],
    indent: str = "",
) -> list[str]:
    feedback_count = len(review_thread_urls) + len(top_level_feedback_urls)
    sections = (
        ("Inline threads", review_thread_urls),
        ("Top-level threads", top_level_feedback_urls),
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
    return lines


def managed_status_comments(repo: str, pr_number: int) -> list[dict[str, Any]]:
    comments = gh_api(
        f"/repos/{repo}/issues/{pr_number}/comments?per_page=100",
        paginate=True,
    )
    return [
        comment
        for comment in comments or []
        if (comment.get("performed_via_github_app") or {}).get("slug") == DASHBOARD_APP_SLUG
        and STATUS_MARKER in (comment.get("body") or "")
    ]


def upsert_status_comment(
    repo: str,
    pr_number: int,
    body: str,
    *,
    create: bool = True,
    locked: bool = False,
    preserve_clearance: bool = False,
) -> None:
    comments = managed_status_comments(repo, pr_number)
    if comments:
        comment = comments[0]
        if preserve_clearance:
            command_id, head_sha = status_reviewer_handoff_clearance(comments)
            clearance_marker = reviewer_handoff_cleared_marker(command_id, head_sha)
            acknowledged_id, acknowledged_head, since, _ = acknowledged_override(
                normalize_issue_comments(comments)
            )
            acknowledgement_marker = override_ack_marker(
                command_id,
                head_sha,
                (
                    since
                    if (acknowledged_id, acknowledged_head)
                    == (command_id, head_sha)
                    else ""
                ),
            )
            preserved_markers = [
                marker
                for marker in (acknowledgement_marker, clearance_marker)
                if command_id and head_sha and marker not in body
            ]
            if preserved_markers:
                lines = body.splitlines()
                lines[2:2] = preserved_markers
                body = "\n".join(lines)
        if locked and (comment.get("body") != body or len(comments) > 1):
            raise StatusCommentDeferred(
                f"PR #{pr_number} is locked; deferring terminal status comment"
            )
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

    if not create:
        print(
            f"PR #{pr_number} has no status comment to update; skipping creation",
            file=sys.stderr,
        )
        return

    print(f"creating PR #{pr_number} status comment", file=sys.stderr)
    run_gh([
        "gh", "api", "--method", "POST",
        f"repos/{repo}/issues/{pr_number}/comments",
        "-f", f"body={body}",
    ])


def publish_pr_status(
    repo: str,
    pr_number: int,
    dashboard_state: DashboardState,
) -> None:
    pr = gh_api(f"/repos/{repo}/pulls/{pr_number}")
    result = dashboard_state.result_for(pr_number)
    terminal = is_terminal_pr(pr)
    # A terminal status only exists to move an already published comment to its
    # final state. Creating one instead would announce a merge or close on a
    # pull request the dashboard never commented on.
    upsert_status_comment(
        repo,
        pr_number,
        render_status_comment(pr, result),
        create=not terminal,
        locked=terminal and bool(pr.get("locked")),
        preserve_clearance=True,
    )


def update_targeted_status_comment_from_state(repo: str, pr_number: int) -> list[str]:
    dashboard_state = load_dashboard_state_cache()
    if dashboard_state is None:
        print("dashboard result state not found; skipping PR status comment", file=sys.stderr)
        return []

    rollout_state = load_status_comment_rollout_state()
    pending_pr_numbers = list(
        dict.fromkeys(rollout_state.get("pending_pr_numbers") or [])
    )
    if pr_number not in pending_pr_numbers:
        return []
    try:
        publish_pr_status(repo, pr_number, dashboard_state)
    except StatusCommentDeferred as e:
        print(e, file=sys.stderr)
        return []
    except Exception as e:
        return [f"PR #{pr_number}: {e}"]

    pending_pr_numbers.remove(pr_number)
    rollout_state["pending_pr_numbers"] = pending_pr_numbers
    if not pending_pr_numbers:
        rollout_state["completed_revision"] = rollout_state["target_revision"]
    save_status_comment_rollout_state(rollout_state)
    return []


def prepare_rollout_state(
    rollout_state: dict[str, Any],
    open_pr_numbers: set[int],
) -> dict[str, Any]:
    if rollout_state.get("target_revision") != STATUS_COMMENT_REVISION:
        return {
            "target_revision": STATUS_COMMENT_REVISION,
            "completed_revision": int(rollout_state.get("completed_revision") or 0),
            "pending_pr_numbers": sorted(open_pr_numbers),
            "draft_reconciliation_cursor": int(
                rollout_state.get("draft_reconciliation_cursor") or 0
            ),
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
        "draft_reconciliation_cursor": int(
            rollout_state.get("draft_reconciliation_cursor") or 0
        ),
    }


def reconcile_missing_draft_status_comments(
    repo: str,
    rollout_state: dict[str, Any],
    open_draft_pr_numbers: set[int],
) -> list[str]:
    if not open_draft_pr_numbers:
        return []
    cursor = int(rollout_state.get("draft_reconciliation_cursor") or 0)
    ordered_numbers = sorted(open_draft_pr_numbers)
    candidates = (
        [number for number in ordered_numbers if number > cursor]
        + [number for number in ordered_numbers if number <= cursor]
    )[:STATUS_COMMENT_ROLLOUT_BATCH_SIZE]
    pending_pr_numbers = rollout_state["pending_pr_numbers"]
    pending_set = set(pending_pr_numbers)
    errors: list[str] = []
    for number in candidates:
        if number in pending_set:
            continue
        # A draft whose comments cannot be listed must not stop the rollout, so
        # a failed lookup is reported like a failed publish and the loop goes on.
        try:
            comments = managed_status_comments(repo, number)
        except Exception as e:
            errors.append(f"PR #{number}: draft comment lookup failed: {e}")
            continue
        if not comments:
            pending_pr_numbers.append(number)
            pending_set.add(number)
    if candidates:
        rollout_state["draft_reconciliation_cursor"] = candidates[-1]
    return errors


def update_status_comments_from_state(
    repo: str,
    open_pr_numbers: set[int],
    excluded_pr_numbers: set[int] | None = None,
    *,
    open_draft_pr_numbers: set[int] | None = None,
) -> list[str]:
    dashboard_state = load_dashboard_state_cache()
    if dashboard_state is None:
        print("dashboard result state not found; skipping PR status comment", file=sys.stderr)
        return []

    saved_rollout_state = load_status_comment_rollout_state()
    queued_pr_numbers = list(
        dict.fromkeys(saved_rollout_state.get("pending_pr_numbers") or [])
    )
    rollout_state = prepare_rollout_state(saved_rollout_state, open_pr_numbers)
    errors = reconcile_missing_draft_status_comments(
        repo,
        rollout_state,
        open_draft_pr_numbers or set(),
    )
    queued_pr_number_set = set(queued_pr_numbers)
    pending_pr_numbers = queued_pr_numbers + [
        number
        for number in rollout_state["pending_pr_numbers"]
        if number not in queued_pr_number_set
    ]
    excluded_pr_numbers = excluded_pr_numbers or set()
    rollout_pr_numbers = [
        number
        for number in pending_pr_numbers
        if number not in excluded_pr_numbers
    ][:STATUS_COMMENT_ROLLOUT_BATCH_SIZE]
    successful_pr_numbers: set[int] = set()
    deferred_pr_numbers: set[int] = set()
    for number in rollout_pr_numbers:
        try:
            publish_pr_status(repo, number, dashboard_state)
        except StatusCommentDeferred as e:
            print(e, file=sys.stderr)
            deferred_pr_numbers.add(number)
        except Exception as e:
            errors.append(f"PR #{number}: {e}")
        else:
            successful_pr_numbers.add(number)

    rollout_state["pending_pr_numbers"] = [
        number
        for number in pending_pr_numbers
        if number not in successful_pr_numbers
        and number not in deferred_pr_numbers
    ] + [
        number for number in rollout_pr_numbers if number in deferred_pr_numbers
    ]
    pending = rollout_state["pending_pr_numbers"]
    if not pending:
        rollout_state["completed_revision"] = STATUS_COMMENT_REVISION
    save_status_comment_rollout_state(rollout_state)
    return errors
