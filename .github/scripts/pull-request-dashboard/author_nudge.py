#!/usr/bin/env python3
"""Track and post reminders for pull requests waiting on authors."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import sys
from typing import Any

from github_cli import gh_api, run_gh
from pr_status_comment import (
    DASHBOARD_APP_SLUG,
    managed_status_comments,
    publish_pr_status,
)
from state import (
    author_nudge_state_path,
    load_author_nudges,
    load_dashboard_state_cache,
    save_author_nudges,
)
from utils import format_ts, parse_ts


NUDGE_AFTER = timedelta(weeks=1)
NUDGE_MARKER_PREFIX = "<!-- pull-request-dashboard-author-nudge:"


def nudge_marker(waiting_since: str) -> str:
    return f"{NUDGE_MARKER_PREFIX}{waiting_since} -->"


def plan_nudge(
    result: dict[str, Any] | None,
    previous: dict[str, Any] | None,
    now: datetime,
) -> tuple[bool, dict[str, Any] | None]:
    entry = dict(previous or {})
    nudged_at = entry.get("nudged_at") or ""
    if result and (
        result.get("failed")
        or result.get("route") in ("transient-failure", "unknown")
    ):
        return False, entry or None
    if not result or result.get("route") != "author":
        return False, None
    if nudged_at:
        return False, entry

    waiting_since = parse_ts(entry.get("waiting_since") or "")
    if waiting_since is None:
        return False, {
            "waiting_since": format_ts(now),
            "nudged_at": "",
        }
    return now - waiting_since >= NUDGE_AFTER, entry


def existing_nudge_comment(
    repo: str,
    pr_number: int,
    waiting_since: str,
) -> dict[str, Any] | None:
    marker = nudge_marker(waiting_since)
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


def render_nudge(author: str, status_url: str, waiting_since: str) -> str:
    return "\n".join([
        nudge_marker(waiting_since),
        f"@{author}, this pull request has been waiting on your follow-up for one week.",
        "",
        f"See the [dashboard status comment]({status_url}) for the remaining items.",
        "",
    ])


def ensure_nudge(
    repo: str,
    pr_number: int,
    result: dict[str, Any],
    dashboard_state: dict[str, Any],
    waiting_since: str,
    now: datetime,
) -> str | None:
    existing = existing_nudge_comment(repo, pr_number, waiting_since)
    if existing:
        return existing.get("created_at") or format_ts(now)

    pr = gh_api(f"/repos/{repo}/pulls/{pr_number}") or {}
    if pr.get("state") != "open" or pr.get("draft"):
        return None

    publish_pr_status(repo, pr_number, dashboard_state)
    status_comments = managed_status_comments(repo, pr_number)
    if not status_comments or not status_comments[0].get("html_url"):
        raise RuntimeError(f"dashboard status comment not found for PR #{pr_number}")
    author = str(((result.get("facts") or {}).get("author") or "")).strip()
    author = author or str((pr.get("user") or {}).get("login") or "").strip()
    if not author:
        raise RuntimeError(f"author not found for PR #{pr_number}")
    run_gh([
        "gh", "api", "--method", "POST",
        f"repos/{repo}/issues/{pr_number}/comments",
        "-f", f"body={render_nudge(author, status_comments[0]['html_url'], waiting_since)}",
    ])
    return format_ts(now)


def record_author_nudge_observation(
    pr_number: int,
    result: dict[str, Any] | None,
    now: datetime,
    *,
    prepare_due: bool = False,
) -> None:
    updated = dict(load_author_nudges())
    key = str(pr_number)
    due, entry = plan_nudge(result, updated.get(key), now)
    if due and prepare_due and entry is not None:
        facts = (result or {}).get("facts") or {}
        head_sha = facts.get("head_sha") or ""
        source_fingerprint = facts.get("source_fingerprint") or ""
        if head_sha and source_fingerprint:
            entry = {
                **entry,
                "pending_at": format_ts(now),
                "head_sha": head_sha,
                "source_fingerprint": source_fingerprint,
            }
    if entry is None:
        updated.pop(key, None)
    else:
        updated[key] = entry
    save_author_nudges(updated)


def current_source_fingerprint(
    repo: str,
    pr_number: int,
    non_blocking_check_patterns: list[str],
) -> tuple[dict[str, Any], str]:
    from dashboard import dashboard_source_fingerprint, fetch_pr_raw

    owner, repo_name = repo.split("/", 1)
    raw = fetch_pr_raw(
        repo,
        owner,
        repo_name,
        {"number": pr_number},
        non_blocking_check_patterns,
    )
    return raw, dashboard_source_fingerprint(raw)


def deliver_prepared_author_nudges(
    repo: str,
    now: datetime,
    non_blocking_check_patterns: list[str],
    retry_snapshot_path: Path | None = None,
) -> list[str]:
    dashboard_state = load_dashboard_state_cache()
    if dashboard_state is None:
        print("dashboard state not found; skipping author nudges", file=sys.stderr)
        return []
    updated = dict(load_author_nudges(retry_snapshot_path))
    dashboard_prs = dashboard_state.get("prs") or {}
    errors: list[str] = []
    for key, entry in sorted(updated.items(), key=lambda item: int(item[0])):
        if not (entry or {}).get("pending_at"):
            continue
        pr_number = int(key)
        result = dashboard_prs.get(key)
        if not result or result.get("route") != "author":
            _due, reset_entry = plan_nudge(result, entry, now)
            if reset_entry is None:
                updated.pop(key, None)
            else:
                updated[key] = reset_entry
            continue
        try:
            raw, source_fingerprint = current_source_fingerprint(
                repo,
                pr_number,
                non_blocking_check_patterns,
            )
            pr = raw.get("pr") or {}
            expected_head = entry.get("head_sha") or ""
            current_head = ((raw.get("commits") or [{}])[-1].get("sha") or "")
            if (
                pr.get("state") != "OPEN"
                or pr.get("isDraft")
                or (expected_head and current_head != expected_head)
                or source_fingerprint != entry.get("source_fingerprint")
            ):
                updated[key] = {
                    name: value
                    for name, value in entry.items()
                    if name not in ("pending_at", "head_sha", "source_fingerprint")
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
            updated[key] = {
                "waiting_since": entry.get("waiting_since") or "",
                "nudged_at": nudged_at,
            }
    save_author_nudges(updated)
    return errors
