"""Evaluate one pull request into a dashboard routing result."""

from __future__ import annotations

import sys
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from classification import (
    classify_discussion_domains,
    normalize_discussion_action,
)
from copilot_review import copilot_review_status, is_copilot_reviewer
from dashboard_contracts import (
    DashboardFacts,
    DashboardRoute,
    EvaluationDiagnostics,
    EvaluationFailure,
    EvaluationResult,
    EvaluationSuccess,
    StoredDashboardResult,
)
from dashboard_override import append_command_ack_reply, dashboard_override_facts
from dashboard_status import status_author_nudge_episode_id
from discussion_lifecycle import (
    DiscussionClassifications,
    DiscussionInput,
    LifecycleMode,
    prepare_discussions,
    resolve_discussions,
)
from github_cli import TransientGhError, fetch_pr_routing_raw, gh_api
from pull_request_activity import (
    ActivityInput,
    PullRequestActivity,
    build_activity_timeline,
)
from reviewer_state import (
    PreparedReviewers,
    ReviewerDiscussionInput,
    ReviewerInput,
    prepare_reviewers,
    resolve_reviewers,
)
from routing_decision import (
    RoutingInput,
    resolve_routing,
    reviewer_handoff_active,
    routing_failure_facts,
)
from routing_snapshot import build_routing_snapshot
from utils import actor_login, compute_conflicts, format_ts, parse_ts


# Copilot appears in two API shapes: `gh pr view`'s `author` field uses the
# `app/<slug>` form, while the Pulls/commits endpoint's `committer.login`
# field can return the bare `copilot` slug. Do not treat either form as the
# human author behind a Copilot-authored PR.
_COPILOT_COMMITTER_LOGINS = {"copilot"}
_COPILOT_PR_AUTHORS = {"app/copilot-swe-agent", "copilot"}
_MAINTENANCE_BOT_PR_AUTHORS = {"app/otelbot", "app/renovate"}


@dataclass(frozen=True)
class PullRequestEvaluationConfig:
    repo: str
    owner: str
    repo_name: str
    approver_logins: frozenset[str]
    classifier_model: str
    required_approvals: int
    non_blocking_check_patterns: tuple[str, ...] = ()
    require_clean_copilot_review_branches: frozenset[str] = frozenset()


@dataclass(frozen=True)
class PullRequestEvaluationInput:
    pr_summary: dict[str, Any]
    previous_result: StoredDashboardResult | None = None


def _human_author_for_copilot_pr(raw: dict[str, Any]) -> str:
    assignees = [actor_login(a) for a in (raw["pr"].get("assignees") or [])]
    for login in assignees:
        low = login.lower()
        if (
            login
            and low not in _COPILOT_PR_AUTHORS
            and not low.startswith("app/")
            and not low.endswith("[bot]")
        ):
            return login

    commits = raw["commits"]
    if not commits:
        return ""
    login = actor_login(commits[0].get("committer") or {})
    if not login or login.lower() in _COPILOT_COMMITTER_LOGINS:
        return ""
    return login


def _effective_author(raw: dict[str, Any]) -> str:
    pr = raw["pr"]
    summary = raw["summary"]
    author = actor_login(pr.get("author") or {}) or actor_login(
        summary.get("author") or {}
    )
    if author.lower() in _COPILOT_PR_AUTHORS:
        human_author = _human_author_for_copilot_pr(raw)
        if human_author:
            return human_author
    return author


def _fetch_pr_raw(
    config: PullRequestEvaluationConfig,
    pr_summary: dict[str, Any],
) -> dict[str, Any]:
    number = pr_summary["number"]
    with ThreadPoolExecutor() as pool:
        commits_future = pool.submit(
            gh_api,
            (
                f"/repos/{config.owner}/{config.repo_name}"
                f"/pulls/{number}/commits?per_page=100"
            ),
            True,
        )
        raw = fetch_pr_routing_raw(
            config.repo,
            config.owner,
            config.repo_name,
            number,
            list(config.non_blocking_check_patterns),
        )
        return {
            **raw,
            "summary": pr_summary,
            "commits": commits_future.result() or [],
        }


def _compute_facts(
    raw: dict[str, Any],
    author: str,
    activity: PullRequestActivity,
    prepared_reviewers: PreparedReviewers,
    approver_logins: frozenset[str],
    previous_facts: DashboardFacts,
) -> DashboardFacts:
    pr = raw["pr"]
    snapshot = build_routing_snapshot(raw)
    checks = snapshot.checks
    failing = [c for c in checks or [] if c.get("bucket") in ("fail", "cancel")]
    pending = [c for c in checks or [] if c.get("bucket") == "pending"]
    failing_timestamps = [parse_ts(c.get("completed_at") or "") for c in failing]
    failing_timestamps = [ts for ts in failing_timestamps if ts is not None]
    created_ts = parse_ts(pr["createdAt"])
    last_activity_ts = max(
        [
            ts
            for ts in (
                activity.latest_participant_activity_at,
                created_ts,
            )
            if ts is not None
        ],
        default=None,
    )
    api_author = actor_login(pr.get("author") or {})
    head_sha = snapshot.head_sha
    (
        copilot_review_exists,
        copilot_review_stale,
        copilot_review_findings,
    ) = copilot_review_status(
        raw.get("reviews") or [],
        head_sha,
        snapshot.review_threads,
    )
    override = dashboard_override_facts(
        raw,
        author,
        set(approver_logins),
        head_sha,
        previous_facts,
    )
    non_blocking_check_failures = tuple(sorted(
        {
            check.get("name") or ""
            for check in raw.get("non_blocking_check_failures") or []
            if check.get("name")
        },
        key=lambda name: (name.casefold(), name),
    ))
    return DashboardFacts(
        author=author,
        assignees=prepared_reviewers.assignee_logins,
        head_sha=head_sha,
        routing_input_fingerprint=snapshot.routing_input_fingerprint,
        copilot_request_fingerprint=snapshot.copilot_request_fingerprint,
        dashboard_override_command_id=override.command_id,
        dashboard_override_command_user=override.command_user,
        dashboard_override_head_sha=override.head_sha,
        dashboard_command_replies=override.command_replies,
        copilot_review_requested=any(
            is_copilot_reviewer(request)
            for request in snapshot.review_requests
        ),
        copilot_review_exists=copilot_review_exists,
        copilot_review_stale=copilot_review_stale,
        copilot_review_needed=copilot_review_stale or copilot_review_findings,
        is_maintenance_bot=api_author.lower() in _MAINTENANCE_BOT_PR_AUTHORS,
        is_draft=bool(pr.get("isDraft")),
        approval_count=prepared_reviewers.approval_count,
        conflicts=compute_conflicts(pr),
        created_at=format_ts(created_ts),
        last_activity_at=format_ts(last_activity_ts),
        last_author_activity_at=format_ts(
            activity.latest_author_activity_at
        ),
        last_approver_activity_at=format_ts(
            activity.latest_approver_activity_at
        ),
        ci_failing_count=len(failing) if checks is not None else None,
        ci_failing_since=(
            format_ts(min(failing_timestamps))
            if failing_timestamps
            else None
        ),
        ci_pending_count=len(pending) if checks is not None else None,
        non_blocking_check_failures=non_blocking_check_failures,
    )


def _author_action_discussion_urls(
    discussions: list[dict[str, Any]],
    pending_actions: dict[str, dict[str, Any]],
) -> tuple[str, ...]:
    by_id = {
        discussion["discussion_id"]: discussion for discussion in discussions
    }
    urls: list[str] = []
    for discussion_id, entry in pending_actions.items():
        action = normalize_discussion_action(entry.get("action") or "")
        if action != "author":
            continue
        discussion = by_id.get(discussion_id)
        url = (discussion or {}).get("discussion_url") or ""
        if url and url not in urls:
            urls.append(url)
    return tuple(urls)


def _assign_author_nudge_episode(
    facts: DashboardFacts,
    route: DashboardRoute,
    previous_result: StoredDashboardResult | None,
    issue_comments: list[dict[str, Any]],
) -> DashboardFacts:
    if route is not DashboardRoute.AUTHOR:
        return facts.with_changes(author_nudge_episode_id=None)
    previous_facts = (
        previous_result.facts
        if previous_result is not None
        else DashboardFacts()
    )
    previous_episode_id = (
        previous_facts.author_nudge_episode_id
        if (
            previous_result is not None
            and previous_result.route is DashboardRoute.AUTHOR
        )
        else ""
    )
    recovered_episode_id = (
        status_author_nudge_episode_id(issue_comments)
        if previous_result is None
        else ""
    )
    return facts.with_changes(
        author_nudge_episode_id=str(
            previous_episode_id or recovered_episode_id or uuid.uuid4().hex
        )
    )


def _failure_result(
    number: int,
    route: DashboardRoute,
    error: Exception,
) -> EvaluationFailure:
    return EvaluationFailure(
        pr_number=number,
        route=route,
        error=repr(error),
    )


def _evaluation_diagnostics(
    lifecycle: Any,
) -> EvaluationDiagnostics:
    return EvaluationDiagnostics(
        review_threads=lifecycle.prepared.review_threads,
        top_level_items=lifecycle.prepared.top_level_items,
        top_level_author_comment_items=(
            lifecycle.prepared.top_level_author_comment_items
        ),
        review_thread_classifications=(
            lifecycle.classifications.review_threads
        ),
        top_level_classifications=lifecycle.classifications.top_level_items,
        top_level_author_comment_classifications=(
            lifecycle.classifications.top_level_author_comments
        ),
    )


def evaluate_pull_request(
    config: PullRequestEvaluationConfig,
    source: PullRequestEvaluationInput,
) -> EvaluationResult | None:
    """Fetch and evaluate one pull request without mutating dashboard state."""
    number = source.pr_summary["number"]
    previous_result = source.previous_result
    previous_facts = (
        previous_result.facts
        if previous_result is not None
        else DashboardFacts()
    )
    previous_top_level_history = (
        previous_result.top_level_history
        if previous_result is not None
        else {}
    )
    try:
        raw = _fetch_pr_raw(config, source.pr_summary)
        if raw["pr"].get("state") != "OPEN" or raw["pr"].get("isDraft"):
            return None
        author = _effective_author(raw)
        activity = build_activity_timeline(
            ActivityInput(raw, author, config.approver_logins)
        )
        prepared_reviewers = prepare_reviewers(
            ReviewerInput(
                activity.events,
                tuple(raw.get("review_requests") or []),
                tuple(raw["pr"].get("assignees") or []),
            )
        )
        facts = _compute_facts(
            raw,
            author,
            activity,
            prepared_reviewers,
            config.approver_logins,
            previous_facts,
        )
        manual_reviewer_handoff = reviewer_handoff_active(facts)
        prepared_discussions = prepare_discussions(
            DiscussionInput(
                tuple(raw["review_threads"]),
                activity.events,
                author,
                config.approver_logins,
                facts.conflicts,
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
                config.classifier_model,
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
        diagnostics = _evaluation_diagnostics(lifecycle)
        if lifecycle.failed_classifications:
            return EvaluationFailure(
                pr_number=number,
                pr_title=raw["pr"].get("title") or "",
                pr_url=raw["pr"].get("url") or "",
                facts=routing_failure_facts(facts, previous_facts),
                diagnostics=diagnostics,
                route=DashboardRoute.UNKNOWN,
                error=(
                    f"{len(lifecycle.failed_classifications)} "
                    "discussion classification(s) failed"
                ),
            )
        pending_actions = lifecycle.pending_actions
        review_threads = list(prepared_discussions.review_threads)
        top_level_items = list(prepared_discussions.top_level_items)
        routing_outcome = resolve_routing(
            RoutingInput(
                facts=facts,
                pending_actions=pending_actions,
                previous_route=(
                    previous_result.route
                    if previous_result is not None
                    else None
                ),
                previous_facts=previous_facts,
                required_approvals=config.required_approvals,
                require_clean_copilot_review=(
                    (raw["pr"].get("baseRefName") or "")
                    in config.require_clean_copilot_review_branches
                ),
                manual_reviewer_handoff=manual_reviewer_handoff,
                pending_human_reviewer_logins=(
                    prepared_reviewers.pending_human_reviewer_logins
                ),
            )
        )
        route = routing_outcome.route
        facts = routing_outcome.facts
        facts = _assign_author_nudge_episode(
            facts,
            route,
            previous_result,
            raw.get("issue_comments") or [],
        )
        facts = append_command_ack_reply(raw, facts, route)
        facts = facts.with_changes(
            author_action_review_thread_urls=(
                _author_action_discussion_urls(review_threads, pending_actions)
            ),
            author_action_top_level_feedback_urls=(
                _author_action_discussion_urls(top_level_items, pending_actions)
            ),
            reviewers=resolve_reviewers(
                prepared_reviewers,
                ReviewerDiscussionInput(
                    tuple(review_threads),
                    tuple(top_level_items),
                    pending_actions,
                ),
            ),
        )
        return EvaluationSuccess(
            pr_number=number,
            pr_title=raw["pr"].get("title") or "",
            pr_url=raw["pr"].get("url") or "",
            facts=facts,
            diagnostics=diagnostics,
            pending_actions=pending_actions,
            top_level_history=lifecycle.top_level_history,
            route=route,
        )
    except TransientGhError as error:
        return _failure_result(
            number,
            DashboardRoute.TRANSIENT_FAILURE,
            error,
        )
    except Exception as error:
        print(
            f"  warning: PR #{number} failed to build result:",
            file=sys.stderr,
        )
        traceback.print_exc()
        return _failure_result(number, DashboardRoute.UNKNOWN, error)
