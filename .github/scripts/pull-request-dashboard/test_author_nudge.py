from __future__ import annotations

from datetime import datetime, timezone
import unittest
from unittest.mock import patch

import author_nudge


NOW = datetime(2026, 7, 17, tzinfo=timezone.utc)


def author_result(route: str = "author") -> dict:
    return {
        "route": route,
        "facts": {
            "author": "alice",
            "head_sha": "current-head",
            "source_fingerprint": "current-source",
        },
    }


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

    @patch.object(author_nudge, "save_author_nudges")
    @patch.object(
        author_nudge,
        "load_author_nudges",
        return_value={"1": {"waiting_since": "2026-07-01T00:00:00+00:00", "nudged_at": ""}},
    )
    def test_due_accepted_observation_records_pending_nudge(
        self,
        _load_nudges,
        save_nudges,
    ) -> None:
        author_nudge.record_author_nudge_observation(
            1,
            author_result(),
            NOW,
            prepare_due=True,
        )

        self.assertEqual(
            save_nudges.call_args.args[0],
            {
                "1": {
                    "waiting_since": "2026-07-01T00:00:00+00:00",
                    "nudged_at": "",
                    "pending_at": "2026-07-17T00:00:00+00:00",
                    "head_sha": "current-head",
                    "source_fingerprint": "current-source",
                }
            },
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
                "source_fingerprint": "current-source",
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
        "current_source_fingerprint",
        return_value=(
            {
                "pr": {"state": "OPEN", "isDraft": False},
                "commits": [{"sha": "current-head"}],
            },
            "current-source",
        ),
    )
    def test_delivery_records_posted_nudge(
        self,
        current_source,
        _load_dashboard_state,
        _load_nudges,
        save_nudges,
        ensure_nudge,
    ) -> None:
        errors = author_nudge.deliver_prepared_author_nudges(
            "open-telemetry/example",
            NOW,
            ["optional-*"],
        )

        self.assertEqual([], errors)
        current_source.assert_called_once_with("open-telemetry/example", 1, ["optional-*"])
        ensure_nudge.assert_called_once()
        save_nudges.assert_called_once_with({
            "1": {"nudged_at": "2026-07-17T00:00:00+00:00"},
        })

    @patch.object(author_nudge, "ensure_nudge")
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
                "source_fingerprint": "accepted-source",
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
        "current_source_fingerprint",
        return_value=(
            {
                "pr": {"state": "OPEN", "isDraft": False},
                "commits": [{"sha": "current-head"}],
            },
            "changed-source",
        ),
    )
    def test_delivery_defers_when_routing_inputs_changed(
        self,
        _current_source,
        _load_dashboard_state,
        _load_nudges,
        save_nudges,
        ensure_nudge,
    ) -> None:
        errors = author_nudge.deliver_prepared_author_nudges(
            "open-telemetry/example",
            NOW,
            [],
        )

        self.assertEqual([], errors)
        ensure_nudge.assert_not_called()
        save_nudges.assert_called_once_with({
            "1": {
                "waiting_since": "2026-07-01T00:00:00+00:00",
                "nudged_at": "",
            },
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