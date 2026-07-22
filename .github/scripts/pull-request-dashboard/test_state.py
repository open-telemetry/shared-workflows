from __future__ import annotations

from contextlib import nullcontext, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from state import (
    AUTHOR_NUDGE_STATE_VERSION,
    BACKFILL_STATE_VERSION,
    COPILOT_REVIEW_REQUEST_STATE_VERSION,
    DASHBOARD_STATE_VERSION,
    DELIVERY_REVISION_STATE_VERSION,
    NOTIFICATION_STATE_VERSION,
    STATUS_COMMENT_ROLLOUT_STATE_VERSION,
    author_nudge_state_path,
    backfill_state_path,
    copilot_review_request_state_path,
    claim_delivery_revision,
    dashboard_state_path,
    empty_state,
    enqueue_status_comment_update,
    load_accepted_dashboard_state,
    load_author_nudges,
    load_backfill_state,
    load_copilot_review_requests,
    load_dashboard_state_cache,
    load_delivery_revision_state,
    load_state_file,
    load_status_comment_rollout_state,
    load_notifications,
    main,
    notification_state_path,
    save_state_file,
    save_author_nudges,
    save_copilot_review_requests,
    save_dashboard_state_cache,
    save_notifications,
    save_status_comment_rollout_state,
    stored_result,
    union_merge_author_nudges,
    union_merge_copilot_review_requests,
    update_dashboard_state_for_pr,
)


class StateTest(unittest.TestCase):
    @patch(
        "state.load_accepted_dashboard_state",
        return_value={"initial_backfill_complete": True, "prs": {}},
    )
    def test_cli_prints_initial_backfill_readiness(self, load_state: object) -> None:
        output = StringIO()
        with (
            patch("sys.argv", [
                "state.py",
                "--repo", "example",
                "--state-branch", "state-branch",
            ]),
            redirect_stdout(output),
        ):
            status = main()

        self.assertEqual(0, status)
        self.assertEqual("true\n", output.getvalue())
        load_state.assert_called_once_with("open-telemetry/example", "state-branch")

    def test_loads_accepted_dashboard_state_from_state_branch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            checkout_dir = Path(temp_dir)
            state_path = checkout_dir / "example" / "dashboard-state.json"
            state_path.parent.mkdir()
            state_path.write_text(
                json.dumps({
                    "version": DASHBOARD_STATE_VERSION,
                    "initial_backfill_complete": True,
                    "prs": {"123": {"route": "author"}},
                }),
                encoding="utf-8",
            )
            with patch(
                "state.state_branch.accepted_state_dir",
                return_value=nullcontext(checkout_dir),
            ) as accepted_state_dir:
                dashboard_state = load_accepted_dashboard_state(
                    "open-telemetry/example",
                    "state-branch",
                )

        self.assertEqual(
            dashboard_state,
            {
                "version": DASHBOARD_STATE_VERSION,
                "initial_backfill_complete": True,
                "prs": {"123": {"route": "author"}},
            },
        )
        accepted_state_dir.assert_called_once_with("state-branch", required=False)

    def test_versioned_state_helpers_preserve_arbitrary_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "state.json"

            save_state_file(
                path,
                {"cursor": {"last_pr_number": 78}, "_runtime_only": True},
                9,
            )

            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")),
                {"cursor": {"last_pr_number": 78}, "version": 9},
            )
            self.assertEqual(
                load_state_file(path, 9),
                {"cursor": {"last_pr_number": 78}, "version": 9},
            )

    def test_state_specific_loaders_own_payload_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("state._state_dir", Path(temp_dir)):
            dashboard_state_path().write_text(
                json.dumps({"version": DASHBOARD_STATE_VERSION, "unknown": "discard me"}),
                encoding="utf-8",
            )
            notification_state_path().write_text(
                json.dumps({"version": NOTIFICATION_STATE_VERSION}),
                encoding="utf-8",
            )
            backfill_state_path().write_text(
                json.dumps({"version": BACKFILL_STATE_VERSION}),
                encoding="utf-8",
            )

            self.assertEqual(
                load_dashboard_state_cache(),
                {
                    "version": DASHBOARD_STATE_VERSION,
                    "initial_backfill_complete": False,
                    "prs": {},
                },
            )
            self.assertEqual(load_notifications(), {})
            self.assertEqual(
                load_backfill_state(),
                {"version": BACKFILL_STATE_VERSION, "cursor": {}},
            )

    def test_dashboard_state_save_writes_explicit_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("state._state_dir", Path(temp_dir)):
            save_dashboard_state_cache({"unknown": "discard me"})

            self.assertEqual(
                json.loads(dashboard_state_path().read_text(encoding="utf-8")),
                {
                    "version": DASHBOARD_STATE_VERSION,
                    "initial_backfill_complete": False,
                    "prs": {},
                },
            )

    def test_notification_state_version_is_independent(self) -> None:
        self.assertEqual(BACKFILL_STATE_VERSION, 3)
        self.assertEqual(NOTIFICATION_STATE_VERSION, 3)
        self.assertEqual(DASHBOARD_STATE_VERSION, 5)
        self.assertEqual(STATUS_COMMENT_ROLLOUT_STATE_VERSION, 1)
        self.assertEqual(AUTHOR_NUDGE_STATE_VERSION, 2)
        self.assertEqual(COPILOT_REVIEW_REQUEST_STATE_VERSION, 3)

    def test_author_nudge_state_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("state._state_dir", Path(temp_dir)):
            save_author_nudges({
                "123": {
                    "waiting_since": "2026-07-10T00:00:00Z",
                    "nudged_at": "",
                }
            })

            self.assertEqual(
                load_author_nudges(),
                {
                    "123": {
                        "waiting_since": "2026-07-10T00:00:00Z",
                        "nudged_at": "",
                    }
                },
            )
            self.assertTrue(author_nudge_state_path().exists())

    def test_copilot_review_request_state_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("state._state_dir", Path(temp_dir)):
            save_copilot_review_requests({
                "123": {
                    "head_sha": "current-head",
                    "observed_at": "2026-07-20T01:00:00Z",
                    "requested_at": "",
                    "routing_input_fingerprint": "accepted-fingerprint",
                }
            })

            self.assertEqual(
                load_copilot_review_requests(),
                {
                    "123": {
                        "head_sha": "current-head",
                        "observed_at": "2026-07-20T01:00:00Z",
                        "requested_at": "",
                        "routing_input_fingerprint": "accepted-fingerprint",
                    }
                },
            )
            self.assertTrue(copilot_review_request_state_path().exists())

    def test_retry_snapshot_preserves_posted_author_nudge(self) -> None:
        self.assertEqual(
            {
                "7": {
                    "waiting_since": "2026-07-10T02:00:00Z",
                    "nudged_at": "2026-07-20T02:00:00Z",
                }
            },
            union_merge_author_nudges(
                {"7": {"waiting_since": "2026-07-10T02:00:00Z", "nudged_at": ""}},
                {
                    "7": {
                        "waiting_since": "2026-07-10T02:00:00Z",
                        "nudged_at": "2026-07-20T02:00:00Z",
                    }
                },
            ),
        )

    def test_retry_snapshot_does_not_suppress_new_author_episode(self) -> None:
        self.assertEqual(
            {"7": {"waiting_since": "2026-07-20T02:00:00Z", "nudged_at": ""}},
            union_merge_author_nudges(
                {"7": {"waiting_since": "2026-07-20T02:00:00Z", "nudged_at": ""}},
                {
                    "7": {
                        "waiting_since": "2026-07-10T02:00:00Z",
                        "nudged_at": "2026-07-17T02:00:00Z",
                    }
                },
            ),
        )

    def test_retry_snapshot_preserves_same_head_copilot_request(self) -> None:
        self.assertEqual(
            {
                "7": {
                    "head_sha": "current-head",
                    "observed_at": "2026-07-20T01:00:00Z",
                    "requested_at": "2026-07-20T02:00:00Z",
                    "routing_input_fingerprint": "accepted-fingerprint",
                }
            },
            union_merge_copilot_review_requests(
                {
                    "7": {
                        "head_sha": "current-head",
                        "observed_at": "2026-07-20T01:00:00Z",
                        "requested_at": "",
                        "routing_input_fingerprint": "accepted-fingerprint",
                    }
                },
                {
                    "7": {
                        "head_sha": "current-head",
                        "observed_at": "2026-07-20T01:00:00Z",
                        "requested_at": "2026-07-20T02:00:00Z",
                        "routing_input_fingerprint": "accepted-fingerprint",
                    }
                },
            ),
        )

    def test_retry_snapshot_does_not_overwrite_new_copilot_head(self) -> None:
        self.assertEqual(
            {
                "7": {
                    "head_sha": "new-head",
                    "observed_at": "2026-07-20T03:00:00Z",
                    "requested_at": "",
                    "routing_input_fingerprint": "new-fingerprint",
                }
            },
            union_merge_copilot_review_requests(
                {
                    "7": {
                        "head_sha": "new-head",
                        "observed_at": "2026-07-20T03:00:00Z",
                        "requested_at": "",
                        "routing_input_fingerprint": "new-fingerprint",
                    }
                },
                {
                    "7": {
                        "head_sha": "old-head",
                        "observed_at": "2026-07-20T01:00:00Z",
                        "requested_at": "2026-07-20T02:00:00Z",
                        "routing_input_fingerprint": "old-fingerprint",
                    }
                },
            ),
        )

    def test_retry_snapshot_does_not_suppress_new_same_head_request(self) -> None:
        pending = {
            "7": {
                "head_sha": "current-head",
                "observed_at": "2026-07-20T03:00:00Z",
                "requested_at": "",
                "routing_input_fingerprint": "new-fingerprint",
            }
        }

        self.assertEqual(
            pending,
            union_merge_copilot_review_requests(
                pending,
                {
                    "7": {
                        "head_sha": "current-head",
                        "observed_at": "2026-07-20T01:00:00Z",
                        "requested_at": "2026-07-20T02:00:00Z",
                        "routing_input_fingerprint": "old-fingerprint",
                    }
                },
            ),
        )

    def test_status_comment_rollout_state_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("state._state_dir", Path(temp_dir)):
            save_status_comment_rollout_state({
                "target_revision": 2,
                "completed_revision": 1,
                "pending_pr_numbers": [34, 12, 34],
            })

            self.assertEqual(
                load_status_comment_rollout_state(),
                {
                    "version": STATUS_COMMENT_ROLLOUT_STATE_VERSION,
                    "target_revision": 2,
                    "completed_revision": 1,
                    "pending_pr_numbers": [12, 34],
                },
            )

    def test_enqueue_status_comment_update_is_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("state._state_dir", Path(temp_dir)):
            enqueue_status_comment_update(34)
            enqueue_status_comment_update(12)
            enqueue_status_comment_update(34)

            self.assertEqual(
                [12, 34],
                load_status_comment_rollout_state()["pending_pr_numbers"],
            )

    def test_delivery_revision_only_advances(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("state._state_dir", Path(temp_dir)):
            with patch("state.DELIVERY_REVISION", 1):
                self.assertTrue(claim_delivery_revision())
                self.assertTrue(claim_delivery_revision())
            with patch("state.DELIVERY_REVISION", 2):
                self.assertTrue(claim_delivery_revision())
            with patch("state.DELIVERY_REVISION", 1):
                self.assertFalse(claim_delivery_revision())

            self.assertEqual(
                {
                    "version": DELIVERY_REVISION_STATE_VERSION,
                    "active_revision": 2,
                },
                load_delivery_revision_state(),
            )

    def test_delivery_revision_state_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("state._state_dir", Path(temp_dir)):
            delivery_state = Path(temp_dir) / "delivery-revision-state.json"
            delivery_state.write_text("not json", encoding="utf-8")

            self.assertFalse(claim_delivery_revision())

    def test_backfill_state_preserves_version_three_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("state._state_dir", Path(temp_dir)):
            backfill_state_path().write_text(
                json.dumps(
                    {
                        "version": 3,
                        "cursor": {"last_pr_number": 78},
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(
                load_backfill_state(),
                {
                    "version": BACKFILL_STATE_VERSION,
                    "cursor": {"last_pr_number": 78},
                },
            )

    def test_targeted_update_preserves_initial_backfill_marker(self) -> None:
        state = empty_state()
        state["initial_backfill_complete"] = True
        state["unknown"] = "discard me"

        updated = update_dashboard_state_for_pr(state, 123, None)

        self.assertEqual(
            updated,
            {
                "version": DASHBOARD_STATE_VERSION,
                "initial_backfill_complete": True,
                "prs": {},
            },
        )

    def test_notification_state_write_ignores_dashboard_version(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch("state._state_dir", Path(temp_dir)),
            patch("state.DASHBOARD_STATE_VERSION", 4),
        ):
            save_notifications({"123": {"last_notified_at": "2026-07-14T03:00:00Z"}})

            state = json.loads(notification_state_path().read_text(encoding="utf-8"))
            self.assertEqual(state["version"], NOTIFICATION_STATE_VERSION)

    def test_stored_result_preserves_top_level_history(self) -> None:
        result = stored_result(
            {
                "pr_number": 123,
                "pending_actions": {
                    "inline-thread": {
                        "action": "author",
                        "since": "2026-07-14T02:00:00Z",
                    },
                },
                "top_level_history": {
                    "pr-review-456": {
                        "evidence": {
                            "commit": "2026-07-14T03:00:00Z",
                            "description": "2026-07-14T04:00:00Z",
                        },
                    },
                },
            }
        )

        self.assertEqual(
            result["top_level_history"],
            {
                "pr-review-456": {
                    "evidence": {
                        "commit": "2026-07-14T03:00:00Z",
                        "description": "2026-07-14T04:00:00Z",
                    },
                },
            },
        )
        self.assertNotIn("pending_actions", result)

    def test_notification_state_survives_dashboard_version_bump(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("state._state_dir", Path(temp_dir)):
            notification_state_path().write_text(
                json.dumps(
                    {
                        "version": 3,
                        "prs": {
                            "123": {
                                "last_notified_at": "2026-07-14T03:00:00Z",
                                "last_notification_kind": "initial",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(
                load_notifications(),
                {
                    "123": {
                        "last_notified_at": "2026-07-14T03:00:00Z",
                        "last_notification_kind": "initial",
                    }
                },
            )


if __name__ == "__main__":
    unittest.main()