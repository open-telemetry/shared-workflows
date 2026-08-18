from __future__ import annotations

import unittest
from unittest.mock import ANY, patch

from github_cli import (
    TransientGhError,
    code_scanning_tools,
    fetch_head_push_at,
    fetch_pr_issue_comments,
    fetch_pr_reviews,
    fetch_pr_routing_raw,
    fetch_review_requests,
    gh_branch_rules,
    gh_pr_check_rollup,
    gh_pr_checks,
    gh_pr_view,
    include_missing_required_checks,
    is_retryable_gh_error,
    list_open_prs,
    merge_code_scanning_checks,
    request_copilot_review,
    required_check_contexts,
    required_code_scanning_checks,
    settled_check_suite_app_ids,
)


class HeadPushActivityTest(unittest.TestCase):
    @patch("github_cli.gh_api")
    def test_returns_push_time_for_exact_head(self, gh_api) -> None:
        gh_api.return_value = [
            {
                "after": "new-head",
                "timestamp": "2026-08-11T13:00:00Z",
                "activity_type": "push",
            },
            {
                "after": "old-head",
                "timestamp": "2026-08-11T12:00:00Z",
                "activity_type": "push",
            },
        ]

        pushed_at = fetch_head_push_at("owner/repo", "feature/branch", "new-head")

        self.assertEqual("2026-08-11T13:00:00Z", pushed_at)
        gh_api.assert_called_once_with(
            "repos/owner/repo/activity?ref=feature%2Fbranch"
            "&activity_type=push&per_page=100"
        )


def _review_requests_page(nodes, has_next=False, cursor=""):
    return {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewRequests": {
                        "pageInfo": {
                            "hasNextPage": has_next,
                            "endCursor": cursor,
                        },
                        "nodes": nodes,
                    }
                }
            }
        }
    }


def _rollup_page(nodes):
    return {
        "data": {
            "node": {
                "commits": {
                    "nodes": [{
                        "commit": {
                            "statusCheckRollup": {
                                "contexts": {
                                    "nodes": nodes,
                                    "pageInfo": {"hasNextPage": False},
                                },
                            },
                        },
                    }],
                },
            },
        },
    }


class FetchReviewRequestsTest(unittest.TestCase):
    def test_returns_bot_reviewers_that_gh_pr_view_omits(self) -> None:
        # `gh pr view --json reviewRequests` exports only User and Team nodes,
        # so a pending Copilot request is invisible through that adapter.
        page = _review_requests_page([
            {"requestedReviewer": {"__typename": "Team", "slug": "approvers"}},
            {"requestedReviewer": {"__typename": "User", "login": "adrielp"}},
            {
                "requestedReviewer": {
                    "__typename": "Bot",
                    "login": "copilot-pull-request-reviewer",
                }
            },
        ])

        with patch("github_cli.gh_graphql", return_value=page) as gh_graphql:
            reviewers = fetch_review_requests("open-telemetry", "example", 7)

        self.assertIn(
            {
                "__typename": "Bot",
                "login": "copilot-pull-request-reviewer",
            },
            reviewers,
        )
        # A Bot login is only returned when the query asks for it explicitly.
        self.assertIn("... on Bot", gh_graphql.call_args.args[0])

    def test_skips_reviewers_the_caller_cannot_see(self) -> None:
        page = _review_requests_page([
            {"requestedReviewer": None},
            {"requestedReviewer": {"__typename": "User", "login": "adrielp"}},
        ])

        with patch("github_cli.gh_graphql", return_value=page):
            reviewers = fetch_review_requests("open-telemetry", "example", 7)

        self.assertEqual(
            [{"__typename": "User", "login": "adrielp"}],
            reviewers,
        )

    def test_follows_pagination(self) -> None:
        pages = [
            _review_requests_page(
                [{"requestedReviewer": {"__typename": "User", "login": "first"}}],
                has_next=True,
                cursor="cursor-1",
            ),
            _review_requests_page(
                [{"requestedReviewer": {"__typename": "User", "login": "second"}}],
            ),
        ]

        with patch("github_cli.gh_graphql", side_effect=pages) as gh_graphql:
            reviewers = fetch_review_requests("open-telemetry", "example", 7)

        self.assertEqual(["first", "second"], [r["login"] for r in reviewers])
        self.assertEqual(
            "cursor-1",
            gh_graphql.call_args_list[1].args[1]["after"],
        )


class GithubCliTest(unittest.TestCase):
    @patch("github_cli.run_gh_json")
    def test_pr_view_fetches_body_for_routing_freshness(self, run_json) -> None:
        run_json.return_value = {"mergeable": "MERGEABLE"}

        gh_pr_view("open-telemetry/example", 7)

        fields = run_json.call_args.args[0][-1]
        self.assertIn("title", fields.split(","))
        self.assertIn("body", fields.split(","))

    @patch("github_cli.gh_graphql")
    def test_request_copilot_review_uses_request_reviews_mutation(
        self,
        graphql,
    ) -> None:
        request_copilot_review("PR_node_id")

        graphql.assert_called_once_with(
            ANY,
            {
                "pullRequestId": "PR_node_id",
                "botId": "BOT_kgDOCnlnWA",
            },
        )
        mutation = graphql.call_args.args[0]
        self.assertIn("requestReviews", mutation)
        self.assertIn("botIds: [$botId]", mutation)
        self.assertIn("union: true", mutation)

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

    def test_tls_certificate_error_is_retryable(self) -> None:
        self.assertTrue(
            is_retryable_gh_error(
                'tls: failed to verify certificate: x509: certificate is not '
                'valid for any names, but wanted to match api.github.com'
            )
        )

    def test_permanent_tls_certificate_errors_are_not_retryable(self) -> None:
        errors = (
            'tls: failed to verify certificate: x509: certificate has expired '
            'or is not yet valid',
            'tls: failed to verify certificate: x509: certificate signed by '
            'unknown authority',
        )

        for error in errors:
            with self.subTest(error=error):
                self.assertFalse(is_retryable_gh_error(error))

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
                                                "url": "https://github.com/open-telemetry/example/runs/87974237001",
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
    def test_check_rollup_rejects_pages_from_different_commits(self, graphql) -> None:
        def page(oid: str, name: str, has_next: bool) -> dict:
            return {
                "data": {
                    "node": {
                        "commits": {
                            "nodes": [{
                                "commit": {
                                    "oid": oid,
                                    "statusCheckRollup": {
                                        "contexts": {
                                            "nodes": [{
                                                "__typename": "CheckRun",
                                                "name": name,
                                                "status": "COMPLETED",
                                                "conclusion": "SUCCESS",
                                                "url": "https://github.com/open-telemetry/example/runs/1",
                                                "isRequired": True,
                                            }],
                                            "pageInfo": {
                                                "hasNextPage": has_next,
                                                "endCursor": "cursor-1",
                                            },
                                        },
                                    },
                                },
                            }],
                        },
                    },
                },
            }

        graphql.side_effect = [
            page("stale-head", "build", True),
            page("current-head", "test", False),
        ]

        rollup = gh_pr_check_rollup("open-telemetry/example", "PR_id", [])

        self.assertIsNone(rollup)

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
            required_check_contexts(
                gh_branch_rules("open-telemetry/example", "release/1.x")
            ),
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
                        gh_branch_rules("open-telemetry/example", "main")
                    )
                else:
                    with self.assertRaises(Exception):
                        gh_branch_rules("open-telemetry/example", "main")

    def test_code_scanning_tools_deduplicate_across_rules(self) -> None:
        rules = [
            {"type": "required_status_checks", "parameters": {}},
            {
                "type": "code_scanning",
                "parameters": {
                    "code_scanning_tools": [
                        {"tool": "CodeQL"},
                        {"tool": "zizmor"},
                    ],
                },
            },
            {
                "type": "code_scanning",
                "parameters": {"code_scanning_tools": [{"tool": "CodeQL"}]},
            },
        ]

        self.assertEqual(["CodeQL", "zizmor"], code_scanning_tools(rules))
        self.assertEqual([], code_scanning_tools(None))

    def test_undetermined_code_scanning_result_is_a_failure(self) -> None:
        checks = [
            {"name": "CodeQL", "bucket": "skipping", "state": "NEUTRAL"},
            {"name": "zizmor", "bucket": "pass", "state": "SUCCESS"},
            {"name": "Dependabot", "bucket": "skipping", "state": "NEUTRAL"},
        ]

        self.assertEqual(
            [("CodeQL", "fail"), ("zizmor", "pass")],
            [
                (check["name"], check["bucket"])
                for check in required_code_scanning_checks(
                    checks,
                    ["CodeQL", "zizmor"],
                    False,
                )
            ],
        )

    def test_undetermined_code_scanning_result_is_pending_while_checks_run(
        self,
    ) -> None:
        self.assertEqual(
            [("CodeQL", "pending")],
            [
                (check["name"], check["bucket"])
                for check in required_code_scanning_checks(
                    [{"name": "CodeQL", "bucket": "skipping", "state": "NEUTRAL"}],
                    ["CodeQL"],
                    True,
                )
            ],
        )

    def test_code_scanning_checks_are_ignored_without_a_ruleset_rule(self) -> None:
        self.assertEqual(
            [],
            required_code_scanning_checks(
                [{"name": "CodeQL", "bucket": "skipping"}],
                [],
                False,
            ),
        )

    def test_code_scanning_result_replaces_its_required_context_entry(self) -> None:
        self.assertEqual(
            [("build", "pass"), ("CodeQL", "fail")],
            [
                (check["name"], check["bucket"])
                for check in merge_code_scanning_checks(
                    [
                        {"name": "build", "integration_id": 1, "bucket": "pass"},
                        {"name": "CodeQL", "integration_id": 57789, "bucket": "skipping"},
                    ],
                    [{"name": "CodeQL", "integration_id": 57789, "bucket": "fail"}],
                )
            ],
        )

    @patch("github_cli.gh_graphql")
    def test_check_rollup_keeps_the_latest_code_scanning_attempt(self, graphql) -> None:
        graphql.return_value = _rollup_page([
            {
                "__typename": "CheckRun",
                "name": "CodeQL",
                "status": "COMPLETED",
                "conclusion": "NEUTRAL",
                "url": "https://github.com/open-telemetry/example/runs/1",
                "isRequired": False,
                "checkSuite": {"app": {"databaseId": 57789}},
            },
            {
                "__typename": "CheckRun",
                "name": "CodeQL",
                "status": "COMPLETED",
                "conclusion": "SUCCESS",
                "url": "https://github.com/open-telemetry/example/runs/2",
                "isRequired": False,
                "checkSuite": {"app": {"databaseId": 57789}},
            },
        ])

        rollup = gh_pr_check_rollup("open-telemetry/example", "PR_id", [])

        self.assertIsNotNone(rollup)
        self.assertEqual(
            [("CodeQL", "pass")],
            [(check["name"], check["bucket"]) for check in rollup["code_scanning"]],
        )

    @patch("github_cli.gh_graphql")
    def test_check_rollup_reports_unfinished_optional_checks_as_pending(
        self,
        graphql,
    ) -> None:
        graphql.return_value = _rollup_page([
            {
                "__typename": "CheckRun",
                "name": "CodeQL",
                "status": "COMPLETED",
                "conclusion": "NEUTRAL",
                "url": "https://github.com/open-telemetry/example/runs/1",
                "isRequired": False,
                "checkSuite": {"app": {"databaseId": 57789}},
            },
            {
                "__typename": "CheckRun",
                "name": "Analyze (java)",
                "status": "IN_PROGRESS",
                "url": "https://github.com/open-telemetry/example/runs/2",
                "isRequired": False,
                "checkSuite": {"app": {"databaseId": 15368}},
            },
        ])

        rollup = gh_pr_check_rollup("open-telemetry/example", "PR_id", [])

        self.assertIsNotNone(rollup)
        self.assertEqual(
            ["Analyze (java)"],
            [check["name"] for check in rollup["pending"]],
        )

    @patch("github_cli.gh_graphql")
    def test_check_rollup_keeps_same_named_checks_from_separate_workflows(
        self,
        graphql,
    ) -> None:
        graphql.return_value = _rollup_page([
            {
                "__typename": "CheckRun",
                "name": "test",
                "status": "COMPLETED",
                "conclusion": "FAILURE",
                "url": "https://github.com/open-telemetry/example/runs/1",
                "isRequired": True,
                "checkSuite": {
                    "app": {"databaseId": 15368},
                    "workflowRun": {
                        "databaseId": 100,
                        "workflow": {"name": "build"},
                    },
                },
            },
            {
                "__typename": "CheckRun",
                "name": "test",
                "status": "COMPLETED",
                "conclusion": "SUCCESS",
                "url": "https://github.com/open-telemetry/example/runs/2",
                "isRequired": True,
                "checkSuite": {
                    "app": {"databaseId": 15368},
                    "workflowRun": {
                        "databaseId": 200,
                        "workflow": {"name": "native-tests"},
                    },
                },
            },
        ])

        rollup = gh_pr_check_rollup("open-telemetry/example", "PR_id", [])

        self.assertIsNotNone(rollup)
        self.assertEqual(
            [("build", "fail"), ("native-tests", "pass")],
            sorted(
                (check["workflow"], check["bucket"])
                for check in rollup["required"]
            ),
        )

    @patch("github_cli.gh_graphql")
    def test_check_rollup_keeps_same_named_checks_from_separate_runs(
        self,
        graphql,
    ) -> None:
        graphql.return_value = _rollup_page([
            {
                "__typename": "CheckRun",
                "name": "test",
                "status": "IN_PROGRESS",
                "url": "https://github.com/open-telemetry/example/runs/1",
                "isRequired": False,
                "checkSuite": {
                    "app": {"databaseId": 15368},
                    "workflowRun": {
                        "databaseId": 100,
                        "workflow": {"name": "build"},
                    },
                },
            },
            {
                "__typename": "CheckRun",
                "name": "test",
                "status": "COMPLETED",
                "conclusion": "SUCCESS",
                "url": "https://github.com/open-telemetry/example/runs/2",
                "isRequired": False,
                "checkSuite": {
                    "app": {"databaseId": 15368},
                    "workflowRun": {
                        "databaseId": 200,
                        "workflow": {"name": "build"},
                    },
                },
            },
        ])

        rollup = gh_pr_check_rollup("open-telemetry/example", "PR_id", [])

        self.assertIsNotNone(rollup)
        self.assertEqual(
            [(100, "test")],
            [
                (check["workflow_run_id"], check["name"])
                for check in rollup["pending"]
            ],
        )

    @patch("github_cli.gh_graphql")
    def test_check_rollup_collapses_rerun_attempts_of_one_run(
        self,
        graphql,
    ) -> None:
        graphql.return_value = _rollup_page([
            {
                "__typename": "CheckRun",
                "name": "test",
                "status": "IN_PROGRESS",
                "url": "https://github.com/open-telemetry/example/runs/1",
                "isRequired": False,
                "checkSuite": {
                    "app": {"databaseId": 15368},
                    "workflowRun": {
                        "databaseId": 100,
                        "workflow": {"name": "build"},
                    },
                },
            },
            {
                "__typename": "CheckRun",
                "name": "test",
                "status": "COMPLETED",
                "conclusion": "SUCCESS",
                "url": "https://github.com/open-telemetry/example/runs/2",
                "isRequired": False,
                "checkSuite": {
                    "app": {"databaseId": 15368},
                    "workflowRun": {
                        "databaseId": 100,
                        "workflow": {"name": "build"},
                    },
                },
            },
        ])

        rollup = gh_pr_check_rollup("open-telemetry/example", "PR_id", [])

        self.assertIsNotNone(rollup)
        self.assertEqual([], rollup["pending"])

    @patch("github_cli.gh_graphql")
    def test_required_check_keeps_only_the_latest_attempt_of_a_name(
        self,
        graphql,
    ) -> None:
        # A superseded run leaves a cancelled check behind that the run which
        # replaced it has already passed.
        graphql.return_value = _rollup_page([
            {
                "__typename": "CheckRun",
                "name": "changelog",
                "status": "COMPLETED",
                "conclusion": "CANCELLED",
                "url": "https://github.com/open-telemetry/example/runs/1",
                "isRequired": True,
                "checkSuite": {
                    "app": {"databaseId": 15368},
                    "workflowRun": {
                        "databaseId": 100,
                        "workflow": {"name": "Changelog"},
                    },
                },
            },
            {
                "__typename": "CheckRun",
                "name": "changelog",
                "status": "COMPLETED",
                "conclusion": "SUCCESS",
                "url": "https://github.com/open-telemetry/example/runs/2",
                "isRequired": True,
                "checkSuite": {
                    "app": {"databaseId": 15368},
                    "workflowRun": {
                        "databaseId": 200,
                        "workflow": {"name": "Changelog"},
                    },
                },
            },
        ])

        rollup = gh_pr_check_rollup("open-telemetry/example", "PR_id", [])

        self.assertIsNotNone(rollup)
        self.assertEqual(
            [("changelog", "pass")],
            [(check["name"], check["bucket"]) for check in rollup["required"]],
        )

    @patch("github_cli.gh_graphql")
    def test_required_code_scanning_context_keeps_normal_classification(
        self,
        graphql,
    ) -> None:
        graphql.return_value = _rollup_page([
            {
                "__typename": "CheckRun",
                "name": "CodeQL",
                "status": "COMPLETED",
                "conclusion": "NEUTRAL",
                "url": "https://github.com/open-telemetry/example/runs/1",
                "isRequired": True,
                "checkSuite": {"app": {"databaseId": 57789}},
            },
        ])

        rollup = gh_pr_check_rollup("open-telemetry/example", "PR_id", [])

        self.assertIsNotNone(rollup)
        self.assertEqual(["CodeQL"], [check["name"] for check in rollup["required"]])
        self.assertEqual(
            ["CodeQL"],
            [check["name"] for check in rollup["code_scanning"]],
        )

    @patch("github_cli.gh_graphql")
    def test_check_rollup_separates_code_scanning_results(self, graphql) -> None:
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
                                                "name": "CodeQL",
                                                "status": "COMPLETED",
                                                "conclusion": "NEUTRAL",
                                                "url": "https://github.com/open-telemetry/example/runs/1",
                                                "isRequired": False,
                                                "checkSuite": {"app": {"databaseId": 57789}},
                                            },
                                            {
                                                "__typename": "CheckRun",
                                                "name": "Analyze (java)",
                                                "status": "COMPLETED",
                                                "conclusion": "FAILURE",
                                                "url": "https://github.com/open-telemetry/example/runs/2",
                                                "isRequired": False,
                                                "checkSuite": {"app": {"databaseId": 15368}},
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

        rollup = gh_pr_check_rollup("open-telemetry/example", "PR_id", ["Analyze *"])

        self.assertIsNotNone(rollup)
        self.assertEqual([], rollup["required"])
        self.assertEqual(
            ["Analyze (java)"],
            [check["name"] for check in rollup["non_blocking_failures"]],
        )
        self.assertEqual(
            [("CodeQL", "skipping")],
            [(check["name"], check["bucket"]) for check in rollup["code_scanning"]],
        )

    @patch(
        "github_cli.gh_branch_rules",
        return_value=[{
            "type": "code_scanning",
            "parameters": {"code_scanning_tools": [{"tool": "CodeQL"}]},
        }],
    )
    @patch(
        "github_cli.gh_pr_check_rollup",
        return_value={
            "head_oid": "current-head",
            "required": [{"name": "build", "bucket": "pass", "integration_id": 1}],
            "non_blocking_failures": [],
            "code_scanning": [
                {"name": "CodeQL", "bucket": "skipping", "integration_id": 57789},
            ],
            "pending": [],
        },
    )
    @patch("github_cli.fetch_review_threads", return_value=[])
    @patch("github_cli.fetch_review_requests", return_value=[])
    @patch("github_cli.fetch_pr_reviews", return_value=[])
    @patch("github_cli.fetch_pr_issue_comments", return_value=[])
    @patch("github_cli.gh_api", return_value=[])
    @patch("github_cli.gh_pr_view")
    def test_routing_raw_reports_unsatisfied_code_scanning_rule(
        self,
        gh_pr_view,
        _gh_api,
        _issue_comments,
        _reviews,
        _review_requests,
        _review_threads,
        _rollup,
        _branch_rules,
    ) -> None:
        gh_pr_view.return_value = {
            "id": "PR_node",
            "baseRefName": "main",
            "headRefOid": "current-head",
        }

        raw = fetch_pr_routing_raw(
            "open-telemetry/example",
            "open-telemetry",
            "example",
            7,
        )

        self.assertEqual(
            [("build", "pass"), ("CodeQL", "fail")],
            [(check["name"], check["bucket"]) for check in raw["checks"]],
        )

    @patch("github_cli.gh_branch_rules", return_value=[])
    @patch(
        "github_cli.gh_pr_check_rollup",
        return_value={
            "head_oid": "previous-head",
            "required": [{"name": "build", "bucket": "pass", "integration_id": 1}],
            "non_blocking_failures": [],
            "code_scanning": [],
        },
    )
    @patch("github_cli.fetch_review_threads", return_value=[])
    @patch("github_cli.fetch_review_requests", return_value=[])
    @patch("github_cli.fetch_pr_reviews", return_value=[])
    @patch("github_cli.fetch_pr_issue_comments", return_value=[])
    @patch("github_cli.gh_api", return_value=[])
    @patch("github_cli.gh_pr_view")
    def test_routing_raw_discards_checks_from_a_superseded_head(
        self,
        gh_pr_view,
        _gh_api,
        _issue_comments,
        _reviews,
        _review_requests,
        _review_threads,
        _rollup,
        _branch_rules,
    ) -> None:
        gh_pr_view.return_value = {
            "id": "PR_node",
            "baseRefName": "main",
            "headRefOid": "current-head",
        }

        raw = fetch_pr_routing_raw(
            "open-telemetry/example",
            "open-telemetry",
            "example",
            7,
        )

        self.assertIsNone(raw["checks"])

    @patch("github_cli.settled_check_suite_app_ids", return_value=set())
    @patch(
        "github_cli.gh_branch_rules",
        return_value=[{
            "type": "required_status_checks",
            "parameters": {
                "required_status_checks": [{"context": "build", "integration_id": 1}],
            },
        }],
    )
    @patch(
        "github_cli.gh_pr_check_rollup",
        return_value={
            "head_oid": "current-head",
            "required": [{"name": "build", "bucket": "pass", "integration_id": 1}],
            "non_blocking_failures": [],
            "code_scanning": [],
            "pending": [],
        },
    )
    @patch("github_cli.fetch_review_threads", return_value=[])
    @patch("github_cli.fetch_review_requests", return_value=[])
    @patch("github_cli.fetch_pr_reviews", return_value=[])
    @patch("github_cli.fetch_pr_issue_comments", return_value=[])
    @patch("github_cli.gh_api", return_value=[])
    @patch("github_cli.gh_pr_view")
    def test_routing_raw_skips_check_suites_when_every_context_reported(
        self,
        gh_pr_view,
        _gh_api,
        _issue_comments,
        _reviews,
        _review_requests,
        _review_threads,
        _rollup,
        _branch_rules,
        settled_app_ids,
    ) -> None:
        gh_pr_view.return_value = {
            "id": "PR_node",
            "baseRefName": "main",
            "headRefOid": "current-head",
        }

        raw = fetch_pr_routing_raw(
            "open-telemetry/example",
            "open-telemetry",
            "example",
            7,
        )

        self.assertEqual(
            [("build", "pass")],
            [(check["name"], check["bucket"]) for check in raw["checks"]],
        )
        settled_app_ids.assert_not_called()

    @patch("github_cli.settled_check_suite_app_ids", return_value={2})
    @patch(
        "github_cli.gh_branch_rules",
        return_value=[{
            "type": "required_status_checks",
            "parameters": {
                "required_status_checks": [
                    {"context": "build", "integration_id": 1},
                    {"context": "windows", "integration_id": 2},
                ],
            },
        }],
    )
    @patch(
        "github_cli.gh_pr_check_rollup",
        return_value={
            "head_oid": "current-head",
            "required": [{"name": "build", "bucket": "pass", "integration_id": 1}],
            "non_blocking_failures": [],
            "code_scanning": [],
            "pending": [],
        },
    )
    @patch("github_cli.fetch_review_threads", return_value=[])
    @patch("github_cli.fetch_review_requests", return_value=[])
    @patch("github_cli.fetch_pr_reviews", return_value=[])
    @patch("github_cli.fetch_pr_issue_comments", return_value=[])
    @patch("github_cli.gh_api", return_value=[])
    @patch("github_cli.gh_pr_view")
    def test_routing_raw_reads_check_suites_for_an_unreported_context(
        self,
        gh_pr_view,
        _gh_api,
        _issue_comments,
        _reviews,
        _review_requests,
        _review_threads,
        _rollup,
        _branch_rules,
        settled_app_ids,
    ) -> None:
        gh_pr_view.return_value = {
            "id": "PR_node",
            "baseRefName": "main",
            "headRefOid": "current-head",
        }

        raw = fetch_pr_routing_raw(
            "open-telemetry/example",
            "open-telemetry",
            "example",
            7,
        )

        self.assertEqual(
            [("build", "pass")],
            [(check["name"], check["bucket"]) for check in raw["checks"]],
        )
        settled_app_ids.assert_called_once_with(
            "open-telemetry/example", "current-head"
        )

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

    def test_settled_app_never_reports_its_missing_required_check(self) -> None:
        self.assertEqual(
            [],
            include_missing_required_checks(
                [],
                [{"context": "windows-unittest", "integration_id": 15368}],
                {15368},
            ),
        )

    def test_unsettled_app_still_owes_its_missing_required_check(self) -> None:
        checks = include_missing_required_checks(
            [],
            [{"context": "windows-unittest", "integration_id": 15368}],
            {17893},
        )

        self.assertEqual(
            [("windows-unittest", "pending")],
            [(check["name"], check["bucket"]) for check in checks or []],
        )

    @patch("github_cli.gh_api")
    def test_settled_app_ids_require_every_suite_to_complete(self, gh_api) -> None:
        gh_api.return_value = [
            {
                "check_suites": [
                    {"status": "completed", "app": {"id": 15368}},
                    {"status": "completed", "app": {"id": 17893}},
                ],
            },
            {
                "check_suites": [
                    {"status": "in_progress", "app": {"id": 15368}},
                ],
            },
        ]

        self.assertEqual({17893}, settled_check_suite_app_ids("owner/repo", "head"))
        gh_api.assert_called_once_with(
            "/repos/owner/repo/commits/head/check-suites?per_page=100",
            paginate=True,
        )

    @patch("github_cli.gh_api")
    def test_settled_app_ids_are_empty_without_a_head_sha(self, gh_api) -> None:
        self.assertEqual(set(), settled_check_suite_app_ids("owner/repo", ""))
        gh_api.assert_not_called()

    @patch("github_cli.gh_api", side_effect=RuntimeError("boom"))
    def test_settled_app_ids_are_empty_when_suites_cannot_be_read(self, _gh_api) -> None:
        self.assertEqual(set(), settled_check_suite_app_ids("owner/repo", "head"))

    @patch("github_cli.gh_graphql")
    def test_fetch_pr_reviews_normalizes_paginated_reviews(self, graphql) -> None:
        graphql.side_effect = [
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviews": {
                                "nodes": [
                                    {
                                        "fullDatabaseId": "4700712792",
                                        "commit": {"oid": "reviewed-head-1"},
                                        "comments": {"totalCount": 1},
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
                            "reviews": {
                                "nodes": [
                                    {
                                        "fullDatabaseId": "5000000000",
                                        "commit": {"oid": "reviewed-head-2"},
                                        "comments": {"totalCount": 0},
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
            fetch_pr_reviews("open-telemetry", "shared-workflows", 78),
            [
                {
                    "id": 4700712792,
                    "commit_id": "reviewed-head-1",
                    "finding_count": 1,
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
                    "finding_count": 0,
                    "url": "https://example.test/review/5000000000",
                    "user": {"login": "reviewer-2"},
                    "state": "APPROVED",
                    "body": "Looks good.",
                    "submitted_at": "2026-07-15T04:00:00Z",
                    "updated_at": "2026-07-15T04:00:00Z",
                },
            ],
        )
        review_query = graphql.call_args_list[0].args[0]
        self.assertIn("comments {", review_query)
        self.assertIn("totalCount", review_query)
        self.assertEqual(graphql.call_args_list[1].args[1]["after"], "cursor-1")
        self.assertEqual(graphql.call_count, 2)


if __name__ == "__main__":
    unittest.main()