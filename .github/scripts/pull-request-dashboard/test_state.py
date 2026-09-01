from __future__ import annotations

from contextlib import nullcontext, redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dashboard_contracts import (
    DashboardCommandReply,
    DashboardRoute,
    DashboardState,
)
from dashboard_test_support import (
    dashboard_facts,
    dashboard_state,
    evaluation_success,
    stored_dashboard_result,
)
from state import (
    AUTHOR_NUDGE_STATE_VERSION,
    BACKFILL_STATE_VERSION,
    COPILOT_REVIEW_REQUEST_STATE_VERSION,
    DASHBOARD_STATE_VERSION,
    NOTIFICATION_STATE_VERSION,
    STATUS_COMMENT_ROLLOUT_STATE_VERSION,
    author_nudge_state_path,
    backfill_state_path,
    copilot_review_request_state_path,
    claim_delivery_versions,
    current_delivery_versions,
    dashboard_state_path,
    decode_dashboard_facts,
    decode_dashboard_state,
    decode_stored_result,
    empty_state,
    encode_dashboard_facts,
    encode_dashboard_state,
    encode_stored_result,
    enqueue_status_comment_update,
    load_accepted_dashboard_state,
    load_author_nudges,
    load_backfill_state,
    load_copilot_review_requests,
    load_dashboard_state_cache,
    load_delivery_versions,
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
        return_value=DashboardState(initial_backfill_complete=True),
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
            DashboardState(
                initial_backfill_complete=True,
                results=(
                    decode_stored_result(
                        {"route": "author"},
                        pr_number_hint=123,
                    ),
                ),
            ),
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
                DashboardState(),
            )
            self.assertEqual(load_notifications(), {})
            self.assertEqual(
                load_backfill_state(),
                {"version": BACKFILL_STATE_VERSION, "cursor": {}},
            )

    def test_dashboard_state_save_writes_explicit_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("state._state_dir", Path(temp_dir)):
            save_dashboard_state_cache(DashboardState())

            self.assertEqual(
                json.loads(dashboard_state_path().read_text(encoding="utf-8")),
                {
                    "version": DASHBOARD_STATE_VERSION,
                    "initial_backfill_complete": False,
                    "draft_pr_numbers": [],
                    "prs": {},
                },
            )

    def test_dashboard_facts_codec_round_trip(self) -> None:
        facts = dashboard_facts(
            author="alice",
            assignees=("alice", "reviewer"),
            head_sha="current-head",
            routing_input_fingerprint="routing-fingerprint",
            copilot_request_fingerprint="copilot-fingerprint",
            dashboard_override_command_id=91,
            dashboard_override_command_user="alice",
            dashboard_override_bound_command_id=91,
            dashboard_override_head_sha="current-head",
            dashboard_override_since="2026-08-16T08:00:00Z",
            dashboard_override_cleared_by_feedback=True,
            dashboard_command_replies=(
                DashboardCommandReply(
                    91,
                    "routed",
                    "alice",
                    head_sha="current-head",
                    route=DashboardRoute.APPROVER,
                    held_gates="the required checks",
                    since="2026-08-16T08:00:00Z",
                ),
                DashboardCommandReply(
                    91,
                    "cleared_by_feedback",
                    "alice",
                    head_sha="current-head",
                    since="2026-08-16T08:00:00Z",
                ),
                DashboardCommandReply(
                    92,
                    "unauthorized",
                    "outsider",
                    "route:reviewers",
                ),
            ),
            copilot_review_requested=True,
            copilot_review_exists=True,
            copilot_review_stale=True,
            copilot_review_needed=True,
            is_maintenance_bot=False,
            is_draft=False,
            approval_count=2,
            conflicts="no",
            created_at="2026-08-16T08:00:00Z",
            last_activity_at="2026-08-16T12:00:00Z",
            last_author_activity_at="2026-08-16T11:00:00Z",
            last_approver_activity_at="2026-08-16T10:00:00Z",
            ci_failing_count=1,
            ci_failing_since="2026-08-16T09:00:00Z",
            ci_pending_count=2,
            non_blocking_check_failures=("CodeQL",),
            copilot_first_review_missing_since="2026-08-16T08:30:00Z",
            copilot_review_outstanding=True,
            copilot_review_unreported=True,
            copilot_review_request_needed=True,
            required_checks_settled=False,
            route_held_since="2026-08-16T09:30:00Z",
            route_hold_expired=True,
            route_held_for_gates=True,
            waiting_since="2026-08-16T08:00:00Z",
            waiting_age_basis="oldest_pending_action",
            author_nudge_episode_id="episode-1",
            author_action_review_thread_urls=("https://example.test/thread/1",),
            author_action_top_level_feedback_urls=(
                "https://example.test/comment/2",
            ),
            reviewers=(
                {
                    "login": "reviewer",
                    "approved": True,
                    "open_thread": True,
                },
            ),
        )

        self.assertEqual(
            facts,
            decode_dashboard_facts(encode_dashboard_facts(facts)),
        )

    def test_stored_result_and_dashboard_state_codecs_round_trip(self) -> None:
        first = stored_dashboard_result(
            7,
            route=DashboardRoute.APPROVER,
            facts=dashboard_facts(author="alice", head_sha="first-head"),
            top_level_history={
                "feedback": {
                    "kind": "commit",
                    "timestamp": "2026-08-16T08:00:00Z",
                }
            },
        )
        second = stored_dashboard_result(
            8,
            route=DashboardRoute.MAINTAINER,
            facts=dashboard_facts(author="bob", head_sha="second-head"),
        )
        state = dashboard_state(
            second,
            first,
            initial_backfill_complete=True,
            draft_pr_numbers=frozenset({9}),
        )

        self.assertEqual(first, decode_stored_result(encode_stored_result(first)))
        self.assertEqual(state, decode_dashboard_state(encode_dashboard_state(state)))

    def test_stored_result_rejects_explicit_non_object_facts(self) -> None:
        stored = {
            "pr_number": 7,
            "pr_url": "https://github.com/open-telemetry/example/pull/7",
            "failed": False,
            "route": "approver",
        }

        self.assertEqual(dashboard_facts(), decode_stored_result(stored).facts)
        for facts in ([], "", 0, False, None):
            with self.subTest(facts=facts):
                with self.assertRaisesRegex(
                    ValueError,
                    "dashboard result facts must be an object",
                ):
                    decode_stored_result({**stored, "facts": facts})

    def test_dashboard_facts_rejects_null_non_optional_fields(self) -> None:
        cases = (
            ({"author": None}, "facts.author must be a string"),
            ({"assignees": None}, "facts.assignees must be an array of strings"),
            ({"is_draft": None}, "facts.is_draft must be a boolean"),
            ({"approval_count": None}, "facts.approval_count must be an integer"),
            (
                {"dashboard_command_replies": None},
                "facts.dashboard_command_replies must be an array",
            ),
            ({"reviewers": None}, "facts.reviewers must be an array"),
            (
                {"reviewers": [{"login": None}]},
                "facts.reviewers.login must be a string",
            ),
            (
                {
                    "dashboard_command_replies": [{
                        "comment_id": None,
                        "kind": "unauthorized",
                    }]
                },
                "facts.dashboard_command_replies.comment_id must be an integer",
            ),
        )

        for facts, message in cases:
            with self.subTest(facts=facts):
                with self.assertRaises(ValueError) as raised:
                    decode_dashboard_facts(facts)
                self.assertEqual(message, str(raised.exception))

    def test_dashboard_facts_accepts_null_optional_fields(self) -> None:
        self.assertEqual(
            dashboard_facts(),
            decode_dashboard_facts({
                "ci_failing_count": None,
                "ci_failing_since": None,
                "ci_pending_count": None,
                "copilot_first_review_missing_since": None,
                "route_held_since": None,
                "author_nudge_episode_id": None,
            }),
        )

    def test_stored_result_rejects_null_non_optional_fields(self) -> None:
        stored = {
            "pr_number": 7,
            "pr_url": "https://github.com/open-telemetry/example/pull/7",
            "failed": False,
            "route": "approver",
        }
        cases = (
            ("pr_number", "dashboard result pr_number must be an integer"),
            ("pr_url", "dashboard result pr_url must be a string"),
            ("failed", "dashboard result failed must be a boolean"),
            ("route", "dashboard result route must be a string"),
            (
                "top_level_history",
                "dashboard result top_level_history must be an object",
            ),
        )

        for field, message in cases:
            with self.subTest(field=field):
                with self.assertRaises(ValueError) as raised:
                    decode_stored_result({**stored, field: None})
                self.assertEqual(message, str(raised.exception))

    def test_malformed_persisted_results_are_rejected_individually(self) -> None:
        persisted = {
            "version": DASHBOARD_STATE_VERSION,
            "initial_backfill_complete": True,
            "prs": {
                "1": encode_stored_result(
                    stored_dashboard_result(
                        1,
                        facts=dashboard_facts(author="canonical"),
                    )
                ),
                "01": encode_stored_result(
                    stored_dashboard_result(
                        1,
                        facts=dashboard_facts(author="alias"),
                    )
                ),
                "not-a-number": {},
                "0": {},
                "2": [],
                "3": {"pr_number": 3, "failed": True, "route": "unknown"},
                "4": {"pr_number": 40, "failed": False, "route": "author"},
                "5": {"pr_number": 5, "failed": False, "route": "unknown"},
                "6": {
                    "pr_number": 6,
                    "failed": False,
                    "route": "author",
                    "facts": {"reviewers": "not-an-array"},
                },
            },
        }
        sorted_persisted = json.loads(json.dumps(persisted, sort_keys=True))
        warnings = StringIO()

        with redirect_stderr(warnings):
            decoded = decode_dashboard_state(sorted_persisted)

        self.assertEqual(frozenset({1}), decoded.pr_numbers)
        self.assertEqual("canonical", decoded.results[0].facts.author)
        self.assertTrue(decoded.initial_backfill_complete)
        self.assertEqual(8, warnings.getvalue().count(
            "warning: ignoring malformed dashboard result"
        ))

    def test_version_eleven_dashboard_state_migrates_to_current_shape(self) -> None:
        persisted = {
            "version": 11,
            "initial_backfill_complete": True,
            "prs": {
                "123": {
                    "pr_number": 123,
                    "pr_url": "https://github.com/open-telemetry/example/pull/123",
                    "failed": False,
                    "route": "approver",
                    "facts": {
                        "author": "alice",
                        "assignees": ["alice"],
                        "head_sha": "current-head",
                        "routing_input_fingerprint": "routing-fingerprint",
                        "copilot_request_fingerprint": "copilot-fingerprint",
                        "dashboard_override_command_id": 91,
                        "dashboard_override_command_user": "alice",
                        "dashboard_override_bound_command_id": 91,
                        "dashboard_override_head_sha": "current-head",
                        "dashboard_override_since": "2026-08-16T08:00:00Z",
                        "dashboard_override_cleared_by_feedback": True,
                        "dashboard_command_replies": [
                            {
                                "comment_id": 91,
                                "kind": "routed",
                                "head_sha": "current-head",
                                "user": "alice",
                                "since": "2026-08-16T08:00:00Z",
                                "route": "approver",
                                "held_gates": "",
                            },
                            {
                                "comment_id": 91,
                                "kind": "cleared_by_feedback",
                                "head_sha": "current-head",
                                "user": "alice",
                                "since": "2026-08-16T08:00:00Z",
                            },
                        ],
                        "copilot_review_requested": True,
                        "copilot_review_exists": True,
                        "copilot_review_stale": False,
                        "copilot_review_needed": False,
                        "is_maintenance_bot": False,
                        "is_draft": False,
                        "approval_count": 1,
                        "conflicts": "no",
                        "created_at": "2026-08-16T08:00:00Z",
                        "last_activity_at": "2026-08-16T12:00:00Z",
                        "last_author_activity_at": "2026-08-16T11:00:00Z",
                        "last_approver_activity_at": "2026-08-16T10:00:00Z",
                        "copilot_review_outstanding": True,
                        "copilot_review_unreported": False,
                        "copilot_review_request_needed": False,
                        "required_checks_settled": True,
                        "route_hold_expired": False,
                        "route_held_for_gates": False,
                        "waiting_since": "2026-08-16T08:00:00Z",
                        "waiting_age_basis": "last_author_activity",
                        "author_action_review_thread_urls": [
                            "https://github.com/open-telemetry/example/pull/123"
                            "#discussion_r1",
                        ],
                        "author_action_top_level_feedback_urls": [
                            "https://github.com/open-telemetry/example/pull/123"
                            "#pullrequestreview-2",
                        ],
                        "reviewers": [{
                            "login": "reviewer",
                            "approved": True,
                            "approved_non_team": False,
                            "pending_review": False,
                            "changes_requested": False,
                            "open_thread": False,
                            "top_level_feedback": False,
                        }],
                        "ci_failing_count": 0,
                        "ci_pending_count": 0,
                        "non_blocking_check_failures": ["CodeQL"],
                        "copilot_first_review_missing_since": (
                            "2026-08-16T08:30:00Z"
                        ),
                        "route_held_since": "2026-08-16T09:30:00Z",
                        "author_nudge_episode_id": "episode-1",
                    },
                    "top_level_history": {
                        "feedback": {
                            "kind": "commit",
                            "timestamp": "2026-08-16T10:00:00Z",
                        }
                    },
                },
            },
        }

        self.assertEqual(
            {
                **persisted,
                "version": DASHBOARD_STATE_VERSION,
                "draft_pr_numbers": [],
            },
            encode_dashboard_state(decode_dashboard_state(persisted)),
        )

    def test_notification_state_version_is_independent(self) -> None:
        self.assertEqual(BACKFILL_STATE_VERSION, 3)
        self.assertEqual(NOTIFICATION_STATE_VERSION, 3)
        self.assertEqual(DASHBOARD_STATE_VERSION, 13)
        self.assertEqual(STATUS_COMMENT_ROLLOUT_STATE_VERSION, 2)
        self.assertEqual(AUTHOR_NUDGE_STATE_VERSION, 3)
        self.assertEqual(COPILOT_REVIEW_REQUEST_STATE_VERSION, 6)

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

    def test_author_nudge_state_loads_version_two_for_migration(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("state._state_dir", Path(temp_dir)):
            author_nudge_state_path().write_text(
                json.dumps({
                    "version": 2,
                    "prs": {
                        "123": {
                            "waiting_since": "2026-07-10T00:00:00Z",
                            "nudged_at": "2026-07-17T00:00:00Z",
                            "episode_id": "episode-1",
                        },
                    },
                }),
                encoding="utf-8",
            )

            self.assertEqual(
                {
                    "123": {
                        "waiting_since": "2026-07-10T00:00:00Z",
                        "nudged_at": "2026-07-17T00:00:00Z",
                        "episode_id": "episode-1",
                    },
                },
                load_author_nudges(),
            )

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
                    "episode_id": "episode-1",
                }
            },
            union_merge_author_nudges(
                {
                    "7": {
                        "waiting_since": "2026-07-10T02:00:00Z",
                        "nudged_at": "",
                        "pending_at": "2026-07-20T01:00:00Z",
                        "head_sha": "head",
                        "routing_input_fingerprint": "fingerprint",
                    },
                },
                {
                    "7": {
                        "waiting_since": "2026-07-10T02:00:00Z",
                        "nudged_at": "2026-07-20T02:00:00Z",
                        "episode_id": "episode-1",
                    }
                },
            ),
        )

    def test_retry_snapshot_preserves_pending_nudge_completion(self) -> None:
        self.assertEqual(
            {
                "7": {
                    "waiting_since": "2026-07-10T02:00:00Z",
                    "nudged_at": "2026-07-20T02:00:00Z",
                    "episode_id": "episode-1",
                    "completions": [{
                        "episode_id": "previous-episode",
                        "completed_at": "2026-07-21T02:00:00Z",
                    }],
                }
            },
            union_merge_author_nudges(
                {
                    "7": {
                        "waiting_since": "2026-07-10T02:00:00Z",
                        "nudged_at": "2026-07-20T02:00:00Z",
                        "episode_id": "episode-1",
                        "completions": [{
                            "episode_id": "previous-episode",
                            "completed_at": "2026-07-21T02:00:00Z",
                        }],
                    }
                },
                {
                    "7": {
                        "waiting_since": "2026-07-10T02:00:00Z",
                        "nudged_at": "2026-07-20T02:00:00Z",
                    }
                },
            ),
        )

    def test_retry_snapshot_completes_posted_nudge_without_suppressing_new_episode(
        self,
    ) -> None:
        self.assertEqual(
            {
                "7": {
                    "waiting_since": "2026-07-20T02:00:00Z",
                    "nudged_at": "",
                    "episode_id": "episode-2",
                    "completions": [{
                        "episode_id": "episode-1",
                        "completed_at": "2026-07-17T02:00:00Z",
                        "kind": "routing_changed",
                    }],
                }
            },
            union_merge_author_nudges(
                {
                    "7": {
                        "waiting_since": "2026-07-20T02:00:00Z",
                        "nudged_at": "",
                        "episode_id": "episode-2",
                    }
                },
                {
                    "7": {
                        "waiting_since": "2026-07-10T02:00:00Z",
                        "nudged_at": "2026-07-17T02:00:00Z",
                        "episode_id": "episode-1",
                    }
                },
            ),
        )

    def test_retry_snapshot_completes_posted_nudge_removed_from_baseline(self) -> None:
        self.assertEqual(
            {
                "7": {
                    "completions": [{
                        "episode_id": "episode-1",
                        "completed_at": "2026-07-17T02:00:00Z",
                        "kind": "routing_changed",
                    }],
                }
            },
            union_merge_author_nudges(
                {},
                {
                    "7": {
                        "waiting_since": "2026-07-10T02:00:00Z",
                        "nudged_at": "2026-07-17T02:00:00Z",
                        "episode_id": "episode-1",
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
                "draft_reconciliation_cursor": 56,
            })

            self.assertEqual(
                load_status_comment_rollout_state(),
                {
                    "version": STATUS_COMMENT_ROLLOUT_STATE_VERSION,
                    "target_revision": 2,
                    "completed_revision": 1,
                    "pending_pr_numbers": [34, 12],
                    "draft_reconciliation_cursor": 56,
                },
            )

    def test_enqueue_status_comment_update_is_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("state._state_dir", Path(temp_dir)):
            enqueue_status_comment_update(34)
            enqueue_status_comment_update(12)
            enqueue_status_comment_update(34)

            self.assertEqual(
                [34, 12],
                load_status_comment_rollout_state()["pending_pr_numbers"],
            )

    def test_each_delivery_version_rejects_older_workers(self) -> None:
        baseline = current_delivery_versions()
        for name in baseline:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp_dir:
                with patch("state._state_dir", Path(temp_dir)):
                    self.assertTrue(claim_delivery_versions())
                    newer = dict(baseline)
                    newer[name] += 1
                    with patch("state.current_delivery_versions", return_value=newer):
                        self.assertTrue(claim_delivery_versions())
                    self.assertFalse(claim_delivery_versions())
                    self.assertEqual(newer, load_delivery_versions())

    def test_new_delivery_version_rejects_older_workers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("state._state_dir", Path(temp_dir)):
            self.assertTrue(claim_delivery_versions())
            with patch("state.FUTURE_STATE_VERSION", 1, create=True):
                newer = current_delivery_versions()
                self.assertEqual(1, newer["FUTURE_STATE_VERSION"])
                self.assertTrue(claim_delivery_versions())

            self.assertFalse(claim_delivery_versions())
            self.assertEqual(newer, load_delivery_versions())

    def test_delivery_versions_fail_closed(self) -> None:
        malformed_versions = [
            "not json",
            json.dumps([]),
            json.dumps({"DASHBOARD_STATE_VERSION": None}),
            json.dumps({"DASHBOARD_STATE_VERSION": False}),
            json.dumps({"DASHBOARD_STATE_VERSION": -1}),
            json.dumps({"DASHBOARD_STATE_VERSION": 1.5}),
        ]
        for contents in malformed_versions:
            with self.subTest(contents=contents), tempfile.TemporaryDirectory() as temp_dir:
                with patch("state._state_dir", Path(temp_dir)):
                    delivery_state = Path(temp_dir) / "delivery-versions.json"
                    delivery_state.write_text(contents, encoding="utf-8")

                    self.assertFalse(claim_delivery_versions())

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
        state = empty_state().with_initial_backfill_complete()

        updated = update_dashboard_state_for_pr(state, 123, None)

        self.assertEqual(DashboardState(initial_backfill_complete=True), updated)

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
            evaluation_success(
                123,
                top_level_history={
                    "pr-review-456": {
                        "evidence": {
                            "commit": "2026-07-14T03:00:00Z",
                            "description": "2026-07-14T04:00:00Z",
                        },
                    },
                },
                pending_actions={
                    "inline-thread": {
                        "action": "author",
                        "since": "2026-07-14T02:00:00Z",
                    },
                },
            )
        )

        self.assertEqual(
            {
                key: {
                    nested_key: dict(nested_value)
                    for nested_key, nested_value in value.items()
                }
                for key, value in result.top_level_history.items()
            },
            {
                "pr-review-456": {
                    "evidence": {
                        "commit": "2026-07-14T03:00:00Z",
                        "description": "2026-07-14T04:00:00Z",
                    },
                },
            },
        )

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