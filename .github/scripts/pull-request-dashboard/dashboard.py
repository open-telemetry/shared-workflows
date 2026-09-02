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
  accept_dashboard_update
       reconcile the evaluated PR slot with the latest accepted dashboard state
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

Open drafts produce an explicit non-routed evaluation with only ``pr_number``,
``pr_title``, and ``pr_url``. Closed or missing pull requests produce no result.

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
    author_can_act                  bool          Effective author can respond
                                                  to author-routed work.
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
    dashboard_override_bound_command_id
                                      int         ID of the command bound to the
                                                  reviewer handoff.
    dashboard_override_head_sha     str           Head an override is bound to;
                                                  the handoff is active while it
                                                  equals head_sha and has not
                                                  been cleared by newer feedback.
    dashboard_override_since        str (iso)     Effective content timestamp of
                                                  the command bound to the handoff.
    dashboard_override_cleared_by_feedback
                                      bool        Actionable human reviewer
                                                  feedback ended the handoff.
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
from dataclasses import dataclass
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
from classification_execution import (
    DEFAULT_CLASSIFICATION_CACHE_STORE,
)
from classification_policy import (
    ActionDecision,
    ClassificationFailure,
)
from author_nudge import (
    record_author_nudge_observation,
)
from copilot_review_delivery import (
    record_copilot_review_observation,
)
from dashboard_contracts import (
    DashboardState,
    EvaluationFailure,
    EvaluationResult,
)
from dashboard_state_update import (
    DashboardStateUpdate,
    DashboardUpdateAcceptance,
    accept_dashboard_update,
    prepare_dashboard_update,
)
from pull_request_evaluation import (
    PullRequestEvaluationConfig,
    PullRequestEvaluationInput,
    evaluate_pull_request,
)
from state import (
    empty_state,
    enqueue_status_comment_update,
    initial_backfill_complete,
    load_dashboard_state_cache,
    load_backfill_state,
    save_dashboard_state_cache,
    save_backfill_state,
    set_state_dir,
)
import state_branch
from utils import utc_now

# --- CLI defaults ----------------------------------------------------------
DEFAULT_MODEL = "gpt-5.4-mini"
DEFAULT_BACKFILL_MAX_PRS = 50
BACKFILL_RECORDED_FAILURE_STATUS = 2

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
    dashboard_state: DashboardState,
    require_clean_copilot_review_branches: list[str] | None = None,
) -> DashboardStateUpdate:
    print(f"refreshing dashboard state for PR #{pr_number}", file=sys.stderr)
    prepared_update = prepare_dashboard_update(
        dashboard_state,
        open_pr_numbers,
        pr_number,
    )
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
            pr_number=pr_number,
            previous_result=prepared_update.starting_result,
        ),
    )
    return prepared_update.with_evaluated_result(trigger_pr_result)


def dashboard_state_pr_numbers(state: DashboardState) -> set[int]:
    return set(state.pr_numbers)


def complete_initial_backfill_if_ready(
    state: DashboardState,
    open_pr_numbers: set[int],
    failed_pr_numbers: set[int] | None = None,
) -> DashboardState:
    if initial_backfill_complete(state):
        return state
    attempted_pr_numbers = (
        dashboard_state_pr_numbers(state)
        | set(state.draft_pr_numbers)
        | (failed_pr_numbers or set())
    )
    if not open_pr_numbers.issubset(attempted_pr_numbers):
        return state
    return state.with_initial_backfill_complete()


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
    dashboard_state: DashboardState,
    backfill_state: dict[str, Any],
    max_prs: int,
) -> BackfillSelection:
    open_prs_by_number = {p["number"]: p for p in prs}
    open_number_set = set(open_prs_by_number)
    selected_prs_by_number = {
        number: pr
        for number, pr in open_prs_by_number.items()
        if (
            not pr.get("isDraft")
            or not dashboard_state.is_draft(number)
        )
    }
    tracked_numbers = (
        dashboard_state_pr_numbers(dashboard_state)
        | set(dashboard_state.draft_pr_numbers)
    )
    cached_pr_numbers_to_remove = tracked_numbers - open_number_set
    selected_numbers = round_robin_numbers(
        sorted(selected_prs_by_number),
        backfill_cursor_pr_number(backfill_state),
    )[:max_prs]
    return BackfillSelection(
        [selected_prs_by_number[number] for number in selected_numbers],
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
    classification: ClassificationFailure,
    discussion: dict[str, Any] | None,
) -> None:
    decision = classification.decision
    action = (
        decision.action.value
        if isinstance(decision, ActionDecision)
        else ""
    )
    reason = decision.reason
    print(
        "    failed classification: "
        f"discussion_id={classification.identity.discussion_id} "
        f"kind={classification.identity.kind.value} "
        f"action={action or '<unknown>'} "
        f"reason={log_line_value(reason) or '<none>'}",
        file=sys.stderr,
    )
    diagnostics = classification.diagnostics
    if diagnostics.error:
        print(
            f"      error: {log_line_value(diagnostics.error)}",
            file=sys.stderr,
        )
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
    for key, value in (
        ("response_text", diagnostics.response_text),
        ("stderr", diagnostics.stderr),
    ):
        if value:
            log_multiline_value(key, value)


def log_failed_result_diagnostics(result: EvaluationFailure) -> None:
    print("dashboard failure diagnostics:", file=sys.stderr)
    number = result.pr_number
    print(
        f"  PR #{number}: route={result.route.value} "
        f"error={log_line_value(result.error) or '<none>'}",
        file=sys.stderr,
    )
    if result.pr_url:
        print(f"    url: {result.pr_url}", file=sys.stderr)
    discussions = {
        discussion.get("discussion_id"): discussion
        for discussion in (
            result.diagnostics.review_threads
            + result.diagnostics.top_level_items
            + result.diagnostics.top_level_author_comment_items
        )
        if discussion.get("discussion_id")
    }
    failed_classifications = [
        classification
        for classification in (
            result.diagnostics.review_thread_classifications
            + result.diagnostics.top_level_classifications
            + result.diagnostics.top_level_author_comment_classifications
        )
        if isinstance(classification, ClassificationFailure)
    ]
    for classification in failed_classifications:
        log_failed_classification_diagnostics(
            classification,
            discussions.get(classification.identity.discussion_id),
        )


def has_failed_dashboard_result(result: EvaluationResult | None) -> bool:
    return isinstance(result, EvaluationFailure)


def reject_failed_dashboard_result(result: EvaluationResult | None) -> bool:
    if not isinstance(result, EvaluationFailure):
        return False
    number = result.pr_number
    log_failed_result_diagnostics(result)
    print(
        f"dashboard refresh hit PR failure(s); refusing to publish failed state: #{number}",
        file=sys.stderr,
    )
    return True


def save_dashboard_update_state(
    args: argparse.Namespace,
    dashboard_state: DashboardState,
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


def apply_dashboard_update_effects(
    pr_number: int,
    acceptance: DashboardUpdateAcceptance,
    observed_at: datetime,
    *,
    prepare_author_nudges: bool,
) -> None:
    effects = acceptance.effects
    if effects.clear_backfill_failure:
        clear_backfill_pr_failure(pr_number)
    if effects.enqueue_status_comment:
        enqueue_status_comment_update(pr_number)
    if effects.record_observations:
        record_author_nudge_observation(
            pr_number,
            acceptance.accepted_result,
            observed_at,
            prepare_due=prepare_author_nudges,
        )
        record_copilot_review_observation(
            pr_number,
            acceptance.accepted_result,
            observed_at,
        )


def remove_cached_dashboard_prs(
    args: argparse.Namespace,
    pr_numbers_to_remove: set[int],
    observed_at: datetime | None = None,
) -> int:
    if not pr_numbers_to_remove:
        return 0
    dashboard_state = load_dashboard_state_cache() or empty_state()
    observed_at = observed_at or utc_now()
    persist_dashboard_state = False
    for number in sorted(pr_numbers_to_remove):
        update = prepare_dashboard_update(
            dashboard_state,
            {number},
            number,
        ).with_evaluated_result(None)
        acceptance = accept_dashboard_update(update, dashboard_state)
        apply_dashboard_update_effects(
            number,
            acceptance,
            observed_at,
            prepare_author_nudges=False,
        )
        dashboard_state = acceptance.dashboard_state
        persist_dashboard_state |= acceptance.effects.persist_dashboard_state
    return save_dashboard_update_state(
        args,
        dashboard_state,
        not persist_dashboard_state,
    )


def build_targeted_dashboard_update(args: argparse.Namespace) -> DashboardStateUpdate | None:
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
    update: DashboardStateUpdate,
    observed_at: datetime | None = None,
) -> int:
    if args.pr_number is None:
        raise RuntimeError("apply_targeted_dashboard_update requires --pr-number")
    acceptance = accept_dashboard_update(
        update,
        load_dashboard_state_cache(),
    )
    if acceptance.failed_result_rejected:
        reject_failed_dashboard_result(update.evaluated_result)
        return 1
    apply_dashboard_update_effects(
        args.pr_number,
        acceptance,
        observed_at or utc_now(),
        prepare_author_nudges=getattr(args, "prepare_author_nudges", False),
    )
    return save_dashboard_update_state(
        args,
        acceptance.dashboard_state,
        not acceptance.effects.persist_dashboard_state,
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
    DEFAULT_CLASSIFICATION_CACHE_STORE.prune(open_pr_numbers)
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

    # Empty repositories and repositories whose drafts are already tracked still
    # need accepted dashboard state for the publish job.
    if not selection.selected_prs:
        def save_current_dashboard_state() -> int:
            dashboard_state = load_dashboard_state_cache() or empty_state()
            completed_state = complete_initial_backfill_if_ready(
                dashboard_state,
                open_pr_numbers,
            )
            return save_dashboard_update_state(args, completed_state, False)

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
                open_pr_numbers,
                reviewers,
                pr_number,
                args.model,
                args.required_approvals,
                args.non_blocking_check_pattern,
                dashboard_state,
                getattr(args, "require_clean_copilot_review_branches", []),
            )
            acceptance = accept_dashboard_update(
                calculation,
                load_dashboard_state_cache(),
            )
            if acceptance.failed_result_rejected:
                reject_failed_dashboard_result(calculation.evaluated_result)
                failed_pr_numbers = update_backfill_progress(pr_number, failed=True)
                completed_state = complete_initial_backfill_if_ready(
                    acceptance.dashboard_state,
                    open_pr_numbers,
                    failed_pr_numbers,
                )
                return save_dashboard_update_state(
                    args,
                    completed_state,
                    completed_state == acceptance.dashboard_state,
                )
            apply_dashboard_update_effects(
                pr_number,
                acceptance,
                observed_at,
                prepare_author_nudges=getattr(
                    args,
                    "prepare_author_nudges",
                    False,
                ),
            )
            failed_pr_numbers = update_backfill_progress(pr_number, failed=False)
            completed_state = complete_initial_backfill_if_ready(
                acceptance.dashboard_state,
                open_pr_numbers,
                failed_pr_numbers,
            )
            return save_dashboard_update_state(
                args,
                completed_state,
                (
                    not acceptance.effects.persist_dashboard_state
                    and completed_state == acceptance.dashboard_state
                ),
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
        & open_pr_numbers
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
