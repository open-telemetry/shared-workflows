from __future__ import annotations

import unittest
from unittest.mock import patch

from github_cli import (
    TransientGhError,
    fetch_pr_issue_comments,
    fetch_pr_review_data,
    fetch_pr_title_edits,
    gh_pr_check_rollup,
    gh_pr_checks,
    gh_required_check_contexts,
    include_missing_required_checks,
    is_retryable_gh_error,
    list_all_open_pr_numbers,
    list_open_prs,
    request_copilot_review,
)


class GithubCliTest(unittest.TestCase):
    @patch("github_cli.run_gh")
    def test_request_copilot_review_uses_review_requests_api(self, run_gh) -> None:
        request_copilot_review("example", 7)

        run_gh.assert_called_once_with([
            "gh",
            "api",
            "--method",
            "POST",
            "-H",
            "Accept: application/vnd.github+json",
            "/repos/open-telemetry/example/pulls/7/requested_reviewers",
            "-f",
            "reviewers[]=copilot-pull-request-reviewer[bot]",
        ])

    @patch("github_cli.gh_graphql")
    def test_fetch_pr_issue_comments_paginates(self, graphql) -> None:
        graphql.side_effect = [
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "comments": {
                                "nodes": [
                                    {
                                        "fullDatabaseId": "5000000101",
                                        "url": "https://example.test/comment/5000000101",
                                        "body": "Please update the docs.",
                                        "author": {"login": "reviewer"},
                                        "createdAt": "2026-07-14T01:00:00Z",
                                        "lastEditedAt": None,
                                        "isMinimized": False,
                                    },
                                    {
                                        "fullDatabaseId": None,
                                        "url": "https://example.test/comment/missing-id",
                                        "body": "Missing ID",
                                        "author": None,
                                        "createdAt": "2026-07-14T01:30:00Z",
                                        "lastEditedAt": None,
                                        "isMinimized": False,
                                    },
                                ],
                                "pageInfo": {
                                    "hasNextPage": True,
                                    "endCursor": "cursor-1",
                                },
                            }
                        }
                    }
                }
            },
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "comments": {
                                "nodes": [
                                    {
                                        "fullDatabaseId": "5000000102",
                                        "url": "https://example.test/comment/5000000102",
                                        "body": "I updated the docs.",
                                        "author": {
                                            "__typename": "Bot",
                                            "login": "linux-foundation-easycla",
                                        },
                                        "createdAt": "2026-07-14T02:00:00Z",
                                        "lastEditedAt": "2026-07-14T03:00:00Z",
                                        "isMinimized": True,
                                    }
                                ],
                                "pageInfo": {"hasNextPage": False},
                            }
                        }
                    }
                }
            },
        ]

        self.assertEqual(
            fetch_pr_issue_comments(
                "open-telemetry", "shared-workflows", 78
            ),
            [
                {
                    "id": 5000000101,
                    "html_url": "https://example.test/comment/5000000101",
                    "created_at": "2026-07-14T01:00:00Z",
                    "updated_at": "2026-07-14T01:00:00Z",
                    "content_updated_at": "2026-07-14T01:00:00Z",
                    "minimized": False,
                    "user": {"login": "reviewer"},
                    "body": "Please update the docs.",
                },
                {
                    "id": 5000000102,
                    "html_url": "https://example.test/comment/5000000102",
                    "created_at": "2026-07-14T02:00:00Z",
                    "updated_at": "2026-07-14T03:00:00Z",
                    "content_updated_at": "2026-07-14T03:00:00Z",
                    "minimized": True,
                    "user": {"login": "linux-foundation-easycla[bot]"},
                    "body": "I updated the docs.",
                },
            ],
        )
        self.assertIn("fullDatabaseId", graphql.call_args_list[0].args[0])
        self.assertIn("__typename", graphql.call_args_list[0].args[0])
        self.assertIn("author", graphql.call_args_list[0].args[0])
        self.assertIn("body", graphql.call_args_list[0].args[0])
        self.assertIn("isMinimized", graphql.call_args_list[0].args[0])
        self.assertEqual(graphql.call_args_list[1].args[1]["after"], "cursor-1")
        self.assertEqual(graphql.call_count, 2)

    @patch("github_cli.gh_graphql")
    def test_fetch_pr_issue_comments_rejects_missing_page_cursor(
        self, graphql
    ) -> None:
        graphql.return_value = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "comments": {
                            "nodes": [],
                            "pageInfo": {
                                "hasNextPage": True,
                                "endCursor": None,
                            },
                        }
                    }
                }
            }
        }

        with self.assertRaisesRegex(
            TransientGhError,
            "hasNextPage without endCursor",
        ):
            fetch_pr_issue_comments("open-telemetry", "shared-workflows", 78)
        self.assertEqual(graphql.call_count, 1)

    def test_graphql_internal_error_is_retryable(self) -> None:
        self.assertTrue(
            is_retryable_gh_error(
                "GraphQL: Something went wrong while executing your query"
            )
        )

    @patch("github_cli.gh_api")
    def test_list_open_prs_uses_paginated_rest_api(self, gh_api) -> None:
        gh_api.return_value = [
            {
                "number": number,
                "title": f"PR {number}",
                "user": {"login": "author"},
                "draft": number == 501,
                "updated_at": "2026-07-17T00:00:00Z",
                "html_url": f"https://example.test/pull/{number}",
                "labels": [
                    {"name": "size/L"},
                    {"name": ""},
                    {"name": "   "},
                    {"color": "ffffff"},
                    None,
                ] if number == 501 else None,
            }
            for number in range(1, 502)
        ]

        prs = list_open_prs("open-telemetry/example")

        self.assertEqual(501, len(prs))
        self.assertEqual(
            {
                "number": 501,
                "title": "PR 501",
                "author": {"login": "author"},
                "isDraft": True,
                "updatedAt": "2026-07-17T00:00:00Z",
                "url": "https://example.test/pull/501",
                "labels": ["size/L"],
            },
            prs[-1],
        )
        self.assertEqual([], prs[0]["labels"])
        gh_api.assert_called_once_with(
            "/repos/open-telemetry/example/pulls?state=open&per_page=100",
            paginate=True,
        )

    @patch("github_cli.gh_api")
    def test_list_all_open_pr_numbers_uses_paginated_rest_api(self, gh_api) -> None:
        gh_api.return_value = [
            {"number": number}
            for number in range(1, 502)
        ]

        self.assertEqual(
            set(range(1, 502)),
            list_all_open_pr_numbers("open-telemetry/example"),
        )
        gh_api.assert_called_once_with(
            "/repos/open-telemetry/example/pulls?state=open&per_page=100",
            paginate=True,
        )

    @patch("github_cli.gh_graphql")
    def test_gh_pr_checks_preserves_reporting_app_identity(self, graphql) -> None:
        graphql.return_value = {
            "data": {
                "node": {
                    "commits": {
                        "nodes": [{
                            "commit": {
                                "statusCheckRollup": {
                                    "contexts": {
                                        "nodes": [
                                            {
                                                "__typename": "CheckRun",
                                                "name": "build",
                                                "status": "COMPLETED",
                                                "conclusion": "FAILURE",
                                                "startedAt": "2026-07-17T00:30:00Z",
                                                "completedAt": "2026-07-17T01:00:00Z",
                                                "url": "https://github.com/open-telemetry/example/runs/87974236999",
                                                "isRequired": True,
                                                "checkSuite": {"app": {"databaseId": 1}},
                                            },
                                            {
                                                "__typename": "CheckRun",
                                                "name": "build",
                                                "status": "COMPLETED",
                                                "conclusion": "STARTUP_FAILURE",
                                                "startedAt": "2026-07-17T01:30:00Z",
                                                "completedAt": "2026-07-17T02:00:00Z",
                                                "url": "https://github.com/open-telemetry/example/runs/87974237827",
                                                "isRequired": True,
                                                "checkSuite": {"app": {"databaseId": 2}},
                                            },
                                            {
                                                "__typename": "CheckRun",
                                                "name": "build",
                                                "status": "QUEUED",
                                                "conclusion": None,
                                                "startedAt": None,
                                                "completedAt": None,
                                                "url": "https://github.com/open-telemetry/example/runs/87974237000",
                                                "isRequired": True,
                                                "checkSuite": {"app": {"databaseId": 1}},
                                            },
                                            {
                                                "__typename": "CheckRun",
                                                "name": "optional",
                                                "isRequired": False,
                                            },
                                        ],
                                        "pageInfo": {"hasNextPage": False},
                                    },
                                },
                            },
                        }],
                    },
                },
            },
        }

        self.assertEqual(
            [("build", 1, "pending"), ("build", 2, "fail")],
            [
                (check["name"], check["integration_id"], check["bucket"])
                for check in gh_pr_checks("open-telemetry/example", "PR_id") or []
            ],
        )

    @patch("github_cli.gh_graphql", side_effect=RuntimeError("unavailable"))
    def test_gh_pr_checks_failure_returns_unknown(self, _graphql) -> None:
        self.assertIsNone(gh_pr_checks("open-telemetry/example", "PR_id"))

    @patch("github_cli.gh_graphql")
    def test_check_rollup_separates_configured_non_blocking_failures(self, graphql) -> None:
        graphql.return_value = {
            "data": {
                "node": {
                    "commits": {
                        "nodes": [{
                            "commit": {
                                "statusCheckRollup": {
                                    "contexts": {
                                        "nodes": [
                                            {
                                                "__typename": "CheckRun",
                                                "name": "required-build",
                                                "status": "COMPLETED",
                                                "conclusion": "FAILURE",
                                                "url": "https://github.com/open-telemetry/example/runs/1",
                                                "isRequired": True,
                                            },
                                            {
                                                "__typename": "CheckRun",
                                                "name": "CodeQL / Analyze",
                                                "status": "COMPLETED",
                                                "conclusion": "FAILURE",
                                                "url": "https://github.com/open-telemetry/example/runs/2",
                                                "isRequired": False,
                                            },
                                            {
                                                "__typename": "CheckRun",
                                                "name": "CodeQL / Analyze",
                                                "status": "COMPLETED",
                                                "conclusion": "SUCCESS",
                                                "url": "https://github.com/open-telemetry/example/runs/3",
                                                "isRequired": False,
                                            },
                                            {
                                                "__typename": "CheckRun",
                                                "name": "optional-unconfigured",
                                                "status": "COMPLETED",
                                                "conclusion": "FAILURE",
                                                "url": "https://github.com/open-telemetry/example/runs/4",
                                                "isRequired": False,
                                            },
                                            {
                                                "__typename": "StatusContext",
                                                "context": "workflow-notification",
                                                "state": "ERROR",
                                                "targetUrl": "https://example.test/status/5",
                                                "isRequired": False,
                                            },
                                        ],
                                        "pageInfo": {"hasNextPage": False},
                                    },
                                },
                            },
                        }],
                    },
                },
            },
        }

        rollup = gh_pr_check_rollup(
            "open-telemetry/example",
            "PR_id",
            ["CodeQL / *", "workflow-*"],
        )

        self.assertIsNotNone(rollup)
        self.assertEqual(["required-build"], [check["name"] for check in rollup["required"]])
        self.assertEqual(
            ["workflow-notification"],
            [check["name"] for check in rollup["non_blocking_failures"]],
        )

    @patch("github_cli.gh_graphql")
    def test_check_rollup_keeps_required_and_non_blocking_attempts_separate(
        self,
        graphql,
    ) -> None:
        graphql.return_value = {
            "data": {
                "node": {
                    "commits": {
                        "nodes": [{
                            "commit": {
                                "statusCheckRollup": {
                                    "contexts": {
                                        "nodes": [
                                            {
                                                "__typename": "CheckRun",
                                                "name": "build",
                                                "status": "COMPLETED",
                                                "conclusion": "SUCCESS",
                                                "url": "https://github.com/open-telemetry/example/runs/1",
                                                "isRequired": True,
                                                "checkSuite": {"app": {"databaseId": 1}},
                                            },
                                            {
                                                "__typename": "CheckRun",
                                                "name": "build",
                                                "status": "COMPLETED",
                                                "conclusion": "FAILURE",
                                                "url": "https://github.com/open-telemetry/example/runs/2",
                                                "isRequired": False,
                                                "checkSuite": {"app": {"databaseId": 1}},
                                            },
                                        ],
                                        "pageInfo": {"hasNextPage": False},
                                    },
                                },
                            },
                        }],
                    },
                },
            },
        }

        rollup = gh_pr_check_rollup(
            "open-telemetry/example",
            "PR_id",
            ["build"],
        )

        self.assertIsNotNone(rollup)
        self.assertEqual(
            [("build", "pass")],
            [(check["name"], check["bucket"]) for check in rollup["required"]],
        )
        self.assertEqual(
            [("build", "fail")],
            [
                (check["name"], check["bucket"])
                for check in rollup["non_blocking_failures"]
            ],
        )

    @patch("github_cli.gh_api")
    def test_required_check_contexts_include_all_effective_branch_rules(self, api) -> None:
        api.return_value = [
            {
                "type": "required_status_checks",
                "parameters": {
                    "required_status_checks": [
                        {"context": "EasyCLA", "integration_id": 17893},
                        {"context": "build", "integration_id": 15368},
                    ],
                },
            },
            {
                "type": "required_status_checks",
                "parameters": {
                    "required_status_checks": [
                        {"context": "build", "integration_id": 15368},
                    ],
                },
            },
            {"type": "pull_request", "parameters": {}},
        ]

        self.assertEqual(
            [
                {"context": "EasyCLA", "integration_id": 17893},
                {"context": "build", "integration_id": 15368},
            ],
            gh_required_check_contexts("open-telemetry/example", "release/1.x"),
        )
        api.assert_called_once_with(
            "/repos/open-telemetry/example/rules/branches/release%2F1.x?per_page=100",
            paginate=True,
        )

    @patch("github_cli.gh_api")
    def test_required_check_context_failure_returns_unknown(self, api) -> None:
        for error in (
            RuntimeError("forbidden"),
            Exception("unexpected parsing failure"),
        ):
            with self.subTest(error=type(error).__name__):
                api.side_effect = error
                if isinstance(error, RuntimeError):
                    self.assertIsNone(
                        gh_required_check_contexts("open-telemetry/example", "main")
                    )
                else:
                    with self.assertRaises(Exception):
                        gh_required_check_contexts("open-telemetry/example", "main")

    def test_missing_required_checks_are_pending(self) -> None:
        self.assertEqual(
            [
                {
                    "name": "build",
                    "state": "SUCCESS",
                    "bucket": "pass",
                    "integration_id": 1,
                    "status_context": False,
                },
                {
                    "name": "build",
                    "state": "SUCCESS",
                    "bucket": "pass",
                    "integration_id": None,
                    "status_context": True,
                },
                {
                    "name": "build",
                    "state": "EXPECTED",
                    "bucket": "pending",
                    "workflow": "",
                    "description": "Required check has not reported yet.",
                    "link": "",
                    "started_at": "",
                    "completed_at": "",
                    "integration_id": 2,
                    "status_context": False,
                },
            ],
            include_missing_required_checks(
                [
                    {
                        "name": "build",
                        "state": "SUCCESS",
                        "bucket": "pass",
                        "integration_id": 1,
                        "status_context": False,
                    },
                    {
                        "name": "build",
                        "state": "SUCCESS",
                        "bucket": "pass",
                        "integration_id": None,
                        "status_context": True,
                    },
                ],
                [
                    {"context": "build", "integration_id": 1},
                    {"context": "build", "integration_id": 2},
                ],
            ),
        )

    def test_app_bound_legacy_status_is_not_duplicated_as_missing(self) -> None:
        status = {
            "name": "EasyCLA",
            "state": "SUCCESS",
            "bucket": "pass",
            "integration_id": None,
            "status_context": True,
        }

        self.assertEqual(
            [status],
            include_missing_required_checks(
                [status],
                [{"context": "EasyCLA", "integration_id": 17893}],
            ),
        )

    def test_check_fetch_failure_remains_unknown(self) -> None:
        self.assertIsNone(include_missing_required_checks(
            None, [{"context": "build", "integration_id": 1}]
        ))
        self.assertIsNone(include_missing_required_checks([], None))

    @patch("github_cli.gh_graphql")
    def test_fetch_pr_review_data_normalizes_paginated_reviews_and_metadata(self, graphql) -> None:
        graphql.side_effect = [
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "lastEditedAt": "2026-07-15T03:00:00Z",
                            "editor": {"login": "author"},
                            "reviews": {
                                "nodes": [
                                    {
                                        "fullDatabaseId": "4700712792",
                                        "commit": {"oid": "reviewed-head-1"},
                                        "url": "https://example.test/review/4700712792",
                                        "author": {"login": "reviewer-1"},
                                        "state": "COMMENTED",
                                        "body": "Please clarify this.",
                                        "submittedAt": "2026-07-15T03:55:00Z",
                                        "updatedAt": "2026-07-15T03:57:33Z",
                                    }
                                ],
                                "pageInfo": {
                                    "hasNextPage": True,
                                    "endCursor": "cursor-1",
                                },
                            }
                        }
                    }
                }
            },
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "lastEditedAt": "2026-07-15T03:00:00Z",
                            "editor": {"login": "author"},
                            "reviews": {
                                "nodes": [
                                    {
                                        "fullDatabaseId": "5000000000",
                                        "commit": {"oid": "reviewed-head-2"},
                                        "url": "https://example.test/review/5000000000",
                                        "author": {"login": "reviewer-2"},
                                        "state": "APPROVED",
                                        "body": "Looks good.",
                                        "submittedAt": "2026-07-15T04:00:00Z",
                                        "updatedAt": "2026-07-15T04:00:00Z",
                                    }
                                ],
                                "pageInfo": {"hasNextPage": False},
                            }
                        }
                    }
                }
            },
        ]

        self.assertEqual(
            fetch_pr_review_data("open-telemetry", "shared-workflows", 78),
            {
                "reviews": [
                    {
                        "id": 4700712792,
                        "commit_id": "reviewed-head-1",
                        "url": "https://example.test/review/4700712792",
                        "user": {"login": "reviewer-1"},
                        "state": "COMMENTED",
                        "body": "Please clarify this.",
                        "submitted_at": "2026-07-15T03:55:00Z",
                        "updated_at": "2026-07-15T03:57:33Z",
                    },
                    {
                        "id": 5000000000,
                        "commit_id": "reviewed-head-2",
                        "url": "https://example.test/review/5000000000",
                        "user": {"login": "reviewer-2"},
                        "state": "APPROVED",
                        "body": "Looks good.",
                        "submitted_at": "2026-07-15T04:00:00Z",
                        "updated_at": "2026-07-15T04:00:00Z",
                    },
                ],
                "pr_metadata": {
                    "lastEditedAt": "2026-07-15T03:00:00Z",
                    "editor": {"login": "author"},
                },
            },
        )
        self.assertEqual(graphql.call_args_list[1].args[1]["after"], "cursor-1")
        self.assertEqual(graphql.call_count, 2)

    @patch("github_cli.gh_graphql")
    def test_fetch_pr_title_edits_paginates_title_events(self, graphql) -> None:
        graphql.side_effect = [
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "timelineItems": {
                                "nodes": [
                                    {
                                        "actor": {"login": "author"},
                                        "createdAt": "2026-07-15T04:30:00Z",
                                    }
                                ],
                                "pageInfo": {
                                    "hasNextPage": True,
                                    "endCursor": "title-cursor-1",
                                },
                            }
                        }
                    }
                }
            },
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "timelineItems": {
                                "nodes": [
                                    {
                                        "actor": {"login": "maintainer"},
                                        "createdAt": "2026-07-15T05:00:00Z",
                                    }
                                ],
                                "pageInfo": {"hasNextPage": False},
                            }
                        }
                    }
                }
            },
        ]

        self.assertEqual(
            fetch_pr_title_edits("open-telemetry", "shared-workflows", 78),
            [
                {
                    "actor": {"login": "author"},
                    "createdAt": "2026-07-15T04:30:00Z",
                },
                {
                    "actor": {"login": "maintainer"},
                    "createdAt": "2026-07-15T05:00:00Z",
                },
            ],
        )
        self.assertIsNone(graphql.call_args_list[0].args[1]["after"])
        self.assertEqual(graphql.call_args_list[1].args[1]["after"], "title-cursor-1")


if __name__ == "__main__":
    unittest.main()