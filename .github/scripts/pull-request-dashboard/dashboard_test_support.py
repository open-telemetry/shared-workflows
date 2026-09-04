"""Typed builders shared by pull request dashboard tests."""

from __future__ import annotations

from typing import Any

from dashboard_contracts import (
    DashboardFacts,
    DashboardRoute,
    DashboardState,
    EvaluationDiagnostics,
    EvaluationDraft,
    EvaluationFailure,
    EvaluationSuccess,
    ReviewerSummary,
    StoredDashboardResult,
)
from pull_request_source import (
    Actor,
    Check,
    Commit,
    IssueComment,
    NonBlockingFailure,
    PullRequestMetadata,
    PullRequestSource,
    ReactionGroup,
    Review,
    ReviewComment,
    ReviewRequest,
    ReviewThread,
    ReviewThreadComment,
    RoutingFingerprintProjection,
)


def actor(login: str = "", **changes: Any) -> Actor:
    return Actor(login=login, **changes)


def pull_request_metadata(**changes: Any) -> PullRequestMetadata:
    values = {
        "number": 7,
        "node_id": "PR_7",
        "state": "OPEN",
        "title": "Pull request",
        "url": "https://example.test/pull/7",
        "author": actor("author"),
        "mergeable": "MERGEABLE",
        "merge_state_status": "CLEAN",
        "created_at": "2026-08-16T07:00:00Z",
        "updated_at": "2026-08-16T08:00:00Z",
        "head_sha": "abcdef123456",
        "head_branch": "feature",
        "base_branch": "main",
    }
    values.update(changes)
    return PullRequestMetadata(**values)


def commit_source(**changes: Any) -> Commit:
    values = {
        "sha": "abcdef123456",
        "author": actor("author"),
        "committer": actor("author"),
        "author_name": "Author",
        "authored_at": "2026-08-16T07:00:00Z",
        "committed_at": "2026-08-16T07:00:00Z",
        "message": "Update",
        "parent_count": 1,
    }
    values.update(changes)
    return Commit(**values)


def issue_comment(**changes: Any) -> IssueComment:
    values = {
        "database_id": 1,
        "url": "https://example.test/comment/1",
        "body": "Comment",
        "created_at": "2026-08-16T07:00:00Z",
        "updated_at": "2026-08-16T07:00:00Z",
        "content_updated_at": "2026-08-16T07:00:00Z",
        "actor": actor("author"),
    }
    values.update(changes)
    return IssueComment(**values)


def review_comment(**changes: Any) -> ReviewComment:
    values = {
        "database_id": 2,
        "url": "https://example.test/review-comment/2",
        "body": "Inline comment",
        "created_at": "2026-08-16T07:00:00Z",
        "updated_at": "2026-08-16T07:00:00Z",
        "path": "src/example.py",
        "actor": actor("reviewer"),
    }
    values.update(changes)
    return ReviewComment(**values)


def review_source(**changes: Any) -> Review:
    values = {
        "database_id": 3,
        "commit_id": "abcdef123456",
        "url": "https://example.test/review/3",
        "actor": actor("reviewer"),
        "state": "COMMENTED",
        "submitted_at": "2026-08-16T07:00:00Z",
        "updated_at": "2026-08-16T07:00:00Z",
        "content_updated_at": "2026-08-16T07:00:00Z",
    }
    values.update(changes)
    return Review(**values)


def review_request(login: str = "reviewer", **changes: Any) -> ReviewRequest:
    values = {"kind": "User", "login": login}
    values.update(changes)
    return ReviewRequest(**values)


def reaction_group(
    content: str = "THUMBS_UP",
    *user_logins: str,
) -> ReactionGroup:
    return ReactionGroup(content, tuple(user_logins))


def review_thread_comment(**changes: Any) -> ReviewThreadComment:
    values = {
        "node_id": "PRRC_1",
        "url": "https://example.test/review-comment/1",
        "body": "Please update this.",
        "created_at": "2026-08-16T07:00:00Z",
        "updated_at": "2026-08-16T07:00:00Z",
        "actor": actor("reviewer"),
    }
    values.update(changes)
    return ReviewThreadComment(**values)


def review_thread(**changes: Any) -> ReviewThread:
    values = {
        "node_id": "PRRT_1",
        "path": "src/example.py",
        "line": 1,
        "comments": (review_thread_comment(),),
    }
    values.update(changes)
    return ReviewThread(**values)


def check_source(**changes: Any) -> Check:
    values = {
        "name": "build",
        "state": "SUCCESS",
        "bucket": "pass",
    }
    values.update(changes)
    return Check(**values)


def non_blocking_failure(name: str = "optional", **changes: Any) -> NonBlockingFailure:
    return NonBlockingFailure(name=name, **changes)


def _actor_json(value: Actor) -> dict[str, str]:
    result = {"login": value.login} if value.login else {}
    if value.kind:
        result["__typename"] = value.kind
    return result


def pull_request_source(
    *,
    pull_request: PullRequestMetadata | None = None,
    commits: tuple[Commit, ...] = (),
    issue_comments: tuple[IssueComment, ...] = (),
    review_comments: tuple[ReviewComment, ...] = (),
    reviews: tuple[Review, ...] = (),
    review_requests: tuple[ReviewRequest, ...] = (),
    review_threads: tuple[ReviewThread, ...] = (),
    checks: tuple[Check, ...] | None = (),
    non_blocking_failures: tuple[NonBlockingFailure, ...] = (),
) -> PullRequestSource:
    pr = pull_request or pull_request_metadata()
    aggregate = {
        "pr": {
            "id": pr.node_id,
            "number": pr.number,
            "state": pr.state,
            "isDraft": pr.is_draft,
            "title": pr.title,
            "body": pr.body,
            "url": pr.url,
            "author": _actor_json(pr.author),
            "assignees": [_actor_json(value) for value in pr.assignees],
            "mergeable": pr.mergeable,
            "mergeStateStatus": pr.merge_state_status,
            "createdAt": pr.created_at,
            "updatedAt": pr.updated_at,
            "headRefOid": pr.head_sha,
            "headRefName": pr.head_branch,
            "baseRefName": pr.base_branch,
        },
        "issue_comments": [
            {
                "id": value.database_id,
                "html_url": value.url,
                "body": value.body,
                "created_at": value.created_at,
                "updated_at": value.updated_at,
                "content_updated_at": value.content_updated_at,
                "minimized": value.minimized,
                "user": _actor_json(value.actor),
                "performed_via_github_app": (
                    {"slug": value.performed_via_app_slug}
                    if value.performed_via_app_slug
                    else None
                ),
            }
            for value in issue_comments
        ],
        "review_comments": [
            {
                "id": value.database_id,
                "html_url": value.url,
                "body": value.body,
                "created_at": value.created_at,
                "updated_at": value.updated_at,
                "path": value.path,
                "user": _actor_json(value.actor),
            }
            for value in review_comments
        ],
        "reviews": [
            {
                "id": value.database_id,
                "commit_id": value.commit_id,
                "finding_count": value.finding_count,
                "url": value.url,
                "user": _actor_json(value.actor),
                "state": value.state,
                "body": value.body,
                "submitted_at": value.submitted_at,
                "updated_at": value.updated_at,
                "content_updated_at": value.content_updated_at,
            }
            for value in reviews
        ],
        "review_requests": [
            {
                "__typename": value.kind,
                (
                    "slug"
                    if value.kind.lower() == "team"
                    else "login"
                ): value.login,
            }
            for value in review_requests
        ],
        "review_threads": [
            {
                "id": value.node_id,
                "isResolved": value.is_resolved,
                "isOutdated": value.is_outdated,
                "path": value.path,
                "line": value.line,
                "comments": {
                    "nodes": [
                        {
                            "id": comment.node_id,
                            "url": comment.url,
                            "body": comment.body,
                            "createdAt": comment.created_at,
                            "lastEditedAt": comment.updated_at,
                            "author": _actor_json(comment.actor),
                            "reactionGroups": [
                                {
                                    "content": group.content,
                                    "users": {
                                        "nodes": [
                                            {"login": login}
                                            for login in group.user_logins
                                        ]
                                    },
                                }
                                for group in comment.reaction_groups
                            ],
                        }
                        for comment in value.comments
                    ]
                },
            }
            for value in review_threads
        ],
        "checks": (
            None
            if checks is None
            else [
                {
                    "name": value.name,
                    "state": value.state,
                    "bucket": value.bucket,
                    "workflow": value.workflow,
                    "workflow_run_id": value.workflow_run_id,
                    "description": value.description,
                    "link": value.link,
                    "started_at": value.started_at,
                    "completed_at": value.completed_at,
                    "check_run_id": value.check_run_id,
                    "integration_id": value.integration_id,
                    "status_context": value.is_status_context,
                }
                for value in checks
            ]
        ),
    }
    return PullRequestSource(
        pull_request=pr,
        commits=commits,
        issue_comments=issue_comments,
        review_comments=review_comments,
        reviews=reviews,
        review_requests=review_requests,
        review_threads=review_threads,
        checks=checks,
        non_blocking_failures=non_blocking_failures,
        fingerprint=RoutingFingerprintProjection.from_transport(aggregate),
    )


def dashboard_facts(**changes: Any) -> DashboardFacts:
    if "reviewers" in changes:
        changes["reviewers"] = tuple(
            reviewer
            if isinstance(reviewer, ReviewerSummary)
            else ReviewerSummary(**reviewer)
            for reviewer in changes["reviewers"]
        )
    return DashboardFacts().with_changes(**changes)


def evaluation_success(
    pr_number: int = 1,
    route: DashboardRoute | str = DashboardRoute.AUTHOR,
    *,
    facts: DashboardFacts | None = None,
    pr_title: str = "",
    pr_url: str | None = None,
    diagnostics: EvaluationDiagnostics | None = None,
    pending_actions: dict[str, Any] | None = None,
    top_level_history: dict[str, Any] | None = None,
) -> EvaluationSuccess:
    return EvaluationSuccess(
        pr_number=pr_number,
        pr_title=pr_title,
        pr_url=pr_url or f"https://example.test/pull/{pr_number}",
        route=DashboardRoute(route),
        facts=facts or DashboardFacts(),
        diagnostics=diagnostics or EvaluationDiagnostics(),
        pending_actions=pending_actions or {},
        top_level_history=top_level_history or {},
    )


def evaluation_failure(
    pr_number: int = 1,
    route: DashboardRoute | str = DashboardRoute.UNKNOWN,
    *,
    error: str = "failed",
    facts: DashboardFacts | None = None,
    pr_title: str = "",
    pr_url: str | None = None,
    diagnostics: EvaluationDiagnostics | None = None,
) -> EvaluationFailure:
    return EvaluationFailure(
        pr_number=pr_number,
        route=DashboardRoute(route),
        error=error,
        facts=facts,
        pr_title=pr_title,
        pr_url=pr_url or f"https://example.test/pull/{pr_number}",
        diagnostics=diagnostics or EvaluationDiagnostics(),
    )


def evaluation_draft(
    pr_number: int = 1,
    *,
    pr_title: str = "Draft",
    pr_url: str | None = None,
) -> EvaluationDraft:
    return EvaluationDraft(
        pr_number=pr_number,
        pr_title=pr_title,
        pr_url=pr_url or f"https://example.test/pull/{pr_number}",
    )


def stored_dashboard_result(
    pr_number: int = 1,
    route: DashboardRoute | str = DashboardRoute.AUTHOR,
    *,
    facts: DashboardFacts | None = None,
    pr_url: str | None = None,
    top_level_history: dict[str, Any] | None = None,
) -> StoredDashboardResult:
    return StoredDashboardResult(
        pr_number=pr_number,
        pr_url=pr_url or f"https://example.test/pull/{pr_number}",
        route=DashboardRoute(route),
        facts=facts or DashboardFacts(),
        top_level_history=top_level_history or {},
    )


def dashboard_state(
    *results: StoredDashboardResult | EvaluationSuccess,
    initial_backfill_complete: bool = False,
    draft_pr_numbers: frozenset[int] = frozenset(),
) -> DashboardState:
    stored = tuple(
        result
        if isinstance(result, StoredDashboardResult)
        else StoredDashboardResult.from_evaluation(result)
        for result in results
    )
    return DashboardState(
        initial_backfill_complete=initial_backfill_complete,
        results=stored,
        draft_pr_numbers=draft_pr_numbers,
    )
