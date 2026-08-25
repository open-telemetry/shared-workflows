from __future__ import annotations

from dataclasses import FrozenInstanceError
import unittest

from dashboard_contracts import DashboardRoute
from dashboard_state_update import (
    DashboardUpdateDisposition,
    accept_dashboard_update,
    prepare_dashboard_update,
)
from dashboard_test_support import (
    dashboard_facts,
    dashboard_state,
    evaluation_failure,
    evaluation_success,
    stored_dashboard_result,
)


def stored(number: int, route: DashboardRoute | str):
    return stored_dashboard_result(
        number,
        route,
        facts=dashboard_facts(head_sha=DashboardRoute(route).value),
    )


def evaluated(number: int, route: DashboardRoute | str):
    return evaluation_success(
        number,
        route,
        facts=dashboard_facts(head_sha=DashboardRoute(route).value),
    )


class DashboardStateUpdateTest(unittest.TestCase):
    def test_prepared_update_is_frozen_and_records_the_starting_result(self) -> None:
        starting = stored(7, DashboardRoute.AUTHOR)
        prepared = prepare_dashboard_update(
            dashboard_state(starting),
            {7},
            7,
        )

        self.assertEqual(starting, prepared.starting_result)
        with self.assertRaises(FrozenInstanceError):
            prepared.pr_number = 8  # type: ignore[misc]

    def test_identical_current_result_is_unchanged(self) -> None:
        starting = stored(7, DashboardRoute.AUTHOR)
        state = dashboard_state(starting)
        prepared = prepare_dashboard_update(state, {7}, 7)

        acceptance = accept_dashboard_update(
            prepared.with_evaluated_result(
                evaluated(7, DashboardRoute.AUTHOR)
            ),
            state,
        )

        self.assertIs(acceptance.disposition, DashboardUpdateDisposition.UNCHANGED)
        self.assertFalse(acceptance.effects.persist_dashboard_state)
        self.assertFalse(acceptance.effects.enqueue_status_comment)
        self.assertTrue(acceptance.effects.record_observations)
        self.assertTrue(acceptance.effects.clear_backfill_failure)

    def test_matching_result_is_applied_when_latest_state_is_missing(self) -> None:
        starting = stored(7, DashboardRoute.AUTHOR)
        prepared = prepare_dashboard_update(
            dashboard_state(starting),
            {7},
            7,
        )

        acceptance = accept_dashboard_update(
            prepared.with_evaluated_result(
                evaluated(7, DashboardRoute.AUTHOR)
            ),
            None,
        )

        self.assertIs(acceptance.disposition, DashboardUpdateDisposition.APPLIED)
        self.assertEqual(starting, acceptance.accepted_result)
        self.assertTrue(acceptance.effects.persist_dashboard_state)
        self.assertTrue(acceptance.effects.enqueue_status_comment)
        self.assertTrue(acceptance.effects.record_observations)
        self.assertTrue(acceptance.effects.clear_backfill_failure)

    def test_concurrent_other_slot_change_is_retained(self) -> None:
        starting = stored(7, DashboardRoute.AUTHOR)
        other_starting = stored(8, DashboardRoute.AUTHOR)
        other_latest = stored(8, DashboardRoute.APPROVER)
        prepared = prepare_dashboard_update(
            dashboard_state(starting, other_starting),
            {7, 8},
            7,
        )

        acceptance = accept_dashboard_update(
            prepared.with_evaluated_result(
                evaluated(7, DashboardRoute.APPROVER)
            ),
            dashboard_state(starting, other_latest),
        )

        self.assertIs(acceptance.disposition, DashboardUpdateDisposition.APPLIED)
        self.assertIs(
            acceptance.dashboard_state.result_for(8).route,
            DashboardRoute.APPROVER,
        )
        self.assertIs(
            acceptance.dashboard_state.result_for(7).route,
            DashboardRoute.APPROVER,
        )

    def test_concurrent_same_slot_change_wins(self) -> None:
        starting = stored(7, DashboardRoute.AUTHOR)
        concurrent = stored(7, DashboardRoute.APPROVER)
        prepared = prepare_dashboard_update(
            dashboard_state(starting),
            {7},
            7,
        )

        acceptance = accept_dashboard_update(
            prepared.with_evaluated_result(
                evaluated(7, DashboardRoute.MAINTAINER)
            ),
            dashboard_state(concurrent),
        )

        self.assertIs(
            acceptance.disposition,
            DashboardUpdateDisposition.CONCURRENT_UPDATE,
        )
        self.assertEqual(concurrent, acceptance.accepted_result)
        self.assertFalse(acceptance.effects.persist_dashboard_state)
        self.assertFalse(acceptance.effects.enqueue_status_comment)
        self.assertTrue(acceptance.effects.record_observations)
        self.assertTrue(acceptance.effects.clear_backfill_failure)

    def test_tracked_pr_removal_is_accepted(self) -> None:
        starting = stored(7, DashboardRoute.AUTHOR)
        state = dashboard_state(starting)
        prepared = prepare_dashboard_update(state, {7}, 7)

        acceptance = accept_dashboard_update(
            prepared.with_evaluated_result(None),
            state,
        )

        self.assertIs(acceptance.disposition, DashboardUpdateDisposition.APPLIED)
        self.assertIsNone(acceptance.accepted_result)
        self.assertEqual((), acceptance.dashboard_state.results)
        self.assertTrue(acceptance.effects.persist_dashboard_state)
        self.assertTrue(acceptance.effects.enqueue_status_comment)
        self.assertTrue(acceptance.effects.record_observations)
        self.assertFalse(acceptance.effects.clear_backfill_failure)

    def test_untracked_dropped_pr_is_unchanged(self) -> None:
        other = stored(8, DashboardRoute.AUTHOR)
        state = dashboard_state(other)
        prepared = prepare_dashboard_update(state, {7, 8}, 7)

        acceptance = accept_dashboard_update(
            prepared.with_evaluated_result(None),
            state,
        )

        self.assertIs(acceptance.disposition, DashboardUpdateDisposition.UNCHANGED)
        self.assertEqual(state, acceptance.dashboard_state)
        self.assertFalse(acceptance.effects.persist_dashboard_state)
        self.assertFalse(acceptance.effects.enqueue_status_comment)
        self.assertTrue(acceptance.effects.record_observations)

    def test_successful_result_is_accepted_and_plans_all_effects(self) -> None:
        starting = stored(7, DashboardRoute.AUTHOR)
        prepared = prepare_dashboard_update(
            dashboard_state(starting),
            {7},
            7,
        )

        acceptance = accept_dashboard_update(
            prepared.with_evaluated_result(
                evaluated(7, DashboardRoute.APPROVER)
            ),
            dashboard_state(starting),
        )

        self.assertIs(acceptance.disposition, DashboardUpdateDisposition.APPLIED)
        self.assertIs(
            acceptance.accepted_result.route,
            DashboardRoute.APPROVER,
        )
        self.assertTrue(acceptance.effects.persist_dashboard_state)
        self.assertTrue(acceptance.effects.enqueue_status_comment)
        self.assertTrue(acceptance.effects.record_observations)
        self.assertTrue(acceptance.effects.clear_backfill_failure)

    def test_failed_result_is_rejected_and_previous_slot_is_retained(self) -> None:
        starting = stored(7, DashboardRoute.AUTHOR)
        state = dashboard_state(starting)
        prepared = prepare_dashboard_update(state, {7}, 7)

        acceptance = accept_dashboard_update(
            prepared.with_evaluated_result(evaluation_failure(7)),
            state,
        )

        self.assertIs(
            acceptance.disposition,
            DashboardUpdateDisposition.FAILED_RESULT_REJECTED,
        )
        self.assertEqual(starting, acceptance.accepted_result)
        self.assertFalse(acceptance.effects.persist_dashboard_state)
        self.assertFalse(acceptance.effects.enqueue_status_comment)
        self.assertFalse(acceptance.effects.record_observations)
        self.assertFalse(acceptance.effects.clear_backfill_failure)

    def test_failed_result_does_not_override_concurrent_update(self) -> None:
        starting = stored(7, DashboardRoute.AUTHOR)
        concurrent = stored(7, DashboardRoute.APPROVER)
        prepared = prepare_dashboard_update(
            dashboard_state(starting),
            {7},
            7,
        )

        acceptance = accept_dashboard_update(
            prepared.with_evaluated_result(evaluation_failure(7)),
            dashboard_state(concurrent),
        )

        self.assertIs(
            acceptance.disposition,
            DashboardUpdateDisposition.CONCURRENT_UPDATE,
        )
        self.assertEqual(concurrent, acceptance.accepted_result)
        self.assertTrue(acceptance.effects.record_observations)
        self.assertFalse(acceptance.effects.clear_backfill_failure)


if __name__ == "__main__":
    unittest.main()
