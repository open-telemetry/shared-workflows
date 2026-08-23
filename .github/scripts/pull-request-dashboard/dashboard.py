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
                                                               [--github-output PATH]
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

Status comments, author nudges, Copilot re-review requests, and Slack
notifications are delivered by delivery.py in one serialized publishing job.
That job loads the latest accepted dashboard state and delivery ledgers, sends
due updates, and pushes successful acknowledgements with the same git CAS
pattern.

State files are committed and pushed first. Only after that state branch push
succeeds does a follow-up publishing job fetch the accepted dashboard state,
render the dashboard body, and publish it to the dashboard issue.

Runs without --pr-number use backfill and store progress in
backfill-state.json. Every attempted PR advances that cursor. Failures are
recorded separately from accepted dashboard state, later PRs continue to
refresh, and the workflow exits nonzero while an open PR is still recorded as
having failed processing. Single-PR runs are
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
- ``top_level_author_comment_items`` (``list[dict]``): Author comments that could address
    top-level feedback. Internal.
- ``review_thread_classifications`` (``list[dict]``): Current inline actions. Internal.
- ``top_level_classifications`` (``list[dict]``): Immutable ledger decisions. Internal.
- ``top_level_author_comment_classifications`` (``list[dict]``): Per-feedback outcomes
    for candidate author replies. Internal.
- ``pending_actions`` (``dict[str, dict]``): Ephemeral current actions by discussion id;
    each entry contains ``action`` and ``since``.
- ``top_level_history`` (``dict[str, dict]``): Durable author-reply timestamps by
    top_level action id.
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
                                                  from approver-team members,
                                                  excluding reviewers whose review
                                                  has been requested again.
    ci_failing_count                int           Merge-blocking checks only;
                                                  absent when checks could not be
                                                  fetched.
    ci_failing_since                str (iso)     Earliest completion time among
                                                  current required failures.
    ci_pending_count                int           Merge-blocking checks only;
                                                  absent when checks could not be
                                                  fetched, and excludes required
                                                  contexts whose app has already
                                                  finished reporting.
    dashboard_override_head_sha     str           Head an override is bound to;
                                                  the handoff is active while it
                                                  equals head_sha.
    conflicts                       str           "yes" | "no" | "unknown".
    copilot_review_requested        bool          Copilot is a pending requested
                                                  reviewer, so a review is in
                                                  flight and the reviewers
                                                  column shows it as pending.
    copilot_review_exists           bool          Copilot has reviewed this PR
                                                  at least once.
    copilot_review_stale            bool          Copilot has reviewed this PR,
                                                  but no review covers the
                                                  current head, so a re-review
                                                  would see unreviewed code.
                                                  False when Copilot has never
                                                  reviewed; that PR is tracked
                                                  by
                                                  copilot_first_review_missing_since
                                                  instead.
    copilot_first_review_missing_since
                                    str (iso)     When the gate first observed
                                                  this non-draft PR with no
                                                  Copilot review at all. Carried
                                                  forward across passes, and
                                                  absent once a review exists,
                                                  the PR is a draft, or the gate
                                                  does not apply. Once it is
                                                  older than the grace period,
                                                  the automatic first review is
                                                  presumed lost and the
                                                  dashboard requests one.
    copilot_review_needed           bool          The review is stale or Copilot
                                                  owns open review threads,
                                                  meaning unresolved threads
                                                  GitHub has not marked
                                                  outdated.
    created_at                      str (iso)
    last_activity_at                str (iso)     Latest substantive activity by a
                                                  PR participant, never earlier
                                                  than PR creation time.
    last_author_activity_at         str (iso)
    last_approver_activity_at       str (iso)

    Stage 2 — resolve_routing (depends on pending actions + the previous result):
    copilot_review_outstanding      bool          The Copilot review gate applies
                                                  to this PR and its review is
                                                  missing or stale, so the route
                                                  is held.
    copilot_review_unreported       bool          The Copilot review gate applies
                                                  and Copilot has said nothing
                                                  about the current head, so the
                                                  gate is still waiting to
                                                  report. False once a review
                                                  covers this head, even when it
                                                  left open findings, because
                                                  those are the author's to
                                                  clear.
    route_held_for_gates            bool          The PR did not advance to the
                                                  route it computed, because
                                                  the required checks or the
                                                  Copilot review are still
                                                  outstanding.
    required_checks_settled         bool          Every required check has
                                                  reported on the current head,
                                                  so the computed route is not
                                                  provisional.
    route_held_since                str (iso)     When the gates first kept this
                                                  PR off its reviewers on this
                                                  head. Cleared once every gate
                                                  has reported or the author
                                                  pushes.
    route_hold_expired              bool          A gate has reported nothing on
                                                  this head for longer than the
                                                  four-hour hold limit, so the PR
                                                  routes anyway and its status
                                                  comment names the gate that
                                                  never reported.
    waiting_since                   str (iso)     Oldest pending discussion, or
                                                  route-appropriate fallback,
                                                  or PR creation time. Carried
                                                  forward while the handoff is
                                                  held, restarted when a held
                                                  handoff reaches reviewers,
                                                  restarted when a re-review
                                                  returns the PR to approvers,
                                                  and otherwise never moves
                                                  forward on a reviewer route.
    waiting_age_basis               str           Which heuristic chose
                                                  waiting_since.
    author_action_review_thread_urls
                                    list[str]     Canonical links to unresolved
                                                  inline review threads routed
                                                  to the author.
    author_action_top_level_feedback_urls
                                    list[str]     Canonical links to top-level
                                                  feedback routed to the author.
    reviewers                       list[dict]    Reviewers to display (added by
                                                  add_reviewers). Each entry is
                                                  {"login": str, "approved": bool,
                                                  "approved_non_team": bool,
                                                  "pending_review": bool,
                                                  "changes_requested": bool,
                                                  "open_thread": bool,
                                                  "top_level_feedback": bool}; approved
                                                  means an approver-team member
                                                  has an active APPROVED state,
                                                  approved_non_team means someone
                                                  outside the team has an active
                                                  APPROVED state,
                                                  pending_review means a human who
                                                  previously reviewed has a pending
                                                  re-review request,
                                                  changes_requested means a
                                                  reviewer's latest review is
                                                  CHANGES_REQUESTED, which a
                                                  re-review request does not
                                                  clear,
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
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from github_cli import (
    TransientGhError,
    detect_repo,
    fetch_pr_routing_raw,
    gh_api,
    list_open_prs,
    load_reviewer_set,
    normalize_repo,
    repo_state_key,
)
from classification import (
    classify_discussion_domains,
    normalize_discussion_action,
    prune_classification_cache,
)
from discussion_lifecycle import (
    DiscussionClassifications,
    DiscussionInput,
    LifecycleMode,
    prepare_discussions,
    resolve_discussions,
)
from author_nudge import (
    record_author_nudge_observation,
)
from copilot_review import (
    copilot_review_status,
    is_copilot_reviewer,
    record_copilot_review_observation,
)
from dashboard_override import (
    append_command_ack_reply,
    dashboard_command_body_remainder,
    dashboard_override_facts,
)
from pr_status_comment import status_author_nudge_episode_id
from pull_request_activity import (
    is_substantive_activity,
    reviewer_actor_login,
    role_for,
)
from routing_snapshot import build_routing_snapshot
from routing_decision import (
    RoutingInput,
    resolve_routing,
    reviewer_handoff_active,
    routing_failure_facts,
)
from state import (
    INITIAL_BACKFILL_COMPLETE_KEY,
    empty_state,
    enqueue_status_comment_update,
    initial_backfill_complete,
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
from utils import (
    actor_login,
    compute_conflicts,
    format_ts,
    parse_ts,
    utc_now,
)

# --- CLI defaults ----------------------------------------------------------
DEFAULT_MODEL = "gpt-5.4-mini"
DEFAULT_BACKFILL_MAX_PRS = 50
BACKFILL_RECORDED_FAILURE_STATUS = 2

# ---------------------------------------------------------------- model helpers


# `role_for` matches the PR's own author before it checks for bot-shaped logins,
# so a bot-authored PR counts that bot's activity here.
PARTICIPANT_ACTOR_ROLES = {"author", "approver", "outsider"}


# Copilot appears in two API shapes: `gh pr view`'s `author` field uses the
# `app/<slug>` form, while the Pulls/commits endpoint's `committer.login`
# field can return the bare `copilot` slug. Do not treat either form as the
# human author behind a Copilot-authored PR.
_COPILOT_COMMITTER_LOGINS = {"copilot"}
_COPILOT_PR_AUTHORS = {"app/copilot-swe-agent", "copilot"}
_MAINTENANCE_BOT_PR_AUTHORS = {"app/otelbot", "app/renovate"}


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
    non_blocking_check_patterns: list[str],
) -> dict[str, Any]:
    number = pr_summary["number"]
    with ThreadPoolExecutor() as pool:
        f_commits = pool.submit(
            gh_api,
            f"/repos/{owner}/{repo_name}/pulls/{number}/commits?per_page=100",
            True,
        )
        raw = fetch_pr_routing_raw(
            repo,
            owner,
            repo_name,
            number,
            non_blocking_check_patterns,
        )
        return {
            **raw,
            "summary": pr_summary,
            "commits": f_commits.result() or [],
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
        if c.get("minimized"):
            continue
        command_remainder = dashboard_command_body_remainder(c)
        # A `/dashboard` command line is control metadata, not discussion. Skip
        # the comment only when it is command-only; otherwise keep the author's
        # explanation that follows the command as an event.
        if command_remainder is not None and not command_remainder:
            continue
        body = command_remainder if command_remainder is not None else (c.get("body") or "")
        login = reviewer_actor_login(c.get("user") or {})
        timestamp = (
            c.get("content_updated_at")
            or c.get("created_at")
            or c.get("updated_at")
            or ""
        )
        events.append({
            "source_id": c.get("id"),
            "discussion_url": c.get("html_url") or "",
            "kind": "issue-comment",
            "timestamp": timestamp,
            "created_timestamp": c.get("created_at") or timestamp,
            "actor": login,
            "actor_role": role_for(login, author, reviewers),
            "body": body,
            "state": None,
            "path": None,
            "sha": None,
            "is_merge_from_base_by_non_author": False,
        })
    for c in raw["review_comments"]:
        login = reviewer_actor_login(c.get("user") or {})
        timestamp = c.get("updated_at") or c.get("created_at") or ""
        events.append({
            "source_id": c.get("id"),
            "kind": "review-comment",
            "timestamp": timestamp,
            "created_timestamp": c.get("created_at") or timestamp,
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
            "discussion_url": r.get("url") or "",
            "kind": "review-state",
            "timestamp": r.get("submitted_at") or "",
            "created_timestamp": r.get("submitted_at") or "",
            "actor": login,
            "actor_role": role_for(login, author, reviewers),
            "body": r.get("body") or "",
            "state": state,
            "path": None,
            "sha": None,
            "is_merge_from_base_by_non_author": False,
        })
    events = [e for e in events if e["timestamp"]]
    events.sort(key=lambda e: e.get("created_timestamp") or e["timestamp"])
    return events


def latest_substantive_activity(events: list[dict[str, Any]], actor_roles: set[str]) -> datetime | None:
    timestamps = [
        parse_ts(e["timestamp"])
        for e in events
        if e.get("actor_role") in actor_roles and is_substantive_activity(e)
    ]
    timestamps = [ts for ts in timestamps if ts is not None]
    return max(timestamps) if timestamps else None


def current_approval_count(
    events: list[dict[str, Any]],
    review_requests: list[dict[str, Any]] | None = None,
) -> int:
    approvers = approver_logins(events)
    return sum(
        1
        for reviewer, state in active_review_states(events, review_requests).items()
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


def requested_human_reviewer_logins(
    review_requests: list[dict[str, Any]] | None,
) -> set[str]:
    return {
        login
        for request in (review_requests or [])
        if request.get("__typename") == "User"
        and (login := reviewer_actor_login(request))
    }


def pending_review_logins(
    events: list[dict[str, Any]],
    review_requests: list[dict[str, Any]] | None,
) -> set[str]:
    return requested_human_reviewer_logins(review_requests) & reviewing_logins(events)


def active_review_states(
    events: list[dict[str, Any]],
    review_requests: list[dict[str, Any]] | None = None,
) -> dict[str, str]:
    # Only an approval goes stale when its reviewer is asked to look again.
    # GitHub keeps a CHANGES_REQUESTED review blocking the merge until that
    # reviewer submits a new one, so dropping it here would hide a live
    # blocker rather than report a wait.
    requested = requested_human_reviewer_logins(review_requests)
    return {
        reviewer: state
        for reviewer, state in latest_review_states(events).items()
        if state != "APPROVED" or reviewer not in requested
    }


def reviewing_logins(events: list[dict[str, Any]]) -> set[str]:
    return {
        event["actor"]
        for event in events
        if event.get("kind") == "review-state" and event.get("actor")
    }


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
    reviewers: set[str] | None = None,
    previous_facts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pr = raw["pr"]
    snapshot = build_routing_snapshot(raw)
    checks = snapshot.checks
    failing = [c for c in checks or [] if c.get("bucket") in ("fail", "cancel")]
    pending = [c for c in checks or [] if c.get("bucket") == "pending"]
    failing_timestamps = [parse_ts(c.get("completed_at") or "") for c in failing]
    failing_timestamps = [ts for ts in failing_timestamps if ts is not None]
    created_ts = parse_ts(pr["createdAt"])
    # Not pr["updatedAt"]: the dashboard's own status comment bumps it, which
    # would make every refresh look like new activity and retrigger itself.
    activity_ts = latest_substantive_activity(events, PARTICIPANT_ACTOR_ROLES)
    # Commits can carry author dates from before the PR was opened.
    last_activity_ts = max(
        [ts for ts in (activity_ts, created_ts) if ts is not None],
        default=None,
    )
    author_activity_ts = latest_substantive_activity(events, {"author"})
    approver_activity_ts = latest_substantive_activity(events, {"approver"})
    api_author = actor_login(pr.get("author") or {})
    assignees = [reviewer_actor_login(a) for a in (pr.get("assignees") or [])]
    assignees = [a for a in assignees if a]
    # Read the head OID straight from the PR object. Deriving it from
    # raw["commits"] is wrong for PRs with more than 250 commits, where the
    # commits REST endpoint truncates and the last entry is not the real head.
    head_sha = snapshot.head_sha
    copilot_review_exists, copilot_review_stale, copilot_review_findings = copilot_review_status(
        raw.get("reviews") or [],
        head_sha,
        snapshot.review_threads,
    )
    facts = {
        "author": author,
        "assignees": assignees,
        "head_sha": head_sha,
        "routing_input_fingerprint": snapshot.routing_input_fingerprint,
        "copilot_request_fingerprint": snapshot.copilot_request_fingerprint,
        **dashboard_override_facts(
            raw,
            author,
            reviewers or set(),
            head_sha,
            previous_facts,
        ),
        "copilot_review_requested": any(
            is_copilot_reviewer(request)
            for request in snapshot.review_requests
        ),
        "copilot_review_exists": copilot_review_exists,
        "copilot_review_stale": copilot_review_stale,
        "copilot_review_needed": copilot_review_stale or copilot_review_findings,
        "is_maintenance_bot": api_author.lower() in _MAINTENANCE_BOT_PR_AUTHORS,
        "is_draft": snapshot.is_draft,
        "approval_count": current_approval_count(
            events,
            snapshot.review_requests,
        ),
        "conflicts": compute_conflicts(pr),
        "created_at": format_ts(created_ts),
        "last_activity_at": format_ts(last_activity_ts),
        "last_author_activity_at": format_ts(author_activity_ts),
        "last_approver_activity_at": format_ts(approver_activity_ts),
    }
    if checks is not None:
        facts["ci_failing_count"] = len(failing)
        if failing_timestamps:
            facts["ci_failing_since"] = format_ts(min(failing_timestamps))
        facts["ci_pending_count"] = len(pending)
    non_blocking_check_failures = sorted({
        check.get("name") or ""
        for check in raw.get("non_blocking_check_failures") or []
        if check.get("name")
    }, key=lambda name: (name.casefold(), name))
    if non_blocking_check_failures:
        facts["non_blocking_check_failures"] = non_blocking_check_failures
    return facts


# ---------------------------------------------------------------- routing


def author_action_discussion_urls(
    discussions: list[dict[str, Any]],
    pending_actions: dict[str, dict[str, Any]],
) -> list[str]:
    by_id = {discussion["discussion_id"]: discussion for discussion in discussions}
    urls: list[str] = []
    for discussion_id, entry in pending_actions.items():
        action = normalize_discussion_action(entry.get("action") or "")
        if action != "author":
            continue
        thread = by_id.get(discussion_id)
        url = (thread or {}).get("discussion_url") or ""
        if url and url not in urls:
            urls.append(url)
    return urls


# Discussion actions that count as open and unresolved. A reviewer who commented
# in such a discussion is not yet satisfied, even if they have approved.
# "none" means no follow-up is needed, so it does not block a clear check.
OPEN_DISCUSSION_ACTIONS = {"author", "reviewer"}


def reviewers_with_open_threads(
    review_threads: list[dict[str, Any]],
    pending_actions: dict[str, dict[str, Any]],
) -> set[str]:
    logins: set[str] = set()
    for discussion in review_threads:
        entry = pending_actions.get(discussion["discussion_id"]) or {}
        action = entry.get("action")
        if action not in OPEN_DISCUSSION_ACTIONS:
            continue
        # praise was dropped for routing, so it does not make its writer a reviewer here
        comments = discussion.get("comments") or []
        if entry.get("ignored_last_comment"):
            comments = comments[:-1]
        for comment in comments:
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
    review_requests: list[dict[str, Any]] | None = None,
) -> None:
    # Reviewers to display in the dashboard, each flagged with their review
    # stance: approved (by an approver-team member), approved_non_team (an
    # approval from someone outside the team), pending_review (a human review
    # was requested again), changes_requested (their latest review blocks),
    # open_thread (they own an
    # unresolved discussion), and top_level_feedback (their top-level feedback
    # still needs author action). The renderer turns these into icons.
    # Reviewers are everyone who reviewed, owns an open discussion, otherwise
    # commented, or is a PR assignee, sorted alphabetically (case-insensitive).
    pending_reviews = pending_review_logins(events, review_requests)
    states = active_review_states(events, review_requests)
    approvers = approver_logins(events)
    approved = {r for r, s in states.items() if s == "APPROVED" and r in approvers}
    approved_non_team = {r for r, s in states.items() if s == "APPROVED" and r not in approvers}
    changes_requested = {r for r, s in states.items() if s == "CHANGES_REQUESTED"}
    with_open = reviewers_with_open_threads(review_threads, pending_actions)
    with_top_level = reviewers_with_top_level_feedback(top_level_items, pending_actions)
    candidates = (
        approved
        | approved_non_team
        | pending_reviews
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
            "pending_review": login in pending_reviews,
            "changes_requested": login in changes_requested,
            "open_thread": login in with_open,
            "top_level_feedback": login in with_top_level,
        }
        for login in sorted(candidates, key=str.lower)
    ]


def assign_author_nudge_episode(
    facts: dict[str, Any],
    route: str,
    previous_result: dict[str, Any] | None,
    issue_comments: list[dict[str, Any]],
) -> None:
    if route != "author":
        facts.pop("author_nudge_episode_id", None)
        return
    previous_facts = (previous_result or {}).get("facts") or {}
    previous_episode_id = (
        previous_facts.get("author_nudge_episode_id")
        if (previous_result or {}).get("route") == "author"
        else ""
    )
    recovered_episode_id = (
        status_author_nudge_episode_id(issue_comments)
        if previous_result is None
        else ""
    )
    facts["author_nudge_episode_id"] = str(
        previous_episode_id or recovered_episode_id or uuid.uuid4().hex
    )


# ---------------------------------------------------------------- main


def build_pr_result(
    repo: str,
    owner: str,
    repo_name: str,
    pr_summary: dict[str, Any],
    reviewers: set[str],
    model: str,
    required_approvals: int,
    non_blocking_check_patterns: list[str],
    previous_top_level_history: dict[str, dict[str, Any]] | None = None,
    require_clean_copilot_review_branches: list[str] | None = None,
    previous_result: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    number = pr_summary["number"]
    try:
        raw = fetch_pr_raw(
            repo,
            owner,
            repo_name,
            pr_summary,
            non_blocking_check_patterns,
        )
        if raw["pr"].get("state") != "OPEN" or raw["pr"].get("isDraft"):
            return None
        author = effective_author(raw)
        events = normalize_events(raw, author, reviewers)
        previous_facts = (previous_result or {}).get("facts") or {}
        facts = compute_facts(raw, author, events, reviewers, previous_facts)
        manual_reviewer_handoff = reviewer_handoff_active(facts)
        prepared_discussions = prepare_discussions(
            DiscussionInput(
                tuple(raw["review_threads"]),
                tuple(events),
                author,
                frozenset(reviewers),
                facts.get("conflicts") or "unknown",
            )
        )
        if manual_reviewer_handoff:
            lifecycle = resolve_discussions(
                prepared_discussions,
                None,
                previous_top_level_history,
                mode=LifecycleMode.REVIEWER_HANDOFF,
            )
        else:
            (
                review_thread_classifications,
                top_level_classifications,
                top_level_author_comment_classifications,
            ) = classify_discussion_domains(
                number,
                list(prepared_discussions.review_threads),
                list(prepared_discussions.top_level_items),
                list(prepared_discussions.top_level_author_comment_items),
                model,
            )
            lifecycle = resolve_discussions(
                prepared_discussions,
                DiscussionClassifications(
                    tuple(review_thread_classifications),
                    tuple(top_level_classifications),
                    tuple(top_level_author_comment_classifications),
                ),
                previous_top_level_history,
            )
        lifecycle_fields = lifecycle.dashboard_fields()
        failed_classifications = lifecycle.failed_classifications
        if failed_classifications:
            facts = routing_failure_facts(facts, previous_facts)
            return {
                "pr_number": number,
                "pr_title": raw["pr"].get("title") or "",
                "pr_url": raw["pr"].get("url") or "",
                "failed": True,
                "facts": facts,
                **lifecycle_fields,
                "route": "unknown",
                "error": f"{len(failed_classifications)} discussion classification(s) failed",
            }
        pending_actions = lifecycle.pending_actions
        review_threads = list(prepared_discussions.review_threads)
        top_level_items = list(prepared_discussions.top_level_items)
        require_clean_copilot_review = (raw["pr"].get("baseRefName") or "") in (
            require_clean_copilot_review_branches or []
        )
        routing_outcome = resolve_routing(
            RoutingInput(
                facts=facts,
                pending_actions=pending_actions,
                previous_route=(previous_result or {}).get("route"),
                previous_facts=previous_facts,
                required_approvals=required_approvals,
                require_clean_copilot_review=require_clean_copilot_review,
                manual_reviewer_handoff=manual_reviewer_handoff,
                pending_human_reviewer_logins=frozenset(
                    pending_review_logins(
                        events,
                        raw.get("review_requests") or [],
                    )
                ),
            )
        )
        route = routing_outcome.route
        facts = routing_outcome.facts
        assign_author_nudge_episode(
            facts,
            route,
            previous_result,
            raw.get("issue_comments") or [],
        )
        append_command_ack_reply(raw, facts, route)
        facts["author_action_review_thread_urls"] = author_action_discussion_urls(
            review_threads, pending_actions
        )
        facts["author_action_top_level_feedback_urls"] = author_action_discussion_urls(
            top_level_items, pending_actions
        )
        add_reviewers(
            facts,
            events,
            review_threads,
            top_level_items,
            pending_actions,
            raw.get("review_requests") or [],
        )
        return {
            "pr_number": number,
            "pr_title": raw["pr"].get("title") or "",
            "pr_url": raw["pr"].get("url") or "",
            "failed": False,
            "facts": facts,
            **lifecycle_fields,
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
    non_blocking_check_patterns: list[str],
    dashboard_state: dict[str, Any],
    require_clean_copilot_review_branches: list[str] | None = None,
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
        non_blocking_check_patterns,
        previous_top_level_history=(starting_pr_result or {}).get("top_level_history") or {},
        require_clean_copilot_review_branches=require_clean_copilot_review_branches,
        previous_result=starting_pr_result,
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
            previous_pr_result = calculation.starting_pr_result
        if previous_pr_result is None:
            # Nothing is cached for this PR, so there is no routed result to
            # drop. Reporting a change here would queue a status comment for a
            # PR the dashboard never tracked, which is how an event on a
            # long-merged PR ends up posting a first status comment on it.
            results = results_from_dashboard_state(dashboard_state, open_pr_numbers)
            return replace(calculation, results=results, dashboard_state=dashboard_state), True
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


def complete_initial_backfill_if_ready(
    state: dict[str, Any],
    open_pr_numbers: set[int],
    failed_pr_numbers: set[int] | None = None,
) -> bool:
    if initial_backfill_complete(state):
        return False
    attempted_pr_numbers = dashboard_state_pr_numbers(state) | (failed_pr_numbers or set())
    if not open_pr_numbers.issubset(attempted_pr_numbers):
        return False
    state[INITIAL_BACKFILL_COMPLETE_KEY] = True
    return True


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


def backfill_failed_pr_numbers(backfill_state: dict[str, Any]) -> set[int]:
    numbers: set[int] = set()
    for value in (backfill_state.get("failed_pr_numbers") or []):
        try:
            numbers.add(int(value))
        except (TypeError, ValueError):
            continue
    return numbers


def set_backfill_pr_failed(
    backfill_state: dict[str, Any],
    number: int,
    failed: bool,
) -> set[int]:
    failed_pr_numbers = backfill_failed_pr_numbers(backfill_state)
    if failed:
        failed_pr_numbers.add(number)
    else:
        failed_pr_numbers.discard(number)
    backfill_state["failed_pr_numbers"] = sorted(failed_pr_numbers)
    return failed_pr_numbers


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
            + (result.get("top_level_author_comment_items") or [])
        )
        if isinstance(discussion, dict) and discussion.get("discussion_id")
    }
    failed_classifications = [
        classification
        for classification in (
            (result.get("review_thread_classifications") or [])
            + (result.get("top_level_classifications") or [])
            + (result.get("top_level_author_comment_classifications") or [])
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


def update_backfill_progress(pr_number: int, *, failed: bool) -> set[int]:
    backfill_state = load_backfill_state()
    set_backfill_cursor_pr_number(backfill_state, pr_number)
    failed_pr_numbers = set_backfill_pr_failed(backfill_state, pr_number, failed)
    save_backfill_state(backfill_state)
    return failed_pr_numbers


def clear_backfill_pr_failure(pr_number: int) -> None:
    backfill_state = load_backfill_state()
    if pr_number not in backfill_failed_pr_numbers(backfill_state):
        return
    set_backfill_pr_failed(backfill_state, pr_number, False)
    save_backfill_state(backfill_state)


def remove_cached_dashboard_prs(
    args: argparse.Namespace,
    pr_numbers_to_remove: set[int],
    observed_at: datetime | None = None,
) -> int:
    if not pr_numbers_to_remove:
        return 0
    dashboard_state = load_dashboard_state_cache() or empty_state()
    state_prs = dict(dashboard_state.get("prs") or {})
    observed_at = observed_at or utc_now()
    for number in pr_numbers_to_remove:
        state_prs.pop(str(number), None)
        enqueue_status_comment_update(number)
        record_author_nudge_observation(number, None, observed_at)
        record_copilot_review_observation(number, None, observed_at)
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
        args.non_blocking_check_pattern,
        loaded_dashboard_state,
        getattr(args, "require_clean_copilot_review_branches", []),
    )


def apply_targeted_dashboard_update(
    args: argparse.Namespace,
    calculation: DashboardUpdate,
    observed_at: datetime | None = None,
) -> int:
    merged_calculation, dashboard_state_unchanged = merge_dashboard_update_with_latest_state(
        calculation,
        args.pr_number,
        {args.pr_number} if args.pr_number is not None else set(),
    )
    if not dashboard_state_unchanged and reject_failed_dashboard_result(merged_calculation.trigger_pr_result):
        return 1
    if merged_calculation.trigger_pr_result is not None and not has_failed_dashboard_result(
        merged_calculation.trigger_pr_result
    ):
        clear_backfill_pr_failure(args.pr_number)
    if not dashboard_state_unchanged and args.pr_number is not None:
        enqueue_status_comment_update(args.pr_number)
    if args.pr_number is not None:
        observed_at = observed_at or utc_now()
        accepted_result = (merged_calculation.dashboard_state.get("prs") or {}).get(
            str(args.pr_number)
        )
        record_author_nudge_observation(
            args.pr_number,
            accepted_result,
            observed_at,
            prepare_due=getattr(args, "prepare_author_nudges", False),
        )
        record_copilot_review_observation(args.pr_number, accepted_result, observed_at)

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

    observed_at = utc_now()
    return state_branch.push_state_changes(
        state_dir,
        "Update dashboard state",
        lambda: apply_targeted_dashboard_update(args, update, observed_at),
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
        observed_at = utc_now()
        status = state_branch.push_state_changes(
            state_dir,
            "Update dashboard state",
            lambda: remove_cached_dashboard_prs(
                args,
                selection.cached_pr_numbers_to_remove,
                observed_at,
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
            complete_initial_backfill_if_ready(dashboard_state, open_non_draft_pr_numbers)
            return save_dashboard_update_state(args, dashboard_state, False)

        return state_branch.push_state_changes(
            state_dir,
            "Update dashboard state",
            save_current_dashboard_state,
            state_branch=args.state_branch,
        )

    for pr_summary in selection.selected_prs:
        observed_at = utc_now()

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
                args.non_blocking_check_pattern,
                dashboard_state,
                getattr(args, "require_clean_copilot_review_branches", []),
            )
            calculation, dashboard_state_unchanged = merge_dashboard_update_with_latest_state(
                calculation,
                pr_number,
                open_non_draft_pr_numbers,
            )
            if not dashboard_state_unchanged and reject_failed_dashboard_result(calculation.trigger_pr_result):
                failed_pr_numbers = update_backfill_progress(pr_number, failed=True)
                initial_backfill_completed = complete_initial_backfill_if_ready(
                    dashboard_state,
                    open_non_draft_pr_numbers,
                    failed_pr_numbers,
                )
                return save_dashboard_update_state(
                    args,
                    dashboard_state,
                    not initial_backfill_completed,
                )
            record_author_nudge_observation(
                pr_number,
                (calculation.dashboard_state.get("prs") or {}).get(str(pr_number)),
                observed_at,
                prepare_due=getattr(args, "prepare_author_nudges", False),
            )
            record_copilot_review_observation(
                pr_number,
                (calculation.dashboard_state.get("prs") or {}).get(str(pr_number)),
                observed_at,
            )
            failed_pr_numbers = update_backfill_progress(pr_number, failed=False)
            if not dashboard_state_unchanged:
                enqueue_status_comment_update(pr_number)
            initial_backfill_completed = complete_initial_backfill_if_ready(
                calculation.dashboard_state,
                open_non_draft_pr_numbers,
                failed_pr_numbers,
            )
            return save_dashboard_update_state(
                args,
                calculation.dashboard_state,
                dashboard_state_unchanged and not initial_backfill_completed,
            )

        status = state_branch.push_state_changes(
            state_dir,
            "Update dashboard state",
            update_selected_pr,
            state_branch=args.state_branch,
        )
        if status != 0:
            return status
    unresolved_failed_pr_numbers = (
        backfill_failed_pr_numbers(load_backfill_state())
        & open_non_draft_pr_numbers
    )
    if unresolved_failed_pr_numbers:
        failed_list = ", ".join(f"#{number}" for number in sorted(unresolved_failed_pr_numbers))
        print(
            f"backfill completed with PR(s) still recorded as failed: {failed_list}",
            file=sys.stderr,
        )
        return BACKFILL_RECORDED_FAILURE_STATUS
    return 0


def update_dashboard_via_state_branch(args: argparse.Namespace, state_dir: Path) -> int:
    if args.pr_number is None:
        return update_dashboard_for_backfill(args, state_dir)
    return update_dashboard_for_pr_number(args, state_dir)


def write_initial_backfill_output(github_output: Path) -> None:
    complete = initial_backfill_complete(load_dashboard_state_cache())
    with github_output.open("a", encoding="utf-8") as output:
        output.write(f"initial_backfill_complete={'true' if complete else 'false'}\n")


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
    parser.add_argument(
        "--non-blocking-check-pattern",
        action="append",
        default=[],
        help="glob matching a non-required check to mention when it fails; repeat as needed",
    )
    parser.add_argument(
        "--require-clean-copilot-review-branch",
        action="append",
        default=[],
        dest="require_clean_copilot_review_branches",
        metavar="BRANCH",
        help="require a clean Copilot review before reviewer or maintainer handoff for PRs targeting this base branch; repeat as needed",
    )
    parser.add_argument(
        "--prepare-author-nudges",
        action="store_true",
        help="queue due author nudges from accepted dashboard results",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"copilot model (default: {DEFAULT_MODEL})")
    parser.add_argument(
        "--github-output",
        type=Path,
        help="append initial_backfill_complete to this GitHub Actions output file",
    )
    args = parser.parse_args()
    if args.required_approvals < 1:
        parser.error("--required-approvals must be at least 1")
    with state_branch.temporary_state_dir() as state_dir:
        repo_key = repo_state_key(args.repo) if args.repo else repo_state_key(detect_repo())
        set_state_dir(state_dir / repo_key)
        status = update_dashboard_via_state_branch(args, state_dir)
        if args.github_output and status in (0, BACKFILL_RECORDED_FAILURE_STATUS):
            write_initial_backfill_output(args.github_output)
        return status


if __name__ == "__main__":
    sys.exit(main())
