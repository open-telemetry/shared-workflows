from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import ANY, Mock, call, patch

import delivery


class DeliveryTest(unittest.TestCase):
    @patch.object(delivery, "report_stalled_gates", return_value=[])
    @patch.object(delivery, "notify_slack_from_state", return_value=[])
    @patch.object(delivery, "deliver_copilot_review_requests", return_value=[])
    @patch.object(delivery, "deliver_prepared_author_nudges", return_value=[])
    @patch.object(delivery, "deliver_dashboard_command_replies", return_value=[])
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
        dashboard_command_replies,
        author_nudges,
        copilot_reviews,
        slack,
        stalled_gates,
    ) -> None:
        order = Mock()

        def record(label: str) -> list[str]:
            order(label)
            return []

        status_comments.side_effect = lambda *_args: record("status")
        dashboard_command_replies.side_effect = lambda *_args: record("replies")
        author_nudges.side_effect = lambda *_args: record("author")
        copilot_reviews.side_effect = lambda *_args: record("copilot")
        slack.side_effect = lambda *_args: record("slack")
        stalled_gates.side_effect = lambda *_args: record("stalled")
        errors = delivery.deliver_from_state(
            "open-telemetry/example",
            Path("author"),
            Path("copilot"),
            Path("slack"),
        )

        self.assertEqual([], errors)
        _list_open.assert_called_once_with("open-telemetry/example")
        self.assertEqual(
            [
                call("replies"),
                call("author"),
                call("status"),
                call("copilot"),
                call("slack"),
                call("stalled"),
            ],
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
        stalled_gates.assert_called_once_with({7, 8})

    @patch.object(delivery, "report_stalled_gates", return_value=[])
    @patch.object(delivery, "notify_slack_from_state", return_value=[])
    @patch.object(delivery, "deliver_copilot_review_requests", return_value=[])
    @patch.object(delivery, "deliver_prepared_author_nudges", return_value=[])
    @patch.object(delivery, "deliver_dashboard_command_replies", return_value=[])
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
        dashboard_command_replies,
        author_nudges,
        copilot_reviews,
        slack,
        stalled_gates,
    ) -> None:
        errors = delivery.deliver_from_state(
            "open-telemetry/example",
            Path("author"),
            Path("copilot"),
            Path("slack"),
        )

        self.assertIn("status comments: boom", errors)
        dashboard_command_replies.assert_called_once()
        author_nudges.assert_called_once()
        copilot_reviews.assert_called_once()
        slack.assert_called_once()
        stalled_gates.assert_called_once_with({7})

    def test_open_pr_list_failure_skips_dependent_stages(self) -> None:
        with (
            patch.object(delivery, "list_open_prs", side_effect=RuntimeError("unavailable")),
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
        replies.assert_called_once()
        nudges.assert_called_once()
        copilot.assert_called_once()
        status.assert_not_called()
        slack.assert_not_called()

    def test_targeted_delivery_only_processes_triggering_pr(self) -> None:
        with (
            patch.object(delivery, "list_open_prs") as list_open,
            patch.object(
                delivery,
                "gh_api",
                return_value={"state": "open", "draft": False, "title": "Seven"},
            ) as gh_api,
            patch.object(delivery, "deliver_dashboard_command_replies", return_value=[]),
            patch.object(delivery, "deliver_prepared_author_nudges", return_value=[]),
            patch.object(delivery, "update_status_comments_from_state") as bulk_status,
            patch.object(
                delivery,
                "update_targeted_status_comment_from_state",
                return_value=[],
            ) as targeted_status,
            patch.object(delivery, "deliver_copilot_review_requests", return_value=[]),
            patch.object(delivery, "notify_slack_from_state", return_value=[]) as slack,
        ):
            errors = delivery.deliver_from_state(
                "open-telemetry/example",
                Path("author"),
                Path("copilot"),
                Path("slack"),
                7,
            )

        self.assertEqual([], errors)
        list_open.assert_not_called()
        gh_api.assert_called_once_with("/repos/open-telemetry/example/pulls/7")
        bulk_status.assert_not_called()
        targeted_status.assert_called_once_with("open-telemetry/example", 7)
        slack.assert_called_once_with(
            "open-telemetry/example",
            ANY,
            [{"number": 7, "isDraft": False, "title": "Seven"}],
            ANY,
            {7},
        )

    def test_a_stalled_gate_is_reported_when_the_whole_repository_runs(self) -> None:
        state = {
            "prs": {
                "7": {
                    "facts": {
                        "route_hold_expired": True,
                        "copilot_review_outstanding": True,
                        "copilot_review_unreported": True,
                        "required_checks_settled": True,
                        "head_sha": "abc",
                    }
                },
                "8": {"facts": {"route_held_for_gates": True}},
            }
        }

        with patch.object(delivery, "load_dashboard_state_cache", return_value=state):
            errors = delivery.report_stalled_gates({7, 8})

        self.assertEqual(
            ["PR #7: the Copilot review never reported on head abc"],
            errors,
        )

    def test_a_copilot_review_that_reported_is_not_named_as_the_stall(self) -> None:
        # The checks are what went missing. Copilot answered on this head, so
        # naming it would send the reader after a gate that is not missing.
        state = {
            "prs": {
                "7": {
                    "facts": {
                        "route_hold_expired": True,
                        "copilot_review_outstanding": True,
                        "copilot_review_unreported": False,
                        "required_checks_settled": False,
                        "head_sha": "abc",
                    }
                }
            }
        }

        with patch.object(delivery, "load_dashboard_state_cache", return_value=state):
            errors = delivery.report_stalled_gates({7})

        self.assertEqual(
            ["PR #7: the required status checks never reported on head abc"],
            errors,
        )

    def test_an_expired_hold_with_every_gate_reported_is_not_reported(self) -> None:
        state = {
            "prs": {
                "7": {
                    "facts": {
                        "route_hold_expired": True,
                        "copilot_review_unreported": False,
                        "required_checks_settled": True,
                        "head_sha": "abc",
                    }
                }
            }
        }

        with patch.object(delivery, "load_dashboard_state_cache", return_value=state):
            errors = delivery.report_stalled_gates({7})

        self.assertEqual([], errors)

    def test_a_stalled_gate_on_a_closed_pr_is_not_reported(self) -> None:
        state = {"prs": {"7": {"facts": {"route_hold_expired": True}}}}

        with patch.object(delivery, "load_dashboard_state_cache", return_value=state):
            errors = delivery.report_stalled_gates(set())

        self.assertEqual([], errors)

    def test_a_targeted_delivery_does_not_report_stalled_gates(self) -> None:
        with (
            patch.object(
                delivery,
                "gh_api",
                return_value={"state": "open", "draft": False, "title": "Seven"},
            ),
            patch.object(delivery, "deliver_dashboard_command_replies", return_value=[]),
            patch.object(delivery, "deliver_prepared_author_nudges", return_value=[]),
            patch.object(
                delivery,
                "update_targeted_status_comment_from_state",
                return_value=[],
            ),
            patch.object(delivery, "deliver_copilot_review_requests", return_value=[]),
            patch.object(delivery, "notify_slack_from_state", return_value=[]),
            patch.object(delivery, "report_stalled_gates", return_value=[]) as stalled,
        ):
            delivery.deliver_from_state(
                "open-telemetry/example",
                Path("author"),
                Path("copilot"),
                Path("slack"),
                7,
            )

        stalled.assert_not_called()

    @patch.object(delivery.sys, "stderr")
    @patch.object(delivery, "deliver_from_state", return_value=["status comments: boom"])
    @patch.object(delivery, "claim_delivery_versions", return_value=True)
    @patch.object(delivery.state_branch, "push_state_changes")
    def test_reports_delivery_errors_after_state_push(
        self,
        push_state_changes,
        _claim_delivery_versions,
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
                github_output=github_output,
            )
            github_output_text = github_output.read_text(encoding="utf-8")

        self.assertEqual(1, status)
        self.assertEqual("active=true\n", github_output_text)

    @patch.object(delivery, "deliver_from_state")
    @patch.object(delivery, "claim_delivery_versions", return_value=False)
    @patch.object(delivery.state_branch, "push_state_changes")
    def test_stale_versions_skip_delivery_and_report_inactive(
        self,
        push_state_changes,
        claim_delivery_versions,
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
                github_output=github_output,
            )
            github_output_text = github_output.read_text(encoding="utf-8")

        self.assertEqual(0, status)
        claim_delivery_versions.assert_called_once_with()
        deliver_from_state.assert_not_called()
        self.assertEqual("active=false\n", github_output_text)


if __name__ == "__main__":
    unittest.main()