from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, call, patch

import delivery


class DeliveryTest(unittest.TestCase):
    @patch.object(delivery, "notify_slack_from_state", return_value=[])
    @patch.object(delivery, "deliver_copilot_review_requests", return_value=[])
    @patch.object(delivery, "deliver_prepared_author_nudges", return_value=[])
    @patch.object(delivery, "update_status_comments_from_state", return_value=[])
    @patch.object(delivery, "list_all_open_pr_numbers", return_value={7, 8})
    def test_runs_all_targeted_deliveries_in_order(
        self,
        _list_open,
        status_comments,
        author_nudges,
        copilot_reviews,
        slack,
    ) -> None:
        order = Mock()
        status_comments.side_effect = lambda *_args: order("status") or []
        author_nudges.side_effect = lambda *_args: order("author") or []
        copilot_reviews.side_effect = lambda *_args: order("copilot") or []
        slack.side_effect = lambda *_args: order("slack") or []
        with tempfile.TemporaryDirectory() as temp_dir:
            errors_file = Path(temp_dir) / "errors"
            status = delivery.deliver_from_state(
                "open-telemetry/example",
                7,
                "initial",
                Path(temp_dir) / "author",
                Path(temp_dir) / "copilot",
                Path(temp_dir) / "slack",
                errors_file,
            )

        self.assertEqual(0, status)
        self.assertEqual(
            [call("status"), call("author"), call("copilot"), call("slack")],
            order.call_args_list,
        )

    @patch.object(delivery, "notify_slack_from_state", return_value=[])
    @patch.object(delivery, "deliver_copilot_review_requests", return_value=[])
    @patch.object(delivery, "deliver_prepared_author_nudges", return_value=[])
    @patch.object(delivery, "update_status_comments_from_state", side_effect=RuntimeError("boom"))
    @patch.object(delivery, "list_all_open_pr_numbers", return_value={7})
    def test_failure_does_not_block_later_deliveries(
        self,
        _list_open,
        _status_comments,
        author_nudges,
        copilot_reviews,
        slack,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            errors_file = Path(temp_dir) / "errors"
            delivery.deliver_from_state(
                "open-telemetry/example",
                None,
                "follow-up",
                Path(temp_dir) / "author",
                Path(temp_dir) / "copilot",
                Path(temp_dir) / "slack",
                errors_file,
            )
            errors = errors_file.read_text(encoding="utf-8")

        self.assertIn("status comments: boom", errors)
        author_nudges.assert_called_once()
        copilot_reviews.assert_called_once()
        slack.assert_called_once()


if __name__ == "__main__":
    unittest.main()