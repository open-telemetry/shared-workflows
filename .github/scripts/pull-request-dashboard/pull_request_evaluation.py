"""Evaluate one pull request into a dashboard routing result."""

from __future__ import annotations

import sys
import traceback
import uuid
from dataclasses import dataclass
from typing import Any

from classification_execution import (
    DEFAULT_CLASSIFICATION_SERVICE,
    ClassificationExecutionRequest,
    ClassificationOperation,
    ReviewerFeedbackClassificationRequest,
)
from classification_policy import (
    ActionDecision,
    ClassificationDiscussion,
    ClassificationResult,
    DiscussionAction,
    normalize_discussion_action,
)
from copilot_review import (
    copilot_review_status,
    is_copilot_reviewer,
    open_copilot_finding_urls,
)
from dashboard_contracts import (
    DashboardFacts,
    DashboardRoute,
    EvaluationDiagnostics,
    EvaluationDraft,
    EvaluationFailure,
    EvaluationResult,
    EvaluationSuccess,
    StoredDashboardResult,
)
from dashboard_override import (
    DashboardOverrideInput,
    append_command_ack_reply,
    dashboard_override_facts,
)
from dashboard_status import status_author_nudge_episode_id
from discussion_lifecycle import (
    DiscussionInput,
    DiscussionLifecycleOutcome,
    LifecycleMode,
    PreparedDiscussions,
    prepare_discussions,
    prepare_reviewer_handoff_feedback,
    resolve_discussions,
)
from github_cli import TransientGhError
from pull_request_source import (
    Actor,
    IssueComment,
    PullRequestSource,
    fetch_pull_request_source,
)
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
from utils import (
    format_ts,
    is_unattended_author_login,
    normalize_author_identity,
    parse_ts,
)


# Copilot appears under two slugs: `gh pr view`'s `author` field reports
# `app/copilot-swe-agent`, while the Pulls/commits endpoint's `committer.login`
# field can report the bare `copilot` slug. Either slug can name Copilot as the
# author, so the author set carries both while the committer set carries only
# the bare slug. These sets hold the identities `normalize_author_identity`
# returns, without the `app/` prefix or the `[bot]` suffix. Do not treat either
# slug as the human author behind a Copilot-authored PR.
_COPILOT_COMMITTER_IDENTITIES = {"copilot"}
_COPILOT_PR_AUTHOR_IDENTITIES = {"copilot-swe-agent", "copilot"}
_MAINTENANCE_APP_IDENTITIES = {"dependabot", "otelbot", "renovate"}


def _is_maintenance_bot_author(login: str) -> bool:
    normalized_login = (login or "").strip().casefold()
    identity = normalize_author_identity(normalized_login)
    return identity == "opentelemetrybot" or (
        identity in _MAINTENANCE_APP_IDENTITIES
        and (
            normalized_login.startswith("app/")
            or normalized_login.endswith("[bot]")
        )
    )


def _author_can_act(api_author: Actor, effective_author: str) -> bool:
    if is_unattended_author_login(effective_author):
        return False
    return (
        not api_author.is_bot
        or normalize_author_identity(api_author.login)
        != normalize_author_identity(effective_author)
    )


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
    pr_number: int
    previous_result: StoredDashboardResult | None = None

    def __post_init__(self) -> None:
        if self.pr_number <= 0:
            raise ValueError("pull request number must be positive")


def _human_author_for_copilot_pr(source: PullRequestSource) -> str:
    assignees = [
        assignee.login
        for assignee in source.pull_request.assignees
    ]
    for login in assignees:
        identity = normalize_author_identity(login)
        if (
            login
            and identity not in _COPILOT_PR_AUTHOR_IDENTITIES
            and not is_unattended_author_login(login)
        ):
            return login

    commits = source.commits
    if not commits:
        return ""
    committer = commits[0].committer
    login = committer.login
    if (
        not login
        or normalize_author_identity(login) in _COPILOT_COMMITTER_IDENTITIES
        or is_unattended_author_login(login)
        or committer.is_bot
        or committer.is_copilot_reviewer
    ):
        return ""
    return login


def _effective_author(source: PullRequestSource) -> str:
    author = source.pull_request.author.login
    if normalize_author_identity(author) in _COPILOT_PR_AUTHOR_IDENTITIES:
        human_author = _human_author_for_copilot_pr(source)
        if human_author:
            return human_author
    return author


def _compute_facts(
    source: PullRequestSource,
    author: str,
    activity: PullRequestActivity,
    prepared_reviewers: PreparedReviewers,
    approver_logins: frozenset[str],
    previous_facts: DashboardFacts,
) -> DashboardFacts:
    pr = source.pull_request
    snapshot = build_routing_snapshot(source)
    checks = snapshot.checks
    failing = [
        check
        for check in checks or ()
        if check.bucket in ("fail", "cancel", "action_required")
    ]
    pending = [
        check
        for check in checks or ()
        if check.bucket == "pending"
    ]
    maintainer_action_required = [
        check
        for check in checks or ()
        if check.bucket == "maintainer_action_required"
    ]
    failing_timestamps = [parse_ts(check.completed_at) for check in failing]
    failing_timestamps = [ts for ts in failing_timestamps if ts is not None]
    created_ts = parse_ts(pr.created_at)
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
    api_author = pr.author.login
    head_sha = snapshot.head_sha
    (
        copilot_review_exists,
        copilot_review_stale,
        copilot_review_findings,
    ) = copilot_review_status(
        source.reviews,
        head_sha,
        snapshot.review_threads,
    )
    override = dashboard_override_facts(
        DashboardOverrideInput(source.issue_comments),
        author,
        set(approver_logins),
        head_sha,
        previous_facts,
    )
    non_blocking_check_failures = tuple(sorted(
        {
            check.name
            for check in source.non_blocking_failures
            if check.name
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
        dashboard_override_bound_command_id=override.bound_command_id,
        dashboard_override_head_sha=override.head_sha,
        dashboard_override_since=override.since,
        dashboard_override_cleared_by_feedback=override.cleared_by_feedback,
        dashboard_command_replies=override.command_replies,
        copilot_review_requested=any(
            is_copilot_reviewer(request)
            for request in snapshot.review_requests
        ),
        copilot_review_exists=copilot_review_exists,
        copilot_review_stale=copilot_review_stale,
        copilot_review_needed=copilot_review_stale or copilot_review_findings,
        is_maintenance_bot=_is_maintenance_bot_author(api_author),
        author_can_act=_author_can_act(pr.author, author),
        is_draft=pr.is_draft,
        approval_count=prepared_reviewers.approval_count,
        conflicts=pr.conflicts,
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
        ci_maintainer_action_required_count=(
            len(maintainer_action_required)
            if checks is not None
            else None
        ),
        ci_pending_count=len(pending) if checks is not None else None,
        non_blocking_check_failures=non_blocking_check_failures,
    )


def _author_action_discussion_urls(
    discussions: list[dict[str, Any]],
    pending_actions: dict[str, dict[str, Any]],
    additional_urls: tuple[str, ...] = (),
) -> tuple[str, ...]:
    by_id = {
        discussion["discussion_id"]: discussion for discussion in discussions
    }
    urls: list[str] = []
    for discussion_id, entry in pending_actions.items():
        action = normalize_discussion_action(entry.get("action") or "")
        if action is not DiscussionAction.AUTHOR:
            continue
        discussion = by_id.get(discussion_id)
        url = (discussion or {}).get("discussion_url") or ""
        if url and url not in urls:
            urls.append(url)
    for url in additional_urls:
        if url and url not in urls:
            urls.append(url)
    return tuple(urls)


def _assign_author_nudge_episode(
    facts: DashboardFacts,
    route: DashboardRoute,
    previous_result: StoredDashboardResult | None,
    issue_comments: tuple[IssueComment, ...],
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
    lifecycle: DiscussionLifecycleOutcome,
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


def _classify_discussions(
    number: int,
    prepared: PreparedDiscussions,
    classifier_model: str,
    previous_top_level_history: dict[str, dict[str, Any]],
    classification_service: ClassificationOperation,
) -> DiscussionLifecycleOutcome:
    classifications = classification_service.classify(
        ClassificationExecutionRequest(
            pr_number=number,
            model=classifier_model,
            review_threads=tuple(
                ClassificationDiscussion.from_record(discussion)
                for discussion in prepared.review_threads
            ),
            top_level_items=tuple(
                ClassificationDiscussion.from_record(discussion)
                for discussion in prepared.top_level_items
            ),
            top_level_author_comments=tuple(
                ClassificationDiscussion.from_record(discussion)
                for discussion in prepared.top_level_author_comment_items
            ),
        )
    )
    return resolve_discussions(
        prepared,
        classifications,
        previous_top_level_history,
    )


def _handoff_feedback_routes_to_author(
    feedback_classifications: tuple[ClassificationResult, ...],
) -> bool:
    if not feedback_classifications:
        return False
    if any(classification.failed for classification in feedback_classifications):
        return False
    return any(
        isinstance(classification.decision, ActionDecision)
        and classification.decision.action is DiscussionAction.AUTHOR
        for classification in feedback_classifications
    )


def evaluate_pull_request(
    config: PullRequestEvaluationConfig,
    source: PullRequestEvaluationInput,
    classification_service: ClassificationOperation = (
        DEFAULT_CLASSIFICATION_SERVICE
    ),
) -> EvaluationResult | None:
    """Fetch and evaluate one pull request without mutating dashboard state."""
    number = source.pr_number
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
        pr_source = fetch_pull_request_source(
            config.repo,
            config.owner,
            config.repo_name,
            number,
            config.non_blocking_check_patterns,
        )
        pr = pr_source.pull_request
        if pr.state != "OPEN":
            return None
        if pr.is_draft:
            return EvaluationDraft(
                pr_number=number,
                pr_title=pr.title,
                pr_url=pr.url,
            )
        author = _effective_author(pr_source)
        activity = build_activity_timeline(
            ActivityInput(pr_source, author, config.approver_logins)
        )
        prepared_reviewers = prepare_reviewers(
            ReviewerInput(
                activity.events,
                pr_source.review_requests,
                pr.assignees,
            )
        )
        facts = _compute_facts(
            pr_source,
            author,
            activity,
            prepared_reviewers,
            config.approver_logins,
            previous_facts,
        )
        manual_reviewer_handoff = reviewer_handoff_active(facts)
        discussion_input = DiscussionInput(
            pr_source.review_threads,
            activity.events,
            author,
            config.approver_logins,
            facts.conflicts,
        )
        prepared_discussions = prepare_discussions(discussion_input)
        if manual_reviewer_handoff:
            # Old discussions cannot block a break-glass handoff. Only newer
            # human feedback is classified to decide whether the reviewer has
            # handed the pull request back to its author.
            handoff_feedback = prepare_reviewer_handoff_feedback(
                discussion_input,
                facts.dashboard_override_since,
                author,
            )
            has_handoff_feedback = bool(
                handoff_feedback.review_threads
                or handoff_feedback.top_level_items
            )
            feedback_classifications = (
                classification_service.classify_reviewer_feedback(
                    ReviewerFeedbackClassificationRequest(
                        pr_number=number,
                        model=config.classifier_model,
                        discussions=tuple(
                            ClassificationDiscussion.from_record(discussion)
                            for discussion in (
                                *handoff_feedback.review_threads,
                                *handoff_feedback.top_level_items,
                            )
                        ),
                    )
                )
                if has_handoff_feedback
                else ()
            )
            feedback_routes_to_author = _handoff_feedback_routes_to_author(
                feedback_classifications
            )
            if feedback_routes_to_author:
                facts = facts.with_changes(
                    dashboard_override_cleared_by_feedback=True
                )
                manual_reviewer_handoff = False
                lifecycle = _classify_discussions(
                    number,
                    prepared_discussions,
                    config.classifier_model,
                    previous_top_level_history,
                    classification_service,
                )
            else:
                lifecycle = resolve_discussions(
                    prepared_discussions,
                    None,
                    previous_top_level_history,
                    mode=LifecycleMode.REVIEWER_HANDOFF,
                )
        else:
            lifecycle = _classify_discussions(
                number,
                prepared_discussions,
                config.classifier_model,
                previous_top_level_history,
                classification_service,
            )
        diagnostics = _evaluation_diagnostics(lifecycle)
        if lifecycle.failed_classifications:
            return EvaluationFailure(
                pr_number=number,
                pr_title=pr.title,
                pr_url=pr.url,
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
                    pr.base_branch
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
            pr_source.issue_comments,
        )
        facts = append_command_ack_reply(
            DashboardOverrideInput(pr_source.issue_comments),
            facts,
            route,
        )
        facts = facts.with_changes(
            author_action_review_thread_urls=(
                _author_action_discussion_urls(
                    review_threads,
                    pending_actions,
                    (
                        open_copilot_finding_urls(pr_source.review_threads)
                        if facts.copilot_review_outstanding
                        else ()
                    ),
                )
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
            pr_title=pr.title,
            pr_url=pr.url,
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
