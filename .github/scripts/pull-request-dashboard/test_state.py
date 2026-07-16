from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from state import (
    BACKFILL_STATE_VERSION,
    DASHBOARD_STATE_VERSION,
    NOTIFICATION_STATE_VERSION,
    backfill_state_path,
    dashboard_state_path,
    load_backfill_state,
    load_dashboard_state_cache,
    load_state_file,
    load_notifications,
    notification_state_path,
    save_state_file,
    save_notifications,
    stored_result,
)


class StateTest(unittest.TestCase):
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
                json.dumps({"version": DASHBOARD_STATE_VERSION}),
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
                {"version": DASHBOARD_STATE_VERSION, "prs": {}},
            )
            self.assertEqual(load_notifications(), {})
            self.assertEqual(
                load_backfill_state(),
                {"version": BACKFILL_STATE_VERSION, "cursor": {}},
            )

    def test_notification_state_version_is_independent(self) -> None:
        self.assertEqual(BACKFILL_STATE_VERSION, 3)
        self.assertEqual(NOTIFICATION_STATE_VERSION, 3)
        self.assertEqual(DASHBOARD_STATE_VERSION, 4)

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

    def test_notification_state_write_ignores_dashboard_version(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch("state._state_dir", Path(temp_dir)),
            patch("state.DASHBOARD_STATE_VERSION", 4),
        ):
            save_notifications({"123": {"last_notified_at": "2026-07-14T03:00:00Z"}})

            state = json.loads(notification_state_path().read_text(encoding="utf-8"))
            self.assertEqual(state["version"], NOTIFICATION_STATE_VERSION)

    def test_stored_result_preserves_mainline_history(self) -> None:
        result = stored_result(
            {
                "pr_number": 123,
                "pending_actions": {
                    "inline-thread": {
                        "action": "author",
                        "since": "2026-07-14T02:00:00Z",
                    },
                },
                "mainline_history": {
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
            result["mainline_history"],
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