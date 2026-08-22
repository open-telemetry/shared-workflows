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
    DashboardUpdate,
    add_reviewers,
    apply_targeted_dashboard_update,
    assign_author_nudge_episode,
    author_action_discussion_urls,
    backfill_failed_pr_numbers,
    build_pr_result,
    complete_initial_backfill_if_ready,
    compute_facts,
    current_approval_count,
    fetch_pr_raw,
    main,
    merge_dashboard_update_with_latest_state,
    remove_cached_dashboard_prs,
    set_backfill_pr_failed,
    update_dashboard_for_backfill,
    write_initial_backfill_output,
)


class RerequestedReviewTest(unittest.TestCase):
    @staticmethod
    def approval(login: str) -> dict[str, object]:
        return {
            "kind": "review-state",
            "timestamp": "2026-08-01T00:00:00Z",
            "actor": login,
            "actor_role": "approver",
            "state": "APPROVED",
        }

    @staticmethod
    def changes_requested(login: str) -> dict[str, object]:
        return {
            "kind": "review-state",
            "timestamp": "2026-08-01T00:00:00Z",
            "actor": login,
            "actor_role": "approver",
            "state": "CHANGES_REQUESTED",
        }

    def test_rerequested_approval_becomes_pending(self) -> None:
        events = [self.approval("reviewer")]
        review_requests = [{"__typename": "User", "login": "reviewer"}]
        facts = {
            "approval_count": current_approval_count(events, review_requests),
            "assignees": [],
            "is_maintenance_bot": False,
        }

        add_reviewers(facts, events, [], [], {}, review_requests)

        self.assertEqual(0, facts["approval_count"])
        self.assertEqual(
            {
                "login": "reviewer",
                "approved": False,
                "approved_non_team": False,
                "pending_review": True,
                "changes_requested": False,
                "open_thread": False,
                "top_level_feedback": False,
            },
            facts["reviewers"][0],
        )

    def test_other_active_approvals_still_count(self) -> None:
        events = [self.approval("active"), self.approval("rerequested")]
        review_requests = [{"__typename": "User", "login": "rerequested"}]
        facts = {
            "approval_count": current_approval_count(events, review_requests),
            "assignees": [],
            "is_maintenance_bot": False,
        }

        add_reviewers(facts, events, [], [], {}, review_requests)

        self.assertEqual(1, facts["approval_count"])
        self.assertTrue(facts["reviewers"][0]["approved"])
        self.assertTrue(facts["reviewers"][1]["pending_review"])

    def test_first_review_request_is_not_shown_as_a_rereview(self) -> None:
        facts = {"assignees": []}

        add_reviewers(
            facts,
            [],
            [],
            [],
            {},
            [{"__typename": "User", "login": "new-reviewer"}],
        )

        self.assertEqual([], facts["reviewers"])

    def test_team_request_does_not_clear_individual_approvals(self) -> None:
        events = [self.approval("reviewer")]
        review_requests = [{"__typename": "Team", "slug": "example-approvers"}]

        self.assertEqual(1, current_approval_count(events, review_requests))

    def test_rerequest_keeps_a_blocking_changes_requested_state(self) -> None:
        # GitHub keeps the review blocking the merge until the reviewer
        # submits a new one, so the request adds a wait rather than clearing
        # the block.
        events = [self.changes_requested("reviewer")]
        review_requests = [{"__typename": "User", "login": "reviewer"}]
        facts = {"assignees": []}

        add_reviewers(facts, events, [], [], {}, review_requests)

        self.assertTrue(facts["reviewers"][0]["pending_review"])
        self.assertTrue(facts["reviewers"][0]["changes_requested"])






class AuthorNudgeEpisodeTest(unittest.TestCase):
    def test_preserves_episode_while_route_remains_author(self) -> None:
        facts: dict[str, object] = {}

        assign_author_nudge_episode(
            facts,
            "author",
            {
                "route": "author",
                "facts": {"author_nudge_episode_id": "abc123"},
            },
            [],
        )

        self.assertEqual("abc123", facts["author_nudge_episode_id"])

    @patch("dashboard.uuid.uuid4")
    def test_starts_new_episode_after_known_route_departure(self, uuid4: Mock) -> None:
        uuid4.return_value.hex = "def456"
        facts: dict[str, object] = {}

        assign_author_nudge_episode(
            facts,
            "author",
            {"route": "approver", "facts": {}},
            [{
                "performed_via_github_app": {"slug": "opentelemetry-pr-dashboard"},
                "body": (
                    "<!-- pull-request-dashboard-status -->\n"
                    "<!-- pull-request-dashboard-author-nudge-episode:abc123 -->"
                ),
            }],
        )

        self.assertEqual("def456", facts["author_nudge_episode_id"])

    def test_recovers_episode_from_status_comment_after_cache_loss(self) -> None:
        facts: dict[str, object] = {}

        assign_author_nudge_episode(
            facts,
            "author",
            None,
            [{
                "performed_via_github_app": {"slug": "opentelemetry-pr-dashboard"},
                "body": (
                    "<!-- pull-request-dashboard-status -->\n"
                    "<!-- pull-request-dashboard-author-nudge-episode:abc123 -->"
                ),
            }],
        )

        self.assertEqual("abc123", facts["author_nudge_episode_id"])

    def test_preserves_episode_while_route_is_held_for_gates(self) -> None:
        facts: dict[str, object] = {"route_held_for_gates": True}

        assign_author_nudge_episode(
            facts,
            "author",
            {
                "route": "author",
                "facts": {"author_nudge_episode_id": "abc123"},
            },
            [],
        )

        self.assertEqual("abc123", facts["author_nudge_episode_id"])

    def test_preserves_episode_while_pr_is_conflicted(self) -> None:
        facts: dict[str, object] = {"conflicts": "yes"}

        assign_author_nudge_episode(
            facts,
            "author",
            {
                "route": "author",
                "facts": {"author_nudge_episode_id": "abc123"},
            },
            [],
        )

        self.assertEqual("abc123", facts["author_nudge_episode_id"])

class FetchPrRawTest(unittest.TestCase):
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
                "github_cli.gh_pr_view",
                return_value={"id": "PR_node", "baseRefName": "main"},
            ),
            patch(
                "github_cli.fetch_pr_issue_comments",
                return_value=issue_comments,
            ) as fetch_issue_comments,
            patch("github_cli.gh_api", side_effect=gh_api) as routing_rest_api,
            patch("dashboard.gh_api", side_effect=gh_api) as commits_rest_api,
            patch("github_cli.fetch_review_threads", return_value=[]),
            patch("github_cli.fetch_review_requests", return_value=[]),
            patch(
                "github_cli.fetch_pr_reviews",
                return_value=[],
            ),
            patch(
                "github_cli.gh_pr_check_rollup",
                return_value={
                    "head_oid": "",
                    "required": [],
                    "non_blocking_failures": [],
                    "code_scanning": [],
                    "pending": [],
                },
            ),
            patch("github_cli.gh_branch_rules", return_value=[]),
            patch("github_cli.include_missing_required_checks", return_value=[]),
        ):
            raw = fetch_pr_raw(
                "owner/repo",
                "owner",
                "repo",
                {"number": 7},
                [],
            )

        self.assertEqual(raw["issue_comments"], issue_comments)
        fetch_issue_comments.assert_called_once_with("owner", "repo", 7)
        self.assertEqual(
            {
                call.args[0]
                for call in [
                    *routing_rest_api.call_args_list,
                    *commits_rest_api.call_args_list,
                ]
            },
            set(rest_payloads),
        )


class BuildPrResultTest(unittest.TestCase):
    @staticmethod
    def raw_pr(*, checks: list[dict[str, object]] | None = None) -> dict[str, object]:
        return {
            "summary": {"author": {"login": "author"}},
            "pr": {
                "state": "OPEN",
                "isDraft": False,
                "title": "Routing integration",
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
            "issue_comments": [],
            "review_comments": [],
            "reviews": [],
            "review_threads": [],
            "review_requests": [],
            "checks": checks or [],
            "non_blocking_check_failures": [],
        }

    @patch(
        "dashboard.classify_discussion_domains",
        side_effect=AssertionError("classification must be bypassed"),
    )
    @patch("dashboard.fetch_pr_raw")
    def test_override_binds_to_the_observed_head_before_classification(
        self, fetch_raw: Mock, classify: Mock
    ) -> None:
        fetch_raw.return_value = {
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
        }

        result = build_pr_result(
            "owner/repo",
            "owner",
            "repo",
            {"number": 7},
            {"reviewer"},
            "model",
            1,
            [],
            require_clean_copilot_review_branches=["main"],
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result["failed"])
        self.assertEqual("approver", result["route"])
        self.assertEqual({}, result["pending_actions"])
        self.assertEqual(
            "abcdef123456", result["facts"]["dashboard_override_head_sha"]
        )
        self.assertEqual(
            [
                {
                    "comment_id": 102,
                    "kind": "routed",
                    "head_sha": "abcdef123456",
                    "user": "author",
                    "route": "approver",
                    "held_gates": "",
                }
            ],
            result["facts"]["dashboard_command_replies"],
        )
        classify.assert_not_called()

    @patch("dashboard.classify_discussion_domains", return_value=([], [], []))
    @patch("dashboard.fetch_pr_raw")
    def test_conflict_uses_normal_discussion_and_approval_routing(
        self, fetch_raw: Mock, classify: Mock
    ) -> None:
        fetch_raw.return_value = {
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
        }

        result = build_pr_result(
            "owner/repo",
            "owner",
            "repo",
            {"number": 7},
            {"reviewer"},
            "model",
            1,
            [],
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result["failed"])
        self.assertEqual("approver", result["route"])
        self.assertEqual({}, result["pending_actions"])
        self.assertEqual("last_author_activity", result["facts"]["waiting_age_basis"])
        classify.assert_called_once()

    @patch("dashboard.classify_discussion_domains", return_value=([], [], []))
    @patch("dashboard.fetch_pr_raw")
    def test_normal_routing_flows_through_build_pr_result(
        self, fetch_raw: Mock, classify: Mock
    ) -> None:
        fetch_raw.return_value = self.raw_pr(
            checks=[{"name": "required", "bucket": "pass"}]
        )

        result = build_pr_result(
            "owner/repo",
            "owner",
            "repo",
            {"number": 7},
            {"reviewer"},
            "model",
            1,
            [],
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result["failed"])
        self.assertEqual("approver", result["route"])
        self.assertFalse(result["facts"]["route_held_for_gates"])
        classify.assert_called_once()

    @patch("routing_decision.utc_now")
    @patch("dashboard.classify_discussion_domains", return_value=([], [], []))
    @patch("dashboard.fetch_pr_raw")
    def test_running_required_check_keeps_integrated_route_held(
        self, fetch_raw: Mock, classify: Mock, utc_now: Mock
    ) -> None:
        utc_now.return_value = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)
        fetch_raw.return_value = self.raw_pr(
            checks=[{"name": "required", "bucket": "pending"}]
        )

        result = build_pr_result(
            "owner/repo",
            "owner",
            "repo",
            {"number": 7},
            {"reviewer"},
            "model",
            1,
            [],
            previous_result={
                "route": "author",
                "facts": {
                    "head_sha": "abcdef123456",
                    "waiting_since": "2026-08-16T08:00:00+00:00",
                },
            },
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result["failed"])
        self.assertEqual("author", result["route"])
        self.assertTrue(result["facts"]["route_held_for_gates"])
        self.assertEqual(
            "2026-08-16T12:00:00+00:00", result["facts"]["route_held_since"]
        )
        classify.assert_called_once()

    @patch(
        "dashboard.classify_discussion_domains",
        return_value=([], [{"failed": True, "error": "model failed"}], []),
    )
    @patch("dashboard.fetch_pr_raw")
    def test_classification_failure_preserves_integrated_routing_failure_facts(
        self, fetch_raw: Mock, classify: Mock
    ) -> None:
        fetch_raw.return_value = self.raw_pr()

        result = build_pr_result(
            "owner/repo",
            "owner",
            "repo",
            {"number": 7},
            {"reviewer"},
            "model",
            1,
            [],
            previous_result={
                "route": "author",
                "facts": {
                    "copilot_first_review_missing_since": "2026-08-11T12:00:00Z"
                },
            },
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result["failed"])
        self.assertEqual("unknown", result["route"])
        self.assertEqual(
            "2026-08-11T12:00:00Z",
            result["facts"]["copilot_first_review_missing_since"],
        )
        classify.assert_called_once()


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
            ["https://example.test/discussion/1", "https://example.test/discussion/2"],
            author_action_discussion_urls(discussions, pending_actions),
        )


class CopilotReviewGateTest(unittest.TestCase):
    def test_current_head_matches_latest_clean_copilot_review(self) -> None:
        facts = compute_facts(
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

        self.assertTrue(facts["copilot_review_requested"])
        self.assertTrue(facts["copilot_review_exists"])
        self.assertFalse(facts["copilot_review_needed"])

    def test_late_stale_review_does_not_replace_clean_current_head_review(self) -> None:
        facts = compute_facts(
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

        self.assertFalse(facts["copilot_review_needed"])

    def test_push_since_latest_clean_copilot_review_needs_rereview(self) -> None:
        facts = compute_facts(
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

        self.assertFalse(facts["copilot_review_requested"])
        self.assertTrue(facts["copilot_review_needed"])

    def test_unresolved_copilot_thread_on_current_head_needs_rereview(self) -> None:
        facts = compute_facts(
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

        self.assertFalse(facts["copilot_review_stale"])
        self.assertTrue(facts["copilot_review_needed"])

    def test_resolved_copilot_findings_on_current_head_are_clean(self) -> None:
        # A review's comment count never shrinks, so counting it would hold the
        # PR on feedback the author already addressed.
        facts = compute_facts(
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

        self.assertFalse(facts["copilot_review_stale"])
        self.assertFalse(facts["copilot_review_needed"])

    def test_human_thread_does_not_count_as_a_copilot_finding(self) -> None:
        facts = compute_facts(
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

        self.assertFalse(facts["copilot_review_needed"])

    def test_findings_only_history_needs_rereview(self) -> None:
        facts = compute_facts(
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

        self.assertTrue(facts["copilot_review_needed"])

    def test_waits_for_automatic_initial_copilot_review(self) -> None:
        facts = compute_facts(
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

        self.assertFalse(facts["copilot_review_exists"])
        self.assertFalse(facts["copilot_review_needed"])

    def test_pending_first_review_request_is_not_duplicated(self) -> None:
        facts = {
            "ci_pending_count": 0,
            "copilot_review_requested": True,
            "copilot_review_exists": False,
            "copilot_review_stale": False,
            "copilot_first_review_missing_since": "2020-01-01T00:00:00+00:00",
        }

        set_copilot_review_request_needed(facts, "approver", enabled=True)

        self.assertFalse(facts["copilot_review_request_needed"])

    def test_marks_re_review_needed_after_push_since_clean_review(self) -> None:
        facts = {
            "ci_pending_count": 0,
            "copilot_review_requested": False,
            "copilot_review_exists": True,
            "copilot_review_stale": True,
        }

        set_copilot_review_request_needed(facts, "maintainer", enabled=True)

        self.assertTrue(facts["copilot_review_request_needed"])

    def test_marks_re_review_needed_before_reviewer_handoff(self) -> None:
        facts = {
            "ci_pending_count": 0,
            "copilot_review_requested": False,
            "copilot_review_exists": True,
            "copilot_review_stale": True,
        }

        set_copilot_review_request_needed(facts, "approver", enabled=True)

        self.assertTrue(facts["copilot_review_request_needed"])

    def test_open_findings_on_current_head_request_no_re_review(self) -> None:
        # Re-reviewing unchanged code cannot resolve a thread the author owns,
        # so requesting one here would repeat on every pass.
        facts = {
            "ci_pending_count": 0,
            "copilot_review_requested": False,
            "copilot_review_exists": True,
            "copilot_review_stale": False,
            "copilot_review_needed": True,
        }

        set_copilot_review_request_needed(facts, "maintainer", enabled=True)

        self.assertFalse(facts["copilot_review_request_needed"])

    def test_pending_re_review_is_not_requested_twice(self) -> None:
        facts = {
            "ci_pending_count": 0,
            "copilot_review_requested": True,
            "copilot_review_exists": True,
            "copilot_review_stale": True,
        }

        set_copilot_review_request_needed(facts, "maintainer", enabled=True)

        self.assertFalse(facts["copilot_review_request_needed"])

    def test_current_head_clean_review_needs_no_request(self) -> None:
        facts = {
            "ci_pending_count": 0,
            "copilot_review_requested": False,
            "copilot_review_exists": True,
            "copilot_review_stale": False,
        }

        set_copilot_review_request_needed(facts, "maintainer", enabled=True)

        self.assertFalse(facts["copilot_review_request_needed"])

    def test_running_checks_do_not_hold_a_re_review_request(self) -> None:
        # The review and the checks run at once, so a slow suite does not delay
        # the request. Checks that fail send the pull request to its author,
        # which is a route that never reaches here.
        facts = {
            "ci_pending_count": 1,
            "copilot_review_requested": False,
            "copilot_review_exists": True,
            "copilot_review_stale": True,
        }

        set_copilot_review_request_needed(facts, "approver", enabled=True)

        self.assertTrue(facts["copilot_review_request_needed"])

    def test_unavailable_check_results_do_not_hold_a_re_review_request(self) -> None:
        facts = {
            "copilot_review_requested": False,
            "copilot_review_exists": True,
            "copilot_review_stale": True,
        }

        set_copilot_review_request_needed(facts, "approver", enabled=True)

        self.assertTrue(facts["copilot_review_request_needed"])

    def test_author_route_does_not_request_a_re_review(self) -> None:
        facts = {
            "ci_pending_count": 0,
            "copilot_review_requested": False,
            "copilot_review_exists": True,
            "copilot_review_stale": True,
        }

        set_copilot_review_request_needed(facts, "author", enabled=True)

        self.assertFalse(facts["copilot_review_request_needed"])

    def test_disabled_gate_requests_nothing(self) -> None:
        facts = {
            "ci_pending_count": 0,
            "copilot_review_requested": False,
            "copilot_review_exists": True,
            "copilot_review_stale": True,
        }

        set_copilot_review_request_needed(facts, "maintainer", enabled=False)

        self.assertFalse(facts["copilot_review_request_needed"])


class HeadShaSourceTest(unittest.TestCase):
    def test_head_sha_prefers_pr_head_ref_oid_over_truncated_commits(self) -> None:
        facts = compute_facts(
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

        self.assertEqual(facts["head_sha"], "real-head")
        self.assertTrue(facts["copilot_review_exists"])
        self.assertFalse(facts["copilot_review_needed"])


class InitialBackfillCompletionTest(unittest.TestCase):
    def test_marks_complete_only_after_all_open_prs_are_cached(self) -> None:
        state = {"initial_backfill_complete": False, "prs": {"1": {}}}

        self.assertFalse(complete_initial_backfill_if_ready(state, {1, 2}))
        self.assertFalse(state["initial_backfill_complete"])

        state["prs"]["2"] = {}
        self.assertTrue(complete_initial_backfill_if_ready(state, {1, 2}))
        self.assertTrue(state["initial_backfill_complete"])
        self.assertFalse(complete_initial_backfill_if_ready(state, {1, 2}))

    def test_empty_repository_completes_initial_backfill(self) -> None:
        state = {"prs": {}}

        self.assertTrue(complete_initial_backfill_if_ready(state, set()))
        self.assertTrue(state["initial_backfill_complete"])

    def test_writes_initial_backfill_status_to_github_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            for name, state, expected in (
                ("incomplete", None, "false"),
                ("complete", {"initial_backfill_complete": True, "prs": {}}, "true"),
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
        return_value={"prs": {"12": {}, "34": {}, "56": {}}},
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
        self.assertEqual({"56": {}}, saved_state["prs"])
        self.assertEqual(
            [call(12, None, ANY), call(34, None, ANY)],
            sorted(record_nudge.call_args_list, key=lambda value: value.args[0]),
        )

    @patch("dashboard.record_copilot_review_observation")
    @patch("dashboard.record_author_nudge_observation")
    @patch("dashboard.clear_backfill_pr_failure")
    @patch("dashboard.save_dashboard_update_state", return_value=0)
    @patch("dashboard.enqueue_status_comment_update")
    @patch("dashboard.merge_dashboard_update_with_latest_state")
    def test_targeted_state_change_enqueues_status_comment(
        self,
        merge_update: Mock,
        enqueue_update: Mock,
        _save_state: Mock,
        _clear_backfill_failure: Mock,
        record_nudge: Mock,
        record_copilot: Mock,
    ) -> None:
        accepted_result = {"route": "author"}
        calculation = DashboardUpdate(
            results={},
            dashboard_state={"prs": {"12": accepted_result}},
            trigger_pr_result={"route": "approver"},
        )
        merge_update.return_value = (calculation, False)

        status = apply_targeted_dashboard_update(
            Namespace(pr_number=12, prepare_author_nudges=True),
            calculation,
        )

        self.assertEqual(0, status)
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
    @patch("dashboard.merge_dashboard_update_with_latest_state")
    def test_unchanged_targeted_state_does_not_enqueue_status_comment(
        self,
        merge_update: Mock,
        enqueue_update: Mock,
        _save_state: Mock,
        _clear_backfill_failure: Mock,
        record_nudge: Mock,
        _record_copilot: Mock,
    ) -> None:
        accepted_result = {"route": "approver"}
        calculation = DashboardUpdate(
            results={},
            dashboard_state={"prs": {"12": accepted_result}},
            trigger_pr_result={"route": "author"},
        )
        merge_update.return_value = (calculation, True)

        status = apply_targeted_dashboard_update(Namespace(pr_number=12), calculation)

        self.assertEqual(0, status)
        enqueue_update.assert_not_called()
        record_nudge.assert_called_once_with(
            12,
            accepted_result,
            ANY,
            prepare_due=False,
        )

    @patch(
        "dashboard.load_dashboard_state_cache",
        return_value={"prs": {"34": {"route": "author"}}},
    )
    def test_untracked_closed_pr_reports_no_state_change(
        self, _load_state: Mock
    ) -> None:
        calculation = DashboardUpdate(
            results={},
            dashboard_state={"prs": {"34": {"route": "author"}}},
            trigger_pr_result=None,
            starting_pr_result=None,
            used_cached_dashboard_state=True,
        )

        _merged, dashboard_state_unchanged = merge_dashboard_update_with_latest_state(
            calculation, 12, {34}
        )

        self.assertTrue(dashboard_state_unchanged)

    @patch(
        "dashboard.load_dashboard_state_cache",
        return_value={"prs": {"12": {"route": "author"}}},
    )
    def test_tracked_closed_pr_still_reports_a_state_change(
        self, _load_state: Mock
    ) -> None:
        starting_pr_result = {"route": "author"}
        calculation = DashboardUpdate(
            results={},
            dashboard_state={"prs": {"12": starting_pr_result}},
            trigger_pr_result=None,
            starting_pr_result=starting_pr_result,
            used_cached_dashboard_state=True,
        )

        merged, dashboard_state_unchanged = merge_dashboard_update_with_latest_state(
            calculation, 12, set()
        )

        self.assertFalse(dashboard_state_unchanged)
        self.assertEqual({}, merged.dashboard_state["prs"])


class RequiredCiRoutingTest(unittest.TestCase):
    def test_non_blocking_check_failures_use_deterministic_casefold_tiebreaker(self) -> None:
        facts = compute_facts(
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
            ["CodeQL", "codeql"],
            facts["non_blocking_check_failures"],
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
                facts = compute_facts(
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

                self.assertEqual(failing, facts["ci_failing_count"])
                self.assertEqual(pending, facts["ci_pending_count"])
                self.assertEqual(
                    ["workflow-notification"],
                    facts["non_blocking_check_failures"],
                )

    def test_override_command_does_not_clear_required_check_failures(self) -> None:
        facts = compute_facts(
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

        self.assertEqual(3, facts["ci_failing_count"])
        self.assertEqual("2026-07-17T01:00:00+00:00", facts["ci_failing_since"])


class LastActivityTest(unittest.TestCase):
    def _compute_last_activity_at(self, events: list[dict[str, object]]) -> object:
        return compute_facts(
            {
                "pr": {
                    # The dashboard's own status comment already bumped updatedAt.
                    "updatedAt": "2026-07-20T09:00:00Z",
                    "createdAt": "2026-07-20T01:00:00Z",
                    "author": {"login": "author"},
                    "assignees": [],
                    "mergeStateStatus": "CLEAN",
                    "mergeable": "MERGEABLE",
                },
                "checks": [],
            },
            "author",
            events,
        )["last_activity_at"]

    def test_ignores_bot_activity(self) -> None:
        last_activity_at = self._compute_last_activity_at([
            {
                "actor_role": "author",
                "kind": "issue-comment",
                "body": "ready for another look",
                "timestamp": "2026-07-20T02:00:00Z",
            },
            {
                "actor_role": "bot",
                "kind": "issue-comment",
                "body": "Pull request dashboard status",
                "timestamp": "2026-07-20T09:00:00Z",
            },
        ])

        self.assertEqual("2026-07-20T02:00:00+00:00", last_activity_at)

    def test_uses_latest_activity_from_any_non_bot_role(self) -> None:
        last_activity_at = self._compute_last_activity_at([
            {
                "actor_role": "author",
                "kind": "issue-comment",
                "body": "ready for another look",
                "timestamp": "2026-07-20T02:00:00Z",
            },
            {
                "actor_role": "outsider",
                "kind": "issue-comment",
                "body": "hitting this too",
                "timestamp": "2026-07-20T03:00:00Z",
            },
        ])

        self.assertEqual("2026-07-20T03:00:00+00:00", last_activity_at)

    def test_falls_back_to_creation_time_without_activity(self) -> None:
        self.assertEqual("2026-07-20T01:00:00+00:00", self._compute_last_activity_at([]))

    def test_activity_predating_the_pr_is_clamped_to_creation_time(self) -> None:
        # Commits pushed before the PR was opened, and cherry-picks that keep an
        # old author date, must not report activity from before the PR existed.
        last_activity_at = self._compute_last_activity_at([
            {
                "actor_role": "author",
                "kind": "commit",
                "body": "cherry-picked from a 2024 branch",
                "timestamp": "2024-01-05T00:00:00Z",
            },
        ])

        self.assertEqual("2026-07-20T01:00:00+00:00", last_activity_at)


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
        dashboard_state = {
            "initial_backfill_complete": False,
            "prs": {},
        }
        backfill_state = {"cursor": {}}
        refreshed_pr_numbers: list[int] = []

        def load_dashboard_state() -> dict:
            return deepcopy(dashboard_state)

        def load_backfill_state() -> dict:
            return deepcopy(backfill_state)

        def save_backfill_state(state: dict) -> None:
            backfill_state.clear()
            backfill_state.update(deepcopy(state))

        def build_update(*call_args) -> DashboardUpdate:
            pr_number = call_args[5]
            starting_state = call_args[9]
            refreshed_pr_numbers.append(pr_number)
            result = {
                "pr_number": pr_number,
                "failed": pr_number == 1,
                "route": "unknown" if pr_number == 1 else "reviewer",
            }
            updated_state = deepcopy(starting_state)
            updated_state["prs"][str(pr_number)] = result
            return DashboardUpdate(
                results={pr_number: result},
                dashboard_state=updated_state,
                trigger_pr_result=result,
            )

        def save_dashboard_state(_args, state: dict, unchanged: bool) -> int:
            if not unchanged:
                dashboard_state.clear()
                dashboard_state.update(deepcopy(state))
            return 0

        def push_state_changes(_state_dir, _message, update_state, **_kwargs) -> int:
            return update_state()

        with (
            patch("dashboard.list_open_prs", return_value=[{"number": 1}, {"number": 2}]),
            patch("dashboard.prune_classification_cache"),
            patch("dashboard.load_reviewer_set", return_value={"reviewer"}),
            patch("dashboard.load_dashboard_state_cache", side_effect=load_dashboard_state),
            patch("dashboard.load_backfill_state", side_effect=load_backfill_state),
            patch("dashboard.save_backfill_state", side_effect=save_backfill_state),
            patch("dashboard.build_dashboard_update_for_pr", side_effect=build_update),
            patch(
                "dashboard.merge_dashboard_update_with_latest_state",
                side_effect=lambda calculation, *_args: (calculation, False),
            ),
            patch(
                "dashboard.reject_failed_dashboard_result",
                side_effect=lambda result: result["failed"],
            ),
            patch("dashboard.save_dashboard_update_state", side_effect=save_dashboard_state),
            patch("dashboard.record_author_nudge_observation") as record_nudge,
            patch("dashboard.state_branch.configure_git"),
            patch("dashboard.state_branch.checkout_state"),
            patch("dashboard.state_branch.remove_existing_state_dir"),
            patch("dashboard.state_branch.push_state_changes", side_effect=push_state_changes),
        ):
            status = update_dashboard_for_backfill(args, Path("state"))

        self.assertEqual(refreshed_pr_numbers, [1, 2])
        record_nudge.assert_called_once_with(2, ANY, ANY, prepare_due=False)
        self.assertEqual(status, BACKFILL_RECORDED_FAILURE_STATUS)
        self.assertEqual(dashboard_state["prs"], {"2": {"pr_number": 2, "failed": False, "route": "reviewer"}})
        self.assertTrue(dashboard_state["initial_backfill_complete"])
        self.assertEqual(backfill_state["cursor"], {"last_pr_number": 2})
        self.assertEqual(backfill_failed_pr_numbers(backfill_state), {1})

    def test_successful_retry_clears_recorded_failure(self) -> None:
        state = {"failed_pr_numbers": [1, 2]}

        self.assertEqual(set_backfill_pr_failed(state, 1, False), {2})
        self.assertEqual(state["failed_pr_numbers"], [2])

    def test_successful_targeted_update_clears_recorded_failure(self) -> None:
        args = Namespace(pr_number=1)
        calculation = DashboardUpdate(
            results={},
            dashboard_state={"prs": {"1": {"pr_number": 1}}},
            trigger_pr_result={"pr_number": 1, "failed": False},
        )
        backfill_state = {
            "cursor": {"last_pr_number": 7},
            "failed_pr_numbers": [1, 2],
        }
        saved_backfill_state: dict = {}

        with (
            patch(
                "dashboard.merge_dashboard_update_with_latest_state",
                return_value=(calculation, False),
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
        save_dashboard.assert_called_once_with(args, calculation.dashboard_state, False)

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