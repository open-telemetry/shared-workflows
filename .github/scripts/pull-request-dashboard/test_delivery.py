from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import ANY, Mock, call, patch

import delivery
import state


class DeliveryTest(unittest.TestCase):
    def test_canceled_full_job_is_drained_by_next_targeted_job(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            accepted = root / "accepted"
            delivery_dir = root / "delivery"
            accepted.mkdir()
            delivery_dir.mkdir()
            with (
                patch.object(state, "_state_dir", delivery_dir),
                patch.object(state, "_accepted_state_dir", accepted),
                patch.object(state, "_using_delivery_state", True),
                patch.object(delivery, "claim_delivery_versions", return_value=True),
                patch.object(delivery.state_branch, "push_state_changes", side_effect=lambda _d, _m, action, **_kw: action()),
                patch.object(delivery, "deliver_from_state", return_value=[]) as deliver_actions,
            ):
                state.write_full_publish_generation(accepted / state.FULL_PUBLISH_NEEDED_FILE, 4)
                output = root / "output"
                self.assertEqual(
                    0,
                    delivery.deliver_with_state(
                        "open-telemetry/example", "state", root, pr_number=7,
                        github_output=output, delivery_state_branch_name="delivery",
                    ),
                )
                self.assertEqual(None, deliver_actions.call_args.args[-1])
                self.assertIn("full_publish_generation=4\n", output.read_text(encoding="utf-8"))

                state.write_full_publish_generation(accepted / state.FULL_PUBLISH_NEEDED_FILE, 5)
                state.record_full_publish_delivered(4)
                self.assertEqual(
                    0,
                    delivery.deliver_with_state(
                        "open-telemetry/example", "state", root, pr_number=8,
                        github_output=output, delivery_state_branch_name="delivery",
                    ),
                )
                self.assertIsNone(deliver_actions.call_args.args[-1])
                state.record_full_publish_delivered(5)
                self.assertEqual(
                    0,
                    delivery.deliver_with_state(
                        "open-telemetry/example", "state", root, pr_number=8,
                        github_output=output, delivery_state_branch_name="delivery",
                    ),
                )
                self.assertEqual(8, deliver_actions.call_args.args[-1])
                self.assertTrue(output.read_text(encoding="utf-8").endswith("full_publish_generation=0\n"))

    def test_failed_full_delivery_keeps_obligation_for_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            accepted = root / "accepted"
            delivery_dir = root / "delivery"
            accepted.mkdir()
            delivery_dir.mkdir()
            state.write_full_publish_generation(accepted / state.FULL_PUBLISH_NEEDED_FILE, 2)
            with (
                patch.object(state, "_state_dir", delivery_dir),
                patch.object(state, "_accepted_state_dir", accepted),
                patch.object(state, "_using_delivery_state", True),
                patch.object(delivery, "claim_delivery_versions", return_value=True),
                patch.object(delivery.state_branch, "push_state_changes", side_effect=lambda _d, _m, action, **_kw: action()),
                patch.object(delivery, "deliver_from_state", side_effect=[["Slack notifications: failed"], []]) as deliver_actions,
            ):
                self.assertEqual(
                    1,
                    delivery.deliver_with_state(
                        "open-telemetry/example", "state", root, pr_number=7,
                        delivery_state_branch_name="delivery",
                    ),
                )
                self.assertEqual(0, state.read_full_publish_generation(state.full_publish_delivered_path()))
                self.assertEqual(
                    0,
                    delivery.deliver_with_state(
                        "open-telemetry/example", "state", root, pr_number=8,
                        delivery_state_branch_name="delivery",
                    ),
                )
                self.assertEqual([None, None], [call.args[-1] for call in deliver_actions.call_args_list])

    def test_full_publish_acknowledgement_requires_compatible_versions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch.object(delivery.state_branch, "temporary_state_dir", return_value=nullcontext(root)),
                patch.object(delivery.state_branch, "push_state_changes", side_effect=lambda _d, _m, action, **_kw: action()),
                patch.object(delivery, "claim_delivery_versions", side_effect=[False, True]),
            ):
                with self.assertRaisesRegex(RuntimeError, "full publish remains pending"):
                    delivery.complete_full_publish("open-telemetry/example", "state", "delivery", 3)
                self.assertEqual(
                    0,
                    delivery.complete_full_publish("open-telemetry/example", "state", "delivery", 3),
                )
                self.assertEqual(
                    3,
                    state.read_full_publish_generation(root / "example" / state.FULL_PUBLISH_DELIVERED_FILE),
                )

    def test_migrates_existing_receipts_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            accepted = root / "accepted"
            delivery_state = root / "delivery"
            accepted.mkdir()
            delivery_state.mkdir()
            for name in delivery.LEGACY_DELIVERY_FILES:
                (accepted / name).write_text(f"{name}\n", encoding="utf-8")

            with patch.object(state, "_state_dir", delivery_state):
                delivery.initialize_delivery_state(accepted)
                (accepted / "notification-state.json").write_text(
                    "new worker value\n",
                    encoding="utf-8",
                )
                delivery.initialize_delivery_state(accepted)

            self.assertEqual(
                "notification-state.json\n",
                (delivery_state / "notification-state.json").read_text(
                    encoding="utf-8"
                ),
            )
            marker = json.loads(
                (delivery_state / state.DELIVERY_STATE_FILE).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(delivery.DELIVERY_STATE_VERSION, marker["version"])
            self.assertTrue(marker["migrated_from_accepted_state"])

    def test_rejects_an_invalid_delivery_state_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            accepted = root / "accepted"
            delivery_state = root / "delivery"
            accepted.mkdir()
            delivery_state.mkdir()
            (delivery_state / state.DELIVERY_STATE_FILE).write_text(
                '{"version":999}\n',
                encoding="utf-8",
            )

            with (
                patch.object(state, "_state_dir", delivery_state),
                self.assertRaisesRegex(RuntimeError, "incompatible shape"),
            ):
                delivery.initialize_delivery_state(accepted)

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
    ) -> None:
        order = Mock()

        def record(label: str) -> list[str]:
            order(label)
            return []

        status_comments.side_effect = lambda *_args, **_kwargs: record("status")
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
            [
                call("replies"),
                call("author"),
                call("status"),
                call("copilot"),
                call("slack"),
            ],
            order.call_args_list,
        )
        status_comments.assert_called_once_with(
            "open-telemetry/example",
            {7, 8},
            set(),
            open_draft_pr_numbers={8},
        )
        author_nudges.assert_called_once_with(
            "open-telemetry/example",
            ANY,
            Path("author"),
            set(),
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

    def test_command_reply_failure_preserves_affected_status_comment(self) -> None:
        def fail_reply(
            _repo: str,
            failed_pr_numbers: set[int],
            _pr_number: int | None,
        ) -> list[str]:
            failed_pr_numbers.add(7)
            return ["PR #7: acknowledgement failed"]

        with (
            patch.object(
                delivery,
                "list_open_prs",
                return_value=[
                    {"number": 7, "isDraft": False, "title": "Seven"},
                    {"number": 8, "isDraft": False, "title": "Eight"},
                ],
            ),
            patch.object(
                delivery,
                "deliver_dashboard_command_replies",
                side_effect=fail_reply,
            ),
            patch.object(
                delivery,
                "deliver_prepared_author_nudges",
                return_value=[],
            ) as author_nudges,
            patch.object(
                delivery,
                "update_status_comments_from_state",
                return_value=[],
            ) as status_comments,
            patch.object(
                delivery,
                "deliver_copilot_review_requests",
                return_value=[],
            ),
            patch.object(delivery, "notify_slack_from_state", return_value=[]),
        ):
            errors = delivery.deliver_from_state(
                "open-telemetry/example",
                Path("author"),
                Path("copilot"),
                Path("slack"),
            )

        self.assertEqual(
            ["dashboard command replies: PR #7: acknowledgement failed"],
            errors,
        )
        status_comments.assert_called_once_with(
            "open-telemetry/example",
            {7, 8},
            {7},
            open_draft_pr_numbers=set(),
        )
        author_nudges.assert_called_once_with(
            "open-telemetry/example",
            ANY,
            Path("author"),
            {7},
        )

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

    def test_open_pr_list_failure_skips_dependent_stages(self) -> None:
        with (
            patch.object(delivery, "list_open_prs", side_effect=RuntimeError("unavailable")),
            patch.object(delivery, "deliver_dashboard_command_replies", return_value=[]) as replies,
            patch.object(delivery, "deliver_prepared_author_nudges", return_value=[]) as nudges,
            patch.object(delivery, "update_status_comments_from_state", return_value=[]) as status,
            patch.object(delivery, "deliver_copilot_review_requests", return_value=[]) as copilot,
            patch.object(
                delivery,
                "notify_slack_from_state",
                return_value=[],
            ) as slack,
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

    def test_unknown_reply_failure_scope_skips_author_nudges(self) -> None:
        with (
            patch.object(
                delivery,
                "list_open_prs",
                side_effect=RuntimeError("open PRs unavailable"),
            ),
            patch.object(
                delivery,
                "deliver_dashboard_command_replies",
                side_effect=RuntimeError("replies unavailable"),
            ),
            patch.object(
                delivery,
                "deliver_prepared_author_nudges",
                return_value=[],
            ) as nudges,
            patch.object(
                delivery,
                "update_status_comments_from_state",
                return_value=[],
            ) as status,
            patch.object(
                delivery,
                "deliver_copilot_review_requests",
                return_value=[],
            ) as copilot,
            patch.object(delivery, "notify_slack_from_state", return_value=[]) as slack,
        ):
            errors = delivery.deliver_from_state(
                "open-telemetry/example",
                Path("author"),
                Path("copilot"),
                Path("slack"),
            )

        self.assertEqual(
            [
                "open pull requests: open PRs unavailable",
                "dashboard command replies: replies unavailable",
            ],
            errors,
        )
        nudges.assert_not_called()
        status.assert_not_called()
        copilot.assert_called_once()
        slack.assert_not_called()

    def test_targeted_delivery_only_processes_triggering_pr(self) -> None:
        with (
            patch.object(delivery, "list_open_prs") as list_open,
            patch.object(
                delivery,
                "gh_api",
                return_value={"state": "open", "draft": False, "title": "Seven"},
            ) as gh_api,
            patch.object(
                delivery,
                "deliver_dashboard_command_replies",
                return_value=[],
            ) as command_replies,
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
        self.assertEqual(7, command_replies.call_args.args[2])
        bulk_status.assert_not_called()
        targeted_status.assert_called_once_with("open-telemetry/example", 7)
        slack.assert_called_once_with(
            "open-telemetry/example",
            ANY,
            [{"number": 7, "isDraft": False, "title": "Seven"}],
            ANY,
            {7},
        )

    def test_targeted_delivery_discards_missing_pr_lookup(self) -> None:
        def fail_reply(
            _repo: str,
            failed_pr_numbers: set[int],
            _pr_number: int | None,
        ) -> list[str]:
            failed_pr_numbers.add(7)
            return ["PR #7: comments lookup failed: not found"]

        with (
            patch.object(
                delivery,
                "gh_api",
                side_effect=delivery.GhNotFoundError("not found"),
            ),
            patch.object(
                delivery,
                "deliver_dashboard_command_replies",
                side_effect=fail_reply,
            ),
            patch.object(
                delivery,
                "deliver_prepared_author_nudges",
                return_value=[],
            ),
            patch.object(
                delivery,
                "update_targeted_status_comment_from_state",
                return_value=[],
            ) as targeted_status,
            patch.object(
                delivery,
                "deliver_copilot_review_requests",
                return_value=[],
            ),
            patch.object(
                delivery,
                "notify_slack_from_state",
                return_value=[],
            ) as slack,
        ):
            errors = delivery.deliver_from_state(
                "open-telemetry/example",
                Path("author"),
                Path("copilot"),
                Path("slack"),
                7,
            )

        self.assertEqual(
            [
                "dashboard command replies: "
                "PR #7: comments lookup failed: not found"
            ],
            errors,
        )
        targeted_status.assert_called_once_with("open-telemetry/example", 7)
        self.assertEqual([], slack.call_args.args[2])

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
            patch.object(state, "_state_dir", Path(temp_dir)),
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
                delivery_state_branch_name=(
                    "otelbot/pull-request-dashboard-delivery/example"
                ),
            )
            github_output_text = github_output.read_text(encoding="utf-8")

        self.assertEqual(1, status)
        self.assertEqual("active=true\nfull_publish_generation=0\n", github_output_text)
        self.assertEqual(
            "otelbot/pull-request-dashboard-delivery/example",
            push_state_changes.call_args.kwargs["state_branch"],
        )
        retry_sources = {
            source
            for source, _destination in (
                push_state_changes.call_args.kwargs["retry_snapshots"]
            )
        }
        self.assertIn(Path("slack"), retry_sources)

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
                "otelbot/pull-request-dashboard-state/example",
                Path("state"),
                github_output=github_output,
            )
            github_output_text = github_output.read_text(encoding="utf-8")

        self.assertEqual(0, status)
        claim_delivery_versions.assert_called_once_with()
        deliver_from_state.assert_not_called()
        self.assertEqual("active=false\nfull_publish_generation=0\n", github_output_text)
        self.assertEqual(
            "otelbot/pull-request-dashboard-delivery/example",
            push_state_changes.call_args.kwargs["state_branch"],
        )


if __name__ == "__main__":
    unittest.main()