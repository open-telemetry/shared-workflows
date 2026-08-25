from __future__ import annotations

from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from dashboard_test_support import (
    dashboard_facts,
    dashboard_state,
    stored_dashboard_result,
)
from notifications import next_notifications
from notify_slack import notify_slack_from_state


class NotifySlackTest(unittest.TestCase):
    @patch("notifications.send_slack_notification")
    def test_gate_held_pr_does_not_notify_reviewers(self, send_notification) -> None:
        results = (
            stored_dashboard_result(
                7,
                "approver",
                facts=dashboard_facts(
                    route_held_for_gates=True,
                    reviewers=({"login": "reviewer"},),
                    waiting_since="2026-07-20T01:00:00Z",
                ),
            ),
        )

        with patch.dict(
            "os.environ",
            {
                "SLACK_CHANNEL": "dashboard",
                "SLACK_USER_MAP_JSON": '{"reviewer": "U123"}',
            },
            clear=True,
        ):
            updated, errors = next_notifications(
                "open-telemetry/example",
                results,
                {7: "Example"},
                {},
                datetime(2026, 7, 20, 2, tzinfo=timezone.utc),
            )

        self.assertEqual(updated, {})
        self.assertEqual(errors, [])
        send_notification.assert_not_called()

    @patch("notify_slack.save_notifications")
    @patch("notify_slack.load_notifications")
    @patch("notify_slack.load_dashboard_state_cache")
    def test_uncached_pr_does_not_pause_notifications_and_closed_state_is_pruned(
        self,
        load_dashboard_state_cache,
        load_notifications,
        save_notifications,
    ) -> None:
        load_dashboard_state_cache.return_value = dashboard_state(
            stored_dashboard_result(
                2,
                "approver",
                facts=dashboard_facts(route_held_for_gates=True),
            )
        )
        open_prs = [
            {"number": 2, "isDraft": False, "title": "Open PR"},
            {"number": 5, "isDraft": False, "title": "Not cached yet"},
            {"number": 3, "isDraft": True, "title": "Draft PR"},
        ]
        load_notifications.return_value = {
            "2": {
                "last_notified_at": "2026-07-14T03:00:00Z",
                "last_notification_kind": "initial",
            },
            "3": {
                "last_notified_at": "2026-07-14T03:00:00Z",
                "last_notification_kind": "initial",
            },
            "4": {
                "last_notified_at": "2026-07-14T03:00:00Z",
                "last_notification_kind": "initial",
            },
        }

        with patch.dict("os.environ", {"SLACK_CHANNEL": "dashboard"}, clear=True):
            errors = notify_slack_from_state(
                "owner/repo",
                None,
                open_prs,
                datetime(2026, 7, 20, 2, tzinfo=timezone.utc),
            )

        self.assertEqual(errors, [])
        save_notifications.assert_called_once_with(
            {
                "2": {
                    "last_notified_at": "2026-07-14T03:00:00Z",
                    "last_notification_kind": "initial",
                },
            }
        )

    @patch("notify_slack.save_notifications")
    @patch("notify_slack.load_notifications")
    @patch("notify_slack.load_dashboard_state_cache")
    def test_targeted_update_preserves_unrelated_notification_state(
        self,
        load_dashboard_state_cache,
        load_notifications,
        save_notifications,
    ) -> None:
        load_dashboard_state_cache.return_value = dashboard_state(
            stored_dashboard_result(2, "author")
        )
        load_notifications.return_value = {
            "2": {
                "last_notified_at": "2026-07-14T03:00:00Z",
                "last_notification_kind": "initial",
            },
            "unrelated": {
                "last_notified_at": "2026-07-15T03:00:00Z",
                "last_notification_kind": "follow-up",
            },
        }

        with patch.dict("os.environ", {"SLACK_CHANNEL": "dashboard"}, clear=True):
            errors = notify_slack_from_state(
                "owner/repo",
                None,
                [{"number": 2, "isDraft": False, "title": "Open PR"}],
                datetime(2026, 7, 20, 2, tzinfo=timezone.utc),
                {2},
            )

        self.assertEqual(errors, [])
        save_notifications.assert_called_once_with(
            {
                "unrelated": {
                    "last_notified_at": "2026-07-15T03:00:00Z",
                    "last_notification_kind": "follow-up",
                },
            }
        )

    @patch("notify_slack.next_notifications", return_value=({}, []))
    @patch("notify_slack.save_notifications")
    @patch("notify_slack.load_notifications", return_value={})
    @patch("notify_slack.load_dashboard_state_cache")
    def test_targeted_update_filters_dashboard_results(
        self,
        load_dashboard_state_cache,
        _load_notifications,
        _save_notifications,
        next_notifications,
    ) -> None:
        load_dashboard_state_cache.return_value = dashboard_state(
            stored_dashboard_result(2, "author"),
            stored_dashboard_result(3, "approver"),
        )

        with patch.dict("os.environ", {"SLACK_CHANNEL": "dashboard"}, clear=True):
            errors = notify_slack_from_state(
                "owner/repo",
                None,
                [
                    {"number": 2, "isDraft": False, "title": "Target PR"},
                    {"number": 3, "isDraft": False, "title": "Unrelated PR"},
                ],
                datetime(2026, 7, 20, 2, tzinfo=timezone.utc),
                {2},
            )

        self.assertEqual(errors, [])
        results = next_notifications.call_args.args[1]
        self.assertEqual({2}, {result.pr_number for result in results})

    @patch("notifications.send_slack_notification")
    @patch("notify_slack.save_notifications")
    @patch("notify_slack.load_notifications")
    @patch("notify_slack.load_dashboard_state_cache")
    def test_targeted_update_preserves_uninitialized_notification_state(
        self,
        load_dashboard_state_cache,
        load_notifications,
        save_notifications,
        send_notification,
    ) -> None:
        load_dashboard_state_cache.return_value = dashboard_state(
            stored_dashboard_result(
                2,
                "approver",
                facts=dashboard_facts(
                    reviewers=({"login": "reviewer"},),
                    waiting_since="2026-07-20T01:00:00Z",
                ),
            )
        )
        load_notifications.return_value = None

        with patch.dict(
            "os.environ",
            {
                "SLACK_CHANNEL": "dashboard",
                "SLACK_USER_MAP_JSON": '{"reviewer": "U123"}',
            },
            clear=True,
        ):
            errors = notify_slack_from_state(
                "owner/repo",
                None,
                [{"number": 2, "isDraft": False, "title": "Open PR"}],
                datetime(2026, 7, 20, 2, tzinfo=timezone.utc),
                {2},
            )

        self.assertEqual(errors, [])
        send_notification.assert_not_called()
        save_notifications.assert_called_once_with({})


if __name__ == "__main__":
    unittest.main()