"""Deliver dashboard command replies from accepted dashboard state."""

from __future__ import annotations

from dashboard_override import command_reply_exists, render_command_reply
from github_cli import gh_api, run_gh
from pull_request_source import normalize_issue_comments
from state import load_dashboard_state_cache


def deliver_dashboard_command_replies(
    repo: str,
    failed_pr_numbers: set[int] | None = None,
    only_pr_number: int | None = None,
) -> list[str]:
    dashboard_state = load_dashboard_state_cache()
    if dashboard_state is None:
        return []
    errors: list[str] = []
    for result in dashboard_state.results:
        if only_pr_number is not None and result.pr_number != only_pr_number:
            continue
        replies = tuple(
            reply
            for reply in result.facts.dashboard_command_replies
            if reply.kind != "cleared_by_feedback"
        )
        if not replies:
            continue
        pr_number = result.pr_number
        try:
            comments = normalize_issue_comments(gh_api(
                f"/repos/{repo}/issues/{pr_number}/comments?per_page=100",
                paginate=True,
            ))
        except Exception as error:
            errors.append(f"PR #{pr_number}: {error}")
            if failed_pr_numbers is not None:
                failed_pr_numbers.add(pr_number)
            continue
        for reply in replies:
            try:
                if command_reply_exists(
                    comments,
                    reply,
                ):
                    continue
                run_gh([
                    "gh",
                    "api",
                    "--method",
                    "POST",
                    f"repos/{repo}/issues/{pr_number}/comments",
                    "-f",
                    f"body={render_command_reply(reply)}",
                ])
            except Exception as error:
                errors.append(f"PR #{pr_number}: {error}")
                if failed_pr_numbers is not None:
                    failed_pr_numbers.add(pr_number)
    return errors
