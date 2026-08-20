from __future__ import annotations

from datetime import datetime, timezone
import unittest
from unittest.mock import patch

import author_nudge


NOW = datetime(2026, 7, 17, tzinfo=timezone.utc)


def author_result(route: str = "author") -> dict:
    return {
        "route": route,
        "facts": {
            "author": "alice",
            "author_nudge_episode_id": "episode-1",
            "head_sha": "current-head",
            "routing_input_fingerprint": "current-fingerprint",
        },
    }


class AuthorNudgePolicyTest(unittest.TestCase):
    def test_routing_fingerprint_ignores_dashboard_comments_and_tracks_author_activity(self) -> None:
        raw = {
            "checks": [],
            "issue_comments": [],
            "review_comments": [],
            "reviews": [],
            "review_threads": [],
        }
        baseline = author_nudge.routing_input_fingerprint(raw)

        raw["issue_comments"].append({
            "id": 1,
            "user": {"login": "opentelemetry-pr-dashboard[bot]"},
            "body": "dashboard status",
        })
        self.assertEqual(baseline, author_nudge.routing_input_fingerprint(raw))

        raw["issue_comments"].append({
            "id": 2,
            "user": {"login": "alice"},
            "body": "I addressed the feedback.",
        })
        self.assertNotEqual(baseline, author_nudge.routing_input_fingerprint(raw))

    def test_routing_fingerprint_tracks_required_check_state(self) -> None:
        raw = {
            "checks": [{"name": "build", "bucket": "fail"}],
            "issue_comments": [],
            "review_comments": [],
            "reviews": [],
            "review_threads": [],
        }
        failing = author_nudge.routing_input_fingerprint(raw)

        raw["checks"][0]["bucket"] = "pass"

        self.assertNotEqual(failing, author_nudge.routing_input_fingerprint(raw))

    def test_routing_fingerprint_tracks_review_requests(self) -> None:
        raw = {"review_requests": []}
        baseline = author_nudge.routing_input_fingerprint(raw)

        raw["review_requests"].append({
            "__typename": "User",
            "login": "reviewer",
        })

        self.assertNotEqual(baseline, author_nudge.routing_input_fingerprint(raw))

    def test_routing_fingerprint_tracks_pr_title_and_body(self) -> None:
        raw = {
            "checks": [],
            "issue_comments": [],
            "pr": {"title": "Original title", "body": "Original body"},
            "review_comments": [],
            "reviews": [],
            "review_threads": [],
        }
        baseline = author_nudge.routing_input_fingerprint(raw)

        raw["pr"]["title"] = "Updated title"
        title_updated = author_nudge.routing_input_fingerprint(raw)
        raw["pr"]["title"] = "Original title"
        raw["pr"]["body"] = "Updated body"

        self.assertNotEqual(baseline, title_updated)
        self.assertNotEqual(baseline, author_nudge.routing_input_fingerprint(raw))

    def test_routing_fingerprint_tracks_base_branch(self) -> None:
        raw = {"pr": {"baseRefName": "main"}}
        baseline = author_nudge.routing_input_fingerprint(raw)

        raw["pr"]["baseRefName"] = "release"

        self.assertNotEqual(baseline, author_nudge.routing_input_fingerprint(raw))

    def test_routing_fingerprint_tracks_conflict_state(self) -> None:
        raw = {
            "pr": {
                "mergeable": "MERGEABLE",
                "mergeStateStatus": "CLEAN",
            }
        }
        baseline = author_nudge.routing_input_fingerprint(raw)

        raw["pr"]["mergeable"] = "CONFLICTING"
        raw["pr"]["mergeStateStatus"] = "DIRTY"

        self.assertNotEqual(baseline, author_nudge.routing_input_fingerprint(raw))

    @patch("github_cli.gh_branch_rules", return_value=[])
    @patch(
        "github_cli.gh_pr_check_rollup",
        return_value={
            "head_oid": "current-head",
            "required": [{"name": "build", "bucket": "fail"}],
            "non_blocking_failures": [],
            "code_scanning": [],
            "pending": [],
        },
    )
    @patch("github_cli.fetch_review_threads", return_value=[])
    @patch("github_cli.fetch_review_requests", return_value=[])
    @patch("github_cli.fetch_pr_reviews", return_value=[])
    @patch("github_cli.fetch_pr_issue_comments", return_value=[])
    @patch("github_cli.gh_api", return_value=[])
    @patch("github_cli.gh_pr_view")
    def test_fetch_current_routing_state_includes_required_checks(
        self,
        gh_pr_view,
        _gh_api,
        _fetch_issue_comments,
        _fetch_review_data,
        _fetch_review_requests,
        _fetch_review_threads,
        gh_pr_check_rollup,
        gh_branch_rules,
    ) -> None:
        pr = {
            "id": "PR_node",
            "baseRefName": "main",
            "body": "Current body",
            "headRefOid": "current-head",
            "labels": [{"name": "needs-triage"}],
            "title": "Current title",
        }
        gh_pr_view.return_value = pr

        current_pr, fingerprint = author_nudge.fetch_current_pr_routing_state(
            "open-telemetry/example",
            1,
        )

        self.assertEqual(pr, current_pr)
        self.assertEqual(
            author_nudge.routing_input_fingerprint({
                "checks": [{"name": "build", "bucket": "fail"}],
                "issue_comments": [],
                "pr": pr,
                "review_comments": [],
                "reviews": [],
                "review_threads": [],
            }),
            fingerprint,
        )
        gh_pr_check_rollup.assert_called_once_with(
            "open-telemetry/example",
            "PR_node",
            [],
        )
        gh_branch_rules.assert_called_once_with(
            "open-telemetry/example",
            "main",
        )

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
            "is needed, or ask a follow-up. The dashboard routes it onward once "
            "nothing on the list is waiting on you.",
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
        result = author_result()
        result["facts"]["conflicts"] = "yes"

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

    def test_conflict_nudge_is_due_after_standard_week(self) -> None:
        result = author_result()
        result["facts"]["conflicts"] = "yes"

        due, _entry = author_nudge.plan_nudge(
            result,
            {"waiting_since": "2026-07-10T00:00:00+00:00", "nudged_at": ""},
            NOW,
        )

        self.assertTrue(due)

    def test_nudge_is_due_after_one_week(self) -> None:
        due, _entry = author_nudge.plan_nudge(
            author_result(),
            {"waiting_since": "2026-07-10T00:00:00+00:00", "nudged_at": ""},
            NOW,
        )

        self.assertTrue(due)

    def test_nudge_is_not_due_before_one_week(self) -> None:
        due, _entry = author_nudge.plan_nudge(
            author_result(),
            {"waiting_since": "2026-07-10T00:00:01+00:00", "nudged_at": ""},
            NOW,
        )

        self.assertFalse(due)

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
        result = author_result()
        result["facts"]["conflicts"] = "yes"

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
        held = author_result()
        held["facts"]["route_held_for_gates"] = True

        due, entry = author_nudge.plan_nudge(held, previous, NOW)

        self.assertFalse(due)
        self.assertEqual(previous, entry)

    def test_gate_held_route_uses_standard_nudge_deadline(self) -> None:
        held = author_result()
        held["facts"]["route_held_for_gates"] = True

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
        next_episode = author_result()
        next_episode["facts"]["author_nudge_episode_id"] = "episode-2"

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
            {"failed": True, "route": "unknown"},
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
        return_value={"prs": {"1": author_result()}},
    )
    @patch.object(
        author_nudge,
        "fetch_current_pr_routing_state",
        return_value=({
            "state": "OPEN",
            "isDraft": False,
            "headRefOid": "current-head",
        }, "current-fingerprint"),
    )
    def test_delivery_records_posted_nudge(
        self,
        fetch_current_state,
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
        fetch_current_state.assert_called_once_with("open-telemetry/example", 1)
        ensure_nudge.assert_called_once()
        save_nudges.assert_called_once_with({
            "1": {
                "waiting_since": "2026-07-01T00:00:00+00:00",
                "nudged_at": "2026-07-17T00:00:00+00:00",
                "episode_id": "episode-1",
            },
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
        return_value={"prs": {"1": author_result("approver")}},
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
        return_value={"prs": {"1": author_result("approver")}},
    )
    def test_delivery_recovers_legacy_posted_nudge_episode(
        self,
        dashboard_state,
        _load_nudges,
        _save_nudges,
        ensure_completed,
        recover_episode,
    ) -> None:
        errors = author_nudge.deliver_prepared_author_nudges(
            "open-telemetry/example",
            NOW,
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
        return_value={"prs": {"1": author_result("approver")}},
    )
    def test_missing_legacy_comment_discards_completion(
        self,
        _dashboard_state,
        _load_nudges,
        save_nudges,
        ensure_completed,
        _recover_episode,
    ) -> None:
        with patch("sys.stderr") as stderr:
            errors = author_nudge.deliver_prepared_author_nudges(
                "open-telemetry/example",
                NOW,
            )

        self.assertEqual([], errors)
        stderr.write.assert_any_call(
            "PR #1: legacy author nudge comment not found; discarding completion"
        )
        ensure_completed.assert_not_called()
        save_nudges.assert_called_once_with({})

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
                return_value={"prs": {"1": author_result()}},
            ),
            patch.object(
                author_nudge,
                "ensure_nudge_completed",
                side_effect=RuntimeError("retry"),
            ),
            patch.object(
                author_nudge,
                "fetch_current_pr_routing_state",
                return_value=({
                    "state": "CLOSED",
                    "isDraft": False,
                    "headRefOid": "current-head",
                }, "current-fingerprint"),
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

    def test_failed_completion_survives_successful_new_nudge(self) -> None:
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
                return_value={"prs": {"1": author_result()}},
            ),
            patch.object(
                author_nudge,
                "ensure_nudge_completed",
                side_effect=RuntimeError("retry"),
            ),
            patch.object(
                author_nudge,
                "fetch_current_pr_routing_state",
                return_value=({
                    "state": "OPEN",
                    "isDraft": False,
                    "headRefOid": "current-head",
                }, "current-fingerprint"),
            ),
            patch.object(
                author_nudge,
                "ensure_nudge",
                return_value="2026-07-17T00:00:00+00:00",
            ),
        ):
            errors = author_nudge.deliver_prepared_author_nudges(
                "open-telemetry/example",
                NOW,
            )

        self.assertEqual(["PR #1: retry"], errors)
        save_nudges.assert_called_once_with({
            "1": {
                "waiting_since": "2026-07-10T00:00:00+00:00",
                "nudged_at": "2026-07-17T00:00:00+00:00",
                "episode_id": "episode-1",
                "completions": [completion],
            }
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
                "routing_input_fingerprint": "current-fingerprint",
            }
        },
    )
    @patch.object(
        author_nudge,
        "load_dashboard_state_cache",
        return_value={"prs": {"1": author_result()}},
    )
    @patch.object(
        author_nudge,
        "fetch_current_pr_routing_state",
        return_value=({
            "state": "OPEN",
            "isDraft": False,
            "headRefOid": "new-head",
        }, "current-fingerprint"),
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
        return_value={"prs": {"1": author_result()}},
    )
    @patch.object(
        author_nudge,
        "fetch_current_pr_routing_state",
        return_value=({
            "state": "OPEN",
            "isDraft": False,
            "headRefOid": "current-head",
        }, "new-fingerprint"),
    )
    def test_delivery_defers_when_routing_inputs_changed(
        self,
        _fetch_current_state,
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
                    author_nudge.routing_input_fingerprint(clean_raw)
                ),
            }
        }
        with (
            patch.object(author_nudge, "load_author_nudges", return_value=pending),
            patch.object(author_nudge, "save_author_nudges") as save_nudges,
            patch.object(
                author_nudge,
                "load_dashboard_state_cache",
                return_value={"prs": {"1": author_result()}},
            ),
            patch.object(
                author_nudge,
                "fetch_current_pr_routing_state",
                return_value=(
                    {
                        "state": "OPEN",
                        "isDraft": False,
                        "headRefOid": "current-head",
                    },
                    author_nudge.routing_input_fingerprint(conflicted_raw),
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
                    return_value={"prs": {"1": author_result()}},
                ),
                patch.object(
                    author_nudge,
                    "fetch_current_pr_routing_state",
                    return_value=({
                        "state": state,
                        "isDraft": draft,
                        "headRefOid": "current-head",
                    }, "current-fingerprint"),
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
        dashboard_state = {"prs": {"1": author_result("approver")}}

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
                {"prs": {}},
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
            {"prs": {}},
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
            {"prs": {}},
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
            {"prs": {}},
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
        dashboard_state = {"prs": {"1": author_result()}}

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
            {"prs": {"1": author_result()}},
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


if __name__ == "__main__":
    unittest.main()