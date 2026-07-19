from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import unittest

from author_follow_up import plan_follow_up


NOW = datetime(2026, 7, 17, tzinfo=timezone.utc)


def result(*, route: str = "author") -> dict[str, Any]:
    return {"route": route, "facts": {}}


def entry(**overrides: str) -> dict[str, str]:
    value = {
        "waiting_on_author_since": "2026-07-01T00:00:00+00:00",
        "general_nudged_at": "",
    }
    value.update(overrides)
    return value


class AuthorFollowUpPolicyTest(unittest.TestCase):
    def test_new_cycle_starts_when_author_route_is_first_observed(self) -> None:
        action, updated = plan_follow_up(result(), None, NOW)

        self.assertIsNone(action)
        assert updated is not None
        self.assertEqual(updated["waiting_on_author_since"], "2026-07-17T00:00:00+00:00")
        self.assertEqual(updated["general_nudged_at"], "")

    def test_nudge_is_due_one_week_after_first_observation(self) -> None:
        action, updated = plan_follow_up(
            result(),
            entry(waiting_on_author_since="2026-07-10T00:00:00+00:00"),
            NOW,
        )

        self.assertEqual(action, "general-nudge")
        assert updated is not None
        self.assertEqual(updated["general_nudged_at"], "2026-07-17T00:00:00+00:00")

    def test_nudge_is_not_due_before_one_week(self) -> None:
        action, updated = plan_follow_up(
            result(),
            entry(waiting_on_author_since="2026-07-10T00:00:01+00:00"),
            NOW,
        )

        self.assertIsNone(action)
        assert updated is not None
        self.assertEqual(updated["general_nudged_at"], "")

    def test_pr_is_never_nudged_twice(self) -> None:
        action, updated = plan_follow_up(
            result(),
            entry(general_nudged_at="2026-07-08T00:00:00+00:00"),
            NOW,
        )

        self.assertIsNone(action)
        self.assertEqual(updated, {"general_nudged_at": "2026-07-08T00:00:00+00:00"})

    def test_departure_before_nudge_clears_entry(self) -> None:
        action, updated = plan_follow_up(
            result(route="approver"),
            entry(waiting_on_author_since="2026-07-10T00:00:00+00:00"),
            NOW,
        )

        self.assertIsNone(action)
        self.assertIsNone(updated)

    def test_departure_after_nudge_preserves_marker(self) -> None:
        action, updated = plan_follow_up(
            result(route="approver"),
            entry(general_nudged_at="2026-07-08T00:00:00+00:00"),
            NOW,
        )

        self.assertIsNone(action)
        self.assertEqual(updated, {"general_nudged_at": "2026-07-08T00:00:00+00:00"})

    def test_return_to_author_route_after_nudge_does_not_renudge(self) -> None:
        action, updated = plan_follow_up(
            result(),
            {"general_nudged_at": "2026-07-01T00:00:00+00:00"},
            NOW,
        )

        self.assertIsNone(action)
        self.assertEqual(updated, {"general_nudged_at": "2026-07-01T00:00:00+00:00"})

    def test_missing_result_clears_unnudged_entry(self) -> None:
        action, updated = plan_follow_up(None, entry(), NOW)

        self.assertIsNone(action)
        self.assertIsNone(updated)

    def test_transient_failure_preserves_existing_entry(self) -> None:
        previous = entry(general_nudged_at="2026-07-10T00:00:00+00:00")

        action, updated = plan_follow_up(
            {"failed": True, "route": "transient-failure"},
            previous,
            NOW,
        )

        self.assertIsNone(action)
        self.assertEqual(updated, previous)


if __name__ == "__main__":
    unittest.main()
