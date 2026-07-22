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
            "labels": [],
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

    def test_routing_fingerprint_tracks_normalized_labels(self) -> None:
        raw = {
            "checks": [],
            "issue_comments": [],
            "pr": {"labels": [{"name": "needs-triage"}]},
            "review_comments": [],
            "reviews": [],
            "review_threads": [],
        }
        baseline = author_nudge.routing_input_fingerprint(raw)
        raw["pr"]["labels"].append({"name": "documentation"})

        self.assertEqual(baseline, author_nudge.routing_input_fingerprint(raw))

        raw["pr"]["labels"].append({"name": "dashboard:route-overridden"})
        overridden = author_nudge.routing_input_fingerprint(raw)

        self.assertNotEqual(baseline, overridden)
        raw["pr"]["labels"].reverse()
        self.assertEqual(overridden, author_nudge.routing_input_fingerprint(raw))

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

    def test_routing_fingerprint_normalizes_and_tracks_base_branch(self) -> None:
        accepted = {
            "pr": {"baseRefName": "main"},
        }
        live = {
            "pr": {"base": {"ref": "main"}},
        }

        self.assertEqual(
            author_nudge.routing_input_fingerprint(accepted),
            author_nudge.routing_input_fingerprint(live),
        )
        live["pr"]["base"]["ref"] = "release"
        self.assertNotEqual(
            author_nudge.routing_input_fingerprint(accepted),
            author_nudge.routing_input_fingerprint(live),
        )

    @patch.object(author_nudge, "gh_required_check_contexts", return_value=[])
    @patch.object(
        author_nudge,
        "gh_pr_check_rollup",
        return_value={
            "required": [{"name": "build", "bucket": "fail"}],
            "non_blocking_failures": [],
        },
    )
    @patch.object(author_nudge, "fetch_review_threads", return_value=[])
    @patch.object(author_nudge, "fetch_pr_review_data", return_value={})
    @patch.object(author_nudge, "fetch_pr_issue_comments", return_value=[])
    @patch.object(author_nudge, "gh_api")
    def test_fetch_current_routing_state_includes_required_checks(
        self,
        gh_api,
        _fetch_issue_comments,
        _fetch_review_data,
        _fetch_review_threads,
        gh_pr_check_rollup,
        gh_required_check_contexts,
    ) -> None:
        pr = {
            "node_id": "PR_node",
            "base": {"ref": "main"},
            "body": "Current body",
            "head": {"sha": "current-head"},
            "labels": [
                {"name": "needs-triage"},
                {"name": "dashboard:route-overridden"},
            ],
            "title": "Current title",
        }
        gh_api.side_effect = lambda path, paginate=False: (
            pr if path.endswith("/pulls/1") else []
        )

        current_pr, fingerprint = author_nudge.fetch_current_pr_routing_state(
            "open-telemetry/example",
            1,
        )

        self.assertEqual(pr, current_pr)
        self.assertEqual(
            author_nudge.routing_input_fingerprint({
                "checks": [{"name": "build", "bucket": "fail"}],
                "issue_comments": [],
                "labels": [
                    {"name": "needs-triage"},
                    {"name": "dashboard:route-overridden"},
                ],
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
        gh_required_check_contexts.assert_called_once_with(
            "open-telemetry/example",
            "main",
        )

    def test_nudge_advertises_dashboard_override_command(self) -> None:
        body = author_nudge.render_nudge(
            "alice",
            "https://example.test/status",
            "episode-1",
        )

        self.assertIn(
            "comment `/dashboard route:reviewers` to route it from waiting on the "
            "author to waiting on reviewers",
            body,
        )

    def test_first_author_route_observation_starts_clock(self) -> None:
        due, entry = author_nudge.plan_nudge(author_result(), None, NOW)

        self.assertFalse(due)
        self.assertEqual(
            entry,
            {"waiting_since": "2026-07-17T00:00:00+00:00", "nudged_at": ""},
        )

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

    def test_returning_to_author_route_starts_new_episode(self) -> None:
        previous = {
            "waiting_since": "2026-07-01T00:00:00+00:00",
            "nudged_at": "2026-07-10T00:00:00+00:00",
        }

        due, entry = author_nudge.plan_nudge(author_result("approver"), previous, NOW)
        self.assertFalse(due)
        self.assertIsNone(entry)

        due, entry = author_nudge.plan_nudge(author_result(), entry, NOW)
        self.assertFalse(due)
        self.assertEqual(
            entry,
            {"waiting_since": "2026-07-17T00:00:00+00:00", "nudged_at": ""},
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
            {"2": {"waiting_since": "2026-07-17T00:00:00+00:00", "nudged_at": ""}},
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
            "state": "open",
            "draft": False,
            "head": {"sha": "current-head"},
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
            "state": "open",
            "draft": False,
            "head": {"sha": "new-head"},
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
            "state": "open",
            "draft": False,
            "head": {"sha": "current-head"},
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
        for state, draft in (("closed", False), ("open", True)):
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
                        "draft": draft,
                        "head": {"sha": "current-head"},
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
        self.assertIn("[dashboard status comment](https://example.test/status)", body)
        self.assertIn(
            author_nudge.nudge_marker("episode-1"),
            body,
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