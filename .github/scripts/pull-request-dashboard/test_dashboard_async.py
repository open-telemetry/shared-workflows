from __future__ import annotations

import io
import unittest
from argparse import Namespace
from contextlib import redirect_stderr
from copy import deepcopy
from pathlib import Path
from unittest.mock import AsyncMock, patch

import dashboard
from classification_test_support import FakeClassificationOperation
from dashboard_contracts import DashboardRoute
from dashboard_test_support import (
    dashboard_state,
    evaluation_draft,
    evaluation_failure,
    evaluation_success,
    stored_dashboard_result,
)


class BackfillRetryTest(unittest.IsolatedAsyncioTestCase):
    async def run_backfill(self, evaluation, *, concurrent_state, concurrent_failures=()):
        starting = dashboard_state(
            stored_dashboard_result(7, DashboardRoute.AUTHOR),
            stored_dashboard_result(8, DashboardRoute.AUTHOR),
        )
        current = starting
        progress = {"failed_pr_numbers": [7]}
        args = Namespace(
            repo="owner/repo",
            approver_team=["approvers"],
            state_branch="state",
            pr_number=None,
            model="model",
            required_approvals=1,
            non_blocking_check_pattern=[],
        )

        def save_state(state):
            nonlocal current
            current = state

        def save_progress(state):
            progress.clear()
            progress.update(deepcopy(state))

        def push(_directory, _message, update, **_kwargs):
            nonlocal current
            self.assertEqual(1, evaluate.await_count)
            self.assertEqual(0, update())
            current = concurrent_state
            save_progress({"failed_pr_numbers": list(concurrent_failures)})
            self.assertEqual(0, update())
            self.assertEqual(1, evaluate.await_count)
            return 0

        with (
            redirect_stderr(io.StringIO()),
            patch.object(dashboard, "list_open_prs", return_value=[{"number": 7}, {"number": 8}]),
            patch.object(dashboard, "select_backfill_prs", return_value=dashboard.BackfillSelection([{"number": 7}], set())),
            patch.object(dashboard, "DEFAULT_CLASSIFICATION_CACHE_STORE"),
            patch.object(dashboard, "load_reviewer_set", return_value={"reviewer"}),
            patch.object(dashboard, "load_dashboard_state_cache", side_effect=lambda: current),
            patch.object(dashboard, "save_dashboard_state_cache", side_effect=save_state),
            patch.object(dashboard, "load_backfill_state", side_effect=lambda: deepcopy(progress)),
            patch.object(dashboard, "save_backfill_state", side_effect=save_progress),
            patch.object(dashboard, "evaluate_pull_request", return_value=evaluation) as evaluate,
            patch.object(dashboard, "enqueue_status_comment_update") as enqueue,
            patch.object(dashboard, "record_author_nudge_observation") as nudge,
            patch.object(dashboard, "record_copilot_review_observation"),
            patch.object(dashboard.state_branch, "configure_git"),
            patch.object(dashboard.state_branch, "checkout_state"),
            patch.object(dashboard.state_branch, "remove_existing_state_dir"),
            patch.object(dashboard.state_branch, "push_state_changes", side_effect=push),
        ):
            status = await dashboard.update_dashboard_for_backfill(
                args, Path("state"), FakeClassificationOperation()
            )

        evaluate.assert_awaited_once()
        self.assertEqual(starting.result_for(7), evaluate.call_args.args[1].previous_result)
        self.assertEqual({"last_pr_number": 7}, progress["cursor"])
        self.assertTrue(current.initial_backfill_complete)
        return status, current, progress, enqueue, nudge

    async def test_push_retry_reuses_evaluation_and_retains_other_pr_changes(self) -> None:
        concurrent = dashboard_state(
            stored_dashboard_result(7, DashboardRoute.AUTHOR),
            stored_dashboard_result(8, DashboardRoute.MAINTAINER),
        )
        status, state, progress, _, _ = await self.run_backfill(
            evaluation_success(7, DashboardRoute.APPROVER),
            concurrent_state=concurrent,
        )
        self.assertEqual(0, status)
        self.assertEqual(DashboardRoute.APPROVER, state.result_for(7).route)
        self.assertEqual(concurrent.result_for(8), state.result_for(8))
        self.assertEqual([], progress["failed_pr_numbers"])

    async def test_same_pr_update_wins_and_observations_use_accepted_result(self) -> None:
        concurrent = dashboard_state(
            stored_dashboard_result(7, DashboardRoute.MAINTAINER),
            stored_dashboard_result(8),
        )
        status, state, _, enqueue, nudge = await self.run_backfill(
            evaluation_success(7, DashboardRoute.APPROVER),
            concurrent_state=concurrent,
        )
        self.assertEqual(0, status)
        self.assertEqual(concurrent.result_for(7), state.result_for(7))
        self.assertEqual(1, enqueue.call_count)
        self.assertEqual(concurrent.result_for(7), nudge.call_args.args[1])

    async def test_concurrent_draft_and_removal_win_over_stale_success(self) -> None:
        for concurrent in (
            dashboard_state(stored_dashboard_result(8), draft_pr_numbers=frozenset({7})),
            dashboard_state(stored_dashboard_result(8), initial_backfill_complete=True),
        ):
            with self.subTest(concurrent=concurrent):
                status, state, _, enqueue, _ = await self.run_backfill(
                    evaluation_success(7, DashboardRoute.APPROVER),
                    concurrent_state=concurrent,
                )
                self.assertEqual(0, status)
                self.assertIsNone(state.result_for(7))
                self.assertEqual(concurrent.is_draft(7), state.is_draft(7))
                self.assertEqual(1, enqueue.call_count)

    async def test_stale_closed_and_draft_evaluations_do_not_replace_newer_result(self) -> None:
        concurrent = dashboard_state(
            stored_dashboard_result(7, DashboardRoute.APPROVER),
            stored_dashboard_result(8),
        )
        for result in (None, evaluation_draft(7)):
            with self.subTest(result=result):
                status, state, _, _, _ = await self.run_backfill(
                    result,
                    concurrent_state=concurrent,
                )
                self.assertEqual(0, status)
                self.assertEqual(concurrent.result_for(7), state.result_for(7))
                self.assertFalse(state.is_draft(7))

    async def test_superseded_failure_preserves_latest_failure_marker(self) -> None:
        concurrent = dashboard_state(
            stored_dashboard_result(7, DashboardRoute.APPROVER),
            stored_dashboard_result(8),
        )
        for failures in ([], [7]):
            with self.subTest(failures=failures):
                status, state, progress, enqueue, _ = await self.run_backfill(
                    evaluation_failure(7),
                    concurrent_state=concurrent,
                    concurrent_failures=failures,
                )
                self.assertEqual(
                    dashboard.BACKFILL_RECORDED_FAILURE_STATUS if failures else 0,
                    status,
                )
                self.assertEqual(concurrent.result_for(7), state.result_for(7))
                self.assertEqual(failures, progress["failed_pr_numbers"])
                enqueue.assert_not_called()

    async def test_each_selected_pr_reads_state_after_previous_pr_persists(self) -> None:
        state = dashboard_state()
        progress = {}
        evaluated_previous = []

        async def evaluate(_config, source, _service):
            evaluated_previous.append(state.pr_numbers)
            return evaluation_success(source.pr_number)

        def save_state(new_state):
            nonlocal state
            state = new_state

        def save_progress(new_progress):
            progress.clear()
            progress.update(new_progress)

        args = Namespace(
            repo="owner/repo", approver_team=[], state_branch="state", pr_number=None,
            model="model", required_approvals=1, non_blocking_check_pattern=[],
        )
        with (
            redirect_stderr(io.StringIO()),
            patch.object(dashboard, "list_open_prs", return_value=[{"number": 7}, {"number": 8}]),
            patch.object(dashboard, "DEFAULT_CLASSIFICATION_CACHE_STORE"),
            patch.object(dashboard, "load_reviewer_set", return_value=set()),
            patch.object(dashboard, "load_dashboard_state_cache", side_effect=lambda: state),
            patch.object(dashboard, "save_dashboard_state_cache", side_effect=save_state),
            patch.object(dashboard, "load_backfill_state", side_effect=lambda: deepcopy(progress)),
            patch.object(dashboard, "save_backfill_state", side_effect=save_progress),
            patch.object(dashboard, "evaluate_pull_request", side_effect=evaluate),
            patch.object(dashboard, "enqueue_status_comment_update"),
            patch.object(dashboard, "record_author_nudge_observation"),
            patch.object(dashboard, "record_copilot_review_observation"),
            patch.object(dashboard.state_branch, "configure_git"),
            patch.object(dashboard.state_branch, "checkout_state"),
            patch.object(dashboard.state_branch, "remove_existing_state_dir"),
            patch.object(dashboard.state_branch, "push_state_changes", side_effect=lambda _d, _m, update, **_kw: update()),
        ):
            self.assertEqual(
                0,
                await dashboard.update_dashboard_for_backfill(
                    args, Path("state"), FakeClassificationOperation()
                ),
            )
        self.assertEqual([frozenset(), frozenset({7})], evaluated_previous)
        self.assertTrue(state.initial_backfill_complete)


class DashboardClientScopeTest(unittest.IsolatedAsyncioTestCase):
    async def test_both_modes_use_a_scoped_runner_and_injected_service(self) -> None:
        for pr_number, operation in (
            (7, "update_dashboard_for_pr_number"),
            (None, "update_dashboard_for_backfill"),
        ):
            with self.subTest(operation=operation):
                runner = AsyncMock()
                context = AsyncMock()
                context.__aenter__.return_value = runner
                with (
                    patch.object(dashboard, "CopilotSdkModelRunner", return_value=context),
                    patch.object(dashboard, operation, return_value=0) as update,
                ):
                    status = await dashboard.update_dashboard_via_state_branch(
                        Namespace(pr_number=pr_number), Path("state")
                    )
                self.assertEqual(0, status)
                self.assertIs(runner, update.call_args.args[2].runner)
                context.__aenter__.assert_awaited_once()
                context.__aexit__.assert_awaited_once()
