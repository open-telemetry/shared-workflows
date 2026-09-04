from __future__ import annotations

from dataclasses import FrozenInstanceError
import unittest
from unittest.mock import patch

from github_cli import TransientGhError
from pull_request_source import (
    Actor,
    IssueComment,
    PullRequestMetadata,
    fetch_pull_request_source,
    normalize_actor,
    normalize_issue_comments,
    normalize_pull_request_source,
)


class PullRequestSourceNormalizationTest(unittest.TestCase):
    def test_issue_comment_uses_updated_at_only_as_a_missing_source_fallback(
        self,
    ) -> None:
        self.assertEqual(
            "2026-08-20T04:00:00Z",
            IssueComment(
                updated_at="2026-08-20T04:00:00Z"
            ).effective_content_timestamp,
        )

    def test_non_numeric_issue_comment_database_id_normalizes_to_zero(self) -> None:
        comments = normalize_issue_comments([
            {"databaseId": "not-a-number", "body": "Comment"}
        ])

        self.assertEqual(0, comments[0].database_id)

    def test_normalizes_mixed_gh_rest_and_graphql_shapes(self) -> None:
        source = normalize_pull_request_source(
            {
                "pr": {
                    "id": 123,
                    "node_id": "PR_7",
                    "number": 7,
                    "state": "open",
                    "draft": False,
                    "html_url": "https://example.test/pull/7",
                    "url": "https://api.example.test/pulls/7",
                    "user": {"login": "author"},
                    "head": {"sha": "head-sha", "ref": "feature"},
                    "base": {"ref": "main"},
                    "mergeable": "MERGEABLE",
                    "merge_state_status": "CLEAN",
                    "created_at": "2026-08-20T01:00:00Z",
                },
                "commits": [
                    {
                        "sha": "head-sha",
                        "author": {"login": "author"},
                        "committer": {"login": "committer"},
                        "commit": {
                            "author": {
                                "name": "Author",
                                "date": "2026-08-20T01:00:00Z",
                            },
                            "committer": {
                                "date": "2026-08-20T02:00:00Z",
                            },
                            "message": "Update",
                        },
                        "parents": [{"sha": "parent"}],
                    }
                ],
                "issue_comments": [
                    {
                        "id": "IC_node",
                        "fullDatabaseId": "11",
                        "url": "https://example.test/comment/11",
                        "body": "Automated",
                        "author": {
                            "__typename": "Bot",
                            "login": "status",
                        },
                        "createdAt": "2026-08-20T03:00:00Z",
                        "lastEditedAt": "2026-08-20T04:00:00Z",
                    }
                ],
                "review_comments": [
                    {
                        "id": 12,
                        "html_url": "https://example.test/comment/12",
                        "user": {"login": "reviewer"},
                        "created_at": "2026-08-20T05:00:00Z",
                        "updated_at": "2026-08-20T06:00:00Z",
                        "path": "src/example.py",
                    }
                ],
                "reviews": [
                    {
                        "id": "PRR_node",
                        "fullDatabaseId": "13",
                        "commit": {"oid": "head-sha"},
                        "comments": {"totalCount": 2},
                        "author": {"login": "reviewer"},
                        "state": "approved",
                        "submittedAt": "2026-08-20T07:00:00Z",
                        "lastEditedAt": "2026-08-20T07:30:00Z",
                    }
                ],
                "review_requests": [
                    {
                        "requestedReviewer": {
                            "__typename": "Team",
                            "slug": "maintainers",
                        }
                    }
                ],
                "review_threads": [
                    {
                        "id": "PRRT_1",
                        "isResolved": False,
                        "isOutdated": False,
                        "path": "src/example.py",
                        "line": 9,
                        "comments": {
                            "nodes": [
                                {
                                    "id": "PRRC_1",
                                    "url": "https://example.test/thread/1",
                                    "body": "Please update this.",
                                    "createdAt": "2026-08-20T08:00:00Z",
                                    "lastEditedAt": "2026-08-20T08:30:00Z",
                                    "author": {"login": "reviewer"},
                                    "reactionGroups": [
                                        {
                                            "content": "THUMBS_UP",
                                            "users": {
                                                "nodes": [{"login": "author"}]
                                            },
                                        }
                                    ],
                                }
                            ]
                        },
                    }
                ],
                "checks": [
                    {
                        "name": "build",
                        "state": "SUCCESS",
                        "bucket": "pass",
                        "integration_id": 1,
                    },
                    {
                        "name": "workflow approval",
                        "state": "ACTION_REQUIRED",
                        "bucket": "maintainer_action_required",
                        "integration_id": 2,
                    },
                ],
                "non_blocking_check_failures": [
                    {
                        "name": "optional",
                        "bucket": "fail",
                        "completed_at": "2026-08-20T09:00:00Z",
                    }
                ],
            }
        )

        self.assertEqual(
            PullRequestMetadata(
                number=7,
                node_id="PR_7",
                state="OPEN",
                url="https://example.test/pull/7",
                author=Actor("author"),
                mergeable="MERGEABLE",
                merge_state_status="CLEAN",
                created_at="2026-08-20T01:00:00Z",
                head_sha="head-sha",
                head_branch="feature",
                base_branch="main",
            ),
            source.pull_request,
        )
        self.assertEqual("committer", source.commits[0].committer.login)
        self.assertEqual(11, source.issue_comments[0].database_id)
        self.assertEqual("status[bot]", source.issue_comments[0].actor.login)
        self.assertEqual("reviewer", source.review_comments[0].actor.login)
        self.assertEqual("head-sha", source.reviews[0].commit_id)
        self.assertEqual(
            "2026-08-20T07:30:00Z",
            source.reviews[0].content_updated_at,
        )
        self.assertEqual(13, source.reviews[0].database_id)
        self.assertEqual("maintainers", source.review_requests[0].login)
        self.assertEqual(
            "2026-08-20T08:30:00Z",
            source.review_threads[0].comments[0].updated_at,
        )
        self.assertEqual(
            ("author",),
            source.review_threads[0]
            .comments[0]
            .reaction_groups[0]
            .user_logins,
        )
        self.assertEqual("pass", source.checks[0].bucket)
        self.assertEqual(
            "maintainer_action_required",
            source.checks[1].bucket,
        )
        self.assertEqual("optional", source.non_blocking_failures[0].name)

    def test_normalizes_bot_and_human_actor_cases(self) -> None:
        self.assertEqual(
            Actor("copilot", "Bot").reviewer_login,
            "copilot-pull-request-reviewer[bot]",
        )
        copilot = normalize_actor({"__typename": "Bot", "login": "copilot"})
        self.assertTrue(copilot.is_bot)
        self.assertTrue(copilot.is_copilot_reviewer)
        self.assertEqual(
            "renovate[bot]",
            normalize_actor(
                {"__typename": "Bot", "login": "renovate"}
            ).login,
        )
        self.assertFalse(normalize_actor({"login": "alice"}).is_bot)

    def test_normalizes_rest_mergeability(self) -> None:
        source = normalize_pull_request_source({
            "pr": {
                "number": 7,
                "mergeable": True,
                "mergeable_state": "dirty",
            },
        })

        self.assertEqual("MERGEABLE", source.pull_request.mergeable)
        self.assertEqual("DIRTY", source.pull_request.merge_state_status)
        self.assertEqual("yes", source.pull_request.conflicts)

    def test_source_and_nested_records_are_immutable(self) -> None:
        source = normalize_pull_request_source({
            "pr": {"number": 7, "state": "OPEN"},
            "issue_comments": [{"id": 1, "body": "hello"}],
            "checks": [],
        })

        with self.assertRaises(FrozenInstanceError):
            source.pull_request.state = "CLOSED"  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            source.issue_comments[0].body = "changed"  # type: ignore[misc]
        with self.assertRaises(TypeError):
            source.fingerprint.issue_comments[0]["body"] = "changed"

    def test_fingerprint_projection_matches_legacy_inputs(self) -> None:
        raw = {
            "pr": {
                "baseRefName": "main",
                "title": "Title",
                "body": "Line one\r\nLine two",
                "mergeable": "CONFLICTING",
                "mergeStateStatus": "DIRTY",
            },
            "checks": [{"name": "build", "bucket": "pending"}],
            "issue_comments": [
                {
                    "id": 1,
                    "user": {
                        "login": "opentelemetry-pr-dashboard[bot]",
                    },
                    "body": "ignored",
                },
                {"id": 2, "user": {"login": "alice"}, "body": "current"},
            ],
            "review_comments": [{"id": 3, "body": "inline"}],
            "review_requests": [{"__typename": "User", "login": "bob"}],
            "reviews": [{"id": 4, "state": "COMMENTED"}],
            "review_threads": [{"isResolved": False}],
        }
        source = normalize_pull_request_source(raw)

        self.assertEqual(
            {
                "base_branch": "main",
                "checks": [{"name": "build", "bucket": "pending"}],
                "conflicts": "yes",
                "issue_comments": [
                    {"id": 2, "user": {"login": "alice"}, "body": "current"}
                ],
                "pr_text": {
                    "body": "Line one\nLine two",
                    "title": "Title",
                },
                "review_comments": [{"id": 3, "body": "inline"}],
                "review_requests": [
                    {"__typename": "User", "login": "bob"}
                ],
                "reviews": [{"id": 4, "state": "COMMENTED"}],
                "review_threads": [{"isResolved": False}],
            },
            source.fingerprint.routing_inputs(),
        )


class PullRequestSourceFetchTest(unittest.TestCase):
    @patch("pull_request_source.gh_branch_rules", return_value=[])
    @patch(
        "pull_request_source.gh_pr_check_rollup",
        return_value={
            "head_oid": "head-sha",
            "required": [{"name": "build", "bucket": "pass"}],
            "non_blocking_failures": [{"name": "optional", "bucket": "fail"}],
            "code_scanning": [],
            "pending": [],
        },
    )
    @patch("pull_request_source.fetch_review_threads", return_value=[])
    @patch("pull_request_source.fetch_review_requests", return_value=[])
    @patch("pull_request_source.fetch_pr_reviews", return_value=[])
    @patch("pull_request_source.fetch_pr_issue_comments", return_value=[])
    @patch("pull_request_source.gh_api")
    @patch(
        "pull_request_source.gh_pr_view",
        return_value={
            "id": "PR_7",
            "number": 7,
            "state": "OPEN",
            "baseRefName": "main",
            "headRefOid": "head-sha",
        },
    )
    def test_fetches_and_normalizes_one_aggregate(
        self,
        _pr_view,
        gh_api,
        _issue_comments,
        _reviews,
        _review_requests,
        _review_threads,
        _rollup,
        _branch_rules,
    ) -> None:
        gh_api.side_effect = lambda path, _paginate: (
            [{"sha": "head-sha"}]
            if path.endswith("/commits?per_page=100")
            else [{"id": 12, "user": {"login": "reviewer"}}]
        )

        source = fetch_pull_request_source(
            "owner/repo",
            "owner",
            "repo",
            7,
            ("optional-*",),
        )

        self.assertEqual("head-sha", source.commits[0].sha)
        self.assertEqual(12, source.review_comments[0].database_id)
        self.assertEqual("build", source.checks[0].name)
        self.assertEqual("optional", source.non_blocking_failures[0].name)
        self.assertEqual(
            {
                "/repos/owner/repo/pulls/7/comments?per_page=100",
                "/repos/owner/repo/pulls/7/commits?per_page=100",
            },
            {call.args[0] for call in gh_api.call_args_list},
        )

    @patch("pull_request_source.ThreadPoolExecutor")
    @patch(
        "pull_request_source.gh_pr_view",
        return_value={
            "id": "PR_7",
            "number": 7,
            "state": "OPEN",
            "isDraft": True,
            "title": "Draft",
            "url": "https://example.test/pull/7",
        },
    )
    def test_draft_skips_full_evaluation_fetches(
        self,
        _pr_view,
        thread_pool,
    ) -> None:
        source = fetch_pull_request_source("owner/repo", "owner", "repo", 7)

        self.assertTrue(source.pull_request.is_draft)
        self.assertEqual("Draft", source.pull_request.title)
        thread_pool.assert_not_called()

    @patch("pull_request_source.fetch_pr_issue_comments", return_value=[])
    @patch("pull_request_source.fetch_pr_reviews", return_value=[])
    @patch("pull_request_source.fetch_review_requests", return_value=[])
    @patch("pull_request_source.fetch_review_threads", return_value=[])
    @patch("pull_request_source.gh_branch_rules", return_value=[])
    @patch("pull_request_source.gh_pr_check_rollup", return_value=None)
    @patch("pull_request_source.gh_api", side_effect=TransientGhError("temporary"))
    @patch(
        "pull_request_source.gh_pr_view",
        return_value={"id": "PR_7", "state": "OPEN", "baseRefName": "main"},
    )
    def test_propagates_transient_transport_failures(
        self,
        _pr_view,
        _gh_api,
        _rollup,
        _branch_rules,
        _review_threads,
        _review_requests,
        _reviews,
        _issue_comments,
    ) -> None:
        with self.assertRaises(TransientGhError):
            fetch_pull_request_source("owner/repo", "owner", "repo", 7)


if __name__ == "__main__":
    unittest.main()
