from __future__ import annotations

import unittest
from unittest.mock import patch

from github_cli import fetch_pr_review_data, fetch_pr_title_edits


class GithubCliTest(unittest.TestCase):
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
                        "user": {"login": "reviewer-1"},
                        "state": "COMMENTED",
                        "body": "Please clarify this.",
                        "submitted_at": "2026-07-15T03:55:00Z",
                        "updated_at": "2026-07-15T03:57:33Z",
                    },
                    {
                        "id": 5000000000,
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