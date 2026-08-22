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

``result`` (one per PR) — produced by ``evaluate_pull_request``:

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

  Stage 1 — pull request evaluation (deterministic from GitHub data):
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
    reviewers                       list[dict]    Reviewer summaries projected after
                                                  discussion resolution. Each entry is
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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from github_cli import (
    detect_repo,
    list_open_prs,
    load_reviewer_set,
    normalize_repo,
    repo_state_key,
)
from classification import (
    prune_classification_cache,
)
from author_nudge import (
    record_author_nudge_observation,
)
from copilot_review_delivery import (
    record_copilot_review_observation,
)
from pull_request_evaluation import (
    PullRequestEvaluationConfig,
    PullRequestEvaluationInput,
    evaluate_pull_request,
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
from utils import utc_now

# --- CLI defaults ----------------------------------------------------------
DEFAULT_MODEL = "gpt-5.4-mini"
DEFAULT_BACKFILL_MAX_PRS = 50
BACKFILL_RECORDED_FAILURE_STATUS = 2

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
    trigger_pr_result = evaluate_pull_request(
        PullRequestEvaluationConfig(
            repo=repo,
            owner=owner,
            repo_name=repo_name,
            approver_logins=frozenset(reviewers),
            classifier_model=model,
            required_approvals=required_approvals,
            non_blocking_check_patterns=tuple(non_blocking_check_patterns),
            require_clean_copilot_review_branches=frozenset(
                require_clean_copilot_review_branches or []
            ),
        ),
        PullRequestEvaluationInput(
            pr_summary={"number": pr_number},
            previous_result=starting_pr_result,
        ),
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
