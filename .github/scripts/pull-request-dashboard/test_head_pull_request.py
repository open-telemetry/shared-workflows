from __future__ import annotations

import unittest
from unittest.mock import patch

from head_pull_request import resolve_head_pull_request, search_open_head_pull_request


class HeadPullRequestTest(unittest.TestCase):
    @patch("head_pull_request.gh_graphql")
    @patch("head_pull_request.gh_api")
    def test_base_repository_commit_association(self, gh_api, gh_graphql) -> None:
        gh_api.return_value = [
            {"number": 8, "state": "closed", "head": {"sha": "head"}},
            {"number": 7, "state": "open", "head": {"sha": "head"}},
        ]

        self.assertEqual(resolve_head_pull_request("owner/repo", "head"), 7)
        gh_graphql.assert_not_called()

    @patch("head_pull_request.gh_graphql")
    @patch("head_pull_request.gh_api", return_value=[])
    def test_fork_commit_found_by_search_after_empty_association(
        self, gh_api, gh_graphql
    ) -> None:
        gh_graphql.return_value = {
            "data": {
                "search": {
                    "nodes": [
                        {"number": 1, "headRefOid": "previous-head", "state": "OPEN"},
                        {"number": 20254, "headRefOid": "head", "state": "OPEN"},
                    ],
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                }
            }
        }

        self.assertEqual(
            resolve_head_pull_request(
                "open-telemetry/example", "head", token="app-token"
            ),
            20254,
        )
        gh_api.assert_called_once_with(
            "repos/open-telemetry/example/commits/head/pulls", token="app-token"
        )
        self.assertEqual(
            gh_graphql.call_args.args[1]["searchQuery"],
            "repo:open-telemetry/example is:pr is:open head",
        )
        self.assertEqual(gh_graphql.call_args.kwargs, {"token": "app-token"})

    @patch("head_pull_request.gh_graphql")
    def test_search_paginates_and_rejects_stale_heads(self, gh_graphql) -> None:
        gh_graphql.side_effect = [
            {
                "data": {
                    "search": {
                        "nodes": [{"number": 3, "headRefOid": "old-head", "state": "OPEN"}],
                        "pageInfo": {"hasNextPage": True, "endCursor": "next"},
                    }
                }
            },
            {
                "data": {
                    "search": {
                        "nodes": [
                            {"number": 2, "headRefOid": "head", "state": "CLOSED"},
                            {"number": 4, "headRefOid": "head", "state": "OPEN"},
                        ],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                }
            },
        ]

        self.assertEqual(search_open_head_pull_request("owner/repo", "head"), 4)
        self.assertEqual(gh_graphql.call_args.args[1]["after"], "next")

    @patch("head_pull_request.gh_graphql")
    @patch("head_pull_request.gh_api", return_value=[])
    def test_no_open_matching_head(self, _gh_api, gh_graphql) -> None:
        gh_graphql.return_value = {
            "data": {
                "search": {
                    "nodes": [{"number": 3, "headRefOid": "old-head"}],
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                }
            }
        }

        self.assertIsNone(resolve_head_pull_request("owner/repo", "head"))


if __name__ == "__main__":
    unittest.main()
