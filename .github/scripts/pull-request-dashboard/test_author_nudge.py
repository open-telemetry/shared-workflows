from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import os
import unittest
from unittest.mock import patch

import author_nudge
from dashboard_test_support import (
    dashboard_facts,
    dashboard_state as build_dashboard_state,
    evaluation_failure,
    stored_dashboard_result,
)
from routing_snapshot import build_routing_snapshot
from pull_request_source import normalize_pull_request_source


NOW = datetime(2026, 7, 17, tzinfo=timezone.utc)


def routing_snapshot(
    *,
    state: str = "OPEN",
    is_draft: bool = False,
    head_sha: str = "current-head",
    fingerprint: str = "current-fingerprint",
):
    snapshot = build_routing_snapshot(normalize_pull_request_source({
        "checks": [],
        "pr": {
            "state": state,
            "isDraft": is_draft,
            "headRefOid": head_sha,
        },
    }))
    return replace(snapshot, routing_input_fingerprint=fingerprint)


def author_result(route: str = "author", **fact_changes: object):
    facts = {
        "author": "alice",
        "author_nudge_episode_id": "episode-1",
        "head_sha": "current-head",
        "routing_input_fingerprint": "current-fingerprint",
    }
    facts.update(fact_changes)
    return stored_dashboard_result(
        route=route,
        facts=dashboard_facts(**facts),
    )


class AuthorNudgePolicyTest(unittest.TestCase):
    def test_nudge_advertises_dashboard_override_command(self) -> None:
        body = author_nudge.render_nudge(
            "alice",
            "https://example.test/status",
            "episode-1",
        )

        self.assertIn("just a friendly reminder", body)
        self.assertIn(
            "The [dashboard status comment](https://example.test/status) has the "
            "open items and is kept current.",
            body,
        )
        self.assertIn(
            "- Replying is enough to hand it off — answer, explain why no change "
            "is needed, or ask a follow-up. The dashboard rechecks who needs to "
            "act once nothing on the list is waiting on you.",
            body,
        )
        self.assertIn(
            "- To hand it back for any other reason, including the dashboard "
            "getting this wrong, comment `/dashboard route:reviewers`.",
            body,
        )

    def test_first_author_route_observation_starts_clock(self) -> None:
        due, entry = author_nudge.plan_nudge(author_result(), None, NOW)

        self.assertFalse(due)
        self.assertEqual(
            entry,
            {
                "waiting_since": "2026-07-17T00:00:00+00:00",
                "nudged_at": "",
                "episode_id": "episode-1",
            },
        )

    def test_conflict_starts_standard_nudge_clock(self) -> None:
        result = author_result(conflicts="yes")

        due, entry = author_nudge.plan_nudge(result, None, NOW)

        self.assertFalse(due)
        self.assertEqual(
            {
                "waiting_since": "2026-07-17T00:00:00+00:00",
                "nudged_at": "",
                "episode_id": "episode-1",
            },
            entry,
        )

    def test_conflict_nudge_is_due_after_five_business_days(self) -> None:
        result = author_result(conflicts="yes")

        due, _entry = author_nudge.plan_nudge(
            result,
            {"waiting_since": "2026-07-10T00:00:00+00:00", "nudged_at": ""},
            NOW,
        )

        self.assertTrue(due)

    def test_nudge_is_due_after_five_business_days(self) -> None:
        due, _entry = author_nudge.plan_nudge(
            author_result(),
            {"waiting_since": "2026-07-10T00:00:00+00:00", "nudged_at": ""},
            NOW,
        )

        self.assertTrue(due)

    def test_nudge_is_not_due_before_five_business_days(self) -> None:
        due, _entry = author_nudge.plan_nudge(
            author_result(),
            {"waiting_since": "2026-07-10T00:00:01+00:00", "nudged_at": ""},
            NOW,
        )

        self.assertFalse(due)

    def test_default_interval_counts_five_utc_weekdays(self) -> None:
        for now, expected in (
            ("2026-07-17T23:59:59+00:00", False),
            ("2026-07-18T00:00:00+00:00", True),
        ):
            with self.subTest(now=now), patch.dict(os.environ):
                os.environ.pop("AUTHOR_REMINDER_AFTER_BUSINESS_DAYS", None)
                due, _entry = author_nudge.plan_nudge(
                    author_result(),
                    {"waiting_since": "2026-07-13T00:00:00+00:00", "nudged_at": ""},
                    datetime.fromisoformat(now),
                )

            self.assertEqual(expected, due)

    def test_nudge_uses_configured_interval_for_existing_wait(self) -> None:
        for waiting_since, expected in (
            ("2026-07-15T00:00:01+00:00", False),
            ("2026-07-15T00:00:00+00:00", True),
        ):
            with self.subTest(waiting_since=waiting_since), patch.dict(
                os.environ, {"AUTHOR_REMINDER_AFTER_BUSINESS_DAYS": "2"},
            ):
                due, entry = author_nudge.plan_nudge(
                    author_result(),
                    {"waiting_since": waiting_since, "nudged_at": ""},
                    NOW,
                )

            self.assertEqual(expected, due)
            self.assertEqual(waiting_since, entry["waiting_since"])

    def test_two_business_days_pause_over_weekends_in_utc(self) -> None:
        for waiting_since, now, expected in (
            ("2026-07-17T12:00:00+00:00", "2026-07-19T12:00:00+00:00", False),
            ("2026-07-17T12:00:00+00:00", "2026-07-21T11:59:59+00:00", False),
            ("2026-07-17T12:00:00+00:00", "2026-07-21T12:00:00+00:00", True),
            ("2026-07-18T12:00:00+00:00", "2026-07-21T23:59:59+00:00", False),
            ("2026-07-18T12:00:00+00:00", "2026-07-22T00:00:00+00:00", True),
            ("2026-07-17T08:00:00-04:00", "2026-07-21T12:00:00+00:00", True),
            ("2026-07-17T23:00:00-04:00", "2026-07-22T00:00:00+00:00", True),
            ("2026-07-15T00:00:00+00:00", "2026-07-14T00:00:00+00:00", False),
        ):
            with self.subTest(waiting_since=waiting_since, now=now), patch.dict(
                os.environ, {"AUTHOR_REMINDER_AFTER_BUSINESS_DAYS": "2"},
            ):
                due, _entry = author_nudge.plan_nudge(
                    author_result(),
                    {"waiting_since": waiting_since, "nudged_at": ""},
                    datetime.fromisoformat(now),
                )

            self.assertEqual(expected, due)

    def test_business_day_interval_can_span_multiple_weeks(self) -> None:
        for now, expected in (
            ("2026-07-28T11:59:59+00:00", False),
            ("2026-07-28T12:00:00+00:00", True),
        ):
            with self.subTest(now=now), patch.dict(
                os.environ, {"AUTHOR_REMINDER_AFTER_BUSINESS_DAYS": "7"},
            ):
                due, _entry = author_nudge.plan_nudge(
                    author_result(),
                    {"waiting_since": "2026-07-17T12:00:00+00:00", "nudged_at": ""},
                    datetime.fromisoformat(now),
                )

            self.assertEqual(expected, due)

    def test_invalid_reminder_interval_is_rejected(self) -> None:
        for value in ("0", "-1", "1.5", "invalid"):
            with self.subTest(value=value), patch.dict(
                os.environ, {"AUTHOR_REMINDER_AFTER_BUSINESS_DAYS": value},
            ), self.assertRaises(ValueError):
                author_nudge.plan_nudge(
                    author_result(),
                    {"waiting_since": "2026-07-10T00:00:00+00:00", "nudged_at": ""},
                    NOW,
                )

    def test_leaving_author_route_resets_unnudged_clock(self) -> None:
        due, entry = author_nudge.plan_nudge(
            author_result("approver"),
            {"waiting_since": "2026-07-10T00:00:00+00:00", "nudged_at": ""},
            NOW,
        )

        self.assertFalse(due)
        self.assertIsNone(entry)

    def test_leaving_author_route_prepares_posted_nudge_for_completion(self) -> None:
        due, entry = author_nudge.plan_nudge(
            author_result("approver"),
            {
                "waiting_since": "2026-07-10T00:00:00+00:00",
                "nudged_at": "2026-07-17T00:00:00+00:00",
                "episode_id": "episode-1",
            },
            NOW,
        )

        self.assertFalse(due)
        self.assertEqual(
            entry,
            {
                "completions": [{
                    "episode_id": "episode-1",
                    "completed_at": "2026-07-17T00:00:00+00:00",
                    "kind": "left_author",
                }],
            },
        )

    def test_conflict_keeps_posted_nudge_active(self) -> None:
        result = author_result(conflicts="yes")

        due, entry = author_nudge.plan_nudge(
            result,
            {
                "waiting_since": "2026-07-10T00:00:00+00:00",
                "nudged_at": "2026-07-17T00:00:00+00:00",
                "episode_id": "episode-1",
            },
            NOW,
        )

        self.assertFalse(due)
        self.assertEqual(
            {
                "waiting_since": "2026-07-10T00:00:00+00:00",
                "nudged_at": "2026-07-17T00:00:00+00:00",
                "episode_id": "episode-1",
            },
            entry,
        )

    def test_legacy_posted_nudge_queues_marker_recovery(self) -> None:
        due, entry = author_nudge.plan_nudge(
            author_result("approver"),
            {
                "waiting_since": "2026-07-10T00:00:00+00:00",
                "nudged_at": "2026-07-17T00:00:00+00:00",
            },
            NOW,
        )

        self.assertFalse(due)
        self.assertEqual(
            {
                "completions": [{
                    "episode_id": (
                        "legacy-nudge:2026-07-17T00:00:00+00:00"
                    ),
                    "completed_at": "2026-07-17T00:00:00+00:00",
                    "kind": "left_author",
                }],
            },
            entry,
        )

    def test_removed_pr_does_not_claim_author_wait_ended(self) -> None:
        previous = {
            "waiting_since": "2026-07-10T00:00:00+00:00",
            "nudged_at": "2026-07-17T00:00:00+00:00",
            "episode_id": "episode-1",
        }

        self.assertEqual(
            (
                False,
                {
                    "completions": [{
                        "episode_id": "episode-1",
                        "completed_at": "2026-07-17T00:00:00+00:00",
                        "kind": "routing_changed",
                    }],
                },
            ),
            author_nudge.plan_nudge(None, previous, NOW),
        )

    def test_gate_hold_keeps_posted_reminder_active(self) -> None:
        previous = {
            "waiting_since": "2026-07-10T00:00:00+00:00",
            "nudged_at": "2026-07-17T00:00:00+00:00",
            "episode_id": "episode-1",
        }
        held = author_result(route_held_for_gates=True)

        due, entry = author_nudge.plan_nudge(held, previous, NOW)

        self.assertFalse(due)
        self.assertEqual(previous, entry)

    def test_gate_held_route_uses_standard_nudge_deadline(self) -> None:
        held = author_result(route_held_for_gates=True)

        due, entry = author_nudge.plan_nudge(
            held,
            {"waiting_since": "2026-07-10T00:00:00+00:00", "nudged_at": ""},
            NOW,
        )

        self.assertTrue(due)
        self.assertEqual(
            {
                "waiting_since": "2026-07-10T00:00:00+00:00",
                "nudged_at": "",
                "episode_id": "episode-1",
            },
            entry,
        )

    def test_returning_to_author_route_starts_new_episode(self) -> None:
        previous = {
            "waiting_since": "2026-07-01T00:00:00+00:00",
            "nudged_at": "2026-07-10T00:00:00+00:00",
            "episode_id": "previous-episode",
        }

        due, entry = author_nudge.plan_nudge(author_result("approver"), previous, NOW)
        self.assertFalse(due)
        completion = {
            "episode_id": "previous-episode",
            "completed_at": "2026-07-17T00:00:00+00:00",
            "kind": "left_author",
        }
        self.assertEqual({"completions": [completion]}, entry)

        due, entry = author_nudge.plan_nudge(author_result(), entry, NOW)
        self.assertFalse(due)
        self.assertEqual(
            entry,
            {
                "waiting_since": "2026-07-17T00:00:00+00:00",
                "nudged_at": "",
                "episode_id": "episode-1",
                "completions": [completion],
            },
        )

    def test_new_episode_id_closes_posted_reminder_before_resetting_clock(self) -> None:
        next_episode = author_result(author_nudge_episode_id="episode-2")

        due, entry = author_nudge.plan_nudge(
            next_episode,
            {
                "waiting_since": "2026-07-01T00:00:00+00:00",
                "nudged_at": "2026-07-10T00:00:00+00:00",
                "episode_id": "episode-1",
            },
            NOW,
        )

        self.assertFalse(due)
        self.assertEqual(
            {
                "waiting_since": "2026-07-17T00:00:00+00:00",
                "nudged_at": "",
                "episode_id": "episode-2",
                "completions": [{
                    "episode_id": "episode-1",
                    "completed_at": "2026-07-17T00:00:00+00:00",
                    "kind": "routing_changed",
                }],
            },
            entry,
        )

    def test_failed_refresh_preserves_clock(self) -> None:
        previous = {"waiting_since": "2026-07-10T00:00:00+00:00", "nudged_at": ""}

        due, entry = author_nudge.plan_nudge(
            evaluation_failure(),
            previous,
            NOW,
        )

        self.assertFalse(due)
        self.assertEqual(entry, previous)


class AuthorNudgeProcessingTest(unittest.TestCase):
    @patch.object(author_nudge, "save_author_nudges")
    @patch.object(author_nudge, "load_author_nudges", return_value={})
    def test_observation_starts_clock(
        self,
        _load_nudges,
        save_nudges,
    ) -> None:
        author_nudge.record_author_nudge_observation(2, author_result(), NOW)

        self.assertEqual(
            save_nudges.call_args.args[0],
            {
                "2": {
                    "waiting_since": "2026-07-17T00:00:00+00:00",
                    "nudged_at": "",
                    "episode_id": "episode-1",
                }
            },
        )

    @patch.object(author_nudge, "save_author_nudges")
    @patch.object(
        author_nudge,
        "load_author_nudges",
        return_value={
            "1": {
                "waiting_since": "2026-07-10T00:00:00+00:00",
                "nudged_at": "",
            }
        },
    )
    def test_departure_observation_resets_clock(
        self,
        _load_nudges,
        save_nudges,
    ) -> None:
        author_nudge.record_author_nudge_observation(1, None, NOW)

        self.assertEqual(save_nudges.call_args.args[0], {})

    @patch.object(author_nudge, "save_author_nudges")
    @patch.object(
        author_nudge,
        "load_author_nudges",
        return_value={"1": {"waiting_since": "2026-07-01T00:00:00+00:00", "nudged_at": ""}},
    )
    def test_due_accepted_observation_records_pending_nudge(
        self,
        _load_nudges,
        save_nudges,
    ) -> None:
        author_nudge.record_author_nudge_observation(
            1,
            author_result(),
            NOW,
            prepare_due=True,
        )

        self.assertEqual(
            save_nudges.call_args.args[0],
            {
                "1": {
                    "waiting_since": "2026-07-01T00:00:00+00:00",
                    "nudged_at": "",
                    "pending_at": "2026-07-17T00:00:00+00:00",
                    "head_sha": "current-head",
                    "routing_input_fingerprint": "current-fingerprint",
                    "episode_id": "episode-1",
                }
            },
        )

    @patch.object(
        author_nudge,
        "ensure_nudge",
        return_value="2026-07-17T00:00:00+00:00",
    )
    @patch.object(author_nudge, "save_author_nudges")
    @patch.object(
        author_nudge,
        "load_author_nudges",
        return_value={
            "1": {
                "waiting_since": "2026-07-01T00:00:00+00:00",
                "nudged_at": "",
                "pending_at": "2026-07-17T00:00:00+00:00",
                "head_sha": "current-head",
                "routing_input_fingerprint": "current-fingerprint",
            }
        },
    )
    @patch.object(
        author_nudge,
        "load_dashboard_state_cache",
        return_value=build_dashboard_state(author_result()),
    )
    @patch.object(
        author_nudge,
        "fetch_routing_snapshot",
        return_value=routing_snapshot(),
    )
    def test_delivery_records_posted_nudge(
        self,
        fetch_snapshot,
        _load_dashboard_state,
        _load_nudges,
        save_nudges,
        ensure_nudge,
    ) -> None:
        errors = author_nudge.deliver_prepared_author_nudges(
            "open-telemetry/example",
            NOW,
        )

        self.assertEqual([], errors)
        fetch_snapshot.assert_called_once_with("open-telemetry/example", 1)
        ensure_nudge.assert_called_once()
        save_nudges.assert_called_once_with({
            "1": {
                "waiting_since": "2026-07-01T00:00:00+00:00",
                "nudged_at": "2026-07-17T00:00:00+00:00",
                "episode_id": "episode-1",
            },
        })

    def test_delivery_rechecks_increased_interval_after_preparation(self) -> None:
        waiting_since = "2026-07-15T00:00:00+00:00"
        with (
            patch.dict(os.environ, {"AUTHOR_REMINDER_AFTER_BUSINESS_DAYS": "2"}),
            patch.object(
                author_nudge, "load_author_nudges",
                return_value={"1": {"waiting_since": waiting_since, "nudged_at": ""}},
            ) as load_nudges,
            patch.object(author_nudge, "save_author_nudges") as save_nudges,
            patch.object(
                author_nudge, "load_dashboard_state_cache",
                return_value=build_dashboard_state(author_result()),
            ),
            patch.object(
                author_nudge, "fetch_routing_snapshot", return_value=routing_snapshot(),
            ),
            patch.object(author_nudge, "existing_nudge_comment", return_value=None),
            patch.object(
                author_nudge, "gh_api", return_value={"state": "open", "draft": False},
            ),
            patch.object(author_nudge, "publish_pr_status") as publish_status,
            patch.object(
                author_nudge, "managed_status_comments",
                return_value=[{"html_url": "https://example.test/status"}],
            ),
            patch.object(author_nudge, "run_gh") as run_gh,
        ):
            author_nudge.record_author_nudge_observation(
                1, author_result(), NOW, prepare_due=True,
            )
            pending = save_nudges.call_args.args[0]
            self.assertEqual("2026-07-17T00:00:00+00:00", pending["1"]["pending_at"])
            load_nudges.return_value = pending

            os.environ["AUTHOR_REMINDER_AFTER_BUSINESS_DAYS"] = "5"
            author_nudge.record_author_nudge_observation(
                1, author_result(), NOW, prepare_due=True,
            )
            self.assertEqual(pending, save_nudges.call_args.args[0])
            errors = author_nudge.deliver_prepared_author_nudges(
                "open-telemetry/example", NOW,
            )

            self.assertEqual([], errors)
            run_gh.assert_not_called()
            publish_status.assert_not_called()
            self.assertEqual(pending, save_nudges.call_args.args[0])

            errors = author_nudge.deliver_prepared_author_nudges(
                "open-telemetry/example",
                datetime(2026, 7, 22, tzinfo=timezone.utc),
            )

            self.assertEqual([], errors)
            run_gh.assert_called_once()
            self.assertEqual({
                "1": {
                    "waiting_since": waiting_since,
                    "nudged_at": "2026-07-22T00:00:00+00:00",
                    "episode_id": "episode-1",
                },
            }, save_nudges.call_args.args[0])

    @patch.object(author_nudge, "ensure_nudge")
    @patch.object(author_nudge, "save_author_nudges")
    @patch.object(
        author_nudge,
        "load_author_nudges",
        return_value={
            "1": {
                "waiting_since": "2026-07-01T00:00:00+00:00",
                "pending_at": "2026-07-17T00:00:00+00:00",
            }
        },
    )
    @patch.object(
        author_nudge,
        "load_dashboard_state_cache",
        return_value=build_dashboard_state(author_result()),
    )
    def test_delivery_retains_excluded_nudge(
        self,
        _load_dashboard_state,
        _load_nudges,
        save_nudges,
        ensure_nudge,
    ) -> None:
        errors = author_nudge.deliver_prepared_author_nudges(
            "open-telemetry/example",
            NOW,
            excluded_pr_numbers={1},
        )

        self.assertEqual([], errors)
        ensure_nudge.assert_not_called()
        save_nudges.assert_called_once_with({
            "1": {
                "waiting_since": "2026-07-01T00:00:00+00:00",
                "pending_at": "2026-07-17T00:00:00+00:00",
            }
        })

    @patch.object(author_nudge, "ensure_nudge_completed")
    @patch.object(author_nudge, "save_author_nudges")
    @patch.object(
        author_nudge,
        "load_author_nudges",
        return_value={
            "1": {
                "waiting_since": "2026-07-01T00:00:00+00:00",
                "nudged_at": "2026-07-10T00:00:00+00:00",
                "episode_id": "episode-1",
                "completions": [{
                    "episode_id": "previous-episode",
                    "completed_at": "2026-07-17T00:00:00+00:00",
                }],
            }
        },
    )
    @patch.object(
        author_nudge,
        "load_dashboard_state_cache",
        return_value=build_dashboard_state(author_result("approver")),
    )
    def test_delivery_completes_posted_nudge(
        self,
        dashboard_state,
        _load_nudges,
        save_nudges,
        ensure_completed,
    ) -> None:
        errors = author_nudge.deliver_prepared_author_nudges(
            "open-telemetry/example",
            NOW,
        )

        self.assertEqual([], errors)
        ensure_completed.assert_called_once_with(
            "open-telemetry/example",
            1,
            "previous-episode",
            dashboard_state.return_value,
            NOW,
            "left_author",
        )
        save_nudges.assert_called_once_with({
            "1": {
                "waiting_since": "2026-07-01T00:00:00+00:00",
                "nudged_at": "2026-07-10T00:00:00+00:00",
                "episode_id": "episode-1",
            }
        })

    @patch.object(
        author_nudge,
        "recover_legacy_nudge_episode_id",
        return_value="recovered-episode",
    )
    @patch.object(author_nudge, "ensure_nudge_completed")
    @patch.object(author_nudge, "save_author_nudges")
    @patch.object(
        author_nudge,
        "load_author_nudges",
        return_value={
            "1": {
                "completions": [{
                    "episode_id": (
                        "legacy-nudge:2026-07-10T00:00:00+00:00"
                    ),
                    "completed_at": "2026-07-17T00:00:00+00:00",
                    "kind": "left_author",
                }],
            },
        },
    )
    @patch.object(
        author_nudge,
        "load_dashboard_state_cache",
        return_value=build_dashboard_state(author_result("approver")),
    )
    def test_delivery_recovers_legacy_posted_nudge_episode(
        self,
        dashboard_state,
        _load_nudges,
        save_nudges,
        ensure_completed,
        recover_episode,
    ) -> None:
        _load_nudges.return_value["1"]["completions"].append({
            "episode_id": "recovered-episode",
            "completed_at": "2026-07-17T00:00:00+00:00",
        })
        with patch.object(author_nudge, "using_delivery_state", return_value=True):
            errors = author_nudge.deliver_prepared_author_nudges(
                "open-telemetry/example", NOW,
            )

        self.assertEqual([], errors)
        recover_episode.assert_called_once_with(
            "open-telemetry/example",
            1,
            "legacy-nudge:2026-07-10T00:00:00+00:00",
        )
        ensure_completed.assert_called_once_with(
            "open-telemetry/example",
            1,
            "recovered-episode",
            dashboard_state.return_value,
            NOW,
            "left_author",
        )
        save_nudges.assert_called_once_with({
            "1": {"delivered_completion_ids": [
                "legacy-nudge:2026-07-10T00:00:00+00:00", "recovered-episode",
            ]},
        })

    @patch.object(
        author_nudge,
        "recover_legacy_nudge_episode_id",
        return_value="",
    )
    @patch.object(author_nudge, "ensure_nudge_completed")
    @patch.object(author_nudge, "save_author_nudges")
    @patch.object(
        author_nudge,
        "load_author_nudges",
        return_value={
            "1": {
                "completions": [{
                    "episode_id": (
                        "legacy-nudge:2026-07-10T00:00:00.403635+00:00"
                    ),
                    "completed_at": "2026-07-17T00:00:00+00:00",
                    "kind": "left_author",
                }],
            },
        },
    )
    @patch.object(
        author_nudge,
        "load_dashboard_state_cache",
        return_value=build_dashboard_state(author_result("approver")),
    )
    def test_missing_legacy_comment_discards_completion(
        self,
        _dashboard_state,
        _load_nudges,
        save_nudges,
        ensure_completed,
        _recover_episode,
    ) -> None:
        with (
            patch.object(author_nudge, "using_delivery_state", return_value=True),
            patch("sys.stderr") as stderr,
        ):
            errors = author_nudge.deliver_prepared_author_nudges(
                "open-telemetry/example", NOW,
            )

        self.assertEqual([], errors)
        stderr.write.assert_any_call(
            "PR #1: legacy author nudge comment not found; discarding completion"
        )
        ensure_completed.assert_not_called()
        save_nudges.assert_called_once_with({
            "1": {"delivered_completion_ids": [
                "legacy-nudge:2026-07-10T00:00:00.403635+00:00",
            ]},
        })

    def test_failed_completion_survives_closed_pr_delivery(self) -> None:
        completion = {
            "episode_id": "previous-episode",
            "completed_at": "2026-07-17T00:00:00+00:00",
        }
        pending = {
            "1": {
                "waiting_since": "2026-07-10T00:00:00+00:00",
                "nudged_at": "",
                "episode_id": "episode-1",
                "pending_at": "2026-07-17T00:00:00+00:00",
                "head_sha": "current-head",
                "routing_input_fingerprint": "current-fingerprint",
                "completions": [completion],
            }
        }
        with (
            patch.object(author_nudge, "load_author_nudges", return_value=pending),
            patch.object(author_nudge, "save_author_nudges") as save_nudges,
            patch.object(
                author_nudge,
                "load_dashboard_state_cache",
                return_value=build_dashboard_state(author_result()),
            ),
            patch.object(
                author_nudge,
                "ensure_nudge_completed",
                side_effect=RuntimeError("retry"),
            ),
            patch.object(
                author_nudge,
                "fetch_routing_snapshot",
                return_value=routing_snapshot(state="CLOSED"),
            ),
            patch.object(author_nudge, "ensure_nudge") as ensure_nudge,
        ):
            errors = author_nudge.deliver_prepared_author_nudges(
                "open-telemetry/example",
                NOW,
            )

        self.assertEqual(["PR #1: retry"], errors)
        ensure_nudge.assert_not_called()
        save_nudges.assert_called_once_with({"1": {"completions": [completion]}})

    def test_missing_node_cleanup_requires_a_recorded_note_and_matching_node(self) -> None:
        missing = author_nudge.GhGraphQLNodeNotFoundError("missing", "IC_17")
        for stage, note_recorded, error, handled in (
            ("comment_minimization_reason", False, missing, True),
            ("comment_minimization_reason", True, missing, True),
            ("unminimize_comment", True, missing, True),
            ("minimize_comment", True, missing, True),
            ("run_gh", False, missing, False),
            ("run_gh", False, RuntimeError("Not Found (HTTP 404)"), False),
            ("run_gh", False, author_nudge.GhReadOnlyConversationError("archived"), False),
            ("comment_minimization_reason", False, RuntimeError("Forbidden"), False),
            (
                "comment_minimization_reason", False,
                author_nudge.GhGraphQLNodeNotFoundError("other node", "IC_18"), False,
            ),
        ):
            with (
                self.subTest(stage=stage, note_recorded=note_recorded, error=error),
                patch.object(
                    author_nudge, "existing_nudge_comment",
                    return_value={
                        "id": 17,
                        "node_id": "IC_17",
                        "body": author_nudge.completed_nudge_marker("episode-1")
                        if note_recorded else "Reminder",
                    },
                ),
                patch.object(
                    author_nudge, "managed_status_comments",
                    return_value=[{"html_url": "https://example.test/status"}],
                ),
                patch.object(author_nudge, "run_gh") as run_gh,
                patch.object(
                    author_nudge, "comment_minimization_reason",
                    return_value="SPAM" if stage == "unminimize_comment" else "",
                ),
                patch.object(author_nudge, "unminimize_comment"),
                patch.object(author_nudge, "minimize_comment"),
                patch.object(author_nudge, stage, side_effect=error),
                patch("sys.stderr"),
            ):
                if handled:
                    author_nudge.ensure_nudge_completed(
                        "open-telemetry/example", 1, "episode-1",
                        build_dashboard_state(), NOW,
                    )
                    self.assertEqual(0 if note_recorded else 1, run_gh.call_count)
                else:
                    with self.assertRaises(type(error)) as raised:
                        author_nudge.ensure_nudge_completed(
                            "open-telemetry/example", 1, "episode-1",
                            build_dashboard_state(), NOW,
                        )
                    self.assertIs(error, raised.exception)

    def test_cleanup_state_survives_new_nudge_delivery(self) -> None:
        completion = {
            "episode_id": "previous-episode",
            "completed_at": "2026-07-17T00:00:00+00:00",
        }
        pending = {
            "1": {
                "waiting_since": "2026-07-10T00:00:00+00:00",
                "nudged_at": "",
                "episode_id": "episode-1",
                "pending_at": "2026-07-17T00:00:00+00:00",
                "head_sha": "current-head",
                "routing_input_fingerprint": "current-fingerprint",
                "completions": [completion],
            }
        }
        archived = author_nudge.GhReadOnlyConversationError("archived")
        for error, stage, expected_errors, retired in (
            (None, "posted", [], True),
            (archived, "posted", [], True),
            (RuntimeError("retry"), "posted", ["PR #1: retry"], False),
            (archived, "routing", ["PR #1: new reminder failed"], True),
            (archived, "publication", ["PR #1: new reminder failed"], True),
            (archived, "no-publication", [], True),
        ):
            with (
                self.subTest(error=error, stage=stage),
                patch.object(author_nudge, "using_delivery_state", return_value=True),
                patch.object(author_nudge, "load_author_nudges", return_value=pending),
                patch.object(author_nudge, "save_author_nudges") as save_nudges,
                patch.object(
                    author_nudge, "load_dashboard_state_cache",
                    return_value=build_dashboard_state(author_result()),
                ),
                patch.object(author_nudge, "ensure_nudge_completed", side_effect=error),
                patch.object(
                    author_nudge, "fetch_routing_snapshot",
                    return_value=routing_snapshot(),
                ) as routing,
                patch.object(
                    author_nudge, "ensure_nudge",
                    return_value="2026-07-17T00:00:00+00:00" if stage == "posted" else None,
                ) as publish,
                patch("sys.stderr"),
            ):
                if stage in ("routing", "publication"):
                    (routing if stage == "routing" else publish).side_effect = RuntimeError(
                        "new reminder failed"
                    )

                errors = author_nudge.deliver_prepared_author_nudges(
                    "open-telemetry/example", NOW,
                )

                self.assertEqual(expected_errors, errors)
                saved = save_nudges.call_args.args[0]["1"]
                self.assertEqual(
                    ["previous-episode"] if retired else [],
                    saved.get("delivered_completion_ids", []),
                )
                self.assertEqual(
                    [] if retired else [completion], saved.get("completions", []),
                )
                self.assertEqual(
                    "2026-07-17T00:00:00+00:00" if stage == "posted" else "",
                    saved.get("nudged_at", ""),
                )
                self.assertEqual("2026-07-10T00:00:00+00:00", saved["waiting_since"])
                self.assertEqual("episode-1", saved["episode_id"])
                if stage == "posted":
                    self.assertNotIn("pending_at", saved)
                else:
                    self.assertEqual("2026-07-17T00:00:00+00:00", saved["pending_at"])

    def test_planning_preserves_retired_completion_receipts(self) -> None:
        for result, clock in (
            (None, {"nudged_at": "2026-07-10T00:00:00Z"}),
            (author_result("approver"), {"nudged_at": "2026-07-10T00:00:00Z"}),
            (author_result(author_nudge_episode_id="episode-2"), {"episode_id": "episode-1"}),
            (author_result(), {}),
        ):
            with self.subTest(result=result, clock=clock):
                _due, entry = author_nudge.plan_nudge(
                    result, {**clock, "delivered_completion_ids": ["old-episode"]}, NOW,
                )

                self.assertIsNotNone(entry)
                self.assertEqual(["old-episode"], entry["delivered_completion_ids"])

    @patch.object(author_nudge, "ensure_nudge")
    @patch.object(author_nudge, "save_author_nudges")
    @patch.object(
        author_nudge,
        "load_author_nudges",
        return_value={
            "1": {
                "waiting_since": "2026-07-01T00:00:00+00:00",
                "nudged_at": "",
                "pending_at": "2026-07-17T00:00:00+00:00",
                "head_sha": "current-head",
                "routing_input_fingerprint": "current-fingerprint",
            }
        },
    )
    @patch.object(
        author_nudge,
        "load_dashboard_state_cache",
        return_value=build_dashboard_state(author_result()),
    )
    @patch.object(
        author_nudge,
        "fetch_routing_snapshot",
        return_value=routing_snapshot(head_sha="new-head"),
    )
    def test_delivery_defers_when_head_advanced(
        self,
        _gh_api,
        _load_dashboard_state,
        _load_nudges,
        save_nudges,
        ensure_nudge,
    ) -> None:
        errors = author_nudge.deliver_prepared_author_nudges(
            "open-telemetry/example",
            NOW,
        )

        self.assertEqual([], errors)
        ensure_nudge.assert_not_called()
        save_nudges.assert_called_once_with({
            "1": {
                "waiting_since": "2026-07-01T00:00:00+00:00",
                "nudged_at": "",
            },
        })

    @patch.object(author_nudge, "ensure_nudge")
    @patch.object(author_nudge, "save_author_nudges")
    @patch.object(
        author_nudge,
        "load_author_nudges",
        return_value={
            "1": {
                "waiting_since": "2026-07-01T00:00:00+00:00",
                "nudged_at": "",
                "pending_at": "2026-07-17T00:00:00+00:00",
                "head_sha": "current-head",
                "routing_input_fingerprint": "accepted-fingerprint",
            }
        },
    )
    @patch.object(
        author_nudge,
        "load_dashboard_state_cache",
        return_value=build_dashboard_state(author_result()),
    )
    @patch.object(
        author_nudge,
        "fetch_routing_snapshot",
        return_value=routing_snapshot(fingerprint="new-fingerprint"),
    )
    def test_delivery_defers_when_routing_inputs_changed(
        self,
        _fetch_snapshot,
        _load_dashboard_state,
        _load_nudges,
        save_nudges,
        ensure_nudge,
    ) -> None:
        errors = author_nudge.deliver_prepared_author_nudges(
            "open-telemetry/example",
            NOW,
        )

        self.assertEqual([], errors)
        ensure_nudge.assert_not_called()
        save_nudges.assert_called_once_with({
            "1": {
                "waiting_since": "2026-07-01T00:00:00+00:00",
                "nudged_at": "",
            },
        })

    def test_delivery_discards_nudge_when_pr_becomes_conflicted(self) -> None:
        clean_raw = {
            "pr": {
                "mergeable": "MERGEABLE",
                "mergeStateStatus": "CLEAN",
            }
        }
        conflicted_raw = {
            "pr": {
                "mergeable": "CONFLICTING",
                "mergeStateStatus": "DIRTY",
            }
        }
        pending = {
            "1": {
                "waiting_since": "2026-07-01T00:00:00+00:00",
                "nudged_at": "",
                "pending_at": "2026-07-17T00:00:00+00:00",
                "head_sha": "current-head",
                "routing_input_fingerprint": (
                    build_routing_snapshot(
                        normalize_pull_request_source(clean_raw)
                    ).routing_input_fingerprint
                ),
            }
        }
        with (
            patch.object(author_nudge, "load_author_nudges", return_value=pending),
            patch.object(author_nudge, "save_author_nudges") as save_nudges,
            patch.object(
                author_nudge,
                "load_dashboard_state_cache",
                return_value=build_dashboard_state(author_result()),
            ),
            patch.object(
                author_nudge,
                "fetch_routing_snapshot",
                return_value=replace(
                    routing_snapshot(),
                    routing_input_fingerprint=build_routing_snapshot(
                        normalize_pull_request_source(conflicted_raw)
                    ).routing_input_fingerprint,
                ),
            ),
            patch.object(author_nudge, "ensure_nudge") as ensure_nudge,
        ):
            errors = author_nudge.deliver_prepared_author_nudges(
                "open-telemetry/example",
                NOW,
            )

        self.assertEqual([], errors)
        ensure_nudge.assert_not_called()
        save_nudges.assert_called_once_with({
            "1": {
                "waiting_since": "2026-07-01T00:00:00+00:00",
                "nudged_at": "",
            },
        })

    def test_delivery_clears_episode_for_closed_or_draft_pr(self) -> None:
        pending = {
            "1": {
                "waiting_since": "2026-07-01T00:00:00+00:00",
                "nudged_at": "",
                "pending_at": "2026-07-17T00:00:00+00:00",
                "head_sha": "current-head",
                "routing_input_fingerprint": "current-fingerprint",
            }
        }
        for state, draft in (("CLOSED", False), ("OPEN", True)):
            with (
                self.subTest(state=state, draft=draft),
                patch.object(author_nudge, "load_author_nudges", return_value=pending),
                patch.object(author_nudge, "save_author_nudges") as save_nudges,
                patch.object(
                    author_nudge,
                    "load_dashboard_state_cache",
                    return_value=build_dashboard_state(author_result()),
                ),
                patch.object(
                    author_nudge,
                    "fetch_routing_snapshot",
                    return_value=routing_snapshot(
                        state=state,
                        is_draft=draft,
                    ),
                ),
                patch.object(author_nudge, "ensure_nudge") as ensure_nudge,
            ):
                errors = author_nudge.deliver_prepared_author_nudges(
                    "open-telemetry/example",
                    NOW,
                )

                self.assertEqual([], errors)
                ensure_nudge.assert_not_called()
                save_nudges.assert_called_once_with({})

    def test_rendered_nudge_mentions_author_and_links_status(self) -> None:
        body = author_nudge.render_nudge(
            "alice",
            "https://example.test/status",
            "episode-1",
        )

        self.assertIn("@alice", body)
        self.assertIn(
            "just a friendly reminder that this pull request is waiting on you",
            body,
        )
        self.assertNotIn("had been waiting on you for a week", body)
        self.assertIn("[dashboard status comment](https://example.test/status)", body)
        self.assertIn(
            author_nudge.nudge_marker("episode-1"),
            body,
        )

    def test_rendered_completed_nudge_appends_handoff_note(self) -> None:
        original = "\n".join([
            author_nudge.nudge_marker("episode-1"),
            "Original friendly reminder.",
            "/dashboard route:reviewers",
        ])
        body = author_nudge.render_completed_nudge(
            original,
            "https://example.test/status",
            "episode-1",
            NOW,
        )

        self.assertTrue(body.startswith(original))
        self.assertIn(
            "_Outdated as of 2026-07-17 00:00 UTC: this pull request is no "
            "longer waiting on you.",
            body,
        )
        self.assertIn("[dashboard status comment](https://example.test/status)", body)
        self.assertIn("/dashboard route:reviewers", body)
        self.assertIn(author_nudge.completed_nudge_marker("episode-1"), body)
        self.assertEqual(
            1,
            body.count(author_nudge.completed_nudge_marker("episode-1")),
        )

    def test_rendered_gate_completion_does_not_claim_author_handoff(self) -> None:
        body = author_nudge.render_completed_nudge(
            "Original friendly reminder.",
            "https://example.test/status",
            "episode-1",
            NOW,
            "routing_changed",
        )

        self.assertIn("this reminder no longer reflects the current dashboard state", body)
        self.assertIn("to see whether action is needed", body)
        self.assertNotIn("no longer waiting on you", body)

    @patch.object(author_nudge, "minimize_comment")
    @patch.object(author_nudge, "comment_minimization_reason", return_value="")
    @patch.object(author_nudge, "run_gh")
    @patch.object(author_nudge, "publish_pr_status")
    @patch.object(
        author_nudge,
        "managed_status_comments",
        return_value=[{"html_url": "https://example.test/status"}],
    )
    @patch.object(
        author_nudge,
        "existing_nudge_comment",
        return_value={
            "id": 17,
            "node_id": "IC_17",
            "created_at": "2026-07-10T00:00:00Z",
            "body": "\n".join([
                author_nudge.nudge_marker("episode-1"),
                "Original friendly reminder.",
            ]),
        },
    )
    def test_completion_appends_note_then_marks_comment_outdated(
        self,
        _existing_nudge,
        _status_comments,
        publish_status,
        run_gh,
        minimization_reason,
        minimize_comment,
    ) -> None:
        dashboard_state = build_dashboard_state(author_result("approver"))

        author_nudge.ensure_nudge_completed(
            "open-telemetry/example",
            1,
            "episode-1",
            dashboard_state,
            NOW,
        )

        publish_status.assert_not_called()
        command = run_gh.call_args.args[0]
        self.assertEqual(command[2:4], ["--method", "PATCH"])
        self.assertIn("repos/open-telemetry/example/issues/comments/17", command)
        self.assertIn("Original friendly reminder.", command[-1])
        self.assertIn(
            "this pull request is no longer waiting on you",
            command[-1],
        )
        self.assertIn(author_nudge.completed_nudge_marker("episode-1"), command[-1])
        minimization_reason.assert_called_once_with("IC_17")
        minimize_comment.assert_called_once_with("IC_17")

    @patch.object(
        author_nudge,
        "minimize_comment",
        side_effect=RuntimeError("minimize failed"),
    )
    @patch.object(author_nudge, "comment_minimization_reason", return_value="")
    @patch.object(author_nudge, "run_gh")
    @patch.object(author_nudge, "publish_pr_status")
    @patch.object(
        author_nudge,
        "managed_status_comments",
        return_value=[{"html_url": "https://example.test/status"}],
    )
    @patch.object(
        author_nudge,
        "existing_nudge_comment",
        return_value={
            "id": 17,
            "node_id": "IC_17",
            "body": "\n".join([
                author_nudge.nudge_marker("episode-1"),
                "Original friendly reminder.",
            ]),
        },
    )
    def test_failed_minimization_happens_after_completion_note_is_patched(
        self,
        _existing_nudge,
        _status_comments,
        _publish_status,
        run_gh,
        _is_minimized,
        minimize_comment,
    ) -> None:
        with self.assertRaisesRegex(RuntimeError, "minimize failed"):
            author_nudge.ensure_nudge_completed(
                "open-telemetry/example",
                1,
                "episode-1",
                build_dashboard_state(),
                NOW,
            )

        self.assertIn(
            author_nudge.completed_nudge_marker("episode-1"),
            run_gh.call_args.args[0][-1],
        )
        minimize_comment.assert_called_once_with("IC_17")

    @patch.object(author_nudge, "minimize_comment")
    @patch.object(author_nudge, "comment_minimization_reason", return_value="")
    @patch.object(author_nudge, "run_gh")
    @patch.object(author_nudge, "publish_pr_status")
    @patch.object(
        author_nudge,
        "existing_nudge_comment",
        return_value={
            "id": 17,
            "node_id": "IC_17",
            "created_at": "2026-07-10T00:00:00Z",
            "body": "\n".join([
                author_nudge.nudge_marker("episode-1"),
                author_nudge.completed_nudge_marker("episode-1"),
            ]),
        },
    )
    def test_completion_marker_prevents_duplicate_edit(
        self,
        _existing_nudge,
        publish_status,
        run_gh,
        minimization_reason,
        minimize_comment,
    ) -> None:
        author_nudge.ensure_nudge_completed(
            "open-telemetry/example",
            1,
            "episode-1",
            build_dashboard_state(),
            NOW,
        )

        publish_status.assert_not_called()
        run_gh.assert_not_called()
        minimization_reason.assert_called_once_with("IC_17")
        minimize_comment.assert_called_once_with("IC_17")

    @patch.object(author_nudge, "minimize_comment")
    @patch.object(
        author_nudge,
        "comment_minimization_reason",
        return_value="OUTDATED",
    )
    @patch.object(author_nudge, "run_gh")
    @patch.object(author_nudge, "publish_pr_status")
    @patch.object(
        author_nudge,
        "existing_nudge_comment",
        return_value={
            "id": 17,
            "node_id": "IC_17",
            "body": "\n".join([
                author_nudge.nudge_marker("episode-1"),
                author_nudge.completed_nudge_marker("episode-1"),
            ]),
        },
    )
    def test_completed_and_minimized_comment_is_unchanged(
        self,
        _existing_nudge,
        publish_status,
        run_gh,
        minimization_reason,
        minimize_comment,
    ) -> None:
        author_nudge.ensure_nudge_completed(
            "open-telemetry/example",
            1,
            "episode-1",
            build_dashboard_state(),
            NOW,
        )

        publish_status.assert_not_called()
        run_gh.assert_not_called()
        minimization_reason.assert_called_once_with("IC_17")
        minimize_comment.assert_not_called()

    @patch.object(author_nudge, "minimize_comment")
    @patch.object(author_nudge, "unminimize_comment")
    @patch.object(
        author_nudge,
        "comment_minimization_reason",
        return_value="SPAM",
    )
    @patch.object(author_nudge, "run_gh")
    @patch.object(author_nudge, "publish_pr_status")
    @patch.object(
        author_nudge,
        "existing_nudge_comment",
        return_value={
            "id": 17,
            "node_id": "IC_17",
            "body": "\n".join([
                author_nudge.nudge_marker("episode-1"),
                author_nudge.completed_nudge_marker("episode-1"),
            ]),
        },
    )
    def test_completed_comment_with_other_classifier_is_reclassified(
        self,
        _existing_nudge,
        publish_status,
        run_gh,
        minimization_reason,
        unminimize_comment,
        minimize_comment,
    ) -> None:
        author_nudge.ensure_nudge_completed(
            "open-telemetry/example",
            1,
            "episode-1",
            build_dashboard_state(),
            NOW,
        )

        publish_status.assert_not_called()
        run_gh.assert_not_called()
        minimization_reason.assert_called_once_with("IC_17")
        unminimize_comment.assert_called_once_with("IC_17")
        minimize_comment.assert_called_once_with("IC_17")

    @patch.object(
        author_nudge,
        "gh_graphql",
        return_value={
            "data": {
                "node": {
                    "isMinimized": True,
                    "minimizedReason": "outdated",
                },
            },
        },
    )
    def test_comment_minimization_reason_uses_node_id(self, graphql) -> None:
        self.assertEqual(
            "OUTDATED",
            author_nudge.comment_minimization_reason("IC_17"),
        )

        query, variables = graphql.call_args.args
        self.assertIn("isMinimized", query)
        self.assertIn("minimizedReason", query)
        self.assertEqual({"id": "IC_17"}, variables)

    @patch.object(
        author_nudge,
        "gh_graphql",
        return_value={
            "data": {
                "unminimizeComment": {
                    "unminimizedComment": {"isMinimized": False},
                },
            },
        },
    )
    def test_unminimize_comment_uses_node_id(self, graphql) -> None:
        author_nudge.unminimize_comment("IC_17")

        query, variables = graphql.call_args.args
        self.assertIn("unminimizeComment", query)
        self.assertEqual({"id": "IC_17"}, variables)

    @patch.object(
        author_nudge,
        "gh_graphql",
        return_value={
            "data": {
                "minimizeComment": {
                    "minimizedComment": {"isMinimized": True},
                },
            },
        },
    )
    def test_minimize_comment_classifies_comment_as_outdated(self, graphql) -> None:
        author_nudge.minimize_comment("IC_17")

        query, variables = graphql.call_args.args
        self.assertIn("classifier: OUTDATED", query)
        self.assertEqual({"id": "IC_17"}, variables)

    @patch.object(
        author_nudge,
        "gh_api",
        return_value=[
            {
                "performed_via_github_app": {
                    "slug": "opentelemetry-pr-dashboard",
                },
                "created_at": "2026-07-17T00:00:02Z",
                "body": author_nudge.nudge_marker("recovered-episode"),
            },
        ],
    )
    def test_recovers_legacy_episode_from_posted_comment(self, _gh_api) -> None:
        self.assertEqual(
            "recovered-episode",
            author_nudge.recover_legacy_nudge_episode_id(
                "open-telemetry/example",
                1,
                "legacy-nudge:2026-07-17T00:00:00.403635+00:00",
            ),
        )

    @patch.object(
        author_nudge,
        "gh_api",
        return_value=[
            {
                "performed_via_github_app": {
                    "slug": "opentelemetry-pr-dashboard",
                },
                "created_at": "2026-07-17T12:00:00Z",
                "body": author_nudge.nudge_marker("recovered-episode"),
            },
        ],
    )
    def test_recovers_legacy_episode_from_long_delivery_run(self, _gh_api) -> None:
        self.assertEqual(
            "recovered-episode",
            author_nudge.recover_legacy_nudge_episode_id(
                "open-telemetry/example",
                1,
                "legacy-nudge:2026-07-17T00:00:00+00:00",
            ),
        )

    @patch.object(
        author_nudge,
        "gh_api",
        return_value=[
            {
                "performed_via_github_app": {"slug": "opentelemetry-pr-dashboard"},
                "body": author_nudge.nudge_marker("previous-episode"),
            },
            {
                "performed_via_github_app": {"slug": "opentelemetry-pr-dashboard"},
                "body": author_nudge.nudge_marker("episode-1"),
                "created_at": "2026-07-17T00:00:00Z",
            },
        ],
    )
    def test_existing_nudge_matches_current_episode(self, _gh_api) -> None:
        comment = author_nudge.existing_nudge_comment(
            "open-telemetry/example",
            1,
            "episode-1",
        )

        self.assertEqual(comment["created_at"], "2026-07-17T00:00:00Z")

    @patch.object(author_nudge, "run_gh")
    @patch.object(author_nudge, "publish_pr_status")
    @patch.object(author_nudge, "managed_status_comments")
    @patch.object(
        author_nudge,
        "gh_api",
        side_effect=[
            [],
            {"state": "open", "draft": False, "user": {"login": "alice"}},
        ],
    )
    def test_posts_nudge_after_ensuring_status_comment(
        self,
        _gh_api,
        managed_status_comments,
        publish_status,
        run_gh,
    ) -> None:
        managed_status_comments.return_value = [
            {"html_url": "https://example.test/status"}
        ]
        dashboard_state = build_dashboard_state(author_result())

        nudged_at = author_nudge.ensure_nudge(
            "open-telemetry/example",
            1,
            author_result(),
            dashboard_state,
            "2026-07-10T00:00:00+00:00",
            NOW,
        )

        self.assertEqual(nudged_at, "2026-07-17T00:00:00+00:00")
        publish_status.assert_called_once_with(
            "open-telemetry/example", 1, dashboard_state
        )
        self.assertIn("@alice", run_gh.call_args.args[0][-1])

    @patch.object(author_nudge, "run_gh")
    @patch.object(author_nudge, "publish_pr_status")
    @patch.object(
        author_nudge,
        "existing_nudge_comment",
        return_value={"created_at": "2026-07-11T00:00:00Z"},
    )
    def test_existing_episode_marker_prevents_duplicate_after_state_loss(
        self,
        existing_comment,
        publish_status,
        run_gh,
    ) -> None:
        nudged_at = author_nudge.ensure_nudge(
            "open-telemetry/example",
            1,
            author_result(),
            build_dashboard_state(author_result()),
            "2026-07-17T00:00:00+00:00",
            NOW,
        )

        self.assertEqual(nudged_at, "2026-07-11T00:00:00Z")
        existing_comment.assert_called_once_with(
            "open-telemetry/example",
            1,
            "episode-1",
        )
        publish_status.assert_not_called()
        run_gh.assert_not_called()

def run_gh_calls(run_gh) -> list[list[str]]:
    """The commands passed to the comment helper, in call order."""
    return [call.args[0] for call in run_gh.all_calls if "comments" in call.args[0][-3]]


class StaleCloseTest(unittest.TestCase):
    NUDGED = {
        "1": {
            "waiting_since": "2026-06-01T00:00:00+00:00",
            "nudged_at": "2026-07-01T00:00:00+00:00",
            "episode_id": "episode-1",
        }
    }

    def deliver(
        self,
        *,
        env: dict[str, str] | None = None,
        snapshot=None,
        pr: dict | None = None,
        existing_nudge: dict | None = {"id": 7},
        comments: list | None = None,
        nudges: dict | None = None,
        facts: dict | None = None,
        events: list | None = None,
        close_fails: bool = False,
        comment_fails: bool = False,
    ):
        env = {"CLOSE_STALE_AFTER_DAYS": "14"} if env is None else env
        pr = {"state": "open", "draft": False, "labels": [], **(pr or {})}
        with patch.dict("os.environ", env, clear=False), \
            patch.object(author_nudge, "save_author_nudges") as saved, \
            patch.object(
                author_nudge,
                "load_author_nudges",
                return_value=nudges or self.NUDGED,
            ), \
            patch.object(
                author_nudge,
                "load_dashboard_state_cache",
                return_value=build_dashboard_state(
                    author_result(**{
                        "last_new_activity_at": "2026-06-01T00:00:00+00:00",
                        **(facts or {}),
                    })
                ),
            ), \
            patch.object(
                author_nudge,
                "fetch_routing_snapshot",
                return_value=snapshot or routing_snapshot(),
            ), \
            patch.object(
                author_nudge, "existing_nudge_comment", return_value=existing_nudge
            ), \
            patch.object(
                author_nudge,
                "managed_status_comments",
                return_value=[{"html_url": "https://example.test/status"}],
            ), \
            patch.object(
                author_nudge,
                "gh_api",
                side_effect=lambda path, **_: (
                    pr if "/pulls/" in path
                    else (events or []) if "/events" in path
                    else (comments or [])
                ),
            ), \
            patch.object(
                author_nudge,
                "run_gh_json",
                side_effect=RuntimeError("comment failed")
                if comment_fails
                else lambda cmd, **_: {"id": 99},
            ) as run_gh_json, \
            patch.object(
                author_nudge,
                "run_gh",
                side_effect=lambda cmd, **_: (_ for _ in ()).throw(
                    RuntimeError("close failed")
                )
                if close_fails and "PATCH" in cmd
                else "",
            ) as run_gh:
            errors = author_nudge.deliver_prepared_author_nudges(
                "open-telemetry/example",
                NOW,
            )
        self.errors = errors
        # Callers inspect every gh call in order, whichever helper made it.
        run_gh.all_calls = run_gh_json.call_args_list + run_gh.call_args_list
        return run_gh, saved

    def test_comments_then_closes_and_persists_closure(self) -> None:
        run_gh, saved = self.deliver()

        self.assertEqual([], self.errors)
        comment_call = run_gh_calls(run_gh)[0]
        close_call, confirm_call = (
            call.args[0] for call in run_gh.call_args_list
        )
        self.assertEqual(
            ["gh", "api", "--method", "PATCH",
             "repos/open-telemetry/example/pulls/1", "-f", "state=closed"],
            close_call,
        )
        # The explanation is posted pending, and only confirmed after the
        # close succeeded.
        self.assertIn("issues/1/comments", comment_call[4])
        self.assertIn(
            "<!-- pull-request-dashboard-stale-close-pending:episode-1 -->",
            comment_call[-1],
        )
        self.assertIn("@alice", comment_call[-1])
        self.assertIn("issues/comments/99", confirm_call[4])
        self.assertIn(
            "<!-- pull-request-dashboard-stale-close:episode-1 -->",
            confirm_call[-1],
        )
        self.assertNotIn("close-pending", confirm_call[-1])
        # The clock is gone and the reminder is queued for retirement, so a
        # reopened PR cannot be closed again from the old entry.
        self.assertEqual(
            {"1": {"completions": [{
                "episode_id": "episode-1",
                "completed_at": "2026-07-17T00:00:00+00:00",
                "kind": "routing_changed",
            }]}},
            saved.call_args.args[0],
        )

    def test_reopened_episode_is_not_closed_again(self) -> None:
        run_gh, _saved = self.deliver(comments=[{
            "body": author_nudge.stale_close_marker("episode-1"),
            "performed_via_github_app": {"slug": author_nudge.DASHBOARD_APP_SLUG},
        }])

        run_gh.assert_not_called()

    def test_disabled_by_default(self) -> None:
        run_gh, _saved = self.deliver(env={"CLOSE_STALE_AFTER_DAYS": ""})

        run_gh.assert_not_called()

    def test_waits_for_full_period_after_reminder(self) -> None:
        run_gh, _saved = self.deliver(env={"CLOSE_STALE_AFTER_DAYS": "30"})

        run_gh.assert_not_called()

    def test_exempt_label_prevents_close(self) -> None:
        run_gh, _saved = self.deliver(
            env={
                "CLOSE_STALE_AFTER_DAYS": "14",
                "CLOSE_STALE_EXEMPT_LABELS_JSON": '["never-stale"]',
            },
            pr={"labels": [{"name": "never-stale"}]},
        )

        run_gh.assert_not_called()

    def test_changed_routing_input_prevents_close(self) -> None:
        run_gh, _saved = self.deliver(snapshot=routing_snapshot(fingerprint="newer"))

        run_gh.assert_not_called()

    def test_new_activity_inside_the_period_prevents_close(self) -> None:
        # A reviewer comment three days ago restarts the quiet period even
        # though the reminder is 16 days old and the route is unchanged.
        run_gh, _saved = self.deliver(
            facts={"last_new_activity_at": "2026-07-14T00:00:00+00:00"},
        )

        self.assertEqual([], run_gh.all_calls)

    def test_activity_before_the_reminder_does_not_delay_close(self) -> None:
        run_gh, _saved = self.deliver(
            facts={"last_new_activity_at": "2026-06-20T00:00:00+00:00"},
        )

        self.assertEqual(3, len(run_gh.all_calls))

    def test_closes_once_period_has_passed_since_latest_activity(self) -> None:
        run_gh, _saved = self.deliver(
            facts={"last_new_activity_at": "2026-07-02T00:00:00+00:00"},
        )

        self.assertEqual(3, len(run_gh.all_calls))

    def test_cached_result_without_activity_time_does_not_close(self) -> None:
        run_gh, _saved = self.deliver(facts={"last_new_activity_at": ""})

        self.assertEqual([], run_gh.all_calls)

    def test_comment_failure_leaves_pr_open_and_clock_intact(self) -> None:
        run_gh, saved = self.deliver(comment_fails=True)

        run_gh.assert_not_called()
        self.assertEqual(1, len(self.errors))
        self.assertEqual(self.NUDGED["1"], saved.call_args.args[0]["1"])

    def test_close_failure_keeps_pending_explanation_and_clock(self) -> None:
        run_gh, saved = self.deliver(close_fails=True)

        self.assertEqual(1, len(self.errors))
        self.assertEqual(
            [], [c for c in run_gh.call_args_list if "DELETE" in c.args[0]]
        )
        self.assertEqual(self.NUDGED["1"], saved.call_args.args[0]["1"])

    def test_reopened_after_unconfirmed_close_is_not_closed_again(self) -> None:
        pending = {
            "id": 55,
            "created_at": "2026-07-16T00:00:00Z",
            "body": author_nudge.stale_close_pending_marker("episode-1"),
            "performed_via_github_app": {"slug": author_nudge.DASHBOARD_APP_SLUG},
        }
        run_gh, _saved = self.deliver(
            comments=[pending],
            events=[
                {"event": "closed", "created_at": "2026-07-16T00:00:05Z"},
                {"event": "reopened", "created_at": "2026-07-16T01:00:00Z"},
            ],
        )

        self.assertEqual([], self.errors)
        # Only the marker is confirmed; the PR is not closed a second time.
        (confirm_call,) = (call.args[0] for call in run_gh.call_args_list)
        self.assertIn("issues/comments/55", confirm_call[4])
        self.assertIn(
            author_nudge.stale_close_marker("episode-1"), confirm_call[-1]
        )

    def test_close_before_pending_comment_does_not_count(self) -> None:
        pending = {
            "id": 55,
            "created_at": "2026-07-16T00:00:00Z",
            "body": author_nudge.stale_close_pending_marker("episode-1"),
            "performed_via_github_app": {"slug": author_nudge.DASHBOARD_APP_SLUG},
        }
        run_gh, _saved = self.deliver(
            comments=[pending],
            events=[{"event": "closed", "created_at": "2026-07-01T00:00:00Z"}],
        )

        close_call = run_gh.call_args_list[0].args[0]
        self.assertEqual("state=closed", close_call[-1])

    def test_retry_finishes_interrupted_close_without_reposting(self) -> None:
        pending = {
            "id": 55,
            "body": author_nudge.stale_close_pending_marker("episode-1"),
            "performed_via_github_app": {"slug": author_nudge.DASHBOARD_APP_SLUG},
        }
        run_gh, _saved = self.deliver(comments=[pending])

        self.assertEqual([], self.errors)
        self.assertEqual(
            [], [c for c in run_gh.all_calls if "POST" in c.args[0]]
        )
        close_call, confirm_call = (
            call.args[0] for call in run_gh.call_args_list
        )
        self.assertEqual("state=closed", close_call[-1])
        self.assertIn("issues/comments/55", confirm_call[4])
        self.assertIn(
            author_nudge.stale_close_marker("episode-1"), confirm_call[-1]
        )

    def test_missing_reminder_prevents_close(self) -> None:
        run_gh, _saved = self.deliver(existing_nudge=None)

        run_gh.assert_not_called()


if __name__ == "__main__":
    unittest.main()