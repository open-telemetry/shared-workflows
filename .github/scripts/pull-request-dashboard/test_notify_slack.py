from __future__ import annotations

from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from notifications import next_notifications
from notify_slack import notify_slack_from_state


class NotifySlackTest(unittest.TestCase):
    @patch("notifications.send_slack_notification")
    def test_copilot_route_does_not_notify_reviewers(self, send_notification) -> None:
        results = {
            7: {
                "pr_number": 7,
                "route": "copilot",
                "facts": {
                    "reviewers": [{"login": "reviewer"}],
                    "waiting_since": "2026-07-20T01:00:00Z",
                },
            },
        }

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
        load_dashboard_state_cache.return_value = {
            "prs": {
                "2": {
                    "pr_number": 2,
                    "failed": True,
                    "route": "unknown",
                },
            }
        }
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
        load_dashboard_state_cache.return_value = {
            "prs": {"2": {"pr_number": 2, "route": "author"}}
        }
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
        load_dashboard_state_cache.return_value = {
            "prs": {
                "2": {
                    "pr_number": 2,
                    "route": "approver",
                    "facts": {
                        "reviewers": [{"login": "reviewer"}],
                        "waiting_since": "2026-07-20T01:00:00Z",
                    },
                }
            }
        }
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