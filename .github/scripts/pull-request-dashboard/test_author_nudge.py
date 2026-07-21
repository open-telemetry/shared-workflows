from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import author_nudge
import dashboard


NOW = datetime(2026, 7, 17, tzinfo=timezone.utc)


def author_result(route: str = "author") -> dict:
    return {"route": route, "facts": {"author": "alice", "head_sha": "current-head"}}


class AuthorNudgePolicyTest(unittest.TestCase):
    def test_first_author_route_observation_starts_clock(self) -> None:
        due, entry = author_nudge.plan_nudge(author_result(), None, NOW)

        self.assertFalse(due)
        self.assertEqual(
            entry,
            {"waiting_since": "2026-07-17T00:00:00+00:00", "nudged_at": ""},
        )

    def test_nudge_is_due_after_one_week(self) -> None:
        due, _entry = author_nudge.plan_nudge(
            author_result(),
            {"waiting_since": "2026-07-10T00:00:00+00:00", "nudged_at": ""},
            NOW,
        )

        self.assertTrue(due)

    def test_nudge_is_not_due_before_one_week(self) -> None:
        due, _entry = author_nudge.plan_nudge(
            author_result(),
            {"waiting_since": "2026-07-10T00:00:01+00:00", "nudged_at": ""},
            NOW,
        )

        self.assertFalse(due)

    def test_leaving_author_route_resets_unnudged_clock(self) -> None:
        due, entry = author_nudge.plan_nudge(
            author_result("approver"),
            {"waiting_since": "2026-07-10T00:00:00+00:00", "nudged_at": ""},
            NOW,
        )

        self.assertFalse(due)
        self.assertIsNone(entry)

    def test_lifetime_nudge_marker_survives_route_changes(self) -> None:
        previous = {"nudged_at": "2026-07-10T00:00:00+00:00"}

        for result in (author_result("approver"), None, author_result()):
            due, entry = author_nudge.plan_nudge(result, previous, NOW)
            self.assertFalse(due)
            self.assertEqual(entry, previous)

    def test_failed_refresh_preserves_clock(self) -> None:
        previous = {"waiting_since": "2026-07-10T00:00:00+00:00", "nudged_at": ""}

        due, entry = author_nudge.plan_nudge(
            {"failed": True, "route": "unknown"},
            previous,
            NOW,
        )

        self.assertFalse(due)
        self.assertEqual(entry, previous)


class AuthorNudgeProcessingTest(unittest.TestCase):
    @patch.object(author_nudge, "save_author_nudges")
    @patch.object(author_nudge, "load_author_nudges", return_value={})
    def test_observation_starts_clock(
        self,
        _load_nudges,
        save_nudges,
    ) -> None:
        author_nudge.record_author_nudge_observation(2, author_result(), NOW)

        self.assertEqual(
            save_nudges.call_args.args[0],
            {"2": {"waiting_since": "2026-07-17T00:00:00+00:00", "nudged_at": ""}},
        )

    @patch.object(author_nudge, "save_author_nudges")
    @patch.object(
        author_nudge,
        "load_author_nudges",
        return_value={
            "1": {
                "waiting_since": "2026-07-10T00:00:00+00:00",
                "nudged_at": "",
            }
        },
    )
    def test_departure_observation_resets_clock(
        self,
        _load_nudges,
        save_nudges,
    ) -> None:
        author_nudge.record_author_nudge_observation(1, None, NOW)

        self.assertEqual(save_nudges.call_args.args[0], {})

    @patch.object(author_nudge, "load_reviewer_set", return_value={"reviewer"})
    @patch.object(dashboard, "build_dashboard_update_for_pr")
    def test_fresh_result_uses_dashboard_routing_configuration(
        self,
        build_update,
        _load_reviewers,
    ) -> None:
        result = {"pr_number": 1, **author_result()}
        build_update.return_value = SimpleNamespace(trigger_pr_result=result)
        dashboard_state = {"version": 1, "prs": {}}

        fresh_result, fresh_dashboard_state = author_nudge.refresh_author_nudge_result(
            "open-telemetry/example",
            1,
            dashboard_state,
            ["approvers"],
            2,
            ["optional-*"],
            True,
        )

        self.assertEqual(result, fresh_result)
        self.assertEqual("author", fresh_dashboard_state["prs"]["1"]["route"])
        build_update.assert_called_once_with(
            "open-telemetry/example",
            "open-telemetry",
            "example",
            {1},
            {"reviewer"},
            1,
            dashboard.DEFAULT_MODEL,
            2,
            ["optional-*"],
            dashboard_state,
            True,
        )

    @patch.object(
        author_nudge,
        "refresh_author_nudge_result",
        return_value=(author_result(), {"prs": {"1": author_result()}}),
    )
    @patch.object(author_nudge, "save_author_nudges")
    @patch.object(
        author_nudge,
        "load_author_nudges",
        return_value={"1": {"waiting_since": "2026-07-01T00:00:00+00:00", "nudged_at": ""}},
    )
    @patch.object(
        author_nudge,
        "load_dashboard_state_cache",
        return_value={"prs": {"1": author_result()}},
    )
    def test_preparation_records_pending_nudge(
        self,
        _load_dashboard_state,
        _load_nudges,
        save_nudges,
        refresh_result,
    ) -> None:
        author_nudge.prepare_author_nudges(
            "open-telemetry/example", {1}, NOW, ["approvers"], 1, [], False
        )

        refresh_result.assert_called_once()
        self.assertEqual(
            save_nudges.call_args.args[0],
            {
                "1": {
                    "waiting_since": "2026-07-01T00:00:00+00:00",
                    "nudged_at": "",
                    "pending_at": "2026-07-17T00:00:00+00:00",
                    "head_sha": "current-head",
                }
            },
        )

    @patch.object(
        author_nudge,
        "refresh_author_nudge_result",
        return_value=(author_result("approver"), {"prs": {"1": author_result("approver")}}),
    )
    @patch.object(author_nudge, "save_author_nudges")
    @patch.object(
        author_nudge,
        "load_author_nudges",
        return_value={"1": {"waiting_since": "2026-07-01T00:00:00+00:00", "nudged_at": ""}},
    )
    @patch.object(
        author_nudge,
        "load_dashboard_state_cache",
        return_value={"prs": {"1": author_result()}},
    )
    def test_preparation_resets_clock_for_fresh_route_departure(
        self,
        _load_dashboard_state,
        _load_nudges,
        save_nudges,
        refresh_result,
    ) -> None:
        author_nudge.prepare_author_nudges(
            "open-telemetry/example", {1}, NOW, ["approvers"], 1, [], False
        )

        refresh_result.assert_called_once()
        self.assertEqual(save_nudges.call_args.args[0], {})

    @patch.object(
        author_nudge,
        "refresh_author_nudge_result",
        return_value=({"failed": True, "route": "unknown"}, {"prs": {}}),
    )
    @patch.object(author_nudge, "save_author_nudges")
    @patch.object(
        author_nudge,
        "load_author_nudges",
        return_value={"1": {"waiting_since": "2026-07-01T00:00:00+00:00", "nudged_at": ""}},
    )
    @patch.object(
        author_nudge,
        "load_dashboard_state_cache",
        return_value={"prs": {"1": author_result()}},
    )
    def test_preparation_preserves_clock_when_fresh_refresh_fails(
        self,
        _load_dashboard_state,
        _load_nudges,
        save_nudges,
        _refresh_result,
    ) -> None:
        author_nudge.prepare_author_nudges(
            "open-telemetry/example", {1}, NOW, ["approvers"], 1, [], False
        )

        self.assertEqual(
            save_nudges.call_args.args[0],
            {"1": {"waiting_since": "2026-07-01T00:00:00+00:00", "nudged_at": ""}},
        )

    @patch.object(
        author_nudge,
        "ensure_nudge",
        return_value="2026-07-17T00:00:00+00:00",
    )
    @patch.object(author_nudge, "save_author_nudges")
    @patch.object(
        author_nudge,
        "load_author_nudges",
        return_value={
            "1": {
                "waiting_since": "2026-07-01T00:00:00+00:00",
                "nudged_at": "",
                "pending_at": "2026-07-17T00:00:00+00:00",
                "head_sha": "current-head",
            }
        },
    )
    @patch.object(
        author_nudge,
        "load_dashboard_state_cache",
        return_value={"prs": {"1": author_result()}},
    )
    @patch.object(
        author_nudge,
        "gh_api",
        return_value={"state": "open", "draft": False, "head": {"sha": "current-head"}},
    )
    def test_delivery_records_posted_nudge(
        self,
        _gh_api,
        _load_dashboard_state,
        _load_nudges,
        save_nudges,
        ensure_nudge,
    ) -> None:
        errors = author_nudge.deliver_prepared_author_nudges("open-telemetry/example", NOW)

        self.assertEqual([], errors)
        ensure_nudge.assert_called_once()
        save_nudges.assert_called_once_with({
            "1": {"nudged_at": "2026-07-17T00:00:00+00:00"},
        })

    def test_rendered_nudge_mentions_author_and_links_status(self) -> None:
        body = author_nudge.render_nudge("alice", "https://example.test/status")

        self.assertIn("@alice", body)
        self.assertIn("[dashboard status comment](https://example.test/status)", body)
        self.assertIn(author_nudge.NUDGE_MARKER, body)

    @patch.object(author_nudge, "run_gh")
    @patch.object(author_nudge, "publish_pr_status")
    @patch.object(author_nudge, "managed_status_comments")
    @patch.object(
        author_nudge,
        "gh_api",
        side_effect=[
            [],
            {"state": "open", "draft": False, "user": {"login": "alice"}},
        ],
    )
    def test_posts_nudge_after_ensuring_status_comment(
        self,
        _gh_api,
        managed_status_comments,
        publish_status,
        run_gh,
    ) -> None:
        managed_status_comments.return_value = [
            {"html_url": "https://example.test/status"}
        ]
        dashboard_state = {"prs": {"1": author_result()}}

        nudged_at = author_nudge.ensure_nudge(
            "open-telemetry/example",
            1,
            author_result(),
            dashboard_state,
            NOW,
        )

        self.assertEqual(nudged_at, "2026-07-17T00:00:00+00:00")
        publish_status.assert_called_once_with(
            "open-telemetry/example", 1, dashboard_state
        )
        self.assertIn("@alice", run_gh.call_args.args[0][-1])

    @patch.object(author_nudge, "run_gh")
    @patch.object(author_nudge, "publish_pr_status")
    @patch.object(
        author_nudge,
        "existing_nudge_comment",
        return_value={"created_at": "2026-07-11T00:00:00Z"},
    )
    def test_existing_marker_prevents_duplicate_after_state_loss(
        self,
        _existing_comment,
        publish_status,
        run_gh,
    ) -> None:
        nudged_at = author_nudge.ensure_nudge(
            "open-telemetry/example",
            1,
            author_result(),
            {"prs": {"1": author_result()}},
            NOW,
        )

        self.assertEqual(nudged_at, "2026-07-11T00:00:00Z")
        publish_status.assert_not_called()
        run_gh.assert_not_called()


if __name__ == "__main__":
    unittest.main()