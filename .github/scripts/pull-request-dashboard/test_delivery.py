from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import ANY, Mock, call, patch

import delivery


class DeliveryTest(unittest.TestCase):
    @patch.object(delivery, "notify_slack_from_state", return_value=[])
    @patch.object(delivery, "deliver_copilot_review_requests", return_value=[])
    @patch.object(delivery, "deliver_prepared_author_nudges", return_value=[])
    @patch.object(delivery, "deliver_dashboard_command_replies", return_value=[])
    @patch.object(delivery, "deliver_dashboard_override_requests", return_value=[])
    @patch.object(delivery, "update_status_comments_from_state", return_value=[])
    @patch.object(delivery, "list_all_open_pr_numbers", return_value={7, 8})
    def test_runs_all_targeted_deliveries_in_order(
        self,
        _list_open,
        status_comments,
        dashboard_overrides,
        dashboard_command_replies,
        author_nudges,
        copilot_reviews,
        slack,
    ) -> None:
        order = Mock()

        def record(label: str) -> list[str]:
            order(label)
            return []

        status_comments.side_effect = lambda *_args: record("status")
        dashboard_overrides.side_effect = lambda *_args: record("override")
        dashboard_command_replies.side_effect = lambda *_args: record("replies")
        author_nudges.side_effect = lambda *_args: record("author")
        copilot_reviews.side_effect = lambda *_args: record("copilot")
        slack.side_effect = lambda *_args: record("slack")
        errors = delivery.deliver_from_state(
            "open-telemetry/example",
            Path("author"),
            Path("copilot"),
            Path("slack"),
        )

        self.assertEqual([], errors)
        self.assertEqual(
            [call("override"), call("replies"), call("author"), call("status"), call("copilot"), call("slack")],
            order.call_args_list,
        )
        status_comments.assert_called_once_with(
            "open-telemetry/example",
            None,
            {7, 8},
        )
        slack.assert_called_once_with(
            "open-telemetry/example",
            ANY,
            None,
            None,
            ANY,
        )

    @patch.object(delivery, "notify_slack_from_state", return_value=[])
    @patch.object(delivery, "deliver_copilot_review_requests", return_value=[])
    @patch.object(delivery, "deliver_prepared_author_nudges", return_value=[])
    @patch.object(delivery, "deliver_dashboard_command_replies", return_value=[])
    @patch.object(delivery, "deliver_dashboard_override_requests", return_value=[])
    @patch.object(delivery, "update_status_comments_from_state", side_effect=RuntimeError("boom"))
    @patch.object(delivery, "list_all_open_pr_numbers", return_value={7})
    def test_failure_does_not_block_later_deliveries(
        self,
        _list_open,
        _status_comments,
        dashboard_overrides,
        dashboard_command_replies,
        author_nudges,
        copilot_reviews,
        slack,
    ) -> None:
        errors = delivery.deliver_from_state(
            "open-telemetry/example",
            Path("author"),
            Path("copilot"),
            Path("slack"),
        )

        self.assertIn("status comments: boom", errors)
        dashboard_overrides.assert_called_once()
        dashboard_command_replies.assert_called_once()
        author_nudges.assert_called_once()
        copilot_reviews.assert_called_once()
        slack.assert_called_once()

    @patch.object(delivery.sys, "stderr")
    @patch.object(delivery, "deliver_from_state", return_value=["status comments: boom"])
    @patch.object(delivery.state_branch, "push_state_changes")
    def test_reports_delivery_errors_after_state_push(
        self,
        push_state_changes,
        _deliver_from_state,
        _stderr,
    ) -> None:
        push_state_changes.side_effect = (
            lambda _state_dir, _message, update_state, **_kwargs: update_state()
        )

        with (
            patch.object(delivery, "author_nudge_state_path", return_value=Path("author")),
            patch.object(delivery, "copilot_review_request_state_path", return_value=Path("copilot")),
            patch.object(delivery, "notification_state_path", return_value=Path("slack")),
        ):
            status = delivery.deliver_with_state(
                "open-telemetry/example",
                "dashboard-state",
                Path("state"),
            )

        self.assertEqual(1, status)


if __name__ == "__main__":
    unittest.main()