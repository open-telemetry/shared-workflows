#!/usr/bin/env python3
"""Track and post reminders for pull requests waiting on authors."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

from github_cli import (
    fetch_pr_issue_comments,
    fetch_pr_review_data,
    fetch_review_threads,
    gh_api,
    gh_pr_check_rollup,
    gh_required_check_contexts,
    include_missing_required_checks,
    run_gh,
)
from dashboard_override import author_override_guidance
from dashboard_override import DASHBOARD_OVERRIDE_LABEL
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
from utils import format_ts, parse_ts


NUDGE_AFTER = timedelta(weeks=1)
NUDGE_MARKER_PREFIX = "<!-- pull-request-dashboard-author-nudge:"


def nudge_marker(episode_id: str) -> str:
    return f"{NUDGE_MARKER_PREFIX}{episode_id} -->"


def routing_input_fingerprint(raw: dict[str, Any]) -> str:
    dashboard_login = f"{DASHBOARD_APP_SLUG}[bot]"
    pr = raw.get("pr") or raw
    labels = raw.get("labels")
    if labels is None:
        labels = pr.get("labels") or []
    issue_comments = [
        comment
        for comment in raw.get("issue_comments") or []
        if (comment.get("user") or {}).get("login") != dashboard_login
    ]
    routing_inputs = {
        "base_branch": str(
            pr.get("baseRefName")
            or (pr.get("base") or {}).get("ref")
            or ""
        ),
        "checks": raw.get("checks"),
        "issue_comments": issue_comments,
        "labels": sorted(
            label.get("name") or ""
            for label in labels
            if isinstance(label, dict)
            and label.get("name") == DASHBOARD_OVERRIDE_LABEL
        ),
        "pr_text": {
            "body": str(pr.get("body") or "").replace("\r\n", "\n"),
            "title": str(pr.get("title") or ""),
        },
        "review_comments": raw.get("review_comments") or [],
        "reviews": raw.get("reviews") or [],
        "review_threads": raw.get("review_threads") or [],
    }
    encoded = json.dumps(
        routing_inputs,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def fetch_current_pr_routing_state(
    repo: str,
    pr_number: int,
) -> tuple[dict[str, Any], str]:
    owner, repo_name = repo.split("/", 1)
    with ThreadPoolExecutor() as pool:
        pr_future = pool.submit(gh_api, f"/repos/{repo}/pulls/{pr_number}")
        issue_comments_future = pool.submit(
            fetch_pr_issue_comments,
            owner,
            repo_name,
            pr_number,
        )
        review_comments_future = pool.submit(
            gh_api,
            f"/repos/{repo}/pulls/{pr_number}/comments?per_page=100",
            True,
        )
        review_data_future = pool.submit(
            fetch_pr_review_data,
            owner,
            repo_name,
            pr_number,
        )
        review_threads_future = pool.submit(
            fetch_review_threads,
            owner,
            repo_name,
            pr_number,
        )
        pr = pr_future.result() or {}
        check_rollup_future = pool.submit(
            gh_pr_check_rollup,
            repo,
            pr.get("node_id") or "",
            [],
        )
        required_contexts_future = pool.submit(
            gh_required_check_contexts,
            repo,
            ((pr.get("base") or {}).get("ref") or ""),
        )
        review_data = review_data_future.result() or {}
        check_rollup = check_rollup_future.result()
        fingerprint = routing_input_fingerprint({
            "checks": include_missing_required_checks(
                None if check_rollup is None else check_rollup["required"],
                required_contexts_future.result(),
            ),
            "issue_comments": issue_comments_future.result() or [],
            "labels": pr.get("labels") or [],
            "pr": pr,
            "review_comments": review_comments_future.result() or [],
            "reviews": review_data.get("reviews") or [],
            "review_threads": review_threads_future.result() or [],
        })
        return pr, fingerprint


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


def render_nudge(author: str, status_url: str, episode_id: str) -> str:
    return "\n".join([
        nudge_marker(episode_id),
        f"Hi @{author} — just a friendly reminder that this pull request has "
        "been waiting on you for a week.",
        "",
        f"There are still items that need your attention. See the "
        f"[dashboard status comment]({status_url}) for the full list. You don't "
        "need to push a code change to hand it back — replying to move each "
        "discussion forward is enough, whether that's answering a question, "
        "explaining why no change is needed, or asking a follow-up. The "
        "dashboard then automatically routes it back to reviewers.",
        "",
        author_override_guidance(),
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
    episode_id = str(
        ((result.get("facts") or {}).get("author_nudge_episode_id") or "")
    )
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
    author = str(((result.get("facts") or {}).get("author") or "")).strip()
    author = author or str((pr.get("user") or {}).get("login") or "").strip()
    if not author:
        raise RuntimeError(f"author not found for PR #{pr_number}")
    run_gh([
        "gh", "api", "--method", "POST",
        f"repos/{repo}/issues/{pr_number}/comments",
        "-f", f"body={render_nudge(author, status_comments[0]['html_url'], episode_id)}",
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
        routing_fingerprint = facts.get("routing_input_fingerprint") or ""
        if head_sha and routing_fingerprint:
            entry = {
                **entry,
                "pending_at": format_ts(now),
                "head_sha": head_sha,
                "routing_input_fingerprint": routing_fingerprint,
            }
    if entry is None:
        updated.pop(key, None)
    else:
        updated[key] = entry
    save_author_nudges(updated)


def deliver_prepared_author_nudges(
    repo: str,
    now: datetime,
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
            pr, current_routing_fingerprint = fetch_current_pr_routing_state(
                repo,
                pr_number,
            )
            expected_head = entry.get("head_sha") or ""
            expected_routing_fingerprint = entry.get("routing_input_fingerprint") or ""
            current_head = ((pr.get("head") or {}).get("sha") or "")
            if pr.get("state") != "open" or pr.get("draft"):
                updated.pop(key, None)
                continue
            if (
                not expected_head
                or current_head != expected_head
                or not expected_routing_fingerprint
                or current_routing_fingerprint != expected_routing_fingerprint
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
            updated[key] = {
                "waiting_since": entry.get("waiting_since") or "",
                "nudged_at": nudged_at,
            }
    save_author_nudges(updated)
    return errors
