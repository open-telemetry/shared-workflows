from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import unittest

from author_follow_up import plan_follow_up, qualifying_author_activity


NOW = datetime(2026, 7, 17, tzinfo=timezone.utc)


def result(
    *,
    route: str = "author",
    author_activity: str = "2026-07-10T00:00:00Z",
    approver_activity: str = "",
    human_head_activity: str = "",
    author_head_activity: str = "",
    waiting_since: str = "2026-07-10T00:00:00Z",
) -> dict[str, Any]:
    return {
        "route": route,
        "facts": {
            "last_author_activity_at": author_activity,
            "last_approver_activity_at": approver_activity,
            "last_external_activity_at": "",
            "human_head_observed_at": human_head_activity,
            "author_head_observed_at": author_head_activity,
            "waiting_since": waiting_since,
        },
    }


def entry(**overrides: str) -> dict[str, str]:
    value = {
        "cycle_id": "2026-07-01T00:00:00+00:00",
        "waiting_on_author_since": "2026-07-01T00:00:00+00:00",
        "pending_handoff_since": "",
        "handoff_nudged_at": "",
        "general_nudged_at": "",
        "stale_applied_at": "",
        "stale_reset_at": "",
    }
    value.update(overrides)
    return value


class AuthorFollowUpPolicyTest(unittest.TestCase):
    def test_new_cycle_starts_when_author_route_is_first_observed(self) -> None:
        action, updated = plan_follow_up(
            result(waiting_since="2026-06-01T00:00:00Z"),
            None,
            NOW,
            stale_enabled=False,
        )

        self.assertIsNone(action)
        assert updated is not None
        self.assertEqual(updated["waiting_on_author_since"], "2026-07-17T00:00:00+00:00")

    def test_author_must_be_latest_substantive_actor(self) -> None:
        self.assertIsNotNone(qualifying_author_activity(result()["facts"]))
        self.assertIsNone(
            qualifying_author_activity(
                result(approver_activity="2026-07-10T00:00:00Z")["facts"]
            )
        )

    def test_author_last_accelerates_nudge_to_one_day(self) -> None:
        action, updated = plan_follow_up(
            result(
                author_activity="2026-07-16T00:00:00Z",
                waiting_since="2026-07-12T00:00:00Z",
            ),
            entry(
                waiting_on_author_since="2026-07-12T00:00:00Z",
                pending_handoff_since="2026-07-16T00:00:00Z",
            ),
            NOW,
            stale_enabled=False,
        )

        self.assertEqual(action, "handoff-nudge")
        assert updated is not None
        self.assertEqual(updated["handoff_nudged_at"], "2026-07-17T00:00:00+00:00")

    def test_author_last_does_not_nudge_before_one_day(self) -> None:
        action, updated = plan_follow_up(
            result(
                author_activity="2026-07-16T00:00:01Z",
                waiting_since="2026-07-12T00:00:00Z",
            ),
            entry(waiting_on_author_since="2026-07-12T00:00:00Z"),
            NOW,
            stale_enabled=False,
        )

        self.assertIsNone(action)
        assert updated is not None
        self.assertEqual(updated["handoff_nudged_at"], "")

    def test_reviewer_push_does_not_trigger_handoff_nudge(self) -> None:
        action, updated = plan_follow_up(
            result(
                author_activity="",
                human_head_activity="2026-07-16T00:00:00Z",
                waiting_since="2026-07-12T00:00:00Z",
            ),
            entry(waiting_on_author_since="2026-07-12T00:00:00Z"),
            NOW,
            stale_enabled=False,
        )

        self.assertIsNone(action)
        assert updated is not None
        self.assertEqual(updated["pending_handoff_since"], "")

    def test_old_dated_author_push_uses_observation_time_for_handoff(self) -> None:
        action, updated = plan_follow_up(
            result(
                author_activity="",
                human_head_activity="2026-07-16T00:00:00Z",
                author_head_activity="2026-07-16T00:00:00Z",
                waiting_since="2026-07-12T00:00:00Z",
            ),
            entry(waiting_on_author_since="2026-07-12T00:00:00Z"),
            NOW,
            stale_enabled=False,
        )

        self.assertEqual(action, "handoff-nudge")
        assert updated is not None
        self.assertEqual(updated["handoff_nudged_at"], "2026-07-17T00:00:00+00:00")

    def test_handoff_nudge_clock_does_not_use_author_route_start(self) -> None:
        action, updated = plan_follow_up(
            result(
                author_activity="2026-07-16T12:00:00Z",
                waiting_since="2026-07-10T12:00:00Z",
            ),
            entry(waiting_on_author_since="2026-07-10T12:00:00Z"),
            NOW,
            stale_enabled=False,
        )

        self.assertIsNone(action)
        assert updated is not None
        self.assertEqual(updated["handoff_nudged_at"], "")

    def test_author_action_before_current_route_period_does_not_trigger_handoff_nudge(self) -> None:
        action, updated = plan_follow_up(
            result(
                author_activity="2026-07-14T00:00:00Z",
                waiting_since="2026-07-16T00:00:00Z",
            ),
            entry(waiting_on_author_since="2026-07-16T00:00:00Z"),
            NOW,
            stale_enabled=False,
        )

        self.assertIsNone(action)
        assert updated is not None
        self.assertEqual(updated["handoff_nudged_at"], "")

    def test_general_nudge_after_one_week_when_reviewer_acted_last(self) -> None:
        action, updated = plan_follow_up(
            result(
                approver_activity="2026-07-11T00:00:00Z",
                waiting_since="2026-07-10T00:00:00Z",
            ),
            entry(waiting_on_author_since="2026-07-10T00:00:00Z"),
            NOW,
            stale_enabled=False,
        )

        self.assertEqual(action, "general-nudge")
        assert updated is not None
        self.assertEqual(updated["general_nudged_at"], "2026-07-17T00:00:00+00:00")

    def test_reviewer_last_does_not_nudge_before_one_week(self) -> None:
        action, updated = plan_follow_up(
            result(
                approver_activity="2026-07-16T00:00:00Z",
                waiting_since="2026-07-10T00:00:01Z",
            ),
            entry(waiting_on_author_since="2026-07-10T00:00:01Z"),
            NOW,
            stale_enabled=False,
        )

        self.assertIsNone(action)
        assert updated is not None
        self.assertEqual(updated["general_nudged_at"], "")

    def test_general_nudge_waits_one_week_after_handoff_nudge(self) -> None:
        action, updated = plan_follow_up(
            result(waiting_since="2026-07-10T00:00:00Z"),
            entry(
                waiting_on_author_since="2026-07-10T00:00:00Z",
                handoff_nudged_at="2026-07-16T00:00:00Z",
            ),
            NOW,
            stale_enabled=False,
        )

        self.assertIsNone(action)
        assert updated is not None
        self.assertEqual(updated["general_nudged_at"], "")

    def test_general_nudge_is_due_one_week_after_handoff_nudge(self) -> None:
        action, updated = plan_follow_up(
            result(waiting_since="2026-07-01T00:00:00Z"),
            entry(
                waiting_on_author_since="2026-07-01T00:00:00Z",
                handoff_nudged_at="2026-07-10T00:00:00Z",
            ),
            NOW,
            stale_enabled=False,
        )

        self.assertEqual(action, "general-nudge")
        assert updated is not None
        self.assertEqual(updated["general_nudged_at"], "2026-07-17T00:00:00+00:00")

    def test_handoff_nudge_can_follow_general_nudge(self) -> None:
        action, updated = plan_follow_up(
            result(
                author_activity="2026-07-16T00:00:00Z",
                waiting_since="2026-07-08T00:00:00Z",
            ),
            entry(
                waiting_on_author_since="2026-07-08T00:00:00Z",
                general_nudged_at="2026-07-15T00:00:00Z",
            ),
            NOW,
            stale_enabled=False,
        )

        self.assertEqual(action, "handoff-nudge")
        assert updated is not None
        self.assertEqual(updated["handoff_nudged_at"], "2026-07-17T00:00:00+00:00")

    def test_later_author_activity_does_not_postpone_pending_handoff(self) -> None:
        action, updated = plan_follow_up(
            result(
                author_activity="2026-07-16T23:00:00Z",
                waiting_since="2026-07-12T00:00:00Z",
            ),
            entry(
                waiting_on_author_since="2026-07-12T00:00:00Z",
                pending_handoff_since="2026-07-16T00:00:00Z",
            ),
            NOW,
            stale_enabled=False,
        )

        self.assertEqual(action, "handoff-nudge")
        assert updated is not None
        self.assertEqual(updated["handoff_nudged_at"], "2026-07-17T00:00:00+00:00")

    def test_due_handoff_precedes_general_nudge_and_resets_its_clock(self) -> None:
        action, updated = plan_follow_up(
            result(
                author_activity="2026-07-15T00:00:00Z",
                waiting_since="2026-07-10T00:00:00Z",
            ),
            entry(
                waiting_on_author_since="2026-07-10T00:00:00Z",
                pending_handoff_since="2026-07-15T00:00:00Z",
            ),
            NOW,
            stale_enabled=False,
        )

        self.assertEqual(action, "handoff-nudge")
        assert updated is not None
        self.assertEqual(updated["pending_handoff_since"], "")
        self.assertEqual(updated["handoff_nudged_at"], "2026-07-17T00:00:00+00:00")
        self.assertEqual(updated["general_nudged_at"], "")

    def test_pending_handoff_delays_due_general_nudge(self) -> None:
        action, updated = plan_follow_up(
            result(
                author_activity="2026-07-16T12:00:00Z",
                waiting_since="2026-07-10T00:00:00Z",
            ),
            entry(
                waiting_on_author_since="2026-07-10T00:00:00Z",
                pending_handoff_since="2026-07-16T12:00:00Z",
            ),
            NOW,
            stale_enabled=False,
        )

        self.assertIsNone(action)
        assert updated is not None
        self.assertEqual(updated["pending_handoff_since"], "2026-07-16T12:00:00Z")
        self.assertEqual(updated["general_nudged_at"], "")

    def test_stale_escalation_is_disabled_by_default(self) -> None:
        action, _updated = plan_follow_up(
            result(waiting_since="2026-07-01T00:00:00Z"),
            entry(
                handoff_nudged_at="2026-07-02T00:00:00Z",
                general_nudged_at="2026-07-02T00:00:00Z",
            ),
            NOW,
            stale_enabled=False,
        )

        self.assertIsNone(action)

    def test_marks_stale_after_two_weeks_and_one_week_after_nudge(self) -> None:
        action, updated = plan_follow_up(
            result(waiting_since="2026-07-01T00:00:00Z"),
            entry(
                handoff_nudged_at="2026-07-02T00:00:00Z",
                general_nudged_at="2026-07-10T00:00:00Z",
            ),
            NOW,
            stale_enabled=True,
        )

        self.assertEqual(action, "stale")
        assert updated is not None
        self.assertEqual(updated["stale_applied_at"], "2026-07-17T00:00:00+00:00")

    def test_does_not_escalate_old_pr_immediately_after_delayed_nudge(self) -> None:
        action, _updated = plan_follow_up(
            result(waiting_since="2026-06-01T00:00:00Z"),
            entry(
                handoff_nudged_at="2026-06-02T00:00:00Z",
                general_nudged_at="2026-07-16T00:00:00Z",
            ),
            NOW,
            stale_enabled=True,
        )

        self.assertIsNone(action)

    def test_closes_after_three_weeks_and_one_week_after_stale(self) -> None:
        action, _updated = plan_follow_up(
            result(waiting_since="2026-06-20T00:00:00Z"),
            entry(
                waiting_on_author_since="2026-06-20T00:00:00Z",
                handoff_nudged_at="2026-06-21T00:00:00Z",
                general_nudged_at="2026-06-27T00:00:00Z",
                stale_applied_at="2026-07-10T00:00:00Z",
                stale_label_owned=True,
            ),
            NOW,
            stale_enabled=True,
        )

        self.assertEqual(action, "close")

    def test_new_author_activity_removes_stale_and_resets_escalation(self) -> None:
        action, updated = plan_follow_up(
            result(author_activity="2026-07-16T00:00:00Z"),
            entry(
                handoff_nudged_at="2026-07-02T00:00:00Z",
                general_nudged_at="2026-07-02T00:00:00Z",
                stale_applied_at="2026-07-15T00:00:00Z",
            ),
            NOW,
            stale_enabled=True,
        )

        self.assertEqual(action, "remove-stale")
        assert updated is not None
        self.assertEqual(updated["stale_applied_at"], "")
        self.assertEqual(updated["stale_reset_at"], "2026-07-16T00:00:00+00:00")

    def test_reviewer_activity_removes_stale_and_resets_escalation(self) -> None:
        action, updated = plan_follow_up(
            result(approver_activity="2026-07-11T00:00:00Z"),
            entry(
                general_nudged_at="2026-07-02T00:00:00Z",
                stale_applied_at="2026-07-10T00:00:00Z",
            ),
            NOW,
            stale_enabled=True,
        )

        self.assertEqual(action, "remove-stale")
        assert updated is not None
        self.assertEqual(updated["stale_applied_at"], "")
        self.assertEqual(updated["stale_reset_at"], "2026-07-11T00:00:00+00:00")

    def test_reviewer_push_removes_stale_and_resets_escalation(self) -> None:
        action, updated = plan_follow_up(
            result(
                author_activity="",
                human_head_activity="2026-07-16T00:00:00Z",
            ),
            entry(
                general_nudged_at="2026-07-01T00:00:00Z",
                stale_applied_at="2026-07-15T00:00:00Z",
            ),
            NOW,
            stale_enabled=True,
        )

        self.assertEqual(action, "remove-stale")
        assert updated is not None
        self.assertEqual(updated["stale_reset_at"], "2026-07-16T00:00:00+00:00")

    def test_leaving_author_route_clears_cycle_after_stale_removal(self) -> None:
        action, updated = plan_follow_up(
            result(route="approver"),
            entry(stale_applied_at="2026-07-10T00:00:00Z"),
            NOW,
            stale_enabled=True,
        )
        self.assertEqual(action, "remove-stale")
        self.assertIsNotNone(updated)

        action, updated = plan_follow_up(
            result(route="approver"),
            updated,
            NOW,
            stale_enabled=True,
        )
        self.assertIsNone(action)
        self.assertIsNone(updated)

    def test_transient_failure_preserves_existing_cycle(self) -> None:
        previous = entry(general_nudged_at="2026-07-10T00:00:00Z")

        action, updated = plan_follow_up(
            {"failed": True, "route": "transient-failure"},
            previous,
            NOW,
            stale_enabled=True,
        )

        self.assertIsNone(action)
        self.assertEqual(updated, previous)

    def test_later_author_activity_does_not_reset_clock_or_create_second_nudge(self) -> None:
        action, updated = plan_follow_up(
            result(author_activity="2026-07-08T00:00:00Z"),
            entry(
                handoff_nudged_at="2026-07-02T00:00:00Z",
                general_nudged_at="2026-07-08T00:00:00Z",
            ),
            NOW,
            stale_enabled=False,
        )

        self.assertIsNone(action)
        assert updated is not None
        self.assertEqual(updated["handoff_nudged_at"], "2026-07-02T00:00:00Z")
        self.assertEqual(updated["general_nudged_at"], "2026-07-08T00:00:00Z")
        self.assertEqual(updated["waiting_on_author_since"], "2026-07-01T00:00:00+00:00")


if __name__ == "__main__":
    unittest.main()