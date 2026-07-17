from __future__ import annotations

from argparse import Namespace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from classification import discussion_prompt_input
from dashboard import (
    DashboardUpdate,
    add_wait_age_facts,
    apply_targeted_dashboard_update,
    author_action_discussion_urls,
    complete_initial_backfill_if_ready,
    compute_facts,
    group_review_threads,
    remove_cached_dashboard_prs,
    route_pr,
    write_initial_backfill_output,
)


class ReviewThreadDiscussionUrlTest(unittest.TestCase):
    def test_group_review_threads_ignores_author_only_annotations(self) -> None:
        thread = {
            "id": "thread-1",
            "isResolved": False,
            "isOutdated": False,
            "comments": {
                "nodes": [
                    {
                        "url": "https://example.test/discussion/1",
                        "body": "todo: automate this later",
                        "createdAt": "2026-07-14T01:00:00Z",
                        "author": {"login": "author"},
                    },
                ],
            },
        }

        self.assertEqual(
            group_review_threads(
                {"review_threads": [thread]},
                "author",
                {"reviewer"},
                {"conflicts": "no"},
            ),
            [],
        )

        thread["comments"]["nodes"].append({
            "url": "https://example.test/discussion/2",
            "body": "Please handle this in the current PR.",
            "createdAt": "2026-07-14T02:00:00Z",
            "author": {"login": "reviewer"},
        })

        self.assertEqual(
            len(group_review_threads(
                {"review_threads": [thread]},
                "author",
                {"reviewer"},
                {"conflicts": "no"},
            )),
            1,
        )

    def test_group_review_threads_stores_first_comment_url_on_thread(self) -> None:
        threads = group_review_threads(
            {
                "review_threads": [
                    {
                        "id": "thread-1",
                        "isResolved": False,
                        "isOutdated": False,
                        "comments": {
                            "nodes": [
                                {
                                    "url": "https://example.test/discussion/1",
                                    "body": "first",
                                    "createdAt": "2026-07-14T02:00:00Z",
                                    "author": {"login": "reviewer"},
                                },
                                {
                                    "url": "https://example.test/discussion/2",
                                    "body": "second",
                                    "createdAt": "2026-07-14T01:00:00Z",
                                    "author": {"login": "author"},
                                },
                            ],
                        },
                    },
                ],
            },
            "author",
            {"reviewer"},
            {"conflicts": "no"},
        )

        self.assertEqual("https://example.test/discussion/1", threads[0]["discussion_url"])
        self.assertEqual("second", threads[0]["comments"][0]["body"])
        self.assertNotIn("url", threads[0]["comments"][0])

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

    def test_discussion_url_is_excluded_from_classifier_input(self) -> None:
        prompt_input = discussion_prompt_input({
            "discussion_id": "thread-1",
            "discussion_kind": "review-comment-thread",
            "discussion_url": "https://example.test/discussion/1",
            "comments": [],
        })

        self.assertNotIn("discussion_url", prompt_input)


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
    @patch("dashboard.save_dashboard_update_state", return_value=0)
    @patch("dashboard.enqueue_status_comment_update")
    @patch(
        "dashboard.load_dashboard_state_cache",
        return_value={"prs": {"12": {}, "34": {}, "56": {}}},
    )
    def test_removed_dashboard_results_enqueue_status_comments(
        self,
        _load_state: object,
        enqueue_update: object,
        save_state: object,
    ) -> None:
        args = Namespace(pr_number=None)

        status = remove_cached_dashboard_prs(args, {12, 34})

        self.assertEqual(0, status)
        self.assertEqual(
            [unittest.mock.call(12), unittest.mock.call(34)],
            sorted(enqueue_update.call_args_list, key=lambda call: call.args[0]),
        )
        saved_state = save_state.call_args.args[1]
        self.assertEqual({"56": {}}, saved_state["prs"])

    @patch("dashboard.save_dashboard_update_state", return_value=0)
    @patch("dashboard.enqueue_status_comment_update")
    @patch("dashboard.merge_dashboard_update_with_latest_state")
    def test_targeted_state_change_enqueues_status_comment(
        self,
        merge_update: object,
        enqueue_update: object,
        _save_state: object,
    ) -> None:
        calculation = DashboardUpdate(results={}, dashboard_state={}, trigger_pr_result={})
        merge_update.return_value = (calculation, False)

        status = apply_targeted_dashboard_update(Namespace(pr_number=12), calculation)

        self.assertEqual(0, status)
        enqueue_update.assert_called_once_with(12)

    @patch("dashboard.save_dashboard_update_state", return_value=0)
    @patch("dashboard.enqueue_status_comment_update")
    @patch("dashboard.merge_dashboard_update_with_latest_state")
    def test_unchanged_targeted_state_does_not_enqueue_status_comment(
        self,
        merge_update: object,
        enqueue_update: object,
        _save_state: object,
    ) -> None:
        calculation = DashboardUpdate(results={}, dashboard_state={}, trigger_pr_result={})
        merge_update.return_value = (calculation, True)

        status = apply_targeted_dashboard_update(Namespace(pr_number=12), calculation)

        self.assertEqual(0, status)
        enqueue_update.assert_not_called()


class RequiredCiRoutingTest(unittest.TestCase):
    def test_required_check_buckets_control_ci_facts_and_author_routing(self) -> None:
        cases = (
            ("TIMED_OUT", "fail", 1, 0, "author"),
            ("ACTION_REQUIRED", "fail", 1, 0, "author"),
            ("STARTUP_FAILURE", "fail", 1, 0, "author"),
            ("CANCELLED", "cancel", 1, 0, "author"),
            ("IN_PROGRESS", "pending", 0, 1, "approver"),
            ("SKIPPED", "skipping", 0, 0, "approver"),
            ("SUCCESS", "pass", 0, 0, "approver"),
        )
        for state, bucket, failing, pending, route in cases:
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
                    },
                    "author",
                    [],
                )

                self.assertEqual(failing, facts["ci_failing_count"])
                self.assertEqual(pending, facts["ci_pending_count"])
                self.assertEqual(route, route_pr(facts, {}, 1))

    def test_required_ci_failure_routes_to_author_before_approval_state(self) -> None:
        facts = {
            "approval_count": 1,
            "ci_failing_count": 1,
            "is_maintenance_bot": False,
        }

        self.assertEqual("author", route_pr(facts, {}, 1))

    def test_required_ci_failure_preserves_maintenance_bot_routing(self) -> None:
        for approval_count, expected_route in ((0, "approver"), (1, "maintainer")):
            with self.subTest(approval_count=approval_count):
                facts = {
                    "approval_count": approval_count,
                    "ci_failing_count": 1,
                    "is_maintenance_bot": True,
                }

                self.assertEqual(expected_route, route_pr(facts, {}, 2))

    def test_required_ci_failure_waits_since_first_current_failure(self) -> None:
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
                    {
                        "bucket": "fail",
                        "completed_at": "2026-07-17T02:00:00Z",
                    },
                    {
                        "bucket": "cancel",
                        "completed_at": "2026-07-17T01:00:00Z",
                    },
                ],
            },
            "author",
            [
                {
                    "actor_role": "author",
                    "kind": "issue-comment",
                    "body": "old activity",
                    "timestamp": "2026-07-14T02:00:00Z",
                }
            ],
        )

        cases = (
            ("2026-07-17T03:00:00Z", "2026-07-17T01:00:00+00:00", "ci_failure"),
            ("2026-07-16T23:00:00Z", "2026-07-16T23:00:00+00:00", "oldest_pending_thread"),
        )
        for discussion_since, waiting_since, basis in cases:
            with self.subTest(discussion_since=discussion_since):
                current_facts = dict(facts)
                add_wait_age_facts(
                    current_facts,
                    "author",
                    {"thread": {"action": "author", "since": discussion_since}},
                )

                self.assertEqual(waiting_since, current_facts["waiting_since"])
                self.assertEqual(basis, current_facts["waiting_age_basis"])


if __name__ == "__main__":
    unittest.main()