#!/usr/bin/env python3
"""Bring dashboard author reminders on open pull requests up to date.

This is a maintenance sweep, run by hand, and nothing in the hourly dashboard
depends on it. It repairs reminders that the dashboard itself can no longer
reach, and rewrites live ones whenever their wording changes.

The dashboard collapses a reminder from its own state: routing leaves the
author, a completion is queued for that episode, and the next delivery patches
and minimizes the comment. A reminder whose state entry is gone is therefore
stranded forever, because nothing records that the comment exists.

The sweep reads the pull request instead. The status comment headline says
whether routing still sits at the author, and its episode marker names the live
reminder; every other reminder on that pull request is stale, whatever the
dashboard remembers. That marker is written from nudge state too, so it is
missing on exactly the pull requests this sweep exists for, and the newest
reminder stands in as the live one unless it has already been finished.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import sys
from typing import Any

from author_nudge import (
    NUDGE_MARKER_RE,
    comment_minimization_reason,
    completed_nudge_marker,
    minimize_comment,
    render_completed_nudge,
    render_nudge,
    unminimize_comment,
)
from github_cli import (
    gh_api,
    list_open_prs,
    normalize_repo,
    run_gh,
)
from pr_status_comment import (
    STATUS_MARKER,
    is_dashboard_app_comment,
    status_author_nudge_episode_id,
)
from route_presentation import status_headline
from utils import utc_now


AUTHOR_STATUS_HEADLINE = f"**{status_headline('author')}** "


def pull_request_comments(repo: str, pr_number: int) -> list[dict[str, Any]]:
    return gh_api(
        f"/repos/{repo}/issues/{pr_number}/comments?per_page=100",
        paginate=True,
    ) or []


def nudge_comments(
    comments: list[dict[str, Any]],
) -> list[tuple[str, dict[str, Any]]]:
    found: list[tuple[str, dict[str, Any]]] = []
    for comment in comments:
        if not is_dashboard_app_comment(comment):
            continue
        match = NUDGE_MARKER_RE.search(comment.get("body") or "")
        if match:
            found.append((match.group(1), comment))
    found.sort(key=lambda found: (found[1].get("created_at") or "", found[1].get("id") or 0))
    return found


def dashboard_status_comment(
    comments: list[dict[str, Any]],
) -> dict[str, Any] | None:
    for comment in comments:
        if (
            is_dashboard_app_comment(comment)
            and STATUS_MARKER in (comment.get("body") or "")
        ):
            return comment
    return None


def waiting_on_author(status_body: str) -> bool:
    return any(
        line.startswith(AUTHOR_STATUS_HEADLINE)
        for line in status_body.splitlines()
    )


def live_nudge_index(
    nudges: list[tuple[str, dict[str, Any]]],
    live_episode_id: str,
) -> int:
    for index, (episode_id, _) in enumerate(nudges):
        if episode_id and episode_id == live_episode_id:
            return index
    if live_episode_id:
        return -1
    # The dashboard only ever keeps one reminder open, so when the status
    # comment has no episode marker, the newest reminder stands in for it. A
    # newest reminder that is already finished means the pull request has no
    # live one, whatever its routing says, and every older reminder is stale.
    if nudges:
        episode_id, comment = nudges[-1]
        if completed_nudge_marker(episode_id) not in (comment.get("body") or ""):
            return len(nudges) - 1
    return -1


def patch_comment_body(repo: str, comment_id: int, body: str) -> None:
    run_gh([
        "gh", "api", "--method", "PATCH",
        f"repos/{repo}/issues/comments/{comment_id}",
        "-f",
        f"body={body}",
    ])


def collapse_stale_nudge(
    repo: str,
    pr_number: int,
    comment: dict[str, Any],
    episode_id: str,
    status_url: str,
    completed_at: datetime,
    kind: str,
    dry_run: bool,
) -> str:
    comment_id = comment.get("id")
    node_id = comment.get("node_id") or ""
    if not comment_id:
        raise RuntimeError(f"author nudge comment id not found for PR #{pr_number}")
    if not node_id:
        raise RuntimeError(f"author nudge comment node id not found for PR #{pr_number}")
    body = comment.get("body") or ""
    steps: list[str] = []

    if completed_nudge_marker(episode_id) not in body:
        steps.append("noted as outdated")
        if not dry_run:
            patch_comment_body(
                repo,
                comment_id,
                render_completed_nudge(
                    body,
                    status_url,
                    episode_id,
                    completed_at,
                    kind,
                ),
            )

    minimization_reason = comment_minimization_reason(node_id)
    if minimization_reason != "OUTDATED":
        steps.append("collapsed")
        if not dry_run:
            # A reminder hidden for some other reason still has to end up
            # classified as outdated, which takes restoring it first.
            if minimization_reason:
                unminimize_comment(node_id)
            minimize_comment(node_id)

    if not steps:
        return ""
    return f"PR #{pr_number}: stale reminder {comment_id} {' and '.join(steps)}"


def refresh_live_nudge(
    repo: str,
    pr_number: int,
    comment: dict[str, Any],
    episode_id: str,
    author: str,
    status_url: str,
    dry_run: bool,
) -> str:
    comment_id = comment.get("id")
    if not comment_id:
        raise RuntimeError(f"author nudge comment id not found for PR #{pr_number}")
    if not author:
        raise RuntimeError(f"author not found for PR #{pr_number}")
    body = render_nudge(author, status_url, episode_id)
    if (comment.get("body") or "") == body:
        return ""
    if not dry_run:
        patch_comment_body(repo, comment_id, body)
    return f"PR #{pr_number}: live reminder {comment_id} rewritten"


def sweep_pull_request(
    repo: str,
    pull: dict[str, Any],
    now: datetime,
    dry_run: bool,
) -> list[str]:
    pr_number = int(pull["number"])
    comments = pull_request_comments(repo, pr_number)
    nudges = nudge_comments(comments)
    if not nudges:
        return []

    # Without a status comment the sweep can neither read routing nor link one,
    # and the dashboard has not published this pull request yet.
    status = dashboard_status_comment(comments)
    status_url = (status or {}).get("html_url") or ""
    if not status_url:
        print(
            f"PR #{pr_number}: no dashboard status comment; "
            f"left {len(nudges)} reminder(s) alone",
            file=sys.stderr,
        )
        return []

    waiting = waiting_on_author(status.get("body") or "")
    live_index = (
        live_nudge_index(nudges, status_author_nudge_episode_id(comments))
        if waiting
        else -1
    )
    author = (pull.get("author") or {}).get("login") or ""
    actions: list[str] = []
    for index, (episode_id, comment) in enumerate(nudges):
        if index == live_index:
            action = refresh_live_nudge(
                repo,
                pr_number,
                comment,
                episode_id,
                author,
                status_url,
                dry_run,
            )
        else:
            action = collapse_stale_nudge(
                repo,
                pr_number,
                comment,
                episode_id,
                status_url,
                now,
                # A pull request that still waits on its author is owed a note
                # saying only this episode is over, not that it can be ignored.
                "routing_changed" if waiting else "left_author",
                dry_run,
            )
        if action:
            actions.append(action)
    return actions


def refresh_author_nudges(repo: str, dry_run: bool) -> int:
    now = utc_now()
    pulls = sorted(list_open_prs(repo), key=lambda pull: int(pull["number"]))
    print(f"{repo}: sweeping {len(pulls)} open pull request(s)", file=sys.stderr)
    actions: list[str] = []
    errors: list[str] = []
    for pull in pulls:
        try:
            for action in sweep_pull_request(repo, pull, now, dry_run):
                print(action, file=sys.stderr)
                actions.append(action)
        except Exception as e:
            errors.append(f"PR #{pull.get('number')}: {e}")

    prefix = "would change" if dry_run else "changed"
    print(f"{repo}: {prefix} {len(actions)} reminder(s)", file=sys.stderr)
    if not errors:
        return 0
    print(f"{repo}: author reminder sweep failed:", file=sys.stderr)
    print("\n".join(errors), file=sys.stderr)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="target repository name")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would change without writing to GitHub",
    )
    args = parser.parse_args()
    return refresh_author_nudges(normalize_repo(args.repo), args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
