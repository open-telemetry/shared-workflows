from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import check_rollup_poll


def rollup_node(number: int, head_sha: str, state: str | None, contexts: int = 3) -> dict[str, object]:
    rollup = None if state is None else {"state": state, "contexts": {"totalCount": contexts}}
    return {
        "number": number,
        "headRefOid": head_sha,
        "commits": {"nodes": [{"commit": {"statusCheckRollup": rollup}}]},
    }


class RollupSignatureTest(unittest.TestCase):
    def test_combines_head_state_and_context_count(self) -> None:
        self.assertEqual(
            "abc:SUCCESS:3",
            check_rollup_poll.rollup_signature(rollup_node(1, "abc", "SUCCESS")),
        )

    def test_separates_an_added_check(self) -> None:
        self.assertNotEqual(
            check_rollup_poll.rollup_signature(rollup_node(1, "abc", "FAILURE", contexts=3)),
            check_rollup_poll.rollup_signature(rollup_node(1, "abc", "FAILURE", contexts=4)),
        )

    def test_reports_a_pull_request_with_no_checks(self) -> None:
        self.assertEqual(
            "abc:NONE:0",
            check_rollup_poll.rollup_signature(rollup_node(1, "abc", None)),
        )

    def test_tolerates_a_missing_commit(self) -> None:
        self.assertEqual(
            "abc:NONE:0",
            check_rollup_poll.rollup_signature({"number": 1, "headRefOid": "abc", "commits": {"nodes": []}}),
        )


class ChangedPullRequestsTest(unittest.TestCase):
    def test_reports_a_new_pull_request(self) -> None:
        self.assertEqual(
            [2],
            check_rollup_poll.changed_pull_requests({1: "abc:SUCCESS"}, {1: "abc:SUCCESS", 2: "def:PENDING"}, 25),
        )

    def test_reports_a_new_head_commit(self) -> None:
        self.assertEqual(
            [1],
            check_rollup_poll.changed_pull_requests({1: "abc:SUCCESS"}, {1: "def:SUCCESS"}, 25),
        )

    def test_reports_a_new_rollup_state(self) -> None:
        self.assertEqual(
            [1],
            check_rollup_poll.changed_pull_requests({1: "abc:PENDING"}, {1: "abc:SUCCESS"}, 25),
        )

    def test_ignores_an_unchanged_pull_request(self) -> None:
        self.assertEqual(
            [],
            check_rollup_poll.changed_pull_requests({1: "abc:SUCCESS"}, {1: "abc:SUCCESS"}, 25),
        )

    def test_ignores_a_closed_pull_request(self) -> None:
        self.assertEqual(
            [],
            check_rollup_poll.changed_pull_requests({1: "abc:SUCCESS", 2: "def:SUCCESS"}, {1: "abc:SUCCESS"}, 25),
        )

    def test_caps_a_burst_at_the_newest_pull_requests(self) -> None:
        current = {number: f"sha{number}:PENDING" for number in range(1, 6)}
        self.assertEqual(
            [5, 4],
            check_rollup_poll.changed_pull_requests({}, current, 2),
        )


class PollStateFileTest(unittest.TestCase):
    def test_round_trips_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / check_rollup_poll.STATE_FILENAME
            state = {"repo-a": {2: "def:SUCCESS", 1: "abc:PENDING"}}
            check_rollup_poll.save_poll_state(path, state)
            self.assertEqual(state, check_rollup_poll.load_poll_state(path))

    def test_reports_no_baseline_for_a_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / check_rollup_poll.STATE_FILENAME
            self.assertEqual({}, check_rollup_poll.load_poll_state(path))

    def test_discards_an_unreadable_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / check_rollup_poll.STATE_FILENAME
            path.write_text("{not json", encoding="utf-8")
            self.assertEqual({}, check_rollup_poll.load_poll_state(path))

    def test_discards_an_older_state_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / check_rollup_poll.STATE_FILENAME
            path.write_text(
                json.dumps({"version": check_rollup_poll.STATE_VERSION - 1, "repositories": {"repo-a": {"1": "abc:X"}}}),
                encoding="utf-8",
            )
            self.assertEqual({}, check_rollup_poll.load_poll_state(path))


class PollRepositoriesTest(unittest.TestCase):
    def test_records_a_baseline_without_dispatching(self) -> None:
        state: dict[str, dict[int, str]] = {}
        with patch.object(
            check_rollup_poll,
            "fetch_repository_signatures",
            return_value={1: "abc:PENDING"},
        ):
            changed = check_rollup_poll.poll_repositories(["repo-a"], "open-telemetry", state, 25)
        self.assertEqual([], changed)
        self.assertEqual({"repo-a": {1: "abc:PENDING"}}, state)

    def test_reports_changes_against_the_baseline(self) -> None:
        state = {"repo-a": {1: "abc:PENDING"}}
        with patch.object(
            check_rollup_poll,
            "fetch_repository_signatures",
            return_value={1: "abc:SUCCESS"},
        ):
            changed = check_rollup_poll.poll_repositories(["repo-a"], "open-telemetry", state, 25)
        self.assertEqual([("repo-a", 1, "abc:SUCCESS")], changed)
        # The caller records the new signature once the refresh is dispatched.
        self.assertEqual({"repo-a": {1: "abc:PENDING"}}, state)

    def test_keeps_a_capped_pull_request_changed(self) -> None:
        state = {"repo-a": {1: "abc:PENDING", 2: "def:PENDING"}}
        with patch.object(
            check_rollup_poll,
            "fetch_repository_signatures",
            return_value={1: "abc:SUCCESS", 2: "def:SUCCESS"},
        ):
            changed = check_rollup_poll.poll_repositories(["repo-a"], "open-telemetry", state, 1)
        self.assertEqual([("repo-a", 2, "def:SUCCESS")], changed)
        self.assertEqual({"repo-a": {1: "abc:PENDING", 2: "def:PENDING"}}, state)

    def test_retires_a_closed_pull_request(self) -> None:
        state = {"repo-a": {1: "abc:SUCCESS", 2: "def:SUCCESS"}}
        with patch.object(
            check_rollup_poll,
            "fetch_repository_signatures",
            return_value={1: "abc:SUCCESS"},
        ):
            changed = check_rollup_poll.poll_repositories(["repo-a"], "open-telemetry", state, 25)
        self.assertEqual([], changed)
        self.assertEqual({"repo-a": {1: "abc:SUCCESS"}}, state)


class FetchRepositorySignaturesTest(unittest.TestCase):
    def test_follows_pagination(self) -> None:
        pages = [
            {
                "data": {
                    "repository": {
                        "pullRequests": {
                            "pageInfo": {"hasNextPage": True, "endCursor": "cursor"},
                            "nodes": [rollup_node(1, "abc", "SUCCESS")],
                        }
                    }
                }
            },
            {
                "data": {
                    "repository": {
                        "pullRequests": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": [rollup_node(2, "def", "PENDING")],
                        }
                    }
                }
            },
        ]
        with patch.object(check_rollup_poll, "gh_graphql", side_effect=pages) as gh_graphql:
            signatures = check_rollup_poll.fetch_repository_signatures("open-telemetry", "repo-a")
        self.assertEqual({1: "abc:SUCCESS:3", 2: "def:PENDING:3"}, signatures)
        self.assertEqual("cursor", gh_graphql.call_args_list[1].args[1]["after"])


if __name__ == "__main__":
    unittest.main()
