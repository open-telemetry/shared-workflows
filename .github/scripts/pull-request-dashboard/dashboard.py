#!/usr/bin/env python3
"""Generate a deterministic pull request dashboard with discussion-level LLM triage.

The script keeps repository facts deterministic and asks the LLM only one
narrow question per unresolved discussion: who has the next action for that
discussion?

This script checks out the workflow state branch, commits changed dashboard
state files, and pushes with `git push --force-with-lease` so concurrent runs
use git refs as the durable compare-and-swap boundary. The publishing job
renders markdown from the accepted state branch and the current open PR list.

Usage:
    python .github/scripts/pull-request-dashboard/dashboard.py --state-branch BRANCH
                                                               --repo REPO
                                                               --approver-team TEAM
                                                               [--approver-team TEAM]
                                                               [--pr-number N]
                                                               [--model NAME]

Architecture overview
---------------------

Workflow state that survives across runs lives on the state branch:

    REPO/dashboard-state.json     cached per-PR routing results
    REPO/notification-state.json  per-PR Slack history

The dashboard issue body is rendered fresh each run; no state markers are
embedded in it.

A run flows like this:

  list_open_prs
       v
  build_dashboard_update_for_pr
       single-PR + cache hit:  reuse cached results, refresh only the trigger PR
       single-PR + cache miss: skip; wait for backfill to create initial state
       no PR target:           backfill, processing PRs one at a time
       v
  merge_dashboard_update_with_latest_state
       reload dashboard-state in case a concurrent run updated it
       v
  save_dashboard_state_cache

Slack notifications are sent by notify_slack.py in a separate serialized
workflow job. That job loads the latest accepted dashboard state and
notification state, sends any due notifications, and pushes the updated
notification state with the same git CAS pattern.

State files are committed and pushed first. Only after that state branch push
succeeds does a follow-up publishing job fetch the accepted dashboard state,
render the dashboard body, and publish it to the dashboard issue.

Runs without --pr-number use backfill and store progress in
backfill-state.json. A selected PR advances that cursor after a successful
refresh; a failure leaves the cursor in place so scheduled failure reporting
continues until the blocked refresh is fixed. Single-PR runs are
optimistic-concurrency updates of just one PR slot in the cached state.

Field schemas
-------------

Two record shapes flow through the pipeline as ``dict[str, Any]``. They are
built up across stages, so not every field is present at every point.

``result`` (one per PR) — produced by ``build_pr_result``:

- ``pr_number`` (``int``): PR number.
- ``pr_title`` (``str``): PR title.
- ``pr_url`` (``str``): PR URL.
- ``failed`` (``bool``): Whether the result failed.
- ``route`` (``str``): Routing bucket from ``ROUTE_ORDER``.
- ``facts`` (``dict``): Deterministic facts described below.
- ``review_threads`` (``list[dict]``): Unresolved inline review threads. Internal.
- ``top_level_items`` (``list[dict]``): Top-level feedback items. Internal.
- ``review_thread_classifications`` (``list[dict]``): Current inline actions. Internal.
- ``top_level_classifications`` (``list[dict]``): Immutable ledger decisions. Internal.
- ``pending_actions`` (``dict[str, dict]``): Ephemeral current actions by discussion id;
    each entry contains ``action`` and ``since``.
- ``top_level_history`` (``dict[str, dict]``): Durable evidence timestamps by
    top_level action id and evidence kind.
- ``error`` (``str``): Failure detail, present only on failure paths.

Only ``pr_number``, ``pr_url``, ``failed``, ``route``, ``facts``, and
``top_level_history`` survives into the cached dashboard state (see
``stored_result``).

``facts`` (one per PR) — built in two stages:

  Stage 1 — compute_facts (deterministic from GitHub data):
    author                          str           Effective author (human, after
                                                  bot-delegation resolution).
    assignees                       list[str]     PR assignees.
    is_maintenance_bot              bool          PR is authored by a
                                                  maintenance bot.
    is_draft                        bool
    approval_count                  int           Current unique APPROVED reviews
                                                  from approver-team members.
    ci_failing_count                int           Absent when checks could not
                                                  be fetched.
    ci_pending_count                int           Absent when checks could not
                                                  be fetched.
    conflicts                       str           "yes" | "no" | "unknown".
    created_at                      str (iso)
    last_activity_at                str (iso)
    last_author_activity_at         str (iso)
    last_approver_activity_at       str (iso)
    last_external_activity_at       str (iso)

    Stage 2 — add_wait_age_facts (depends on routing + pending actions):
    waiting_since                   str (iso)     Oldest pending discussion, or
                                                  route-appropriate fallback,
                                                  or PR creation time.
    waiting_age_basis               str           Which heuristic chose
                                                  waiting_since.
    reviewers                       list[dict]    Reviewers to display (added by
                                                  add_reviewers). Each entry is
                                                  {"login": str, "approved": bool,
                                                  "approved_non_team": bool,
                                                  "changes_requested": bool,
                                                  "open_thread": bool,
                                                  "top_level_feedback": bool}; approved
                                                  means an approver-team member
                                                  is in the APPROVED state,
                                                  approved_non_team means someone
                                                  outside the team approved,
                                                  changes_requested means an
                                                  approver-team member's latest
                                                  review is CHANGES_REQUESTED,
                                                  open_thread means they own an
                                                  unresolved discussion,
                                                  and top_level_feedback means
                                                  their top-level feedback still
                                                  needs author action.

Stage-2 fields are absent on failure paths (failed is True). Human-readable
``age`` strings (e.g. ``3h``) are derived at render time from these
timestamps rather than persisted, so the cached JSON stays stable across
runs when no underlying PR data has changed.
"""

from __future__ import annotations

import argparse
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from github_cli import (
    TransientGhError,
    detect_repo,
    fetch_pr_review_data,
    fetch_pr_title_edits,
    fetch_review_threads,
    gh_api,
    gh_pr_checks,
    gh_pr_view,
    list_open_prs,
    load_reviewer_set,
    normalize_repo,
    repo_state_key,
)
from classification import (
    DISCUSSION_RECENT_COMMENTS_LIMIT,
    TOP_LEVEL_EVIDENCE_KINDS,
    classify_discussion_domains,
    is_conflict_resolution_comment,
    normalize_discussion_action,
    prune_classification_cache,
)
from state import (
    empty_state,
    load_dashboard_state_cache,
    load_backfill_state,
    results_from_dashboard_state,
    save_dashboard_state_cache,
    save_backfill_state,
    set_state_dir,
    stored_result,
    update_dashboard_state_for_pr,
)
import state_branch
from utils import actor_login, format_ts, parse_ts, truncate

# --- CLI defaults ----------------------------------------------------------
DEFAULT_MODEL = "gpt-5.4-mini"
POSITIVE_ACK_REACTIONS = {"THUMBS_UP", "HOORAY", "HEART", "ROCKET"}
DEFAULT_BACKFILL_MAX_PRS = 50

# ---------------------------------------------------------------- model helpers


def role_for(login: str, author: str, reviewers: set[str]) -> str:
    if not login:
        return "outsider"
    low = login.lower()
    if low == author.lower():
        return "author"
    if low in reviewers:
        return "approver"
    if low.startswith("app/") or low.endswith("[bot]"):
        return "bot"
    return "outsider"


# Copilot appears in two API shapes: `gh pr view`'s `author` field uses the
# `app/<slug>` form, while the Pulls/commits endpoint's `committer.login`
# field can return the bare `copilot` slug. Do not treat either form as the
# human author behind a Copilot-authored PR.
_COPILOT_COMMITTER_LOGINS = {"copilot"}
_COPILOT_PR_AUTHORS = {"app/copilot-swe-agent", "copilot"}
_COPILOT_REVIEWER_LOGINS = {"copilot", "copilot-pull-request-reviewer", "copilot-pull-request-reviewer[bot]"}
_MAINTENANCE_BOT_PR_AUTHORS = {"app/otelbot", "app/renovate"}


def reviewer_actor_login(obj: dict[str, Any] | None) -> str:
    login = actor_login(obj)
    if login.lower() in _COPILOT_REVIEWER_LOGINS:
        return "copilot-pull-request-reviewer[bot]"
    return login


def human_author_for_copilot_pr(raw: dict[str, Any]) -> str:
    assignees = [actor_login(a) for a in (raw["pr"].get("assignees") or [])]
    for login in assignees:
        low = login.lower()
        if login and low not in _COPILOT_PR_AUTHORS and not low.startswith("app/") and not low.endswith("[bot]"):
            return login

    commits = raw["commits"]
    if not commits:
        return ""
    first_commit = commits[0]
    login = actor_login(first_commit.get("committer") or {})
    low = login.lower()
    if not login or low in _COPILOT_COMMITTER_LOGINS:
        return ""
    return login


def fetch_pr_raw(
    repo: str,
    owner: str,
    repo_name: str,
    pr_summary: dict[str, Any],
) -> dict[str, Any]:
    number = pr_summary["number"]
    with ThreadPoolExecutor() as pool:
        f_pr = pool.submit(gh_pr_view, repo, number)
        f_issue = pool.submit(
            gh_api,
            f"/repos/{owner}/{repo_name}/issues/{number}/comments?per_page=100",
            True,
        )
        f_revcom = pool.submit(
            gh_api,
            f"/repos/{owner}/{repo_name}/pulls/{number}/comments?per_page=100",
            True,
        )
        f_commits = pool.submit(
            gh_api,
            f"/repos/{owner}/{repo_name}/pulls/{number}/commits?per_page=100",
            True,
        )
        f_checks = pool.submit(gh_pr_checks, repo, number)
        f_threads = pool.submit(fetch_review_threads, owner, repo_name, number)
        f_review_data = pool.submit(fetch_pr_review_data, owner, repo_name, number)
        review_data = f_review_data.result() or {}
        return {
            "summary": pr_summary,
            "pr": f_pr.result(),
            "issue_comments": f_issue.result() or [],
            "review_comments": f_revcom.result() or [],
            "reviews": review_data.get("reviews") or [],
            "commits": f_commits.result() or [],
            "checks": f_checks.result(),
            "review_threads": f_threads.result() or [],
            "pr_metadata": review_data.get("pr_metadata") or {},
        }


def effective_author(raw: dict[str, Any]) -> str:
    pr = raw["pr"]
    summary = raw["summary"]
    author = actor_login(pr.get("author") or {}) or actor_login(summary.get("author") or {})
    if author.lower() in _COPILOT_PR_AUTHORS:
        human_author = human_author_for_copilot_pr(raw)
        if human_author:
            return human_author
    return author


def is_merge_commit(commit: dict[str, Any]) -> bool:
    return len(commit.get("parents") or []) >= 2


def normalize_events(raw: dict[str, Any], author: str, reviewers: set[str]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for c in raw["commits"]:
        commit_obj = c.get("commit") or {}
        commit_author = commit_obj.get("author") or {}
        commit_committer = commit_obj.get("committer") or {}
        author_login = actor_login(c.get("author") or {})
        committer_login = actor_login(c.get("committer") or {})
        # A change made by someone other than the PR author should be
        # accompanied by an explanatory reply.
        if committer_login.lower() == author.lower():
            login = committer_login
            timestamp = commit_committer.get("date") or commit_author.get("date") or ""
        elif author_login.lower() == author.lower():
            login = author_login
            timestamp = commit_author.get("date") or ""
        elif committer_login:
            login = committer_login
            timestamp = commit_committer.get("date") or ""
        else:
            login = author_login or commit_author.get("name") or ""
            timestamp = commit_author.get("date") or ""
        sha = c.get("sha") or ""
        events.append({
            "kind": "commit",
            "timestamp": timestamp,
            "actor": login,
            "actor_role": role_for(login, author, reviewers),
            "body": commit_obj.get("message") or "",
            "state": None,
            "path": None,
            "sha": sha[:7],
            "is_merge_from_base_by_non_author": is_merge_commit(c) and login.lower() != author.lower(),
        })
    for c in raw["issue_comments"]:
        login = reviewer_actor_login(c.get("user") or {})
        timestamp = c.get("updated_at") or c.get("created_at") or ""
        events.append({
            "source_id": c.get("id"),
            "kind": "issue-comment",
            "timestamp": timestamp,
            "updated_timestamp": timestamp,
            "actor": login,
            "actor_role": role_for(login, author, reviewers),
            "body": c.get("body") or "",
            "state": None,
            "path": None,
            "sha": None,
            "is_merge_from_base_by_non_author": False,
        })
    for c in raw["review_comments"]:
        login = reviewer_actor_login(c.get("user") or {})
        events.append({
            "source_id": c.get("id"),
            "kind": "review-comment",
            "timestamp": c.get("updated_at") or c.get("created_at") or "",
            "actor": login,
            "actor_role": role_for(login, author, reviewers),
            "body": c.get("body") or "",
            "state": None,
            "path": c.get("path"),
            "sha": None,
            "is_merge_from_base_by_non_author": False,
        })
    for r in raw["reviews"]:
        login = reviewer_actor_login(r.get("user") or {})
        state = r.get("state") or ""
        events.append({
            "source_id": r.get("id"),
            "kind": "review-state",
            "timestamp": r.get("submitted_at") or "",
            "updated_timestamp": r.get("updated_at") or r.get("submitted_at") or "",
            "actor": login,
            "actor_role": role_for(login, author, reviewers),
            "body": r.get("body") or "",
            "state": state,
            "path": None,
            "sha": None,
            "is_merge_from_base_by_non_author": False,
        })
    events = [e for e in events if e["timestamp"]]
    events.sort(key=lambda e: e["timestamp"])
    return events


def is_substantive_activity(event: dict[str, Any]) -> bool:
    if event.get("is_merge_from_base_by_non_author"):
        return False
    # Bot events never count as substantive: merge-bot pings, CI status
    # comments, and the like must not refresh the waiting clock. Bot PR
    # authors are remapped to their human delegator in `effective_author`,
    # so a real human's activity still shows up here under that login.
    if event.get("actor_role") == "bot":
        return False
    if event["kind"] == "review-state" and event.get("state") != "COMMENTED":
        return True
    return bool((event.get("body") or "").strip())


def compute_conflicts(pr: dict[str, Any]) -> str:
    merge_state = pr.get("mergeStateStatus")
    mergeable = pr.get("mergeable")
    if mergeable == "CONFLICTING" or merge_state == "DIRTY":
        return "yes"
    if mergeable in (None, "", "UNKNOWN"):
        return "unknown"
    return "no"


def latest_substantive_activity(events: list[dict[str, Any]], actor_roles: set[str]) -> datetime | None:
    timestamps = [
        parse_ts(e["timestamp"])
        for e in events
        if e.get("actor_role") in actor_roles and is_substantive_activity(e)
    ]
    timestamps = [ts for ts in timestamps if ts is not None]
    return max(timestamps) if timestamps else None


def current_approval_count(events: list[dict[str, Any]]) -> int:
    approvers = approver_logins(events)
    return sum(
        1
        for reviewer, state in latest_review_states(events).items()
        if state == "APPROVED" and reviewer in approvers
    )


def approver_logins(events: list[dict[str, Any]]) -> set[str]:
    return {
        event["actor"]
        for event in events
        if event.get("actor_role") == "approver" and event.get("actor")
    }


def latest_review_states(events: list[dict[str, Any]]) -> dict[str, str]:
    latest_by_reviewer: dict[str, tuple[str, str]] = {}
    for event in events:
        if event.get("kind") != "review-state":
            continue
        reviewer = event.get("actor") or ""
        submitted_at = event.get("timestamp") or ""
        state = event.get("state") or ""
        if not reviewer or not submitted_at or state == "COMMENTED":
            continue
        previous = latest_by_reviewer.get(reviewer)
        if previous is None or submitted_at >= previous[0]:
            latest_by_reviewer[reviewer] = (submitted_at, state)
    return {reviewer: state for reviewer, (_, state) in latest_by_reviewer.items()}


def commenting_reviewers(events: list[dict[str, Any]]) -> set[str]:
    # Approver-team members who have participated on the PR in any way: an
    # issue comment, an inline review comment, or a submitted review. This
    # surfaces engaged reviewers even when they have neither approved nor own
    # an open discussion.
    return {
        event["actor"]
        for event in events
        if event.get("actor_role") == "approver"
        and event.get("kind") in ("issue-comment", "review-comment", "review-state")
        and event.get("actor")
    }


def compute_facts(
    raw: dict[str, Any],
    author: str,
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    pr = raw["pr"]
    checks = raw["checks"]
    failing = [c for c in checks or [] if (c.get("state") or "").upper() in ("FAILURE", "ERROR")]
    pending = [c for c in checks or [] if (c.get("state") or "").upper() in ("PENDING", "QUEUED", "IN_PROGRESS")]
    last_activity_ts = parse_ts(pr["updatedAt"])
    created_ts = parse_ts(pr["createdAt"])
    author_activity_ts = latest_substantive_activity(events, {"author"})
    approver_activity_ts = latest_substantive_activity(events, {"approver"})
    external_activity_ts = latest_substantive_activity(events, {"outsider"})
    api_author = actor_login(pr.get("author") or {})
    assignees = [reviewer_actor_login(a) for a in (pr.get("assignees") or [])]
    assignees = [a for a in assignees if a]
    facts = {
        "author": author,
        "assignees": assignees,
        "is_maintenance_bot": api_author.lower() in _MAINTENANCE_BOT_PR_AUTHORS,
        "is_draft": bool(pr.get("isDraft")),
        "approval_count": current_approval_count(events),
        "conflicts": compute_conflicts(pr),
        "created_at": format_ts(created_ts),
        "last_activity_at": format_ts(last_activity_ts),
        "last_author_activity_at": format_ts(author_activity_ts),
        "last_approver_activity_at": format_ts(approver_activity_ts),
        "last_external_activity_at": format_ts(external_activity_ts),
    }
    if checks is not None:
        facts["ci_failing_count"] = len(failing)
        facts["ci_pending_count"] = len(pending)
    return facts


def discussion_comment(
    timestamp: str,
    actor: str,
    author: str,
    reviewers: set[str],
    body: str,
    positive_reactors: set[str] | None = None,
) -> dict[str, Any]:
    return {
        "timestamp": timestamp,
        "actor": actor,
        "actor_role": role_for(actor, author, reviewers),
        "body": truncate(body),
        "positive_reactors": sorted(positive_reactors or set()),
    }


def add_discussion_facts(
    discussion: dict[str, Any],
    comments: list[dict[str, Any]],
    facts: dict[str, Any],
) -> dict[str, Any]:
    discussion["discussion_facts"] = {
        "latest_comment_role": comments[-1].get("actor_role"),
        "current_conflicts": facts.get("conflicts"),
    }
    return discussion


def positive_reaction_logins(comment: dict[str, Any]) -> set[str]:
    logins: set[str] = set()
    for group in comment.get("reactionGroups") or []:
        if group.get("content") not in POSITIVE_ACK_REACTIONS:
            continue
        for user in ((group.get("users") or {}).get("nodes") or []):
            login = actor_login(user).lower()
            if login:
                logins.add(login)
    return logins


def group_review_threads(
    raw: dict[str, Any],
    author: str,
    reviewers: set[str],
    facts: dict[str, Any],
) -> list[dict[str, Any]]:
    discussions: list[dict[str, Any]] = []
    for discussion in raw["review_threads"]:
        # Skip outdated discussions: GitHub marks a discussion outdated when its
        # anchor lines no longer exist, which typically means the author
        # pushed a fix, so surfacing them would treat addressed feedback
        # as live.
        if discussion.get("isResolved") or discussion.get("isOutdated"):
            continue
        comments = []
        for c in ((discussion.get("comments") or {}).get("nodes") or []):
            actor = reviewer_actor_login(c.get("author") or {})
            comments.append(discussion_comment(
                c.get("updatedAt") or c.get("createdAt") or "",
                actor,
                author,
                reviewers,
                c.get("body") or "",
                positive_reaction_logins(c),
            ))
        comments = [c for c in comments if c["timestamp"]]
        comments.sort(key=lambda c: c["timestamp"])
        if not comments:
            continue
        discussions.append(add_discussion_facts({
            "discussion_id": discussion.get("id") or f"review-discussion-{len(discussions) + 1}",
            "discussion_kind": "review-comment-thread",
            "path": discussion.get("path"),
            "line": discussion.get("line"),
            "resolved": False,
            "comments": comments,
        }, comments, facts))
    discussions.sort(key=lambda t: t["comments"][-1]["timestamp"])
    return discussions


def derive_top_level_items(
    events: list[dict[str, Any]],
    facts: dict[str, Any],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for event in events:
        source_kind = event.get("kind") or ""
        if source_kind not in ("issue-comment", "review-state"):
            continue
        state = event.get("state") or ""
        if state == "DISMISSED":
            continue
        body = truncate(event.get("body") or "")
        if source_kind == "review-state" and not body and state != "CHANGES_REQUESTED":
            continue
        root_timestamp = event.get("updated_timestamp") or event.get("timestamp") or ""
        comment = {
            "timestamp": root_timestamp,
            "actor": event.get("actor") or "",
            "actor_role": event.get("actor_role"),
            "body": body,
            "positive_reactors": [],
        }
        if (
            event.get("source_id") is not None
            and comment["actor"]
            and comment["timestamp"]
            and comment["actor_role"] in ("approver", "outsider")
            and (comment["body"] or state == "CHANGES_REQUESTED")
            and not (
                state != "CHANGES_REQUESTED"
                and facts.get("conflicts") == "no"
                and is_conflict_resolution_comment(comment["body"])
            )
        ):
            items.append(add_discussion_facts({
                "discussion_id": (
                    f"pr-issue-comment-{event['source_id']}"
                    if source_kind == "issue-comment"
                    else f"pr-review-{event['source_id']}"
                ),
                "discussion_kind": "top-level-feedback",
                "source_kind": source_kind,
                "source_id": event["source_id"],
                "requester": comment["actor"],
                "review_state": state or None,
                "root_timestamp": root_timestamp,
                "path": None,
                "line": None,
                "resolved": False,
                "comments": [comment],
            }, [comment], facts))
    items.sort(key=lambda item: item["root_timestamp"])
    return items


def collect_author_evidence(
    discussion: dict[str, Any],
    events: list[dict[str, Any]],
    pr_metadata: dict[str, Any],
    author: str,
    previous_entry: dict[str, Any],
) -> dict[str, str]:
    root_timestamp = discussion.get("root_timestamp") or ""
    evidence = {
        kind: timestamp
        for kind, timestamp in (previous_entry.get("evidence") or {}).items()
        if kind in TOP_LEVEL_EVIDENCE_KINDS
        and isinstance(timestamp, str)
        and timestamp > root_timestamp
    }
    for kind, event_kind in (("commit", "commit"), ("reply", "issue-comment")):
        candidates = [
            e["timestamp"]
            for e in events
            if e.get("actor_role") == "author"
            and e.get("kind") == event_kind
            and e["timestamp"] > root_timestamp
            and is_substantive_activity(e)
        ]
        if candidates:
            evidence[kind] = min(candidates + ([evidence[kind]] if kind in evidence else []))
    edited_at = pr_metadata.get("lastEditedAt") or ""
    editor = actor_login(pr_metadata.get("editor") or {})
    if edited_at > root_timestamp and editor.lower() == author.lower():
        evidence["description"] = min(
            edited_at,
            evidence.get("description") or edited_at,
        )
    title_edit_timestamps = [
        edit.get("createdAt") or ""
        for edit in pr_metadata.get("titleEdits") or []
        if actor_login(edit.get("actor") or {}).lower() == author.lower()
        and (edit.get("createdAt") or "") > root_timestamp
    ]
    if title_edit_timestamps:
        evidence["title"] = min(
            title_edit_timestamps + ([evidence["title"]] if "title" in evidence else [])
        )
    return evidence


def evidence_satisfied_at(
    required_kinds: list[str],
    evidence: dict[str, str],
) -> str:
    if evidence.get("reply"):
        return evidence["reply"]
    if not required_kinds or any(kind not in evidence for kind in required_kinds):
        return ""
    return max(evidence[kind] for kind in required_kinds)


def requires_title_edit_lookup(
    top_level_items: list[dict[str, Any]],
    classifications: list[dict[str, Any]],
    previous_history: dict[str, dict[str, Any]] | None = None,
) -> bool:
    by_id = discussions_by_id(top_level_items)
    for classification in classifications:
        decision = classification.get("decision") or {}
        if (
            normalize_discussion_action(decision.get("discussion_action") or "") != "author"
            or "title" not in (decision.get("required_evidence_kinds") or [])
        ):
            continue
        discussion = by_id.get(classification.get("discussion_id") or "")
        if not discussion:
            continue
        previous_entry = (previous_history or {}).get(discussion["discussion_id"]) or {}
        title_evidence_at = (previous_entry.get("evidence") or {}).get("title") or ""
        if title_evidence_at <= (discussion.get("root_timestamp") or ""):
            return True
    return False


def first_change_request_clearance(
    events: list[dict[str, Any]],
    requester: str,
    evidence_at: str,
) -> str | None:
    candidates = [
        e.get("updated_timestamp") or e["timestamp"]
        for e in events
        if e.get("kind") == "review-state"
        and e.get("state") in ("APPROVED", "DISMISSED")
        and (e.get("updated_timestamp") or e["timestamp"]) > evidence_at
        and is_substantive_activity(e)
        and (e.get("actor") or "").lower() == requester.lower()
    ]
    return min(candidates) if candidates else None


def build_review_thread_pending_actions(
    review_threads: list[dict[str, Any]],
    classifications: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    by_id = discussions_by_id(review_threads)
    pending_actions: dict[str, dict[str, Any]] = {}
    for classification in classifications:
        action = normalize_discussion_action(
            (classification.get("decision") or {}).get("discussion_action") or ""
        )
        discussion = by_id.get(classification.get("discussion_id") or "")
        comments = (discussion or {}).get("comments") or []
        if action != "none" and comments:
            pending_actions[classification["discussion_id"]] = {
                "action": action,
                "since": comments[-1].get("timestamp") or "",
            }
    return pending_actions


def advance_top_level_actions(
    top_level_items: list[dict[str, Any]],
    classifications: list[dict[str, Any]],
    events: list[dict[str, Any]],
    pr_metadata: dict[str, Any],
    author: str,
    previous_history: dict[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    by_id = discussions_by_id(top_level_items)
    pending_actions: dict[str, dict[str, Any]] = {}
    top_level_history: dict[str, dict[str, Any]] = {}
    for classification in classifications:
        discussion = by_id.get(classification.get("discussion_id") or "")
        decision = classification.get("decision") or {}
        if not discussion:
            continue
        action = normalize_discussion_action(decision.get("discussion_action") or "")
        root_timestamp = discussion.get("root_timestamp") or ""
        if action == "external":
            pending_actions[discussion["discussion_id"]] = {
                "action": "external",
                "since": root_timestamp,
            }
            continue
        if action == "unclear":
            pending_actions[discussion["discussion_id"]] = {
                "action": "reviewer",
                "since": root_timestamp,
            }
            continue
        if action != "author":
            continue
        previous_entry = (previous_history or {}).get(discussion["discussion_id"]) or {}
        evidence = collect_author_evidence(
            discussion,
            events,
            pr_metadata,
            author,
            previous_entry,
        )
        required_kinds = decision.get("required_evidence_kinds") or []
        evidence_at = evidence_satisfied_at(required_kinds, evidence)
        if evidence:
            top_level_history[discussion["discussion_id"]] = {"evidence": evidence}
        if not evidence_at:
            pending_actions[discussion["discussion_id"]] = {
                "action": "author",
                "since": root_timestamp,
            }
            continue
        if discussion.get("review_state") == "CHANGES_REQUESTED":
            clearance_at = first_change_request_clearance(
                events,
                discussion.get("requester") or "",
                evidence_at,
            )
            if not clearance_at:
                pending_actions[discussion["discussion_id"]] = {
                    "action": "reviewer",
                    "since": evidence_at,
                }
    return pending_actions, top_level_history


# ---------------------------------------------------------------- routing


ROUTE_DISCUSSION_ACTIONS = {
    "author": {"author"},
    "approver": {"reviewer"},
    "maintainer": {"reviewer"},
    "external": {"external"},
}


def action_counts(pending_actions: dict[str, dict[str, Any]]) -> dict[str, int]:
    counts = {"author": 0, "reviewer": 0, "external": 0, "none": 0, "unclear": 0}
    for entry in pending_actions.values():
        counts[normalize_discussion_action(entry.get("action") or "")] += 1
    return counts


def has_blocking_action(pending_actions: dict[str, dict[str, Any]]) -> bool:
    for entry in pending_actions.values():
        action = normalize_discussion_action(entry.get("action") or "")
        if action in ("reviewer", "unclear"):
            return True
    return False


def route_pr(facts: dict[str, Any], pending_actions: dict[str, dict[str, Any]], required_approvals: int) -> str:
    counts = action_counts(pending_actions)
    # Copilot PRs are mapped back to a human author when possible. Maintenance
    # bot PRs have no useful author route and need only one approval.
    is_maintenance_bot = facts.get("is_maintenance_bot")
    approval_threshold = 1 if is_maintenance_bot else required_approvals
    # Precedence:
    #   1. A discussion waiting on the author -> "author".
    #   2. Otherwise a discussion waiting on something external -> "external".
    #   3. If there are enough approvals and no inline or top-level feedback is
    #      still waiting on a reviewer or is unclear -> "maintainer".
    #   4. Otherwise the PR is still waiting on approvers, including for an
    #      addressed CHANGES_REQUESTED review to be approved or dismissed.
    if counts["author"] and not is_maintenance_bot:
        return "author"
    if counts["external"]:
        return "external"
    if facts.get("approval_count", 0) >= approval_threshold and not has_blocking_action(pending_actions):
        return "maintainer"
    return "approver"


def discussions_by_id(discussions: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {t["discussion_id"]: t for t in discussions}


def oldest_pending_action_ts(
    pending_actions: dict[str, dict[str, Any]],
    actions: set[str],
) -> datetime | None:
    timestamps = [
        parse_ts(entry.get("since") or "")
        for entry in pending_actions.values()
        if normalize_discussion_action(entry.get("action") or "") in actions
    ]
    timestamps = [ts for ts in timestamps if ts is not None]
    return min(timestamps) if timestamps else None


def fallback_wait_ts(route: str, facts: dict[str, Any]) -> tuple[datetime | None, str]:
    if route in ("approver", "maintainer"):
        return parse_ts(facts.get("last_author_activity_at") or ""), "last_author_activity"
    if route == "author":
        return parse_ts(facts.get("last_approver_activity_at") or ""), "last_approver_activity"
    if route == "external":
        return parse_ts(facts.get("last_external_activity_at") or ""), "last_external_activity"
    return parse_ts(facts.get("last_activity_at") or ""), "last_activity"


def add_wait_age_facts(
    facts: dict[str, Any],
    route: str,
    pending_actions: dict[str, dict[str, Any]],
) -> None:
    actions = ROUTE_DISCUSSION_ACTIONS.get(route)
    wait_ts = oldest_pending_action_ts(pending_actions, actions) if actions else None
    basis = "oldest_pending_thread" if wait_ts else ""
    if wait_ts is None:
        wait_ts, basis = fallback_wait_ts(route, facts)
    if wait_ts is None:
        wait_ts = parse_ts(facts.get("created_at") or "")
        basis = "created"
    facts["waiting_since"] = format_ts(wait_ts)
    facts["waiting_age_basis"] = basis


# Discussion actions that count as open and unresolved. A reviewer who commented
# in such a discussion is not yet satisfied, even if they have approved.
# "none" means no follow-up is needed, so it does not block a clear check.
OPEN_DISCUSSION_ACTIONS = {"author", "reviewer", "external", "unclear"}


def reviewers_with_open_threads(
    review_threads: list[dict[str, Any]],
    pending_actions: dict[str, dict[str, Any]],
) -> set[str]:
    logins: set[str] = set()
    for discussion in review_threads:
        action = (pending_actions.get(discussion["discussion_id"]) or {}).get("action")
        if action not in OPEN_DISCUSSION_ACTIONS:
            continue
        for comment in discussion.get("comments") or []:
            if comment.get("actor_role") in ("approver", "outsider") and comment.get("actor"):
                logins.add(comment["actor"])
    return logins


def reviewers_with_top_level_feedback(
    top_level_items: list[dict[str, Any]],
    pending_actions: dict[str, dict[str, Any]],
) -> set[str]:
    logins: set[str] = set()
    for discussion in top_level_items:
        action = (pending_actions.get(discussion["discussion_id"]) or {}).get("action")
        if action != "author":
            continue
        if discussion.get("requester"):
            logins.add(discussion["requester"])
    return logins


def add_reviewers(
    facts: dict[str, Any],
    events: list[dict[str, Any]],
    review_threads: list[dict[str, Any]],
    top_level_items: list[dict[str, Any]],
    pending_actions: dict[str, dict[str, Any]],
) -> None:
    # Reviewers to display in the dashboard, each flagged with their review
    # stance: approved (by an approver-team member), approved_non_team (an
    # approval from someone outside the team), changes_requested (an
    # approver-team member's latest review blocks), open_thread (they own an
    # unresolved discussion), and top_level_feedback (their top-level feedback
    # still needs author action). The renderer turns these into icons.
    # Reviewers are everyone who reviewed, owns an open discussion, otherwise
    # commented, or is a PR assignee, sorted alphabetically (case-insensitive).
    states = latest_review_states(events)
    approvers = approver_logins(events)
    approved = {r for r, s in states.items() if s == "APPROVED" and r in approvers}
    approved_non_team = {r for r, s in states.items() if s == "APPROVED" and r not in approvers}
    changes_requested = {r for r, s in states.items() if s == "CHANGES_REQUESTED" and r in approvers}
    with_open = reviewers_with_open_threads(review_threads, pending_actions)
    with_top_level = reviewers_with_top_level_feedback(top_level_items, pending_actions)
    candidates = (
        approved
        | approved_non_team
        | changes_requested
        | with_open
        | with_top_level
        | commenting_reviewers(events)
        | set(facts.get("assignees") or [])
    )
    candidates.discard("")
    facts["reviewers"] = [
        {
            "login": login,
            "approved": login in approved,
            "approved_non_team": login in approved_non_team,
            "changes_requested": login in changes_requested,
            "open_thread": login in with_open,
            "top_level_feedback": login in with_top_level,
        }
        for login in sorted(candidates, key=str.lower)
    ]


# ---------------------------------------------------------------- main


def build_pr_result(
    repo: str,
    owner: str,
    repo_name: str,
    pr_summary: dict[str, Any],
    reviewers: set[str],
    model: str,
    required_approvals: int,
    previous_top_level_history: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    number = pr_summary["number"]
    try:
        raw = fetch_pr_raw(repo, owner, repo_name, pr_summary)
        if raw["pr"].get("state") != "OPEN" or raw["pr"].get("isDraft"):
            return None
        author = effective_author(raw)
        events = normalize_events(raw, author, reviewers)
        facts = compute_facts(raw, author, events)
        review_threads = group_review_threads(raw, author, reviewers, facts)
        top_level_items = derive_top_level_items(events, facts)
        review_thread_classifications, top_level_classifications = (
            classify_discussion_domains(
                number, review_threads, top_level_items, model
            )
        )
        if requires_title_edit_lookup(
            top_level_items,
            top_level_classifications,
            previous_top_level_history,
        ):
            raw["pr_metadata"]["titleEdits"] = fetch_pr_title_edits(
                owner, repo_name, number
            )
        review_thread_pending_actions = build_review_thread_pending_actions(
            review_threads, review_thread_classifications
        )
        top_level_pending_actions, top_level_history = advance_top_level_actions(
            top_level_items,
            top_level_classifications,
            events,
            raw["pr_metadata"],
            author,
            previous_top_level_history,
        )
        pending_actions = review_thread_pending_actions | top_level_pending_actions
        failed_classifications = [
            classification
            for classification in (
                review_thread_classifications + top_level_classifications
            )
            if classification.get("failed")
        ]
        if failed_classifications:
            return {
                "pr_number": number,
                "pr_title": raw["pr"].get("title") or "",
                "pr_url": raw["pr"].get("url") or "",
                "failed": True,
                "facts": facts,
                "review_threads": review_threads,
                "top_level_items": top_level_items,
                "review_thread_classifications": review_thread_classifications,
                "top_level_classifications": top_level_classifications,
                "route": "unknown",
                "error": f"{len(failed_classifications)} discussion classification(s) failed",
            }
        route = route_pr(facts, pending_actions, required_approvals)
        add_wait_age_facts(facts, route, pending_actions)
        add_reviewers(
            facts, events, review_threads, top_level_items, pending_actions
        )
        return {
            "pr_number": number,
            "pr_title": raw["pr"].get("title") or "",
            "pr_url": raw["pr"].get("url") or "",
            "failed": False,
            "facts": facts,
            "review_threads": review_threads,
            "top_level_items": top_level_items,
            "review_thread_classifications": review_thread_classifications,
            "top_level_classifications": top_level_classifications,
            "pending_actions": pending_actions,
            "top_level_history": top_level_history,
            "route": route,
        }
    except TransientGhError as e:
        return {
            "pr_number": number,
            "failed": True,
            "facts": {},
            "review_threads": [],
            "top_level_items": [],
            "review_thread_classifications": [],
            "top_level_classifications": [],
            "route": "transient-failure",
            "error": repr(e),
        }
    except Exception as e:
        # Boundary: keep unexpected PR-specific exceptions as failed results
        # with tracebacks, so callers can fail cleanly instead of crashing
        # mid-refresh.
        print(f"  warning: PR #{number} failed to build result:", file=sys.stderr)
        traceback.print_exc()
        return {
            "pr_number": number,
            "failed": True,
            "facts": {},
            "review_threads": [],
            "top_level_items": [],
            "review_thread_classifications": [],
            "top_level_classifications": [],
            "route": "unknown",
            "error": repr(e),
        }


@dataclass
class DashboardUpdate:
    results: dict[int, dict[str, Any]]
    dashboard_state: dict[str, Any]
    trigger_pr_result: dict[str, Any] | None = None
    current_pr_result: dict[str, Any] | None = None
    starting_pr_result: dict[str, Any] | None = None
    used_cached_dashboard_state: bool = False


def build_dashboard_update_for_pr(
    repo: str,
    owner: str,
    repo_name: str,
    open_pr_numbers: set[int],
    reviewers: set[str],
    pr_number: int,
    model: str,
    required_approvals: int,
    dashboard_state: dict[str, Any],
) -> DashboardUpdate:
    print(f"refreshing dashboard state for PR #{pr_number}", file=sys.stderr)
    results = results_from_dashboard_state(dashboard_state, open_pr_numbers)
    starting_pr_result = results.get(pr_number)
    trigger_pr_result = build_pr_result(
        repo,
        owner,
        repo_name,
        {"number": pr_number},
        reviewers,
        model,
        required_approvals,
        previous_top_level_history=(starting_pr_result or {}).get("top_level_history") or {},
    )
    if trigger_pr_result is None:
        results.pop(pr_number, None)
    else:
        results[pr_number] = trigger_pr_result
    current_pr_result = stored_result(trigger_pr_result) if trigger_pr_result is not None else None
    return DashboardUpdate(
        results=results,
        dashboard_state=dashboard_state,
        trigger_pr_result=trigger_pr_result,
        current_pr_result=current_pr_result,
        starting_pr_result=starting_pr_result,
        used_cached_dashboard_state=True,
    )


def merge_dashboard_update_with_latest_state(
    calculation: DashboardUpdate,
    pr_number: int | None,
    open_pr_numbers: set[int],
) -> tuple[DashboardUpdate, bool]:
    if not pr_number or not calculation.used_cached_dashboard_state:
        return calculation, False

    if calculation.trigger_pr_result is None:
        # The trigger PR is a draft, closed, or was dropped between
        # list_open_prs and the worker run. Drop any outdated cached result so
        # the notification job cannot continue treating the PR as routed.
        dashboard_state = load_dashboard_state_cache()
        if dashboard_state is not None:
            previous_pr_result = dashboard_state["prs"].get(str(pr_number))
            if previous_pr_result != calculation.starting_pr_result:
                results = results_from_dashboard_state(dashboard_state, open_pr_numbers)
                return replace(calculation, results=results, dashboard_state=dashboard_state), True
        else:
            dashboard_state = calculation.dashboard_state
        dashboard_state = update_dashboard_state_for_pr(dashboard_state, pr_number, None)
        results = results_from_dashboard_state(dashboard_state, open_pr_numbers)
        return replace(calculation, results=results, dashboard_state=dashboard_state), False

    # Reload the cache so we pick up any concurrent writer's update of
    # other PR slots before we merge in our own.
    latest_dashboard_state = load_dashboard_state_cache()
    if latest_dashboard_state is None:
        previous_pr_result = None
    else:
        previous_pr_result = latest_dashboard_state["prs"].get(str(pr_number))
    dashboard_state = calculation.dashboard_state
    results = calculation.results

    if previous_pr_result == calculation.current_pr_result:
        if latest_dashboard_state is not None:
            dashboard_state = latest_dashboard_state
            results = results_from_dashboard_state(dashboard_state, open_pr_numbers)
        return replace(calculation, results=results, dashboard_state=dashboard_state), True

    if latest_dashboard_state is not None and previous_pr_result != calculation.starting_pr_result:
        results = results_from_dashboard_state(latest_dashboard_state, open_pr_numbers)
        return replace(calculation, results=results, dashboard_state=latest_dashboard_state), True

    if latest_dashboard_state is not None:
        dashboard_state = latest_dashboard_state
    dashboard_state = update_dashboard_state_for_pr(dashboard_state, pr_number, calculation.trigger_pr_result)
    results = results_from_dashboard_state(dashboard_state, open_pr_numbers)
    return replace(calculation, results=results, dashboard_state=dashboard_state), False


def dashboard_state_pr_numbers(state: dict[str, Any]) -> set[int]:
    numbers: set[int] = set()
    for key in (state.get("prs") or {}):
        try:
            numbers.add(int(key))
        except ValueError:
            continue
    return numbers


def backfill_cursor_pr_number(backfill_state: dict[str, Any]) -> int | None:
    cursor = backfill_state.get("cursor") or {}
    if not isinstance(cursor, dict):
        return None
    last_pr_number = cursor.get("last_pr_number")
    if last_pr_number is None:
        return None
    try:
        return int(last_pr_number)
    except (TypeError, ValueError):
        return None


def set_backfill_cursor_pr_number(backfill_state: dict[str, Any], number: int) -> None:
    backfill_state["cursor"] = {"last_pr_number": number}


def round_robin_numbers(numbers: list[int], last_number: int | None) -> list[int]:
    if last_number is None:
        return numbers
    return (
        [number for number in numbers if number > last_number]
        + [number for number in numbers if number <= last_number]
    )


@dataclass
class BackfillSelection:
    selected_prs: list[dict[str, Any]]
    cached_pr_numbers_to_remove: set[int]


def select_backfill_prs(
    prs: list[dict[str, Any]],
    dashboard_state: dict[str, Any],
    backfill_state: dict[str, Any],
    max_prs: int,
) -> BackfillSelection:
    open_prs_by_number = {p["number"]: p for p in prs if not p.get("isDraft")}
    open_numbers = sorted(open_prs_by_number)
    open_number_set = set(open_numbers)
    cached_numbers = dashboard_state_pr_numbers(dashboard_state)
    cached_pr_numbers_to_remove = cached_numbers - open_number_set
    selected_numbers = round_robin_numbers(open_numbers, backfill_cursor_pr_number(backfill_state))[:max_prs]
    return BackfillSelection(
        [open_prs_by_number[number] for number in selected_numbers],
        cached_pr_numbers_to_remove,
    )


def log_line_value(value: Any) -> str:
    return " ".join(str(value or "").split())


def log_multiline_value(label: str, value: Any) -> None:
    text = str(value or "").strip()
    if not text:
        return
    print(f"      {label}:", file=sys.stderr)
    print(f"      --- BEGIN {label} ---", file=sys.stderr)
    for line in text.splitlines():
        print(f"      | {line}", file=sys.stderr)
    print(f"      --- END {label} ---", file=sys.stderr)


def log_failed_classification_diagnostics(
    classification: dict[str, Any],
    discussion: dict[str, Any] | None,
) -> None:
    decision = classification.get("decision") or {}
    print(
        "    failed classification: "
        f"discussion_id={classification.get('discussion_id') or '<unknown>'} "
        f"kind={classification.get('discussion_kind') or '<unknown>'} "
        f"action={decision.get('discussion_action') or '<unknown>'} "
        f"reason={log_line_value(decision.get('reason')) or '<none>'}",
        file=sys.stderr,
    )
    if classification.get("error"):
        print(f"      error: {log_line_value(classification.get('error'))}", file=sys.stderr)
    if discussion:
        comments = discussion.get("comments") or []
        latest = comments[-1] if comments else {}
        location = discussion.get("path") or ""
        if location and discussion.get("line"):
            location = f"{location}:{discussion.get('line')}"
        print(
            "      discussion: "
            f"location={location or '<none>'} "
            f"latest_actor={latest.get('actor') or '<unknown>'} "
            f"latest_role={latest.get('actor_role') or '<unknown>'} "
            f"latest_at={latest.get('timestamp') or '<unknown>'}",
            file=sys.stderr,
        )
        if latest.get("body"):
            print(f"      latest_body: {log_line_value(latest.get('body'))}", file=sys.stderr)
    for key in ("response_text", "stderr"):
        if classification.get(key):
            log_multiline_value(key, classification.get(key))


def log_failed_result_diagnostics(result: dict[str, Any]) -> None:
    print("dashboard failure diagnostics:", file=sys.stderr)
    number = result.get("pr_number") or "?"
    print(
        f"  PR #{number}: route={result.get('route') or '<unknown>'} "
        f"error={log_line_value(result.get('error')) or '<none>'}",
        file=sys.stderr,
    )
    if result.get("pr_url"):
        print(f"    url: {result.get('pr_url')}", file=sys.stderr)
    discussions = {
        discussion.get("discussion_id"): discussion
        for discussion in (
            (result.get("review_threads") or [])
            + (result.get("top_level_items") or [])
        )
        if isinstance(discussion, dict) and discussion.get("discussion_id")
    }
    failed_classifications = [
        classification
        for classification in (
            (result.get("review_thread_classifications") or [])
            + (result.get("top_level_classifications") or [])
        )
        if classification.get("failed")
    ]
    for classification in failed_classifications:
        log_failed_classification_diagnostics(
            classification,
            discussions.get(classification.get("discussion_id")),
        )


def has_failed_dashboard_result(result: dict[str, Any] | None) -> bool:
    return bool(result and result.get("failed"))


def reject_failed_dashboard_result(result: dict[str, Any] | None) -> bool:
    if result is None or not has_failed_dashboard_result(result):
        return False
    number = result.get("pr_number") or "?"
    log_failed_result_diagnostics(result)
    print(
        f"dashboard refresh hit PR failure(s); refusing to publish failed state: #{number}",
        file=sys.stderr,
    )
    return True


def save_dashboard_update_state(
    args: argparse.Namespace,
    dashboard_state: dict[str, Any],
    dashboard_state_unchanged: bool,
) -> int:
    if dashboard_state_unchanged:
        if args.pr_number:
            print(f"PR #{args.pr_number} dashboard state unchanged", file=sys.stderr)
        else:
            print("dashboard state unchanged", file=sys.stderr)
        return 0

    save_dashboard_state_cache(dashboard_state)
    return 0


def update_backfill_cursor(pr_number: int) -> int:
    backfill_state = load_backfill_state()
    set_backfill_cursor_pr_number(backfill_state, pr_number)
    save_backfill_state(backfill_state)
    return 0


def remove_cached_dashboard_prs(
    args: argparse.Namespace,
    pr_numbers_to_remove: set[int],
) -> int:
    if not pr_numbers_to_remove:
        return 0
    dashboard_state = load_dashboard_state_cache() or empty_state()
    state_prs = dict(dashboard_state.get("prs") or {})
    for number in pr_numbers_to_remove:
        state_prs.pop(str(number), None)
    dashboard_state["prs"] = state_prs
    return save_dashboard_update_state(args, dashboard_state, False)


def build_targeted_dashboard_update(args: argparse.Namespace) -> DashboardUpdate | None:
    if args.pr_number is None:
        raise RuntimeError("build_targeted_dashboard_update requires --pr-number")

    repo = normalize_repo(args.repo) if args.repo else detect_repo()
    owner, repo_name = repo.split("/", 1)

    loaded_dashboard_state = load_dashboard_state_cache()
    if loaded_dashboard_state is None:
        print("dashboard result state not found; skipping targeted refresh", file=sys.stderr)
        return None

    reviewers = load_reviewer_set(owner, args.approver_team)
    return build_dashboard_update_for_pr(
        repo,
        owner,
        repo_name,
        {args.pr_number},
        reviewers,
        args.pr_number,
        args.model,
        args.required_approvals,
        loaded_dashboard_state,
    )


def apply_targeted_dashboard_update(args: argparse.Namespace, calculation: DashboardUpdate) -> int:
    merged_calculation, dashboard_state_unchanged = merge_dashboard_update_with_latest_state(
        calculation,
        args.pr_number,
        {args.pr_number} if args.pr_number is not None else set(),
    )
    if not dashboard_state_unchanged and reject_failed_dashboard_result(merged_calculation.trigger_pr_result):
        return 1

    return save_dashboard_update_state(
        args,
        merged_calculation.dashboard_state,
        dashboard_state_unchanged,
    )


def update_dashboard_for_pr_number(args: argparse.Namespace, state_dir: Path) -> int:
    if args.pr_number is None:
        raise RuntimeError("update_dashboard_for_pr_number requires --pr-number")

    state_branch.configure_git()
    state_branch.checkout_state(state_dir, args.state_branch, require_existing=False)
    try:
        update = build_targeted_dashboard_update(args)
    finally:
        state_branch.remove_existing_state_dir(state_dir)

    if update is None:
        return 0

    return state_branch.push_state_changes(
        state_dir,
        "Update dashboard state",
        lambda: apply_targeted_dashboard_update(args, update),
        state_branch=args.state_branch,
    )


def update_dashboard_for_backfill(args: argparse.Namespace, state_dir: Path) -> int:
    repo = normalize_repo(args.repo) if args.repo else detect_repo()
    owner, repo_name = repo.split("/", 1)
    prs = list_open_prs(repo)
    open_pr_numbers = {p["number"] for p in prs}
    open_non_draft_pr_numbers = {p["number"] for p in prs if not p.get("isDraft")}
    prune_classification_cache(open_pr_numbers)
    reviewers = load_reviewer_set(owner, args.approver_team)
    state_branch.configure_git()
    state_branch.checkout_state(state_dir, args.state_branch, require_existing=False)
    try:
        dashboard_state = load_dashboard_state_cache() or empty_state()
        backfill_state = load_backfill_state()
    finally:
        state_branch.remove_existing_state_dir(state_dir)
    selection = select_backfill_prs(
        prs,
        dashboard_state,
        backfill_state,
        DEFAULT_BACKFILL_MAX_PRS,
    )

    if selection.cached_pr_numbers_to_remove:
        status = state_branch.push_state_changes(
            state_dir,
            "Update dashboard state",
            lambda: remove_cached_dashboard_prs(
                args,
                selection.cached_pr_numbers_to_remove,
            ),
            state_branch=args.state_branch,
        )
        if status != 0:
            return status

    print(
        f"backfill selected {len(selection.selected_prs)} PR(s) "
        f"in {repo} (max={DEFAULT_BACKFILL_MAX_PRS})",
        file=sys.stderr,
    )

    # Empty or draft-only repositories still need accepted dashboard state for
    # the publish job, even when there are no non-draft PRs to refresh.
    if not selection.selected_prs:
        def save_current_dashboard_state() -> int:
            dashboard_state = load_dashboard_state_cache() or empty_state()
            return save_dashboard_update_state(args, dashboard_state, False)

        return state_branch.push_state_changes(
            state_dir,
            "Update dashboard state",
            save_current_dashboard_state,
            state_branch=args.state_branch,
        )

    for pr_summary in selection.selected_prs:
        def update_selected_pr(pr_summary: dict[str, Any] = pr_summary) -> int:
            pr_number = pr_summary["number"]
            dashboard_state = load_dashboard_state_cache() or empty_state()
            calculation = build_dashboard_update_for_pr(
                repo,
                owner,
                repo_name,
                open_non_draft_pr_numbers,
                reviewers,
                pr_number,
                args.model,
                args.required_approvals,
                dashboard_state,
            )
            calculation, dashboard_state_unchanged = merge_dashboard_update_with_latest_state(
                calculation,
                pr_number,
                open_non_draft_pr_numbers,
            )
            if not dashboard_state_unchanged and reject_failed_dashboard_result(calculation.trigger_pr_result):
                return 1
            status = save_dashboard_update_state(
                args,
                calculation.dashboard_state,
                dashboard_state_unchanged,
            )
            if status != 0:
                return status
            return update_backfill_cursor(pr_number)

        status = state_branch.push_state_changes(
            state_dir,
            "Update dashboard state",
            update_selected_pr,
            state_branch=args.state_branch,
        )
        if status != 0:
            return status
    return 0


def update_dashboard_via_state_branch(args: argparse.Namespace, state_dir: Path) -> int:
    if args.pr_number is None:
        return update_dashboard_for_backfill(args, state_dir)
    return update_dashboard_for_pr_number(args, state_dir)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--state-branch",
        required=True,
        help="git branch used for workflow state",
    )
    parser.add_argument("--repo", help="target repository name, e.g. opentelemetry-java-instrumentation")
    parser.add_argument(
        "--approver-team",
        action="append",
        required=True,
        help="approver team slug for the target repository; repeat for multiple teams",
    )
    parser.add_argument("--pr-number", type=int, help="only refresh dashboard state for this PR")
    parser.add_argument(
        "--required-approvals",
        type=int,
        default=1,
        help="minimum non-bot approvals needed before a PR can route to maintainers",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"copilot model (default: {DEFAULT_MODEL})")
    args = parser.parse_args()
    if args.required_approvals < 1:
        parser.error("--required-approvals must be at least 1")
    with state_branch.temporary_state_dir() as state_dir:
        repo_key = repo_state_key(args.repo) if args.repo else repo_state_key(detect_repo())
        set_state_dir(state_dir / repo_key)
        return update_dashboard_via_state_branch(args, state_dir)


if __name__ == "__main__":
    sys.exit(main())
