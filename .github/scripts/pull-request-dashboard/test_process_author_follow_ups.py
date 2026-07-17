from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import process_author_follow_ups
from state import AUTHOR_FOLLOW_UP_STATE_VERSION


NOW = datetime(2026, 7, 17, tzinfo=timezone.utc)
CYCLE_ID = "2026-07-01T00:00:00+00:00"


def follow_up_entry(**overrides: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "cycle_id": CYCLE_ID,
        "waiting_on_author_since": CYCLE_ID,
        "pending_handoff_since": "",
        "handoff_nudged_at": "2026-07-02T00:00:00+00:00",
        "general_nudged_at": "2026-07-08T00:00:00+00:00",
        "stale_applied_at": "",
        "stale_reset_at": "",
    }
    entry.update(overrides)
    return entry


def author_result() -> dict[str, object]:
    return {
        "route": "author",
        "facts": {
            "author": "alice",
            "last_author_activity_at": CYCLE_ID,
            "last_approver_activity_at": "2026-06-30T00:00:00Z",
            "last_external_activity_at": "",
            "waiting_since": CYCLE_ID,
            "head_sha": "accepted-head",
            "human_head_observed_at": "",
            "author_action_review_thread_urls": [
                "https://github.com/open-telemetry/example/pull/1#discussion_r1"
            ],
            "author_action_top_level_feedback_urls": [],
        },
    }


class ProcessAuthorFollowUpsTest(unittest.TestCase):
    @patch.object(process_author_follow_ups, "fetch_review_threads")
    def test_current_author_route_requires_an_unresolved_author_thread(
        self,
        fetch_review_threads,
    ) -> None:
        expected_url = author_result()["facts"]["author_action_review_thread_urls"][0]
        fetch_review_threads.return_value = [{
            "isResolved": True,
            "isOutdated": False,
            "comments": {"nodes": [{"url": expected_url}]},
        }]

        self.assertFalse(process_author_follow_ups.current_author_route(
            "open-telemetry/example",
            1,
            author_result(),
        ))

    @patch.object(
        process_author_follow_ups,
        "gh_pr_view",
        return_value={"headRefOid": "accepted-head"},
    )
    @patch.object(process_author_follow_ups, "fetch_pr_review_data")
    @patch.object(process_author_follow_ups, "gh_api")
    def test_current_human_activity_uses_only_qualifying_event_times(
        self,
        gh_api,
        fetch_pr_review_data,
        _gh_pr_view,
    ) -> None:
        def api_response(path: str, paginate: bool = False):
            self.assertTrue(paginate)
            if "/issues/" in path:
                return [
                    {
                        "user": {"login": "alice", "type": "User"},
                        "created_at": "2026-07-12T00:00:00Z",
                        "updated_at": "2026-07-16T00:00:00Z",
                    },
                    {
                        "user": {"login": "automation[bot]", "type": "Bot"},
                        "created_at": "2026-07-17T00:00:00Z",
                    },
                    {
                        "user": {"login": "dashboard", "type": "User"},
                        "created_at": "2026-07-17T00:00:00Z",
                        "performed_via_github_app": {"slug": "pull-request-dashboard"},
                    },
                ]
            if path.endswith("/comments?per_page=100"):
                return [{
                    "user": {"login": "reviewer", "type": "User"},
                    "created_at": "2026-07-13T00:00:00Z",
                    "updated_at": "2026-07-17T00:00:00Z",
                }]
            return [
                {
                    "author": {"login": "alice"},
                    "committer": {"login": "alice"},
                    "commit": {
                        "author": {"date": "2099-07-14T00:00:00Z"},
                        "committer": {"date": "2099-07-14T00:00:00Z"},
                    },
                },
                {
                    "author": {"login": "someone-else"},
                    "committer": {"login": "someone-else"},
                    "commit": {
                        "author": {"date": "2026-07-16T00:00:00Z"},
                        "committer": {"date": "2026-07-16T00:00:00Z"},
                    },
                },
            ]

        gh_api.side_effect = api_response
        fetch_pr_review_data.return_value = {
            "reviews": [
                {
                    "user": {"login": "reviewer", "__typename": "User"},
                    "submitted_at": "2026-07-15T00:00:00Z",
                    "updated_at": "2026-07-17T00:00:00Z",
                },
                {
                    "user": {"login": "review-bot", "__typename": "Bot"},
                    "submitted_at": "2026-07-17T00:00:00Z",
                    "updated_at": "2026-07-17T00:00:00Z",
                },
            ]
        }

        activity = process_author_follow_ups.current_human_activity(
            "open-telemetry/example",
            1,
            author_result(),
            NOW,
        )

        self.assertEqual(activity, datetime(2026, 7, 15, tzinfo=timezone.utc))

    @patch.object(
        process_author_follow_ups,
        "fetch_pr_review_data",
        return_value={"reviews": []},
    )
    @patch.object(
        process_author_follow_ups,
        "gh_pr_view",
        return_value={"headRefOid": "new-head"},
    )
    @patch.object(process_author_follow_ups, "gh_api")
    def test_current_human_activity_observes_old_dated_author_push(
        self,
        gh_api,
        _gh_pr_view,
        _fetch_pr_review_data,
    ) -> None:
        gh_api.side_effect = [
            [],
            [],
            [{
                "sha": "new-head",
                "author": {"login": "alice"},
                "committer": {"login": "alice"},
                "commit": {
                    "author": {"date": "2020-01-01T00:00:00Z"},
                    "committer": {"date": "2020-01-01T00:00:00Z"},
                },
            }],
        ]

        activity = process_author_follow_ups.current_human_activity(
            "open-telemetry/example",
            1,
            author_result(),
            NOW,
        )

        self.assertEqual(activity, NOW)

    @patch.object(
        process_author_follow_ups,
        "fetch_pr_review_data",
        return_value={"reviews": []},
    )
    @patch.object(
        process_author_follow_ups,
        "gh_pr_view",
        return_value={"headRefOid": "new-head"},
    )
    @patch.object(process_author_follow_ups, "gh_api")
    def test_current_human_activity_observes_reviewer_push(
        self,
        gh_api,
        _gh_pr_view,
        _fetch_pr_review_data,
    ) -> None:
        gh_api.side_effect = [
            [],
            [],
            [{
                "sha": "new-head",
                "author": {"login": "reviewer", "type": "User"},
                "committer": {"login": "web-flow", "type": "User"},
                "commit": {
                    "author": {"date": "2020-01-01T00:00:00Z"},
                    "committer": {"date": "2020-01-01T00:00:00Z"},
                },
            }],
        ]

        activity = process_author_follow_ups.current_human_activity(
            "open-telemetry/example",
            1,
            author_result(),
            NOW,
        )

        self.assertEqual(activity, NOW)

    @patch.object(
        process_author_follow_ups,
        "load_author_follow_ups",
        return_value={"1": {"stale_label_owned": False}},
    )
    def test_retry_snapshot_preserves_stale_label_ownership(
        self,
        _load_author_follow_ups,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot = Path(temp_dir) / "author-follow-up-state.json"
            snapshot.write_text(
                json.dumps({
                    "version": AUTHOR_FOLLOW_UP_STATE_VERSION,
                    "prs": {"1": {"stale_label_owned": True}},
                }),
                encoding="utf-8",
            )

            previous = process_author_follow_ups.previous_author_follow_ups(snapshot)

        self.assertEqual(previous, {"1": {"stale_label_owned": True}})

    @patch.object(process_author_follow_ups, "post_comment")
    @patch.object(process_author_follow_ups, "ensure_status_comment")
    @patch.object(process_author_follow_ups, "lifecycle_comments")
    def test_existing_cycle_marker_makes_nudge_retry_idempotent(
        self,
        lifecycle_comments,
        ensure_status_comment,
        post_comment,
    ) -> None:
        marker = process_author_follow_ups.lifecycle_marker(
            process_author_follow_ups.HANDOFF_NUDGE_MARKER_PREFIX,
            CYCLE_ID,
        )
        lifecycle_comments.return_value = [{
            "body": marker,
            "created_at": "2026-07-08T01:00:00Z",
        }]
        updated = follow_up_entry(handoff_nudged_at="2026-07-17T00:00:00+00:00")

        result = process_author_follow_ups.execute_action(
            "handoff-nudge",
            "open-telemetry/example",
            1,
            author_result(),
            None,
            updated,
            NOW,
        )

        self.assertEqual(result["handoff_nudged_at"], "2026-07-08T01:00:00Z")
        ensure_status_comment.assert_not_called()
        post_comment.assert_not_called()

    @patch.object(process_author_follow_ups, "post_comment")
    @patch.object(
        process_author_follow_ups,
        "ensure_status_comment",
        return_value="https://github.com/open-telemetry/example/pull/1#issuecomment-1",
    )
    @patch.object(process_author_follow_ups, "lifecycle_comments", return_value=[])
    def test_new_nudge_links_status_comment(
        self,
        _lifecycle_comments,
        _ensure_status_comment,
        post_comment,
    ) -> None:
        process_author_follow_ups.execute_action(
            "handoff-nudge",
            "open-telemetry/example",
            1,
            author_result(),
            None,
            follow_up_entry(),
            NOW,
        )

        body = post_comment.call_args.args[2]
        self.assertIn("@alice, this pull request is still waiting on your follow-up", body)
        self.assertIn("[dashboard status comment]", body)
        self.assertIn(process_author_follow_ups.HANDOFF_NUDGE_MARKER_PREFIX, body)

    def test_general_nudge_has_independent_marker_and_message(self) -> None:
        body = process_author_follow_ups.render_nudge(
            author_result(),
            "https://github.com/open-telemetry/example/pull/1#issuecomment-1",
            CYCLE_ID,
            "general-nudge",
        )

        self.assertIn("waiting on your follow-up for one week", body)
        self.assertIn(process_author_follow_ups.GENERAL_NUDGE_MARKER_PREFIX, body)
        self.assertNotIn(process_author_follow_ups.HANDOFF_NUDGE_MARKER_PREFIX, body)

    @patch.object(process_author_follow_ups, "current_author_route", return_value=True)
    @patch.object(
        process_author_follow_ups,
        "current_human_activity",
        return_value=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )
    @patch.object(process_author_follow_ups, "add_stale_label", return_value=False)
    def test_existing_stale_label_is_not_claimed_as_dashboard_owned(
        self,
        _add_stale_label,
        _current_human_activity,
        _current_author_route,
    ) -> None:
        updated = follow_up_entry(stale_applied_at="2026-07-17T00:00:00+00:00")

        result = process_author_follow_ups.execute_action(
            "stale",
            "open-telemetry/example",
            1,
            author_result(),
            follow_up_entry(),
            updated,
            NOW,
        )

        self.assertFalse(result["stale_label_owned"])

    @patch.object(process_author_follow_ups, "add_stale_label")
    @patch.object(process_author_follow_ups, "current_author_route", return_value=False)
    @patch.object(
        process_author_follow_ups,
        "current_human_activity",
        return_value=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )
    def test_stale_is_deferred_after_live_route_departure(
        self,
        _current_human_activity,
        _current_author_route,
        add_stale_label,
    ) -> None:
        updated = process_author_follow_ups.execute_action(
            "stale",
            "open-telemetry/example",
            1,
            author_result(),
            follow_up_entry(),
            follow_up_entry(stale_applied_at="2026-07-17T00:00:00+00:00"),
            NOW,
        )

        self.assertEqual(updated["stale_applied_at"], "")
        self.assertEqual(updated["stale_reset_at"], "2026-07-17T00:00:00+00:00")
        add_stale_label.assert_not_called()

    @patch.object(process_author_follow_ups, "add_stale_label")
    @patch.object(process_author_follow_ups, "current_author_route", return_value=True)
    @patch.object(
        process_author_follow_ups,
        "current_human_activity",
        return_value=datetime(2026, 7, 16, tzinfo=timezone.utc),
    )
    def test_stale_is_deferred_after_new_live_activity(
        self,
        _current_human_activity,
        _current_author_route,
        add_stale_label,
    ) -> None:
        updated = process_author_follow_ups.execute_action(
            "stale",
            "open-telemetry/example",
            1,
            author_result(),
            follow_up_entry(),
            follow_up_entry(stale_applied_at="2026-07-17T00:00:00+00:00"),
            NOW,
        )

        self.assertEqual(updated["stale_applied_at"], "")
        self.assertEqual(updated["stale_reset_at"], "2026-07-16T00:00:00+00:00")
        add_stale_label.assert_not_called()

    @patch.object(
        process_author_follow_ups,
        "stale_label_owned_by_dashboard",
        return_value=True,
    )
    @patch.object(
        process_author_follow_ups,
        "issue_label_names",
        return_value={"stale": "Stale"},
    )
    def test_existing_dashboard_stale_label_recovers_ownership(
        self,
        _issue_label_names,
        _stale_label_owned_by_dashboard,
    ) -> None:
        self.assertTrue(process_author_follow_ups.add_stale_label(
            "open-telemetry/example",
            1,
        ))

    @patch.object(process_author_follow_ups, "gh_api")
    def test_latest_stale_label_event_determines_ownership(self, gh_api) -> None:
        gh_api.return_value = [
            {
                "id": 1,
                "event": "labeled",
                "created_at": "2026-07-10T00:00:00Z",
                "label": {"name": "Stale"},
                "performed_via_github_app": {"slug": "other-app"},
            },
            {
                "id": 2,
                "event": "labeled",
                "created_at": "2026-07-11T00:00:00Z",
                "label": {"name": "stale"},
                "performed_via_github_app": {
                    "slug": process_author_follow_ups.DASHBOARD_APP_SLUG,
                },
            },
        ]

        self.assertTrue(process_author_follow_ups.stale_label_owned_by_dashboard(
            "open-telemetry/example",
            1,
        ))

    @patch.object(process_author_follow_ups, "remove_stale_label")
    def test_removes_only_dashboard_owned_stale_label(self, remove_stale_label) -> None:
        updated = follow_up_entry(stale_applied_at="")
        process_author_follow_ups.execute_action(
            "remove-stale",
            "open-telemetry/example",
            1,
            author_result(),
            follow_up_entry(stale_label_owned=False),
            updated,
            NOW,
        )
        remove_stale_label.assert_not_called()

        process_author_follow_ups.execute_action(
            "remove-stale",
            "open-telemetry/example",
            1,
            author_result(),
            follow_up_entry(stale_label_owned=True),
            updated,
            NOW,
        )
        remove_stale_label.assert_called_once_with("open-telemetry/example", 1)

    @patch.object(process_author_follow_ups, "remove_stale_label")
    @patch.object(process_author_follow_ups, "run_gh")
    @patch.object(process_author_follow_ups, "post_comment")
    @patch.object(process_author_follow_ups, "lifecycle_comments")
    @patch.object(process_author_follow_ups, "current_author_route", return_value=True)
    @patch.object(
        process_author_follow_ups,
        "current_human_activity",
        return_value=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )
    @patch.object(process_author_follow_ups, "issue_details")
    def test_close_retry_does_not_duplicate_explanation(
        self,
        issue_details,
        _current_human_activity,
        _current_author_route,
        lifecycle_comments,
        post_comment,
        run_gh,
        remove_stale_label,
    ) -> None:
        marker = process_author_follow_ups.lifecycle_marker(
            process_author_follow_ups.CLOSE_MARKER_PREFIX,
            CYCLE_ID,
        )
        lifecycle_comments.return_value = [{
            "body": marker,
            "created_at": "2026-07-17T00:00:00Z",
        }]
        issue_details.return_value = {
            "state": "open",
            "pull_request": {"url": "https://api.github.com/repos/open-telemetry/example/pulls/1"},
            "labels": [{"name": "Stale"}],
            "updated_at": "2026-07-17T00:00:00Z",
        }

        updated = process_author_follow_ups.execute_action(
            "close",
            "open-telemetry/example",
            1,
            author_result(),
            follow_up_entry(),
            follow_up_entry(
                stale_applied_at="2026-07-10T00:00:00Z",
                stale_label_owned=True,
            ),
            NOW,
        )

        self.assertIsNone(updated)
        post_comment.assert_not_called()
        self.assertEqual(run_gh.call_args.args[0][3], "PATCH")
        remove_stale_label.assert_called_once_with("open-telemetry/example", 1)

    @patch.object(process_author_follow_ups, "remove_stale_label")
    @patch.object(process_author_follow_ups, "run_gh")
    @patch.object(process_author_follow_ups, "lifecycle_comments", return_value=[])
    @patch.object(process_author_follow_ups, "current_author_route", return_value=True)
    @patch.object(
        process_author_follow_ups,
        "current_human_activity",
        return_value=datetime(2026, 7, 16, tzinfo=timezone.utc),
    )
    @patch.object(process_author_follow_ups, "issue_details")
    def test_close_is_deferred_after_new_human_activity(
        self,
        issue_details,
        _current_human_activity,
        _current_author_route,
        _lifecycle_comments,
        run_gh,
        remove_stale_label,
    ) -> None:
        issue_details.return_value = {
            "state": "open",
            "pull_request": {"url": "https://api.github.com/repos/open-telemetry/example/pulls/1"},
            "labels": [{"name": "Stale"}],
            "updated_at": "2026-07-16T00:00:00Z",
        }

        updated = process_author_follow_ups.execute_action(
            "close",
            "open-telemetry/example",
            1,
            author_result(),
            follow_up_entry(),
            follow_up_entry(
                stale_applied_at="2026-07-10T00:00:00Z",
                stale_label_owned=True,
            ),
            NOW,
        )

        self.assertEqual(updated["stale_applied_at"], "")
        self.assertEqual(updated["stale_reset_at"], "2026-07-16T00:00:00+00:00")
        remove_stale_label.assert_called_once_with("open-telemetry/example", 1)
        run_gh.assert_not_called()

    @patch.object(process_author_follow_ups, "remove_stale_label")
    def test_closed_pr_loses_dashboard_owned_stale_label(
        self,
        remove_stale_label,
    ) -> None:
        updated = process_author_follow_ups.next_author_follow_ups(
            "open-telemetry/example",
            {},
            set(),
            {"1": follow_up_entry(stale_label_owned=True)},
            NOW,
            stale_enabled=True,
        )

        self.assertEqual(updated, {})
        remove_stale_label.assert_called_once_with("open-telemetry/example", 1)

    @patch.object(process_author_follow_ups, "remove_stale_label")
    def test_route_change_removes_stale_and_cycle_in_same_run(
        self,
        remove_stale_label,
    ) -> None:
        updated = process_author_follow_ups.next_author_follow_ups(
            "open-telemetry/example",
            {1: {"route": "approver", "facts": {}}},
            {1},
            {
                "1": follow_up_entry(
                    stale_applied_at="2026-07-10T00:00:00Z",
                    stale_label_owned=True,
                )
            },
            NOW,
            stale_enabled=True,
        )

        self.assertEqual(updated, {})
        remove_stale_label.assert_called_once_with("open-telemetry/example", 1)

    @patch.object(process_author_follow_ups, "remove_stale_label")
    def test_manual_stale_removal_starts_fresh_stale_wait(
        self,
        remove_stale_label,
    ) -> None:
        updated = process_author_follow_ups.next_author_follow_ups(
            "open-telemetry/example",
            {1: author_result()},
            {1},
            {
                "1": follow_up_entry(
                    stale_applied_at="2026-07-10T00:00:00Z",
                    stale_label_owned=True,
                )
            },
            NOW,
            stale_enabled=True,
            current_prs={1: {"number": 1, "labels": []}},
        )

        self.assertEqual(updated["1"]["stale_applied_at"], "")
        self.assertEqual(updated["1"]["stale_reset_at"], "2026-07-17T00:00:00+00:00")
        self.assertNotIn("stale_label_owned", updated["1"])
        remove_stale_label.assert_not_called()

    def test_transient_dashboard_failure_preserves_cycle(self) -> None:
        previous = follow_up_entry(stale_label_owned=True)

        updated = process_author_follow_ups.next_author_follow_ups(
            "open-telemetry/example",
            {1: {"failed": True, "route": "transient-failure", "facts": {}}},
            {1},
            {"1": previous},
            NOW,
            stale_enabled=True,
        )

        self.assertEqual(updated, {"1": previous})

    def test_unrefreshed_result_preserves_cycle_without_action(self) -> None:
        previous = follow_up_entry()

        updated = process_author_follow_ups.next_author_follow_ups(
            "open-telemetry/example",
            {1: author_result()},
            {1},
            {"1": previous},
            NOW,
            stale_enabled=True,
            refreshed_pr_numbers=set(),
        )

        self.assertEqual(updated, {"1": previous})

    @patch.object(process_author_follow_ups, "execute_action")
    def test_targeted_author_result_preserves_due_cycle_without_action(
        self,
        execute_action,
    ) -> None:
        previous = follow_up_entry(
            waiting_on_author_since="2026-07-01T00:00:00Z",
        )

        updated = process_author_follow_ups.next_author_follow_ups(
            "open-telemetry/example",
            {1: author_result()},
            {1},
            {"1": previous},
            NOW,
            stale_enabled=True,
            refreshed_pr_numbers={1},
            reset_only=True,
        )

        self.assertEqual(updated, {"1": previous})
        execute_action.assert_not_called()

    @patch.object(process_author_follow_ups, "remove_stale_label")
    def test_targeted_route_departure_clears_cycle(
        self,
        remove_stale_label,
    ) -> None:
        updated = process_author_follow_ups.next_author_follow_ups(
            "open-telemetry/example",
            {1: {"route": "approver", "facts": {}}},
            {1},
            {
                "1": follow_up_entry(
                    stale_applied_at="2026-07-10T00:00:00Z",
                    stale_label_owned=True,
                )
            },
            NOW,
            stale_enabled=True,
            refreshed_pr_numbers={1},
            reset_only=True,
        )

        self.assertEqual(updated, {})
        remove_stale_label.assert_called_once_with("open-telemetry/example", 1)

    def test_targeted_run_preserves_unselected_missing_cycle(self) -> None:
        selected = follow_up_entry()
        unselected = follow_up_entry(stale_label_owned=True)

        updated = process_author_follow_ups.next_author_follow_ups(
            "open-telemetry/example",
            {1: author_result()},
            {1},
            {"1": selected, "2": unselected},
            NOW,
            stale_enabled=True,
            refreshed_pr_numbers={1},
            reset_only=True,
        )

        self.assertEqual(updated, {"1": selected, "2": unselected})


if __name__ == "__main__":
    unittest.main()