from __future__ import annotations

from dataclasses import FrozenInstanceError
import unittest

from dashboard_state_update import (
    DashboardUpdateDisposition,
    accept_dashboard_update,
    prepare_dashboard_update,
)


def result(number: int, route: str, *, failed: bool = False) -> dict[str, object]:
    return {
        "pr_number": number,
        "pr_url": f"https://example.test/pull/{number}",
        "route": route,
        "failed": failed,
        "facts": {"head_sha": route},
        "top_level_history": {},
    }


def state(*results: dict[str, object]) -> dict[str, object]:
    return {
        "version": 10,
        "initial_backfill_complete": False,
        "prs": {str(value["pr_number"]): value for value in results},
    }


class DashboardStateUpdateTest(unittest.TestCase):
    def test_prepared_update_is_frozen_and_records_the_starting_slot(self) -> None:
        starting = result(7, "author")
        prepared = prepare_dashboard_update(state(starting), {7}, 7)

        self.assertEqual(starting, prepared.starting_result)
        self.assertEqual(frozenset({7}), prepared.open_pr_numbers)
        with self.assertRaises(FrozenInstanceError):
            prepared.pr_number = 8  # type: ignore[misc]

    def test_identical_current_result_is_unchanged(self) -> None:
        starting = result(7, "author")
        prepared = prepare_dashboard_update(state(starting), {7}, 7)

        acceptance = accept_dashboard_update(
            prepared.with_evaluated_result(dict(starting)),
            state(starting),
        )

        self.assertIs(acceptance.disposition, DashboardUpdateDisposition.UNCHANGED)
        self.assertFalse(acceptance.effects.persist_dashboard_state)
        self.assertFalse(acceptance.effects.enqueue_status_comment)
        self.assertTrue(acceptance.effects.record_observations)
        self.assertTrue(acceptance.effects.clear_backfill_failure)

    def test_concurrent_other_slot_change_is_retained(self) -> None:
        starting = result(7, "author")
        other_starting = result(8, "author")
        other_latest = result(8, "approver")
        evaluated = result(7, "reviewer")
        prepared = prepare_dashboard_update(
            state(starting, other_starting),
            {7, 8},
            7,
        )

        acceptance = accept_dashboard_update(
            prepared.with_evaluated_result(evaluated),
            state(starting, other_latest),
        )

        self.assertIs(acceptance.disposition, DashboardUpdateDisposition.APPLIED)
        self.assertEqual(
            "approver",
            acceptance.dashboard_state["prs"]["8"]["route"],
        )
        self.assertEqual(
            "reviewer",
            acceptance.dashboard_state["prs"]["7"]["route"],
        )

    def test_concurrent_same_slot_change_wins(self) -> None:
        starting = result(7, "author")
        concurrent = result(7, "approver")
        prepared = prepare_dashboard_update(state(starting), {7}, 7)

        acceptance = accept_dashboard_update(
            prepared.with_evaluated_result(result(7, "reviewer")),
            state(concurrent),
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
        starting = result(7, "author")
        prepared = prepare_dashboard_update(state(starting), {7}, 7)

        acceptance = accept_dashboard_update(
            prepared.with_evaluated_result(None),
            state(starting),
        )

        self.assertIs(acceptance.disposition, DashboardUpdateDisposition.APPLIED)
        self.assertIsNone(acceptance.accepted_result)
        self.assertEqual({}, acceptance.dashboard_state["prs"])
        self.assertTrue(acceptance.effects.persist_dashboard_state)
        self.assertTrue(acceptance.effects.enqueue_status_comment)
        self.assertTrue(acceptance.effects.record_observations)
        self.assertFalse(acceptance.effects.clear_backfill_failure)

    def test_untracked_dropped_pr_is_unchanged(self) -> None:
        other = result(8, "author")
        prepared = prepare_dashboard_update(state(other), {7, 8}, 7)

        acceptance = accept_dashboard_update(
            prepared.with_evaluated_result(None),
            state(other),
        )

        self.assertIs(acceptance.disposition, DashboardUpdateDisposition.UNCHANGED)
        self.assertEqual(state(other), acceptance.dashboard_state)
        self.assertFalse(acceptance.effects.persist_dashboard_state)
        self.assertFalse(acceptance.effects.enqueue_status_comment)
        self.assertTrue(acceptance.effects.record_observations)

    def test_successful_result_is_accepted_and_plans_all_effects(self) -> None:
        starting = result(7, "author")
        evaluated = result(7, "approver")
        prepared = prepare_dashboard_update(state(starting), {7}, 7)

        acceptance = accept_dashboard_update(
            prepared.with_evaluated_result(evaluated),
            state(starting),
        )

        self.assertIs(acceptance.disposition, DashboardUpdateDisposition.APPLIED)
        self.assertEqual("approver", acceptance.accepted_result["route"])
        self.assertTrue(acceptance.effects.persist_dashboard_state)
        self.assertTrue(acceptance.effects.enqueue_status_comment)
        self.assertTrue(acceptance.effects.record_observations)
        self.assertTrue(acceptance.effects.clear_backfill_failure)

    def test_failed_result_is_rejected_and_previous_slot_is_retained(self) -> None:
        starting = result(7, "author")
        prepared = prepare_dashboard_update(state(starting), {7}, 7)

        acceptance = accept_dashboard_update(
            prepared.with_evaluated_result(result(7, "unknown", failed=True)),
            state(starting),
        )

        self.assertIs(
            acceptance.disposition,
            DashboardUpdateDisposition.FAILED_RESULT_REJECTED,
        )
        self.assertEqual(starting, acceptance.accepted_result)
        self.assertFalse(acceptance.dashboard_state_unchanged)
        self.assertFalse(acceptance.effects.persist_dashboard_state)
        self.assertFalse(acceptance.effects.enqueue_status_comment)
        self.assertFalse(acceptance.effects.record_observations)
        self.assertFalse(acceptance.effects.clear_backfill_failure)

    def test_failed_result_does_not_override_a_concurrent_same_slot_change(self) -> None:
        starting = result(7, "author")
        concurrent = result(7, "approver")
        prepared = prepare_dashboard_update(state(starting), {7}, 7)

        acceptance = accept_dashboard_update(
            prepared.with_evaluated_result(result(7, "unknown", failed=True)),
            state(concurrent),
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
