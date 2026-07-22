from __future__ import annotations

from pathlib import Path
import tempfile
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
    @patch.object(
        delivery,
        "list_open_prs",
        return_value=[
            {"number": 7, "isDraft": False, "title": "Seven"},
            {"number": 8, "isDraft": True, "title": "Eight"},
        ],
    )
    def test_runs_all_repository_deliveries_in_order(
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
        _list_open.assert_called_once_with("open-telemetry/example")
        self.assertEqual(
            [call("override"), call("replies"), call("author"), call("status"), call("copilot"), call("slack")],
            order.call_args_list,
        )
        status_comments.assert_called_once_with(
            "open-telemetry/example",
            {7, 8},
        )
        slack.assert_called_once_with(
            "open-telemetry/example",
            ANY,
            [
                {"number": 7, "isDraft": False, "title": "Seven"},
                {"number": 8, "isDraft": True, "title": "Eight"},
            ],
            ANY,
        )

    @patch.object(delivery, "notify_slack_from_state", return_value=[])
    @patch.object(delivery, "deliver_copilot_review_requests", return_value=[])
    @patch.object(delivery, "deliver_prepared_author_nudges", return_value=[])
    @patch.object(delivery, "deliver_dashboard_command_replies", return_value=[])
    @patch.object(delivery, "deliver_dashboard_override_requests", return_value=[])
    @patch.object(delivery, "update_status_comments_from_state", side_effect=RuntimeError("boom"))
    @patch.object(
        delivery,
        "list_open_prs",
        return_value=[{"number": 7, "isDraft": False, "title": "Seven"}],
    )
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

    def test_open_pr_list_failure_skips_dependent_stages(self) -> None:
        with (
            patch.object(delivery, "list_open_prs", side_effect=RuntimeError("unavailable")),
            patch.object(delivery, "deliver_dashboard_override_requests", return_value=[]) as overrides,
            patch.object(delivery, "deliver_dashboard_command_replies", return_value=[]) as replies,
            patch.object(delivery, "deliver_prepared_author_nudges", return_value=[]) as nudges,
            patch.object(delivery, "update_status_comments_from_state", return_value=[]) as status,
            patch.object(delivery, "deliver_copilot_review_requests", return_value=[]) as copilot,
            patch.object(delivery, "notify_slack_from_state", return_value=[]) as slack,
        ):
            errors = delivery.deliver_from_state(
                "open-telemetry/example",
                Path("author"),
                Path("copilot"),
                Path("slack"),
            )

        self.assertEqual(["open pull requests: unavailable"], errors)
        overrides.assert_called_once()
        replies.assert_called_once()
        nudges.assert_called_once()
        copilot.assert_called_once()
        status.assert_not_called()
        slack.assert_not_called()

    @patch.object(delivery.sys, "stderr")
    @patch.object(delivery, "deliver_from_state", return_value=["status comments: boom"])
    @patch.object(delivery, "claim_delivery_revision", return_value=True)
    @patch.object(delivery.state_branch, "push_state_changes")
    def test_reports_delivery_errors_after_state_push(
        self,
        push_state_changes,
        _claim_delivery_revision,
        _deliver_from_state,
        _stderr,
    ) -> None:
        push_state_changes.side_effect = (
            lambda _state_dir, _message, update_state, **_kwargs: update_state()
        )

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(delivery, "author_nudge_state_path", return_value=Path("author")),
            patch.object(delivery, "copilot_review_request_state_path", return_value=Path("copilot")),
            patch.object(delivery, "notification_state_path", return_value=Path("slack")),
        ):
            github_output = Path(temp_dir) / "github-output"
            status = delivery.deliver_with_state(
                "open-telemetry/example",
                "dashboard-state",
                Path("state"),
                github_output,
            )
            github_output_text = github_output.read_text(encoding="utf-8")

        self.assertEqual(1, status)
        self.assertEqual("active=true\n", github_output_text)

    @patch.object(delivery, "deliver_from_state")
    @patch.object(delivery, "claim_delivery_revision", return_value=False)
    @patch.object(delivery.state_branch, "push_state_changes")
    def test_stale_revision_skips_delivery_and_reports_inactive(
        self,
        push_state_changes,
        claim_delivery_revision,
        deliver_from_state,
    ) -> None:
        push_state_changes.side_effect = (
            lambda _state_dir, _message, update_state, **_kwargs: update_state()
        )

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(delivery, "author_nudge_state_path", return_value=Path("author")),
            patch.object(delivery, "copilot_review_request_state_path", return_value=Path("copilot")),
            patch.object(delivery, "notification_state_path", return_value=Path("slack")),
        ):
            github_output = Path(temp_dir) / "github-output"
            status = delivery.deliver_with_state(
                "open-telemetry/example",
                "dashboard-state",
                Path("state"),
                github_output,
            )
            github_output_text = github_output.read_text(encoding="utf-8")

        self.assertEqual(0, status)
        claim_delivery_revision.assert_called_once_with()
        deliver_from_state.assert_not_called()
        self.assertEqual("active=false\n", github_output_text)


if __name__ == "__main__":
    unittest.main()