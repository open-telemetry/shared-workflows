from __future__ import annotations

from argparse import Namespace
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import ANY, Mock, call, patch

from copilot_review import set_copilot_review_request_needed
from dashboard import (
    BACKFILL_RECORDED_FAILURE_STATUS,
    apply_targeted_dashboard_update,
    backfill_failed_pr_numbers,
    build_dashboard_update_for_pr,
    complete_initial_backfill_if_ready,
    main,
    remove_cached_dashboard_prs,
    select_backfill_prs,
    set_backfill_pr_failed,
    update_dashboard_for_backfill,
    write_initial_backfill_output,
)
from dashboard_state_update import (
    DashboardStateUpdate,
    accept_dashboard_update,
    prepare_dashboard_update,
)
from dashboard_contracts import (
    DashboardCommandReply,
    DashboardFacts,
    DashboardRoute,
    DashboardState,
    EvaluationFailure,
    EvaluationResult,
    EvaluationSuccess,
    ReviewerSummary,
    StoredDashboardResult,
)
from dashboard_test_support import (
    actor,
    check_source,
    dashboard_facts,
    dashboard_state,
    evaluation_success,
    issue_comment,
    pull_request_metadata,
    pull_request_source,
    review_request,
    review_source,
    review_thread,
    review_thread_comment,
    stored_dashboard_result,
)
from classification_policy import (
    ActionDecision,
    AuthorCommentDecision,
    ClassificationDiagnostics,
    ClassificationFailure,
    ClassificationSuccess,
    DiscussionAction,
    DiscussionClassifications,
    DiscussionIdentity,
    DiscussionKind,
    FeedbackOutcome,
)
from classification_test_support import FakeClassificationOperation
from pull_request_source import (
    PullRequestSource,
    fetch_pull_request_source,
    normalize_pull_request_source,
)
from pull_request_evaluation import (
    PullRequestEvaluationConfig,
    PullRequestEvaluationInput,
    _assign_author_nudge_episode,
    _author_action_discussion_urls,
    _compute_facts as evaluation_compute_facts,
    evaluate_pull_request,
)
from pull_request_activity import PullRequestActivity
from reviewer_state import ReviewerInput, prepare_reviewers
from routing_decision import resolve_routing


FAILED_CLASSIFICATION = ClassificationFailure(
    DiscussionIdentity("failed", DiscussionKind.TOP_LEVEL_FEEDBACK),
    ActionDecision(DiscussionAction.AUTHOR, "model failed"),
    ClassificationDiagnostics(error="model failed"),
)


def action_classification(
    discussion_id: str,
    kind: DiscussionKind,
    action: DiscussionAction,
    reason: str,
) -> ClassificationSuccess:
    return ClassificationSuccess(
        DiscussionIdentity(discussion_id, kind),
        ActionDecision(action, reason),
    )


def evaluation_facts(
    raw: dict[str, object],
    author: str,
    events: list[dict[str, object]],
    reviewers: set[str] | None = None,
    previous_facts: DashboardFacts | None = None,
) -> DashboardFacts:
    source = normalize_pull_request_source(raw)
    prepared_reviewers = prepare_reviewers(
        ReviewerInput(
            tuple(events),
            source.review_requests,
            source.pull_request.assignees,
        )
    )
    return evaluation_compute_facts(
        source,
        author,
        PullRequestActivity(tuple(events), None, None, None),
        prepared_reviewers,
        frozenset(reviewers or set()),
        previous_facts or dashboard_facts(),
    )


def evaluation_config(
    *,
    require_clean_copilot_review_branches: list[str] | None = None,
) -> PullRequestEvaluationConfig:
    return PullRequestEvaluationConfig(
        repo="owner/repo",
        owner="owner",
        repo_name="repo",
        approver_logins=frozenset({"reviewer"}),
        classifier_model="model",
        required_approvals=1,
        require_clean_copilot_review_branches=frozenset(
            require_clean_copilot_review_branches or []
        ),
    )


def evaluate_pr(
    pr_summary: dict[str, object],
    *,
    previous_result: StoredDashboardResult | None = None,
    require_clean_copilot_review_branches: list[str] | None = None,
    classification_service: FakeClassificationOperation | None = None,
) -> EvaluationResult | None:
    return evaluate_pull_request(
        evaluation_config(
            require_clean_copilot_review_branches=(
                require_clean_copilot_review_branches
            )
        ),
        PullRequestEvaluationInput(
            pr_number=int(pr_summary["number"]),
            previous_result=previous_result,
        ),
        classification_service or FakeClassificationOperation(),
    )


class AuthorNudgeEpisodeTest(unittest.TestCase):
    def test_preserves_episode_while_route_remains_author(self) -> None:
        facts = _assign_author_nudge_episode(
            dashboard_facts(),
            DashboardRoute.AUTHOR,
            stored_dashboard_result(
                facts=dashboard_facts(author_nudge_episode_id="abc123"),
            ),
            [],
        )

        self.assertEqual("abc123", facts.author_nudge_episode_id)

    @patch("pull_request_evaluation.uuid.uuid4")
    def test_starts_new_episode_after_known_route_departure(self, uuid4: Mock) -> None:
        uuid4.return_value.hex = "def456"
        facts = _assign_author_nudge_episode(
            dashboard_facts(),
            DashboardRoute.AUTHOR,
            stored_dashboard_result(route=DashboardRoute.APPROVER),
            [{
                "performed_via_github_app": {"slug": "opentelemetry-pr-dashboard"},
                "body": (
                    "<!-- pull-request-dashboard-status -->\n"
                    "<!-- pull-request-dashboard-author-nudge-episode:abc123 -->"
                ),
            }],
        )

        self.assertEqual("def456", facts.author_nudge_episode_id)

    def test_recovers_episode_from_status_comment_after_cache_loss(self) -> None:
        facts = _assign_author_nudge_episode(
            dashboard_facts(),
            DashboardRoute.AUTHOR,
            None,
            [{
                "performed_via_github_app": {"slug": "opentelemetry-pr-dashboard"},
                "body": (
                    "<!-- pull-request-dashboard-status -->\n"
                    "<!-- pull-request-dashboard-author-nudge-episode:abc123 -->"
                ),
            }],
        )

        self.assertEqual("abc123", facts.author_nudge_episode_id)

    def test_preserves_episode_while_route_is_held_for_gates(self) -> None:
        facts = _assign_author_nudge_episode(
            dashboard_facts(route_held_for_gates=True),
            DashboardRoute.AUTHOR,
            stored_dashboard_result(
                facts=dashboard_facts(author_nudge_episode_id="abc123"),
            ),
            [],
        )

        self.assertEqual("abc123", facts.author_nudge_episode_id)

    def test_preserves_episode_while_pr_is_conflicted(self) -> None:
        facts = _assign_author_nudge_episode(
            dashboard_facts(conflicts="yes"),
            DashboardRoute.AUTHOR,
            stored_dashboard_result(
                facts=dashboard_facts(author_nudge_episode_id="abc123"),
            ),
            [],
        )

        self.assertEqual("abc123", facts.author_nudge_episode_id)

class PullRequestSourceFetchTest(unittest.TestCase):
    def test_uses_graphql_issue_comments_without_rest_join(self) -> None:
        issue_comments = [{"id": 101, "body": "GraphQL comment"}]
        rest_payloads = {
            "/repos/owner/repo/pulls/7/comments?per_page=100": [
                {"id": 201, "body": "Review comment"}
            ],
            "/repos/owner/repo/pulls/7/commits?per_page=100": [
                {"sha": "abcdef123456"}
            ],
        }

        def gh_api(path: str, paginate: bool) -> list[dict]:
            self.assertTrue(paginate)
            return rest_payloads[path]

        with (
            patch(
                "pull_request_source.gh_pr_view",
                return_value={
                    "id": "PR_node",
                    "state": "OPEN",
                    "baseRefName": "main",
                },
            ),
            patch(
                "pull_request_source.fetch_pr_issue_comments",
                return_value=issue_comments,
            ) as fetch_issue_comments,
            patch(
                "pull_request_source.gh_api",
                side_effect=gh_api,
            ) as source_rest_api,
            patch("pull_request_source.fetch_review_threads", return_value=[]),
            patch("pull_request_source.fetch_review_requests", return_value=[]),
            patch(
                "pull_request_source.fetch_pr_reviews",
                return_value=[],
            ),
            patch(
                "pull_request_source.gh_pr_check_rollup",
                return_value={
                    "head_oid": "",
                    "required": [],
                    "non_blocking_failures": [],
                    "code_scanning": [],
                    "pending": [],
                },
            ),
            patch("pull_request_source.gh_branch_rules", return_value=[]),
            patch(
                "pull_request_source.include_missing_required_checks",
                return_value=[],
            ),
        ):
            source = fetch_pull_request_source(
                "owner/repo",
                "owner",
                "repo",
                7,
            )

        self.assertEqual(101, source.issue_comments[0].database_id)
        fetch_issue_comments.assert_called_once_with("owner", "repo", 7)
        self.assertEqual(
            {
                call.args[0]
                for call in source_rest_api.call_args_list
            },
            set(rest_payloads),
        )


class DashboardEvaluationHandoffTest(unittest.TestCase):
    @patch("dashboard.evaluate_pull_request")
    def test_passes_the_cached_result_and_wraps_the_evaluation(
        self,
        evaluate: Mock,
    ) -> None:
        top_level_history = {
            "feedback": {
                "kind": "commit",
                "timestamp": "2026-08-16T08:00:00Z",
            }
        }
        starting_result = stored_dashboard_result(
            7,
            facts=dashboard_facts(head_sha="old-head"),
            top_level_history=top_level_history,
        )
        evaluated_result = evaluation_success(
            7,
            route=DashboardRoute.APPROVER,
            facts=dashboard_facts(head_sha="new-head"),
            top_level_history=top_level_history,
        )
        evaluate.return_value = evaluated_result

        update = build_dashboard_update_for_pr(
            "owner/repo",
            "owner",
            "repo",
            {7},
            {"reviewer"},
            7,
            "model",
            1,
            ["optional-*"],
            dashboard_state(starting_result),
            ["main"],
        )

        config, source = evaluate.call_args.args
        self.assertEqual(starting_result, source.previous_result)
        self.assertEqual(frozenset({"reviewer"}), config.approver_logins)
        self.assertEqual(("optional-*",), config.non_blocking_check_patterns)
        self.assertEqual(
            frozenset({"main"}),
            config.require_clean_copilot_review_branches,
        )
        self.assertEqual(starting_result, update.prepared.starting_result)
        self.assertEqual(evaluated_result, update.evaluated_result)


class PullRequestEvaluationTest(unittest.TestCase):
    @staticmethod
    def raw_pr(
        *,
        checks: list[dict[str, object]] | None = None,
    ) -> PullRequestSource:
        return pull_request_source(
            pull_request=pull_request_metadata(title="Routing integration"),
            checks=tuple(
                check_source(**check)
                for check in checks or []
            ),
        )

    def test_compute_facts_uses_prepared_approval_count(self) -> None:
        raw = self.raw_pr()
        events = [{
            "kind": "review-state",
            "timestamp": "2026-08-16T08:00:00Z",
            "actor": "reviewer",
            "actor_role": "approver",
            "state": "APPROVED",
        }]
        prepared_reviewers = prepare_reviewers(
            ReviewerInput(tuple(events), (), ())
        )

        facts = evaluation_compute_facts(
            raw,
            "author",
            PullRequestActivity(tuple(events), None, None, None),
            prepared_reviewers,
            frozenset({"reviewer"}),
            dashboard_facts(),
        )

        self.assertEqual(1, facts.approval_count)

    @patch("pull_request_evaluation.resolve_routing", wraps=resolve_routing)
    @patch("pull_request_evaluation.fetch_pull_request_source")
    def test_evaluation_routes_pending_reviewers_and_projects_reviewer_rows(
        self,
        fetch_raw: Mock,
        resolve: Mock,
    ) -> None:
        fetch_raw.return_value = pull_request_source(
            pull_request=pull_request_metadata(title="Routing integration"),
            reviews=(review_source(
                database_id=1,
                state="APPROVED",
                submitted_at="2026-08-16T08:00:00Z",
                body="",
            ),),
            review_requests=(review_request(),),
        )

        result = evaluate_pr(
            {"number": 7},
        )

        self.assertIsNotNone(result)
        assert result is not None
        routing_input = resolve.call_args.args[0]
        self.assertEqual(
            frozenset({"reviewer"}),
            routing_input.pending_human_reviewer_logins,
        )
        self.assertEqual(
            (ReviewerSummary(login="reviewer", pending_review=True),),
            result.facts.reviewers,
        )

    @patch("pull_request_evaluation.fetch_pull_request_source")
    def test_override_binds_to_the_observed_head_before_classification(
        self, fetch_raw: Mock
    ) -> None:
        fetch_raw.return_value = normalize_pull_request_source({
            "summary": {"author": {"login": "author"}},
            "pr": {
                "state": "OPEN",
                "isDraft": False,
                "title": "Needs operator help",
                "url": "https://example.test/pull/7",
                "author": {"login": "author"},
                "assignees": [],
                "mergeStateStatus": "CLEAN",
                "mergeable": "MERGEABLE",
                "createdAt": "2026-08-16T07:00:00Z",
                "updatedAt": "2026-08-16T08:00:00Z",
                "headRefOid": "abcdef123456",
                "headRefName": "feature",
                "headRepository": {"nameWithOwner": "owner/repo"},
                "baseRefName": "main",
            },
            "commits": [],
            "issue_comments": [
                {
                    "id": 101,
                    "body": "Please update this.",
                    "created_at": "2026-08-15T08:00:00Z",
                    "user": {"login": "reviewer"},
                },
                {
                    "id": 102,
                    "body": "/dashboard route:reviewers",
                    "created_at": "2026-08-16T08:00:00Z",
                    "user": {"login": "author"},
                },
            ],
            "review_comments": [],
            "reviews": [],
            "review_threads": [],
            "review_requests": [],
            "checks": [
                {
                    "name": "required",
                    "bucket": "fail",
                    "completed_at": "2026-08-16T07:30:00Z",
                }
            ],
            "non_blocking_check_failures": [],
        })

        classifier = FakeClassificationOperation(
            error=AssertionError("classification must be bypassed")
        )
        result = evaluate_pr(
            {"number": 7},
            require_clean_copilot_review_branches=["main"],
            classification_service=classifier,
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertIsInstance(result, EvaluationSuccess)
        assert isinstance(result, EvaluationSuccess)
        self.assertEqual(DashboardRoute.APPROVER, result.route)
        self.assertEqual({}, result.pending_actions)
        self.assertEqual(
            "abcdef123456", result.facts.dashboard_override_head_sha
        )
        self.assertEqual(
            (
                DashboardCommandReply(
                    102,
                    "routed",
                    "author",
                    head_sha="abcdef123456",
                    route=DashboardRoute.APPROVER,
                    since="2026-08-16T08:00:00Z",
                ),
            ),
            result.facts.dashboard_command_replies,
        )
        self.assertEqual(classifier.requests, [])

    @patch("pull_request_evaluation.fetch_pull_request_source")
    def test_actionable_review_after_override_ends_handoff(
        self, fetch_raw: Mock
    ) -> None:
        fetch_raw.return_value = pull_request_source(
            pull_request=pull_request_metadata(title="Routing integration"),
            issue_comments=(issue_comment(
                database_id=102,
                body="/dashboard route:reviewers",
                created_at="2026-08-16T08:00:00Z",
            ),),
            reviews=(review_source(
                database_id=501,
                url="https://example.test/pull/7#pullrequestreview-501",
                state="COMMENTED",
                submitted_at="2026-08-16T09:00:00Z",
                body="Please update this.",
            ),),
        )

        classification = action_classification(
            "pr-review-501",
            DiscussionKind.TOP_LEVEL_FEEDBACK,
            DiscussionAction.AUTHOR,
            "The reviewer requested a change.",
        )
        classifier = FakeClassificationOperation(
            DiscussionClassifications((), (classification,), ()),
            reviewer_feedback_result=(classification,),
        )
        result = evaluate_pr(
            {"number": 7},
            classification_service=classifier,
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertIsInstance(result, EvaluationSuccess)
        assert isinstance(result, EvaluationSuccess)
        self.assertEqual(DashboardRoute.AUTHOR, result.route)
        self.assertTrue(result.facts.dashboard_override_cleared_by_feedback)
        self.assertEqual(
            {
                "pr-review-501": {
                    "action": "author",
                    "since": "2026-08-16T09:00:00Z",
                }
            },
            result.pending_actions,
        )
        self.assertEqual((), result.facts.dashboard_command_replies)
        self.assertEqual(len(classifier.requests), 1)
        self.assertEqual(len(classifier.reviewer_feedback_requests), 1)

    @patch("pull_request_evaluation.fetch_pull_request_source")
    def test_non_actionable_review_keeps_handoff(
        self, fetch_raw: Mock
    ) -> None:
        fetch_raw.return_value = pull_request_source(
            pull_request=pull_request_metadata(title="Routing integration"),
            issue_comments=(issue_comment(
                database_id=102,
                body="/dashboard route:reviewers",
                created_at="2026-08-16T08:00:00Z",
            ),),
            reviews=(review_source(
                database_id=501,
                state="COMMENTED",
                submitted_at="2026-08-16T09:00:00Z",
                body="Nice work.",
            ),),
        )

        classification = action_classification(
            "pr-review-501",
            DiscussionKind.TOP_LEVEL_FEEDBACK,
            DiscussionAction.NONE,
            "The reviewer left praise.",
        )
        classifier = FakeClassificationOperation(
            reviewer_feedback_result=(classification,)
        )
        result = evaluate_pr(
            {"number": 7},
            classification_service=classifier,
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertIsInstance(result, EvaluationSuccess)
        assert isinstance(result, EvaluationSuccess)
        self.assertEqual(DashboardRoute.APPROVER, result.route)
        self.assertFalse(result.facts.dashboard_override_cleared_by_feedback)
        self.assertEqual({}, result.pending_actions)
        self.assertEqual(classifier.requests, [])
        self.assertEqual(len(classifier.reviewer_feedback_requests), 1)

    @patch("pull_request_evaluation.fetch_pull_request_source")
    def test_feedback_classification_failure_does_not_block_handoff(
        self, fetch_raw: Mock
    ) -> None:
        fetch_raw.return_value = pull_request_source(
            pull_request=pull_request_metadata(title="Routing integration"),
            issue_comments=(issue_comment(
                database_id=102,
                body="/dashboard route:reviewers",
                created_at="2026-08-16T08:00:00Z",
            ),),
            reviews=(review_source(
                database_id=501,
                state="COMMENTED",
                submitted_at="2026-08-16T09:00:00Z",
                body="Please update this.",
            ),),
        )

        failed = ClassificationFailure(
            DiscussionIdentity(
                "pr-review-501",
                DiscussionKind.TOP_LEVEL_FEEDBACK,
            ),
            ActionDecision(
                DiscussionAction.AUTHOR,
                "The reviewer requested a change.",
            ),
            ClassificationDiagnostics(error="model failed"),
        )
        classifier = FakeClassificationOperation(
            reviewer_feedback_result=(failed,)
        )
        result = evaluate_pr(
            {"number": 7},
            classification_service=classifier,
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertIsInstance(result, EvaluationSuccess)
        assert isinstance(result, EvaluationSuccess)
        self.assertEqual(DashboardRoute.APPROVER, result.route)
        self.assertFalse(result.facts.dashboard_override_cleared_by_feedback)
        self.assertEqual(classifier.requests, [])
        self.assertEqual(len(classifier.reviewer_feedback_requests), 1)

    @patch("pull_request_evaluation.fetch_pull_request_source")
    def test_informational_inline_feedback_keeps_handoff(
        self, fetch_raw: Mock
    ) -> None:
        fetch_raw.return_value = pull_request_source(
            pull_request=pull_request_metadata(title="Routing integration"),
            issue_comments=(issue_comment(
                database_id=102,
                body="/dashboard route:reviewers",
                created_at="2026-08-16T08:00:00Z",
            ),),
            review_threads=(review_thread(
                node_id="thread-1",
                path="src/example.py",
                line=7,
                comments=(
                    review_thread_comment(
                        node_id="old",
                        url="https://example.test/thread/old",
                        body="Please change this.",
                        created_at="2026-08-16T07:30:00Z",
                    ),
                    review_thread_comment(
                        node_id="new",
                        url="https://example.test/thread/new",
                        body="For context, this API is deprecated.",
                        created_at="2026-08-16T09:00:00Z",
                    ),
                ),
            ),),
        )

        classification = action_classification(
            "thread-1",
            DiscussionKind.REVIEW_THREAD,
            DiscussionAction.NONE,
            "The comment is informational.",
        )
        classifier = FakeClassificationOperation(
            reviewer_feedback_result=(classification,)
        )
        result = evaluate_pr(
            {"number": 7},
            classification_service=classifier,
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertIsInstance(result, EvaluationSuccess)
        assert isinstance(result, EvaluationSuccess)
        self.assertEqual(DashboardRoute.APPROVER, result.route)
        self.assertFalse(result.facts.dashboard_override_cleared_by_feedback)
        self.assertEqual(classifier.requests, [])
        request = classifier.reviewer_feedback_requests[0]
        thread = request.discussions[0]
        self.assertEqual(
            ["For context, this API is deprecated."],
            [comment.body for comment in thread.comments],
        )

    @patch("pull_request_evaluation.fetch_pull_request_source")
    def test_inactive_inline_feedback_ends_handoff(
        self, fetch_raw: Mock
    ) -> None:
        classification = action_classification(
            "thread-1",
            DiscussionKind.REVIEW_THREAD,
            DiscussionAction.AUTHOR,
            "The reviewer requested a change.",
        )
        classifier = FakeClassificationOperation(
            reviewer_feedback_result=(classification,)
        )
        for state in ({"is_resolved": True}, {"is_outdated": True}):
            with self.subTest(state=state):
                fetch_raw.return_value = pull_request_source(
                    pull_request=pull_request_metadata(title="Routing integration"),
                    issue_comments=(issue_comment(
                        database_id=102,
                        body="/dashboard route:reviewers",
                        created_at="2026-08-16T08:00:00Z",
                    ),),
                    review_threads=(review_thread(
                        node_id="thread-1",
                        **state,
                        comments=(review_thread_comment(
                            body="Please update this.",
                            created_at="2026-08-16T09:00:00Z",
                        ),),
                    ),),
                )

                result = evaluate_pr(
                    {"number": 7},
                    classification_service=classifier,
                )

                self.assertIsNotNone(result)
                assert result is not None
                self.assertTrue(
                    result.facts.dashboard_override_cleared_by_feedback
                )
                request = classifier.reviewer_feedback_requests[-1]
                self.assertEqual(
                    ["Please update this."],
                    [comment.body for comment in request.discussions[0].comments],
                )
                self.assertEqual((), classifier.requests[-1].review_threads)

        self.assertEqual(2, len(classifier.reviewer_feedback_requests))
        self.assertEqual(2, len(classifier.requests))

    @patch("pull_request_evaluation.fetch_pull_request_source")
    def test_author_reply_does_not_reactivate_cleared_handoff(
        self, fetch_raw: Mock
    ) -> None:
        fetch_raw.return_value = pull_request_source(
            pull_request=pull_request_metadata(title="Routing integration"),
            issue_comments=(
                issue_comment(
                    database_id=102,
                    body="/dashboard route:reviewers",
                    created_at="2026-08-16T08:00:00Z",
                ),
                issue_comment(
                    database_id=501,
                    actor=actor("reviewer"),
                    body="Please update this.",
                    created_at="2026-08-16T09:00:00Z",
                ),
                issue_comment(
                    database_id=502,
                    body="Done in the latest commit.",
                    created_at="2026-08-16T10:00:00Z",
                ),
            ),
        )

        feedback = action_classification(
            "pr-issue-comment-501",
            DiscussionKind.TOP_LEVEL_FEEDBACK,
            DiscussionAction.AUTHOR,
            "The reviewer requested a change.",
        )
        author_reply = ClassificationSuccess(
            DiscussionIdentity(
                "pr-author-reply-502",
                DiscussionKind.TOP_LEVEL_AUTHOR_REPLY,
            ),
            AuthorCommentDecision((
                FeedbackOutcome(
                    "pr-issue-comment-501",
                    DiscussionAction.NONE,
                    "The author completed the work.",
                ),
            )),
        )
        classifier = FakeClassificationOperation(
            DiscussionClassifications((), (feedback,), (author_reply,)),
            reviewer_feedback_result=(feedback,),
        )
        result = evaluate_pr(
            {"number": 7},
            classification_service=classifier,
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertIsInstance(result, EvaluationSuccess)
        assert isinstance(result, EvaluationSuccess)
        self.assertTrue(result.facts.dashboard_override_cleared_by_feedback)
        self.assertEqual({}, result.pending_actions)
        self.assertEqual(len(classifier.requests), 1)
        self.assertEqual(len(classifier.reviewer_feedback_requests), 1)

    @patch("pull_request_evaluation.fetch_pull_request_source")
    def test_conflict_uses_normal_discussion_and_approval_routing(
        self, fetch_raw: Mock
    ) -> None:
        fetch_raw.return_value = normalize_pull_request_source({
            "summary": {"author": {"login": "author"}},
            "pr": {
                "state": "OPEN",
                "isDraft": False,
                "title": "Conflicted PR",
                "url": "https://example.test/pull/7",
                "author": {"login": "author"},
                "assignees": [],
                "mergeStateStatus": "DIRTY",
                "mergeable": "CONFLICTING",
                "createdAt": "2026-08-16T07:00:00Z",
                "updatedAt": "2026-08-16T08:00:00Z",
                "headRefOid": "abcdef123456",
                "baseRefName": "main",
            },
            "commits": [
                {
                    "sha": "abcdef123456",
                    "author": {"login": "author"},
                    "committer": {"login": "author"},
                    "commit": {
                        "author": {"date": "2026-08-16T08:00:00Z"},
                        "committer": {"date": "2026-08-16T08:00:00Z"},
                        "message": "Update branch",
                    },
                    "parents": [{"sha": "parent"}],
                }
            ],
            "issue_comments": [
                {
                    "id": 101,
                    "body": "Please update this.",
                    "created_at": "2026-08-15T08:00:00Z",
                    "user": {"login": "reviewer"},
                }
            ],
            "review_comments": [],
            "reviews": [],
            "review_threads": [],
            "review_requests": [],
            "checks": [],
            "non_blocking_check_failures": [],
        })

        classifier = FakeClassificationOperation()
        result = evaluate_pr(
            {"number": 7},
            classification_service=classifier,
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertIsInstance(result, EvaluationSuccess)
        assert isinstance(result, EvaluationSuccess)
        self.assertEqual(DashboardRoute.APPROVER, result.route)
        self.assertEqual({}, result.pending_actions)
        self.assertEqual("last_author_activity", result.facts.waiting_age_basis)
        self.assertEqual(len(classifier.requests), 1)

    @patch("pull_request_evaluation.fetch_pull_request_source")
    def test_normal_routing_flows_through_evaluation(
        self, fetch_raw: Mock
    ) -> None:
        fetch_raw.return_value = self.raw_pr(
            checks=[{"name": "required", "bucket": "pass"}]
        )

        classifier = FakeClassificationOperation()
        result = evaluate_pr(
            {"number": 7},
            classification_service=classifier,
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertIsInstance(result, EvaluationSuccess)
        assert isinstance(result, EvaluationSuccess)
        self.assertEqual(DashboardRoute.APPROVER, result.route)
        self.assertFalse(result.facts.route_held_for_gates)
        self.assertEqual(len(classifier.requests), 1)

    @patch("routing_decision.utc_now")
    @patch("pull_request_evaluation.fetch_pull_request_source")
    def test_running_required_check_keeps_integrated_route_held(
        self, fetch_raw: Mock, utc_now: Mock
    ) -> None:
        utc_now.return_value = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)
        fetch_raw.return_value = self.raw_pr(
            checks=[{"name": "required", "bucket": "pending"}]
        )

        classifier = FakeClassificationOperation()
        result = evaluate_pr(
            {"number": 7},
            previous_result=stored_dashboard_result(
                7,
                facts=dashboard_facts(
                    head_sha="abcdef123456",
                    waiting_since="2026-08-16T08:00:00+00:00",
                ),
            ),
            classification_service=classifier,
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertIsInstance(result, EvaluationSuccess)
        assert isinstance(result, EvaluationSuccess)
        self.assertEqual(DashboardRoute.AUTHOR, result.route)
        self.assertTrue(result.facts.route_held_for_gates)
        self.assertEqual(
            "2026-08-16T12:00:00+00:00", result.facts.route_held_since
        )
        self.assertEqual(len(classifier.requests), 1)

    @patch("pull_request_evaluation.fetch_pull_request_source")
    def test_classification_failure_preserves_integrated_routing_failure_facts(
        self, fetch_raw: Mock
    ) -> None:
        fetch_raw.return_value = self.raw_pr()

        classifier = FakeClassificationOperation(
            DiscussionClassifications(
                (),
                (FAILED_CLASSIFICATION,),
                (),
            )
        )
        result = evaluate_pr(
            {"number": 7},
            previous_result=stored_dashboard_result(
                7,
                facts=dashboard_facts(
                    copilot_first_review_missing_since="2026-08-11T12:00:00Z",
                ),
            ),
            classification_service=classifier,
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertIsInstance(result, EvaluationFailure)
        assert isinstance(result, EvaluationFailure)
        self.assertEqual(DashboardRoute.UNKNOWN, result.route)
        self.assertEqual(
            "2026-08-11T12:00:00Z",
            result.facts.copilot_first_review_missing_since if result.facts else None,
        )
        self.assertEqual(len(classifier.requests), 1)


class ReviewThreadDiscussionUrlTest(unittest.TestCase):


    def test_author_action_urls_use_thread_url_and_deduplicate(self) -> None:
        discussions = [
            {"discussion_id": "thread-1", "discussion_url": "https://example.test/discussion/1"},
            {"discussion_id": "thread-2", "discussion_url": "https://example.test/discussion/1"},
            {"discussion_id": "top-level-1", "discussion_url": "https://example.test/discussion/2"},
        ]
        pending_actions = {
            "thread-1": {"action": "author"},
            "thread-2": {"action": "author"},
            "top-level-1": {"action": "author"},
        }

        self.assertEqual(
            ("https://example.test/discussion/1", "https://example.test/discussion/2"),
            _author_action_discussion_urls(discussions, pending_actions),
        )


class CopilotReviewGateTest(unittest.TestCase):
    def test_current_head_matches_latest_clean_copilot_review(self) -> None:
        facts = evaluation_facts(
            {
                "pr": {
                    "updatedAt": "2026-07-20T03:00:00Z",
                    "createdAt": "2026-07-20T01:00:00Z",
                    "author": {"login": "author"},
                    "assignees": [],
                    "mergeStateStatus": "CLEAN",
                    "mergeable": "MERGEABLE",
                    "headRefOid": "current-head",
                },
                "review_requests": [
                    {
                        "__typename": "Bot",
                        "login": "copilot-pull-request-reviewer",
                    },
                ],
                "reviews": [
                    {
                        "id": 10,
                        "commit_id": "old-head",
                        "finding_count": 1,
                        "user": {"login": "copilot-pull-request-reviewer[bot]"},
                        "submitted_at": "2026-07-20T01:30:00Z",
                    },
                    {
                        "id": 20,
                        "commit_id": "current-head",
                        "finding_count": 0,
                        "user": {"login": "copilot-pull-request-reviewer"},
                        "submitted_at": "2026-07-20T02:30:00Z",
                    },
                ],
                "commits": [{"sha": "old-head"}, {"sha": "current-head"}],
                "review_comments": [
                    {"pull_request_review_id": 10},
                ],
                "checks": [],
            },
            "author",
            [],
        )

        self.assertTrue(facts.copilot_review_requested)
        self.assertTrue(facts.copilot_review_exists)
        self.assertFalse(facts.copilot_review_needed)

    def test_late_stale_review_does_not_replace_clean_current_head_review(self) -> None:
        facts = evaluation_facts(
            {
                "pr": {
                    "updatedAt": "2026-07-20T03:00:00Z",
                    "createdAt": "2026-07-20T01:00:00Z",
                    "author": {"login": "author"},
                    "assignees": [],
                    "mergeStateStatus": "CLEAN",
                    "mergeable": "MERGEABLE",
                    "headRefOid": "current-head",
                },
                "reviews": [
                    {
                        "id": 10,
                        "commit_id": "current-head",
                        "finding_count": 0,
                        "user": {"login": "copilot"},
                        "submitted_at": "2026-07-20T02:30:00Z",
                    },
                    {
                        "id": 20,
                        "commit_id": "old-head",
                        "finding_count": 1,
                        "user": {"login": "copilot"},
                        "submitted_at": "2026-07-20T03:00:00Z",
                    },
                ],
                "commits": [{"sha": "old-head"}, {"sha": "current-head"}],
                "review_comments": [{"pull_request_review_id": 20}],
                "checks": [],
            },
            "author",
            [],
        )

        self.assertFalse(facts.copilot_review_needed)

    def test_push_since_latest_clean_copilot_review_needs_rereview(self) -> None:
        facts = evaluation_facts(
            {
                "pr": {
                    "updatedAt": "2026-07-20T03:00:00Z",
                    "createdAt": "2026-07-20T01:00:00Z",
                    "author": {"login": "author"},
                    "assignees": [],
                    "mergeStateStatus": "CLEAN",
                    "mergeable": "MERGEABLE",
                    "headRefOid": "current-head",
                },
                "reviews": [
                    {
                        "id": 20,
                        "commit_id": "reviewed-head",
                        "finding_count": 0,
                        "user": {"login": "copilot"},
                        "submitted_at": "2026-07-20T02:30:00Z",
                    },
                ],
                "commits": [{"sha": "reviewed-head"}, {"sha": "current-head"}],
                "review_comments": [],
                "checks": [],
            },
            "author",
            [],
        )

        self.assertFalse(facts.copilot_review_requested)
        self.assertTrue(facts.copilot_review_needed)

    def test_unresolved_copilot_thread_on_current_head_needs_rereview(self) -> None:
        facts = evaluation_facts(
            {
                "pr": {
                    "updatedAt": "2026-07-20T03:00:00Z",
                    "createdAt": "2026-07-20T01:00:00Z",
                    "author": {"login": "author"},
                    "assignees": [],
                    "mergeStateStatus": "CLEAN",
                    "mergeable": "MERGEABLE",
                    "headRefOid": "current-head",
                },
                "reviews": [
                    {
                        "id": 10,
                        "commit_id": "current-head",
                        "finding_count": 0,
                        "user": {"login": "copilot"},
                        "submitted_at": "2026-07-20T01:30:00Z",
                    },
                    {
                        "id": 20,
                        "commit_id": "current-head",
                        "finding_count": 1,
                        "user": {"login": "copilot"},
                        "submitted_at": "2026-07-20T02:30:00Z",
                    },
                ],
                "commits": [{"sha": "current-head"}],
                "review_comments": [{"pull_request_review_id": 20}],
                "review_threads": [
                    {
                        "id": "thread-1",
                        "isResolved": False,
                        "isOutdated": False,
                        "comments": {
                            "nodes": [
                                {
                                    "id": "comment-1",
                                    "url": "https://example.com/1",
                                    "body": "this leaks",
                                    "createdAt": "2026-07-20T02:30:00Z",
                                    "author": {"login": "copilot"},
                                },
                            ],
                        },
                    },
                ],
                "checks": [],
            },
            "author",
            [],
        )

        self.assertFalse(facts.copilot_review_stale)
        self.assertTrue(facts.copilot_review_needed)

    def test_resolved_copilot_findings_on_current_head_are_clean(self) -> None:
        # A review's comment count never shrinks, so counting it would hold the
        # PR on feedback the author already addressed.
        facts = evaluation_facts(
            {
                "pr": {
                    "updatedAt": "2026-07-20T03:00:00Z",
                    "createdAt": "2026-07-20T01:00:00Z",
                    "author": {"login": "author"},
                    "assignees": [],
                    "mergeStateStatus": "CLEAN",
                    "mergeable": "MERGEABLE",
                    "headRefOid": "current-head",
                },
                "reviews": [
                    {
                        "id": 20,
                        "commit_id": "current-head",
                        "finding_count": 2,
                        "user": {"login": "copilot"},
                        "submitted_at": "2026-07-20T02:30:00Z",
                    },
                ],
                "commits": [{"sha": "current-head"}],
                "review_comments": [{"pull_request_review_id": 20}],
                "review_threads": [
                    {
                        "id": "thread-1",
                        "isResolved": True,
                        "isOutdated": False,
                        "comments": {
                            "nodes": [
                                {
                                    "id": "comment-1",
                                    "url": "https://example.com/1",
                                    "body": "this leaks",
                                    "createdAt": "2026-07-20T02:30:00Z",
                                    "author": {"login": "copilot"},
                                },
                            ],
                        },
                    },
                    {
                        "id": "thread-2",
                        "isResolved": False,
                        "isOutdated": True,
                        "comments": {
                            "nodes": [
                                {
                                    "id": "comment-2",
                                    "url": "https://example.com/2",
                                    "body": "prefer a constant",
                                    "createdAt": "2026-07-20T02:31:00Z",
                                    "author": {"login": "copilot"},
                                },
                            ],
                        },
                    },
                ],
                "checks": [],
            },
            "author",
            [],
        )

        self.assertFalse(facts.copilot_review_stale)
        self.assertFalse(facts.copilot_review_needed)

    def test_human_thread_does_not_count_as_a_copilot_finding(self) -> None:
        facts = evaluation_facts(
            {
                "pr": {
                    "updatedAt": "2026-07-20T03:00:00Z",
                    "createdAt": "2026-07-20T01:00:00Z",
                    "author": {"login": "author"},
                    "assignees": [],
                    "mergeStateStatus": "CLEAN",
                    "mergeable": "MERGEABLE",
                    "headRefOid": "current-head",
                },
                "reviews": [
                    {
                        "id": 20,
                        "commit_id": "current-head",
                        "finding_count": 0,
                        "user": {"login": "copilot"},
                        "submitted_at": "2026-07-20T02:30:00Z",
                    },
                ],
                "commits": [{"sha": "current-head"}],
                "review_comments": [],
                "review_threads": [
                    {
                        "id": "thread-1",
                        "isResolved": False,
                        "isOutdated": False,
                        "comments": {
                            "nodes": [
                                {
                                    "id": "comment-1",
                                    "url": "https://example.com/1",
                                    "body": "please rename this",
                                    "createdAt": "2026-07-20T02:30:00Z",
                                    "author": {"login": "reviewer"},
                                },
                            ],
                        },
                    },
                ],
                "checks": [],
            },
            "author",
            [],
        )

        self.assertFalse(facts.copilot_review_needed)

    def test_findings_only_history_needs_rereview(self) -> None:
        facts = evaluation_facts(
            {
                "pr": {
                    "updatedAt": "2026-07-20T03:00:00Z",
                    "createdAt": "2026-07-20T01:00:00Z",
                    "author": {"login": "author"},
                    "assignees": [],
                    "mergeStateStatus": "CLEAN",
                    "mergeable": "MERGEABLE",
                    "headRefOid": "current-head",
                },
                "reviews": [
                    {
                        "id": 20,
                        "commit_id": "reviewed-head",
                        "finding_count": 1,
                        "user": {"login": "copilot"},
                        "submitted_at": "2026-07-20T02:30:00Z",
                    },
                ],
                "commits": [{"sha": "reviewed-head"}, {"sha": "current-head"}],
                "review_comments": [{"pull_request_review_id": 20}],
                "checks": [],
            },
            "author",
            [],
        )

        self.assertTrue(facts.copilot_review_needed)

    def test_waits_for_automatic_initial_copilot_review(self) -> None:
        facts = evaluation_facts(
            {
                "pr": {
                    "updatedAt": "2026-07-20T03:00:00Z",
                    "createdAt": "2026-07-20T01:00:00Z",
                    "author": {"login": "author"},
                    "assignees": [],
                    "mergeStateStatus": "CLEAN",
                    "mergeable": "MERGEABLE",
                },
                "reviews": [],
                "commits": [{"sha": "current-head"}],
                "review_comments": [],
                "checks": [],
            },
            "author",
            [],
        )

        self.assertFalse(facts.copilot_review_exists)
        self.assertFalse(facts.copilot_review_needed)

    def test_pending_first_review_request_is_not_duplicated(self) -> None:
        facts = dashboard_facts(
            ci_pending_count=0,
            copilot_review_requested=True,
            copilot_first_review_missing_since="2020-01-01T00:00:00+00:00",
        )

        facts = set_copilot_review_request_needed(facts, "approver", enabled=True)

        self.assertFalse(facts.copilot_review_request_needed)

    def test_marks_re_review_needed_after_push_since_clean_review(self) -> None:
        facts = dashboard_facts(
            ci_pending_count=0,
            copilot_review_exists=True,
            copilot_review_stale=True,
        )

        facts = set_copilot_review_request_needed(facts, "maintainer", enabled=True)

        self.assertTrue(facts.copilot_review_request_needed)

    def test_marks_re_review_needed_before_reviewer_handoff(self) -> None:
        facts = dashboard_facts(
            ci_pending_count=0,
            copilot_review_exists=True,
            copilot_review_stale=True,
        )

        facts = set_copilot_review_request_needed(facts, "approver", enabled=True)

        self.assertTrue(facts.copilot_review_request_needed)

    def test_open_findings_on_current_head_request_no_re_review(self) -> None:
        # Re-reviewing unchanged code cannot resolve a thread the author owns,
        # so requesting one here would repeat on every pass.
        facts = dashboard_facts(
            ci_pending_count=0,
            copilot_review_exists=True,
            copilot_review_needed=True,
        )

        facts = set_copilot_review_request_needed(facts, "maintainer", enabled=True)

        self.assertFalse(facts.copilot_review_request_needed)

    def test_pending_re_review_is_not_requested_twice(self) -> None:
        facts = dashboard_facts(
            ci_pending_count=0,
            copilot_review_requested=True,
            copilot_review_exists=True,
            copilot_review_stale=True,
        )

        facts = set_copilot_review_request_needed(facts, "maintainer", enabled=True)

        self.assertFalse(facts.copilot_review_request_needed)

    def test_current_head_clean_review_needs_no_request(self) -> None:
        facts = dashboard_facts(
            ci_pending_count=0,
            copilot_review_exists=True,
        )

        facts = set_copilot_review_request_needed(facts, "maintainer", enabled=True)

        self.assertFalse(facts.copilot_review_request_needed)

    def test_running_checks_do_not_hold_a_re_review_request(self) -> None:
        # The review and the checks run at once, so a slow suite does not delay
        # the request. Checks that fail send the pull request to its author,
        # which is a route that never reaches here.
        facts = dashboard_facts(
            ci_pending_count=1,
            copilot_review_exists=True,
            copilot_review_stale=True,
        )

        facts = set_copilot_review_request_needed(facts, "approver", enabled=True)

        self.assertTrue(facts.copilot_review_request_needed)

    def test_unavailable_check_results_do_not_hold_a_re_review_request(self) -> None:
        facts = dashboard_facts(
            copilot_review_exists=True,
            copilot_review_stale=True,
        )

        facts = set_copilot_review_request_needed(facts, "approver", enabled=True)

        self.assertTrue(facts.copilot_review_request_needed)

    def test_author_route_does_not_request_a_re_review(self) -> None:
        facts = dashboard_facts(
            ci_pending_count=0,
            copilot_review_exists=True,
            copilot_review_stale=True,
        )

        facts = set_copilot_review_request_needed(facts, "author", enabled=True)

        self.assertFalse(facts.copilot_review_request_needed)

    def test_disabled_gate_requests_nothing(self) -> None:
        facts = dashboard_facts(
            ci_pending_count=0,
            copilot_review_exists=True,
            copilot_review_stale=True,
        )

        facts = set_copilot_review_request_needed(facts, "maintainer", enabled=False)

        self.assertFalse(facts.copilot_review_request_needed)


class HeadShaSourceTest(unittest.TestCase):
    def test_head_sha_prefers_pr_head_ref_oid_over_truncated_commits(self) -> None:
        facts = evaluation_facts(
            {
                "pr": {
                    "updatedAt": "2026-07-20T03:00:00Z",
                    "createdAt": "2026-07-20T01:00:00Z",
                    "author": {"login": "author"},
                    "assignees": [],
                    "mergeStateStatus": "CLEAN",
                    "mergeable": "MERGEABLE",
                    "headRefOid": "real-head",
                },
                "reviews": [
                    {
                        "id": 10,
                        "commit_id": "real-head",
                        "finding_count": 0,
                        "user": {"login": "copilot"},
                        "submitted_at": "2026-07-20T02:30:00Z",
                    },
                ],
                # The commits REST endpoint is bounded at 250 entries, so its
                # last element is not the real head for a large PR.
                "commits": [{"sha": "commit-1"}, {"sha": "commit-250"}],
                "review_comments": [],
                "checks": [],
            },
            "author",
            [],
        )

        self.assertEqual(facts.head_sha, "real-head")
        self.assertTrue(facts.copilot_review_exists)
        self.assertFalse(facts.copilot_review_needed)


class BackfillSelectionTest(unittest.TestCase):
    def test_selects_untracked_drafts_once_and_removes_closed_markers(self) -> None:
        selection = select_backfill_prs(
            [
                {"number": 1, "isDraft": False},
                {"number": 2, "isDraft": True},
                {"number": 3, "isDraft": True},
            ],
            dashboard_state(
                stored_dashboard_result(4),
                draft_pr_numbers=frozenset({3, 5}),
            ),
            {"cursor": {}},
            50,
        )

        self.assertEqual(
            [1, 2],
            [pr["number"] for pr in selection.selected_prs],
        )
        self.assertEqual({4, 5}, selection.cached_pr_numbers_to_remove)


class InitialBackfillCompletionTest(unittest.TestCase):
    def test_open_drafts_must_be_tracked_before_backfill_completes(self) -> None:
        state = dashboard_state(draft_pr_numbers=frozenset({1}))

        state = complete_initial_backfill_if_ready(state, {1, 2})

        self.assertFalse(state.initial_backfill_complete)
        completed = complete_initial_backfill_if_ready(
            dashboard_state(draft_pr_numbers=frozenset({1, 2})),
            {1, 2},
        )
        self.assertTrue(completed.initial_backfill_complete)

    def test_marks_complete_only_after_all_open_prs_are_cached(self) -> None:
        state = dashboard_state(stored_dashboard_result(1))

        state = complete_initial_backfill_if_ready(state, {1, 2})
        self.assertFalse(state.initial_backfill_complete)

        state = state.with_result(2, stored_dashboard_result(2))
        state = complete_initial_backfill_if_ready(state, {1, 2})
        self.assertTrue(state.initial_backfill_complete)
        self.assertIs(state, complete_initial_backfill_if_ready(state, {1, 2}))

    def test_empty_repository_completes_initial_backfill(self) -> None:
        state = complete_initial_backfill_if_ready(dashboard_state(), set())

        self.assertTrue(state.initial_backfill_complete)

    def test_writes_initial_backfill_status_to_github_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            for name, state, expected in (
                ("incomplete", None, "false"),
                (
                    "complete",
                    dashboard_state(initial_backfill_complete=True),
                    "true",
                ),
            ):
                with self.subTest(name=name):
                    output_path = Path(temp_dir) / name
                    with patch("dashboard.load_dashboard_state_cache", return_value=state):
                        write_initial_backfill_output(output_path)

                    self.assertEqual(
                        f"initial_backfill_complete={expected}\n",
                        output_path.read_text(encoding="utf-8"),
                    )

class StatusCommentQueueTest(unittest.TestCase):
    @patch("dashboard.record_copilot_review_observation")
    @patch("dashboard.record_author_nudge_observation")
    @patch("dashboard.save_dashboard_update_state", return_value=0)
    @patch("dashboard.enqueue_status_comment_update")
    @patch(
        "dashboard.load_dashboard_state_cache",
        return_value=dashboard_state(
            stored_dashboard_result(12),
            stored_dashboard_result(34),
            stored_dashboard_result(56),
        ),
    )
    def test_removed_dashboard_results_enqueue_status_comments(
        self,
        _load_state: Mock,
        enqueue_update: Mock,
        save_state: Mock,
        record_nudge: Mock,
        _record_copilot: Mock,
    ) -> None:
        args = Namespace(pr_number=None)

        status = remove_cached_dashboard_prs(args, {12, 34})

        self.assertEqual(0, status)
        self.assertEqual(
            [call(12), call(34)],
            sorted(enqueue_update.call_args_list, key=lambda call: call.args[0]),
        )
        saved_state = save_state.call_args.args[1]
        self.assertEqual(frozenset({56}), saved_state.pr_numbers)
        self.assertEqual(
            [
                call(12, None, ANY, prepare_due=False),
                call(34, None, ANY, prepare_due=False),
            ],
            sorted(record_nudge.call_args_list, key=lambda value: value.args[0]),
        )

    @patch("dashboard.record_copilot_review_observation")
    @patch("dashboard.record_author_nudge_observation")
    @patch("dashboard.clear_backfill_pr_failure")
    @patch("dashboard.save_dashboard_update_state", return_value=0)
    @patch("dashboard.enqueue_status_comment_update")
    @patch("dashboard.load_dashboard_state_cache")
    def test_targeted_state_change_enqueues_status_comment(
        self,
        load_state: Mock,
        enqueue_update: Mock,
        _save_state: Mock,
        _clear_backfill_failure: Mock,
        record_nudge: Mock,
        record_copilot: Mock,
    ) -> None:
        starting_result = stored_dashboard_result(12)
        evaluated_result = evaluation_success(
            12,
            route=DashboardRoute.APPROVER,
        )
        state = dashboard_state(starting_result)
        calculation = prepare_dashboard_update(
            state,
            {12},
            12,
        ).with_evaluated_result(evaluated_result)
        accepted_result = StoredDashboardResult.from_evaluation(evaluated_result)
        load_state.return_value = state

        with patch(
            "dashboard.accept_dashboard_update",
            wraps=accept_dashboard_update,
        ) as accept_update:
            status = apply_targeted_dashboard_update(
                Namespace(pr_number=12, prepare_author_nudges=True),
                calculation,
            )

        self.assertEqual(0, status)
        accept_update.assert_called_once()
        enqueue_update.assert_called_once_with(12)
        record_nudge.assert_called_once_with(
            12,
            accepted_result,
            ANY,
            prepare_due=True,
        )
        record_copilot.assert_called_once_with(
            12,
            accepted_result,
            record_nudge.call_args.args[2],
        )

    @patch("dashboard.record_copilot_review_observation")
    @patch("dashboard.record_author_nudge_observation")
    @patch("dashboard.clear_backfill_pr_failure")
    @patch("dashboard.save_dashboard_update_state", return_value=0)
    @patch("dashboard.enqueue_status_comment_update")
    @patch("dashboard.load_dashboard_state_cache")
    def test_unchanged_targeted_state_does_not_enqueue_status_comment(
        self,
        load_state: Mock,
        enqueue_update: Mock,
        _save_state: Mock,
        _clear_backfill_failure: Mock,
        record_nudge: Mock,
        _record_copilot: Mock,
    ) -> None:
        accepted_result = stored_dashboard_result(
            12,
            route=DashboardRoute.APPROVER,
        )
        state = dashboard_state(accepted_result)
        calculation = prepare_dashboard_update(
            state,
            {12},
            12,
        ).with_evaluated_result(evaluation_success(
            12,
            route=DashboardRoute.APPROVER,
        ))
        load_state.return_value = state

        status = apply_targeted_dashboard_update(Namespace(pr_number=12), calculation)

        self.assertEqual(0, status)
        enqueue_update.assert_not_called()
        record_nudge.assert_called_once_with(
            12,
            accepted_result,
            ANY,
            prepare_due=False,
        )

class RequiredCiRoutingTest(unittest.TestCase):
    def test_non_blocking_check_failures_use_deterministic_casefold_tiebreaker(self) -> None:
        facts = evaluation_facts(
            {
                "pr": {
                    "updatedAt": "2026-07-14T03:00:00Z",
                    "createdAt": "2026-07-14T01:00:00Z",
                    "author": {"login": "author"},
                    "assignees": [],
                    "mergeStateStatus": "CLEAN",
                    "mergeable": "MERGEABLE",
                },
                "checks": [],
                "non_blocking_check_failures": [
                    {"name": "codeql", "bucket": "fail"},
                    {"name": "CodeQL", "bucket": "fail"},
                ],
            },
            "author",
            [],
        )

        self.assertEqual(
            ("CodeQL", "codeql"),
            facts.non_blocking_check_failures,
        )

    def test_required_check_buckets_control_ci_facts(self) -> None:
        cases = (
            ("TIMED_OUT", "fail", 1, 0),
            ("ACTION_REQUIRED", "fail", 1, 0),
            ("STARTUP_FAILURE", "fail", 1, 0),
            ("CANCELLED", "cancel", 1, 0),
            ("IN_PROGRESS", "pending", 0, 1),
            ("SKIPPED", "skipping", 0, 0),
            ("SUCCESS", "pass", 0, 0),
        )
        for state, bucket, failing, pending in cases:
            with self.subTest(state=state, bucket=bucket):
                facts = evaluation_facts(
                    {
                        "pr": {
                            "updatedAt": "2026-07-14T03:00:00Z",
                            "createdAt": "2026-07-14T01:00:00Z",
                            "author": {"login": "author"},
                            "assignees": [],
                            "mergeStateStatus": "CLEAN",
                            "mergeable": "MERGEABLE",
                        },
                        "checks": [{"state": state, "bucket": bucket}],
                        "non_blocking_check_failures": [
                            {"name": "workflow-notification", "bucket": "fail"},
                        ],
                    },
                    "author",
                    [],
                )

                self.assertEqual(failing, facts.ci_failing_count)
                self.assertEqual(pending, facts.ci_pending_count)
                self.assertEqual(
                    ("workflow-notification",),
                    facts.non_blocking_check_failures,
                )

    def test_override_command_does_not_clear_required_check_failures(self) -> None:
        facts = evaluation_facts(
            {
                "pr": {
                    "updatedAt": "2026-07-17T03:00:00Z",
                    "createdAt": "2026-07-14T01:00:00Z",
                    "author": {"login": "author"},
                    "assignees": [],
                    "mergeStateStatus": "CLEAN",
                    "mergeable": "MERGEABLE",
                },
                "checks": [
                    {"bucket": "fail", "completed_at": "2026-07-17T01:00:00Z"},
                    {"bucket": "fail", "completed_at": "2026-07-17T02:00:00Z"},
                    {"bucket": "fail", "completed_at": "2026-07-17T05:00:00Z"},
                ],
                "issue_comments": [
                    {
                        "id": 7,
                        "user": {"login": "author"},
                        "created_at": "2026-07-17T02:00:00Z",
                        "body": "/dashboard route:reviewers",
                    }
                ],
            },
            "author",
            [],
        )

        self.assertEqual(3, facts.ci_failing_count)
        self.assertEqual("2026-07-17T01:00:00+00:00", facts.ci_failing_since)


class ActivityFactsIntegrationTest(unittest.TestCase):
    def test_formats_activity_clocks_and_clamps_overall_activity_to_creation(
        self,
    ) -> None:
        raw = {
            "pr": {
                # A later updatedAt must not advance any activity clock: the
                # dashboard's own status comment bumps it on every refresh.
                "updatedAt": "2026-07-20T09:00:00Z",
                "createdAt": "2026-07-20T01:00:00Z",
                "author": {"login": "author"},
                "assignees": [],
                "mergeStateStatus": "CLEAN",
                "mergeable": "MERGEABLE",
            },
            "checks": [],
        }
        prepared_reviewers = prepare_reviewers(ReviewerInput((), (), ()))
        activity = PullRequestActivity(
            (),
            datetime(2024, 1, 5, tzinfo=timezone.utc),
            datetime(2026, 7, 20, 2, tzinfo=timezone.utc),
            datetime(2026, 7, 20, 3, tzinfo=timezone.utc),
        )

        facts = evaluation_compute_facts(
            normalize_pull_request_source(raw),
            "author",
            activity,
            prepared_reviewers,
            frozenset(),
            dashboard_facts(),
        )

        self.assertEqual("2026-07-20T01:00:00+00:00", facts.last_activity_at)
        self.assertEqual(
            "2026-07-20T02:00:00+00:00",
            facts.last_author_activity_at,
        )
        self.assertEqual(
            "2026-07-20T03:00:00+00:00",
            facts.last_approver_activity_at,
        )

    def test_uses_creation_time_without_participant_activity(self) -> None:
        raw = {
            "pr": {
                "createdAt": "2026-07-20T01:00:00Z",
                "author": {"login": "author"},
                "assignees": [],
                "mergeStateStatus": "CLEAN",
                "mergeable": "MERGEABLE",
            },
            "checks": [],
        }

        facts = evaluation_compute_facts(
            normalize_pull_request_source(raw),
            "author",
            PullRequestActivity((), None, None, None),
            prepare_reviewers(ReviewerInput((), (), ())),
            frozenset(),
            dashboard_facts(),
        )

        self.assertEqual(
            "2026-07-20T01:00:00+00:00",
            facts.last_activity_at,
        )


class BackfillFailureIsolationTest(unittest.TestCase):
    def test_failed_pr_does_not_block_later_backfill_progress(self) -> None:
        args = Namespace(
            repo="repo",
            approver_team=["approvers"],
            state_branch="state",
            model="model",
            required_approvals=1,
            non_blocking_check_pattern=[],
        )
        current_state = dashboard_state()
        backfill_state = {"cursor": {}}
        refreshed_pr_numbers: list[int] = []

        def load_dashboard_state() -> DashboardState:
            return current_state

        def load_backfill_state() -> dict:
            return deepcopy(backfill_state)

        def save_backfill_state(state: dict) -> None:
            backfill_state.clear()
            backfill_state.update(deepcopy(state))

        def build_update(*call_args) -> DashboardStateUpdate:
            pr_number = call_args[5]
            starting_state = call_args[9]
            refreshed_pr_numbers.append(pr_number)
            result = (
                EvaluationFailure(
                    pr_number=pr_number,
                    route=DashboardRoute.UNKNOWN,
                    error="failed",
                )
                if pr_number == 1
                else evaluation_success(
                    pr_number,
                    route=DashboardRoute.APPROVER,
                )
            )
            return prepare_dashboard_update(
                starting_state,
                {1, 2},
                pr_number,
            ).with_evaluated_result(result)

        def save_dashboard_state(
            _args,
            state: DashboardState,
            unchanged: bool,
        ) -> int:
            nonlocal current_state
            if not unchanged:
                current_state = state
            return 0

        def push_state_changes(_state_dir, _message, update_state, **_kwargs) -> int:
            return update_state()

        with (
            patch("dashboard.list_open_prs", return_value=[{"number": 1}, {"number": 2}]),
            patch("dashboard.DEFAULT_CLASSIFICATION_CACHE_STORE"),
            patch("dashboard.load_reviewer_set", return_value={"reviewer"}),
            patch("dashboard.load_dashboard_state_cache", side_effect=load_dashboard_state),
            patch("dashboard.load_backfill_state", side_effect=load_backfill_state),
            patch("dashboard.save_backfill_state", side_effect=save_backfill_state),
            patch("dashboard.build_dashboard_update_for_pr", side_effect=build_update),
            patch(
                "dashboard.accept_dashboard_update",
                wraps=accept_dashboard_update,
            ) as accept_update,
            patch("dashboard.save_dashboard_update_state", side_effect=save_dashboard_state),
            patch("dashboard.record_author_nudge_observation") as record_nudge,
            patch("dashboard.state_branch.configure_git"),
            patch("dashboard.state_branch.checkout_state"),
            patch("dashboard.state_branch.remove_existing_state_dir"),
            patch("dashboard.state_branch.push_state_changes", side_effect=push_state_changes),
        ):
            status = update_dashboard_for_backfill(args, Path("state"))

        self.assertEqual(refreshed_pr_numbers, [1, 2])
        self.assertEqual(2, accept_update.call_count)
        record_nudge.assert_called_once_with(2, ANY, ANY, prepare_due=False)
        self.assertEqual(status, BACKFILL_RECORDED_FAILURE_STATUS)
        self.assertEqual(
            frozenset({2}),
            current_state.pr_numbers,
        )
        self.assertEqual(
            DashboardRoute.APPROVER,
            current_state.result_for(2).route if current_state.result_for(2) else None,
        )
        self.assertTrue(current_state.initial_backfill_complete)
        self.assertEqual(backfill_state["cursor"], {"last_pr_number": 2})
        self.assertEqual(backfill_failed_pr_numbers(backfill_state), {1})

    def test_successful_retry_clears_recorded_failure(self) -> None:
        state = {"failed_pr_numbers": [1, 2]}

        self.assertEqual(set_backfill_pr_failed(state, 1, False), {2})
        self.assertEqual(state["failed_pr_numbers"], [2])

    def test_successful_targeted_update_clears_recorded_failure(self) -> None:
        args = Namespace(pr_number=1)
        starting_result = stored_dashboard_result(1)
        state = dashboard_state(starting_result)
        calculation = prepare_dashboard_update(
            state,
            {1},
            1,
        ).with_evaluated_result(
            evaluation_success(1, route=DashboardRoute.APPROVER)
        )
        backfill_state = {
            "cursor": {"last_pr_number": 7},
            "failed_pr_numbers": [1, 2],
        }
        saved_backfill_state: dict = {}

        with (
            patch(
                "dashboard.load_dashboard_state_cache",
                return_value=state,
            ),
            patch("dashboard.load_backfill_state", return_value=deepcopy(backfill_state)),
            patch(
                "dashboard.save_backfill_state",
                side_effect=lambda state: saved_backfill_state.update(deepcopy(state)),
            ),
            patch("dashboard.save_dashboard_update_state", return_value=0) as save_dashboard,
        ):
            status = apply_targeted_dashboard_update(args, calculation)

        self.assertEqual(status, 0)
        self.assertEqual(saved_backfill_state["cursor"], {"last_pr_number": 7})
        self.assertEqual(saved_backfill_state["failed_pr_numbers"], [2])
        saved_dashboard_state = save_dashboard.call_args.args[1]
        saved_result = saved_dashboard_state.result_for(1)
        self.assertIsNotNone(saved_result)
        self.assertEqual(DashboardRoute.APPROVER, saved_result.route)
        self.assertFalse(save_dashboard.call_args.args[2])

    def test_emits_initial_backfill_status_only_for_accepted_state_outcomes(self) -> None:
        for status, should_emit in (
            (0, True),
            (BACKFILL_RECORDED_FAILURE_STATUS, True),
            (1, False),
        ):
            with (
                self.subTest(status=status),
                tempfile.TemporaryDirectory() as temp_dir,
                patch(
                    "sys.argv",
                    [
                        "dashboard.py",
                        "--state-branch",
                        "state",
                        "--repo",
                        "repo",
                        "--approver-team",
                        "approvers",
                        "--github-output",
                        str(Path(temp_dir) / "output"),
                    ],
                ),
                patch("dashboard.state_branch.temporary_state_dir") as temporary_state_dir,
                patch("dashboard.update_dashboard_via_state_branch", return_value=status),
                patch("dashboard.write_initial_backfill_output") as write_output,
            ):
                temporary_state_dir.return_value.__enter__.return_value = Path(temp_dir)

                self.assertEqual(main(), status)

            if should_emit:
                write_output.assert_called_once_with(Path(temp_dir) / "output")
            else:
                write_output.assert_not_called()


if __name__ == "__main__":
    unittest.main()