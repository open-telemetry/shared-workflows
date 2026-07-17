from __future__ import annotations

import argparse
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from notify_slack import notify_slack_from_state, notify_slack_with_state


class NotifySlackTest(unittest.TestCase):
    @patch(
        "notify_slack.notification_state_path",
        return_value=Path("state/notification-state.json"),
    )
    @patch("notify_slack.notify_slack", return_value=0)
    @patch("notify_slack.state_branch.push_state_changes")
    def test_scheduled_run_uses_only_refreshed_pr_numbers(
        self,
        push_state_changes,
        notify_slack,
        _notification_state_path,
    ) -> None:
        push_state_changes.side_effect = lambda _state_dir, _message, update, **_kwargs: update()
        args = argparse.Namespace(
            repo="owner/repo",
            state_branch="dashboard-state",
            pr_number=None,
            pr_numbers="2,5",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict("os.environ", {"RUNNER_TEMP": temp_dir}):
                status = notify_slack_with_state(args, Path(temp_dir) / "state")

        self.assertEqual(status, 0)
        self.assertEqual(notify_slack.call_args.args[3], {2, 5})
        self.assertEqual(notify_slack.call_args.args[4], {"follow-up"})

    @patch("notify_slack.save_notifications")
    @patch("notify_slack.load_notifications")
    @patch("notify_slack.list_open_prs")
    @patch("notify_slack.load_dashboard_state_cache")
    def test_uncached_pr_does_not_pause_notifications_and_closed_state_is_pruned(
        self,
        load_dashboard_state_cache,
        list_open_prs,
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
        list_open_prs.return_value = [
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
            errors = notify_slack_from_state("owner/repo", None, None, None)

        self.assertEqual(errors, [])
        save_notifications.assert_called_once_with(
            {
                "2": {
                    "last_notified_at": "2026-07-14T03:00:00Z",
                    "last_notification_kind": "initial",
                },
            }
        )


if __name__ == "__main__":
    unittest.main()