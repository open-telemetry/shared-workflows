from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from classification import discussion_prompt_input
from dashboard import (
    BACKFILL_RECORDED_FAILURE_STATUS,
    DashboardUpdate,
    apply_targeted_dashboard_update,
    author_action_discussion_urls,
    backfill_failed_pr_numbers,
    complete_initial_backfill_if_ready,
    group_review_threads,
    main,
    set_backfill_pr_failed,
    update_dashboard_for_backfill,
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


class BackfillFailureIsolationTest(unittest.TestCase):
    def test_failed_pr_does_not_block_later_backfill_progress(self) -> None:
        args = SimpleNamespace(
            repo="repo",
            approver_team=["approvers"],
            state_branch="state",
            model="model",
            required_approvals=1,
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
            starting_state = call_args[8]
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
            patch("dashboard.state_branch.configure_git"),
            patch("dashboard.state_branch.checkout_state"),
            patch("dashboard.state_branch.remove_existing_state_dir"),
            patch("dashboard.state_branch.push_state_changes", side_effect=push_state_changes),
        ):
            status = update_dashboard_for_backfill(args, Path("state"))

        self.assertEqual(refreshed_pr_numbers, [1, 2])
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
        args = SimpleNamespace(pr_number=1)
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