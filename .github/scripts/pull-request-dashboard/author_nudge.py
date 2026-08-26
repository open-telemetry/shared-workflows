#!/usr/bin/env python3
"""Track and post reminders for pull requests waiting on authors."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import sys
from typing import Any

from dashboard_contracts import (
    DashboardRoute,
    DashboardState,
    EvaluationFailure,
    StoredDashboardResult,
)
from github_cli import (
    gh_api,
    gh_graphql,
    run_gh,
)
from dashboard_override import DASHBOARD_COMMAND_PREFIX
from pr_status_comment import (
    DASHBOARD_APP_SLUG,
    managed_status_comments,
    publish_pr_status,
)
from state import (
    load_author_nudges,
    load_dashboard_state_cache,
    save_author_nudges,
)
from routing_snapshot import fetch_routing_snapshot
from utils import format_ts, parse_ts


NUDGE_AFTER = timedelta(weeks=1)
LEGACY_NUDGE_RECOVERY_WINDOW = timedelta(days=1)
NUDGE_MARKER_PREFIX = "<!-- pull-request-dashboard-author-nudge:"
COMPLETED_NUDGE_MARKER_PREFIX = (
    "<!-- pull-request-dashboard-author-nudge-completed:"
)
NUDGE_MARKER_RE = re.compile(
    r"<!-- pull-request-dashboard-author-nudge:([^ ]+) -->"
)
LEGACY_EPISODE_PREFIX = "legacy-nudge:"
COMMENT_MINIMIZATION_STATE_QUERY = """
query($id: ID!) {
    node(id: $id) {
        ... on IssueComment {
            isMinimized
            minimizedReason
        }
    }
}
"""
MINIMIZE_COMMENT_MUTATION = """
mutation($id: ID!) {
    minimizeComment(input: {subjectId: $id, classifier: OUTDATED}) {
        minimizedComment {
            isMinimized
        }
    }
}
"""
UNMINIMIZE_COMMENT_MUTATION = """
mutation($id: ID!) {
    unminimizeComment(input: {subjectId: $id}) {
        unminimizedComment {
            isMinimized
        }
    }
}
"""


def nudge_marker(episode_id: str) -> str:
    return f"{NUDGE_MARKER_PREFIX}{episode_id} -->"


def completed_nudge_marker(episode_id: str) -> str:
    return f"{COMPLETED_NUDGE_MARKER_PREFIX}{episode_id} -->"


def legacy_episode_id(nudged_at: str) -> str:
    return f"{LEGACY_EPISODE_PREFIX}{nudged_at}"


def display_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def completion_only(entry: dict[str, Any]) -> dict[str, Any] | None:
    completions = list(entry.get("completions") or [])
    return {"completions": completions} if completions else None


def queue_completion(
    completions: list[dict[str, Any]],
    episode_id: str,
    completed_at: datetime,
    kind: str,
) -> None:
    if not any(item.get("episode_id") == episode_id for item in completions):
        completions.append({
            "episode_id": episode_id,
            "completed_at": format_ts(completed_at),
            "kind": kind,
        })


def waiting_on_author(
    result: StoredDashboardResult | EvaluationFailure | None,
) -> bool:
    return (
        isinstance(result, StoredDashboardResult)
        and result.route is DashboardRoute.AUTHOR
    )


def plan_nudge(
    result: StoredDashboardResult | EvaluationFailure | None,
    previous: dict[str, Any] | None,
    now: datetime,
) -> tuple[bool, dict[str, Any] | None]:
    entry = dict(previous or {})
    nudged_at = entry.get("nudged_at") or ""
    if isinstance(result, EvaluationFailure):
        return False, entry or None
    if result is None:
        if nudged_at:
            completions = list(entry.get("completions") or [])
            episode_id = (
                entry.get("episode_id")
                or legacy_episode_id(nudged_at)
            )
            queue_completion(
                completions,
                episode_id,
                now,
                "routing_changed",
            )
            return False, {"completions": completions}
        return False, completion_only(entry)
    facts = result.facts
    current_episode_id = facts.author_nudge_episode_id or ""
    if not waiting_on_author(result):
        if result.route in (
            DashboardRoute.APPROVER,
            DashboardRoute.MAINTAINER,
        ):
            completion_kind = "left_author"
        else:
            return False, completion_only(entry)
        if nudged_at:
            episode_id = (
                entry.get("episode_id")
                or legacy_episode_id(nudged_at)
            )
            completions = list(entry.get("completions") or [])
            queue_completion(completions, episode_id, now, completion_kind)
            return False, {"completions": completions}
        return False, completion_only(entry)
    completions = list(entry.get("completions") or [])
    previous_episode_id = entry.get("episode_id") or ""
    if current_episode_id and previous_episode_id and (
        current_episode_id != previous_episode_id
    ):
        if nudged_at:
            queue_completion(
                completions,
                previous_episode_id,
                now,
                "routing_changed",
            )
        entry = {
            "waiting_since": format_ts(now),
            "nudged_at": "",
            "episode_id": current_episode_id,
        }
        if completions:
            entry["completions"] = completions
        return False, entry
    if current_episode_id:
        entry["episode_id"] = current_episode_id
    if nudged_at:
        return False, entry

    waiting_since = parse_ts(entry.get("waiting_since") or "")
    if waiting_since is None:
        entry = {
            "waiting_since": format_ts(now),
            "nudged_at": "",
        }
        if current_episode_id:
            entry["episode_id"] = current_episode_id
        if completions:
            entry["completions"] = completions
        return False, entry
    return now - waiting_since >= NUDGE_AFTER, entry


def existing_nudge_comment(
    repo: str,
    pr_number: int,
    episode_id: str,
) -> dict[str, Any] | None:
    marker = nudge_marker(episode_id)
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
            and marker in (comment.get("body") or "")
        ),
        None,
    )


def recover_legacy_nudge_episode_id(
    repo: str,
    pr_number: int,
    legacy_id: str,
) -> str:
    if not legacy_id.startswith(LEGACY_EPISODE_PREFIX):
        return ""
    expected_created_at = parse_ts(legacy_id.removeprefix(LEGACY_EPISODE_PREFIX))
    if expected_created_at is None:
        return ""
    comments = gh_api(
        f"/repos/{repo}/issues/{pr_number}/comments?per_page=100",
        paginate=True,
    )
    best_match: tuple[timedelta, str] | None = None
    for comment in comments or []:
        if (comment.get("performed_via_github_app") or {}).get("slug") != (
            DASHBOARD_APP_SLUG
        ):
            continue
        created_at = parse_ts(comment.get("created_at") or "")
        match = NUDGE_MARKER_RE.search(comment.get("body") or "")
        if created_at is None or match is None:
            continue
        distance = abs(created_at - expected_created_at)
        if distance > LEGACY_NUDGE_RECOVERY_WINDOW:
            continue
        candidate = (distance, match.group(1))
        if best_match is None or candidate[0] < best_match[0]:
            best_match = candidate
    return best_match[1] if best_match else ""


def render_nudge(
    author: str,
    status_url: str,
    episode_id: str,
) -> str:
    return "\n".join([
        nudge_marker(episode_id),
        f"Hi @{author} — just a friendly reminder that this pull request is "
        f"waiting on you. The [dashboard status comment]({status_url}) has the "
        "open items and is kept current.",
        "",
        "- Replying is enough to hand it off — answer, explain why no change is "
        "needed, or ask a follow-up. The dashboard routes it onward once nothing "
        "on the list is waiting on you.",
        "- To hand it back for any other reason, including the dashboard getting "
        f"this wrong, comment `{DASHBOARD_COMMAND_PREFIX} route:reviewers`.",
        "",
    ])


def render_completed_nudge(
    original_body: str,
    status_url: str,
    episode_id: str,
    completed_at: datetime,
    kind: str = "left_author",
) -> str:
    if kind == "left_author":
        note = (
            f"_Outdated as of {display_time(completed_at)}: this pull request is "
            "no longer waiting on you. See the "
            f"[dashboard status comment]({status_url}) for its current routing._"
        )
    else:
        note = (
            f"_Outdated as of {display_time(completed_at)}: this reminder no "
            "longer reflects the current dashboard state. Check the "
            f"[dashboard status comment]({status_url}) to see whether action is "
            "needed._"
        )
    return "\n\n".join([
        original_body.rstrip(),
        completed_nudge_marker(episode_id),
        note,
    ]) + "\n"


def comment_minimization_reason(node_id: str) -> str:
    data = gh_graphql(COMMENT_MINIMIZATION_STATE_QUERY, {"id": node_id})
    node = (data.get("data") or {}).get("node")
    if not isinstance(node, dict) or "isMinimized" not in node:
        raise RuntimeError("author nudge minimization state not found")
    if not node["isMinimized"]:
        return ""
    reason = node.get("minimizedReason")
    if not isinstance(reason, str) or not reason:
        raise RuntimeError("author nudge minimization reason not found")
    return reason.upper().replace("-", "_")


def unminimize_comment(node_id: str) -> None:
    data = gh_graphql(UNMINIMIZE_COMMENT_MUTATION, {"id": node_id})
    unminimized = (
        ((data.get("data") or {}).get("unminimizeComment") or {})
        .get("unminimizedComment")
        or {}
    )
    if unminimized.get("isMinimized") is not False:
        raise RuntimeError("author nudge was not unminimized")


def minimize_comment(node_id: str) -> None:
    data = gh_graphql(MINIMIZE_COMMENT_MUTATION, {"id": node_id})
    minimized = (
        ((data.get("data") or {}).get("minimizeComment") or {})
        .get("minimizedComment")
        or {}
    )
    if not minimized.get("isMinimized"):
        raise RuntimeError("author nudge was not marked outdated")


def ensure_nudge(
    repo: str,
    pr_number: int,
    result: StoredDashboardResult,
    dashboard_state: DashboardState,
    waiting_since: str,
    now: datetime,
) -> str | None:
    episode_id = result.facts.author_nudge_episode_id or ""
    if not episode_id:
        raise RuntimeError(f"author nudge episode not found for PR #{pr_number}")
    existing = existing_nudge_comment(repo, pr_number, episode_id)
    if existing:
        return existing.get("created_at") or format_ts(now)

    pr = gh_api(f"/repos/{repo}/pulls/{pr_number}") or {}
    if pr.get("state") != "open" or pr.get("draft"):
        return None

    publish_pr_status(repo, pr_number, dashboard_state)
    status_comments = managed_status_comments(repo, pr_number)
    if not status_comments or not status_comments[0].get("html_url"):
        raise RuntimeError(f"dashboard status comment not found for PR #{pr_number}")
    author = result.facts.author.strip()
    author = author or str((pr.get("user") or {}).get("login") or "").strip()
    if not author:
        raise RuntimeError(f"author not found for PR #{pr_number}")
    run_gh([
        "gh", "api", "--method", "POST",
        f"repos/{repo}/issues/{pr_number}/comments",
        "-f",
        f"body={render_nudge(author, status_comments[0]['html_url'], episode_id)}",
    ])
    return format_ts(now)


def ensure_nudge_completed(
    repo: str,
    pr_number: int,
    episode_id: str,
    dashboard_state: DashboardState,
    completed_at: datetime,
    kind: str = "left_author",
) -> None:
    comment = existing_nudge_comment(repo, pr_number, episode_id)
    if comment is None:
        return
    original_body = comment.get("body") or ""
    comment_id = comment.get("id")
    node_id = comment.get("node_id") or ""
    if not comment_id:
        raise RuntimeError(f"author nudge comment id not found for PR #{pr_number}")
    if not node_id:
        raise RuntimeError(f"author nudge comment node id not found for PR #{pr_number}")

    if completed_nudge_marker(episode_id) not in original_body:
        status_comments = managed_status_comments(repo, pr_number)
        if not status_comments:
            publish_pr_status(repo, pr_number, dashboard_state)
            status_comments = managed_status_comments(repo, pr_number)
        if not status_comments or not status_comments[0].get("html_url"):
            raise RuntimeError(f"dashboard status comment not found for PR #{pr_number}")
        body = render_completed_nudge(
            original_body,
            status_comments[0]["html_url"],
            episode_id,
            completed_at,
            kind,
        )
        run_gh([
            "gh", "api", "--method", "PATCH",
            f"repos/{repo}/issues/comments/{comment_id}",
            "-f",
            f"body={body}",
        ])

    minimized_reason = comment_minimization_reason(node_id)
    if minimized_reason != "OUTDATED":
        if minimized_reason:
            unminimize_comment(node_id)
        minimize_comment(node_id)


def record_author_nudge_observation(
    pr_number: int,
    result: StoredDashboardResult | None,
    now: datetime,
    *,
    prepare_due: bool = False,
) -> None:
    updated = dict(load_author_nudges())
    key = str(pr_number)
    due, entry = plan_nudge(result, updated.get(key), now)
    if due and prepare_due and entry is not None:
        facts = result.facts if result is not None else None
        head_sha = facts.head_sha if facts is not None else ""
        routing_fingerprint = (
            facts.routing_input_fingerprint
            if facts is not None
            else ""
        )
        if head_sha and routing_fingerprint:
            episode_id = facts.author_nudge_episode_id or ""
            entry = {
                **entry,
                "pending_at": format_ts(now),
                "head_sha": head_sha,
                "routing_input_fingerprint": routing_fingerprint,
            }
            if episode_id:
                entry["episode_id"] = episode_id
    if entry is None:
        updated.pop(key, None)
    else:
        updated[key] = entry
    save_author_nudges(updated)


def deliver_prepared_author_nudges(
    repo: str,
    now: datetime,
    retry_snapshot_path: Path | None = None,
    excluded_pr_numbers: set[int] | None = None,
) -> list[str]:
    dashboard_state = load_dashboard_state_cache()
    if dashboard_state is None:
        print("dashboard state not found; skipping author nudges", file=sys.stderr)
        return []
    excluded_pr_numbers = excluded_pr_numbers or set()
    updated = dict(load_author_nudges(retry_snapshot_path))
    errors: list[str] = []
    for key, entry in sorted(updated.items(), key=lambda item: int(item[0])):
        pr_number = int(key)
        if pr_number in excluded_pr_numbers:
            continue
        result = dashboard_state.result_for(pr_number)
        completions = list((entry or {}).get("completions") or [])
        remaining_completions: list[dict[str, Any]] = []
        for completion in completions:
            episode_id = completion.get("episode_id") or ""
            if episode_id.startswith(LEGACY_EPISODE_PREFIX):
                recovered_episode_id = recover_legacy_nudge_episode_id(
                    repo,
                    pr_number,
                    episode_id,
                )
                if recovered_episode_id:
                    completion = {
                        **completion,
                        "episode_id": recovered_episode_id,
                    }
                    episode_id = recovered_episode_id
                else:
                    print(
                        f"PR #{pr_number}: legacy author nudge comment not found; "
                        "discarding completion",
                        file=sys.stderr,
                    )
                    continue
            completed_at = parse_ts(completion.get("completed_at") or "")
            kind = completion.get("kind") or "left_author"
            if (
                not episode_id
                or completed_at is None
                or kind not in ("left_author", "routing_changed")
            ):
                errors.append(f"PR #{pr_number}: invalid pending nudge completion")
                remaining_completions.append(completion)
                continue
            try:
                ensure_nudge_completed(
                    repo,
                    pr_number,
                    episode_id,
                    dashboard_state,
                    completed_at,
                    kind,
                )
            except Exception as e:
                errors.append(f"PR #{pr_number}: {e}")
                remaining_completions.append(completion)
        entry = dict(entry or {})
        if remaining_completions:
            entry["completions"] = remaining_completions
        else:
            entry.pop("completions", None)
        if not entry.get("waiting_since") and not entry.get("pending_at"):
            if entry:
                updated[key] = entry
            else:
                updated.pop(key, None)
            continue
        if not (entry or {}).get("pending_at"):
            updated[key] = entry
            continue
        if not waiting_on_author(result):
            _due, reset_entry = plan_nudge(result, entry, now)
            if reset_entry is None:
                updated.pop(key, None)
            else:
                updated[key] = reset_entry
            continue
        try:
            snapshot = fetch_routing_snapshot(
                repo,
                pr_number,
            )
            expected_head = entry.get("head_sha") or ""
            expected_routing_fingerprint = entry.get("routing_input_fingerprint") or ""
            if snapshot.state != "OPEN" or snapshot.is_draft:
                completion_entry = completion_only(entry)
                if completion_entry is None:
                    updated.pop(key, None)
                else:
                    updated[key] = completion_entry
                continue
            if (
                not expected_head
                or snapshot.head_sha != expected_head
                or not expected_routing_fingerprint
                or snapshot.routing_input_fingerprint
                != expected_routing_fingerprint
            ):
                updated[key] = {
                    name: value
                    for name, value in entry.items()
                    if name not in (
                        "pending_at",
                        "head_sha",
                        "routing_input_fingerprint",
                    )
                }
                continue
            nudged_at = ensure_nudge(
                repo,
                pr_number,
                result,
                dashboard_state,
                entry.get("waiting_since") or "",
                now,
            )
        except Exception as e:
            errors.append(f"PR #{pr_number}: {e}")
            continue
        if nudged_at:
            episode_id = str(
                (
                    result.facts.author_nudge_episode_id
                    if result is not None
                    else ""
                )
                or entry.get("episode_id")
                or ""
            )
            updated[key] = {
                "waiting_since": entry.get("waiting_since") or "",
                "nudged_at": nudged_at,
            }
            if episode_id:
                updated[key]["episode_id"] = episode_id
            completions = list(entry.get("completions") or [])
            if completions:
                updated[key]["completions"] = completions
    save_author_nudges(updated)
    return errors
