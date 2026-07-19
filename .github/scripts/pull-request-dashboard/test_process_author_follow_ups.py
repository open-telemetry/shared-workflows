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
        "waiting_on_author_since": CYCLE_ID,
        "general_nudged_at": "2026-07-08T00:00:00+00:00",
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
    @patch.object(
        process_author_follow_ups,
        "gh_pr_view",
        return_value={
            "state": "OPEN",
            "isDraft": False,
            "updatedAt": "2026-07-01T00:00:00Z",
        },
    )
    @patch.object(process_author_follow_ups, "fetch_review_threads")
    def test_current_author_route_requires_an_unresolved_author_thread(
        self,
        fetch_review_threads,
        _gh_pr_view,
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

    @patch.object(process_author_follow_ups, "fetch_review_threads", return_value=[])
    @patch.object(process_author_follow_ups, "gh_pr_view")
    def test_current_author_route_rejects_changed_top_level_metadata(
        self,
        gh_pr_view,
        _fetch_review_threads,
    ) -> None:
        result = author_result()
        result["facts"]["author_action_review_thread_urls"] = []
        result["facts"]["author_action_top_level_feedback_urls"] = [
            "https://github.com/open-telemetry/example/pull/1#pullrequestreview-1"
        ]
        result["facts"]["observed_at"] = "2026-07-17T00:00:00Z"
        gh_pr_view.return_value = {
            "state": "OPEN",
            "isDraft": False,
            "updatedAt": "2026-07-17T00:00:00Z",
        }

        self.assertFalse(process_author_follow_ups.current_author_route(
            "open-telemetry/example",
            1,
            result,
        ))

    @patch.object(process_author_follow_ups, "fetch_review_threads", return_value=[])
    @patch.object(
        process_author_follow_ups,
        "gh_pr_view",
        return_value={
            "state": "OPEN",
            "isDraft": True,
            "updatedAt": "2026-07-01T00:00:00Z",
        },
    )
    def test_current_author_route_rejects_draft_pr(
        self,
        _gh_pr_view,
        fetch_review_threads,
    ) -> None:
        result = author_result()
        result["facts"]["author_action_review_thread_urls"] = []
        result["facts"]["author_action_top_level_feedback_urls"] = [
            "https://github.com/open-telemetry/example/pull/1#pullrequestreview-1"
        ]
        result["facts"]["observed_at"] = "2026-07-17T00:00:00Z"

        self.assertFalse(process_author_follow_ups.current_author_route(
            "open-telemetry/example",
            1,
            result,
        ))
        fetch_review_threads.assert_not_called()

    @patch.object(process_author_follow_ups, "include_missing_required_checks")
    @patch.object(
        process_author_follow_ups,
        "gh_required_check_contexts",
        return_value=[],
    )
    @patch.object(process_author_follow_ups, "gh_pr_checks", return_value=[])
    @patch.object(process_author_follow_ups, "fetch_review_threads")
    @patch.object(
        process_author_follow_ups,
        "gh_pr_view",
        return_value={
            "id": "PR_id",
            "state": "OPEN",
            "isDraft": False,
            "updatedAt": "2026-07-17T00:00:00Z",
            "baseRefName": "main",
        },
    )
    def test_current_author_route_revalidates_ci_only_route(
        self,
        _gh_pr_view,
        fetch_review_threads,
        gh_pr_checks,
        gh_required_check_contexts,
        include_missing_required_checks,
    ) -> None:
        result = author_result()
        result["facts"]["author_action_review_thread_urls"] = []
        result["facts"]["ci_failing_count"] = 1

        for checks, expected in (
            ([{"bucket": "fail"}], True),
            ([{"bucket": "cancel"}], True),
            ([{"bucket": "pass"}], False),
            (None, False),
        ):
            with self.subTest(checks=checks):
                include_missing_required_checks.return_value = checks

                self.assertEqual(
                    expected,
                    process_author_follow_ups.current_author_route(
                        "open-telemetry/example",
                        1,
                        result,
                    ),
                )

        fetch_review_threads.assert_not_called()
        gh_pr_checks.assert_called_with("open-telemetry/example", "PR_id")
        gh_required_check_contexts.assert_called_with(
            "open-telemetry/example",
            "main",
        )

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
            [
                {"sha": "accepted-head"},
                {
                    "sha": "new-head",
                    "author": {"login": "alice"},
                    "committer": {"login": "alice"},
                    "commit": {
                        "author": {"date": "2020-01-01T00:00:00Z"},
                        "committer": {"date": "2020-01-01T00:00:00Z"},
                    },
                },
            ],
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
            [
                {"sha": "accepted-head"},
                {
                    "sha": "new-head",
                    "author": {"login": "reviewer", "type": "User"},
                    "committer": {"login": "web-flow", "type": "User"},
                    "commit": {
                        "author": {"date": "2020-01-01T00:00:00Z"},
                        "committer": {"date": "2020-01-01T00:00:00Z"},
                    },
                },
            ],
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
    def test_current_human_activity_ignores_neutral_bot_committers(
        self,
        gh_api,
        _gh_pr_view,
        _fetch_pr_review_data,
    ) -> None:
        for committer in ("web-flow", "copilot"):
            with self.subTest(committer=committer):
                gh_api.side_effect = [
                    [],
                    [],
                    [
                        {"sha": "accepted-head"},
                        {
                            "sha": "new-head",
                            "author": {"login": "automation[bot]", "type": "Bot"},
                            "committer": {"login": committer, "type": "User"},
                        },
                    ],
                ]

                activity = process_author_follow_ups.current_human_activity(
                    "open-telemetry/example",
                    1,
                    author_result(),
                    NOW,
                )

                self.assertIsNone(activity)

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
    def test_current_human_activity_observes_unattributed_push(
        self,
        gh_api,
        _gh_pr_view,
        _fetch_pr_review_data,
    ) -> None:
        gh_api.side_effect = [
            [],
            [],
            [
                {"sha": "accepted-head"},
                {"sha": "new-head", "author": None, "committer": None},
            ],
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
        return_value={"headRefOid": "bot-head"},
    )
    @patch.object(process_author_follow_ups, "gh_api")
    def test_current_human_activity_observes_human_commit_before_bot_tip(
        self,
        gh_api,
        _gh_pr_view,
        _fetch_pr_review_data,
    ) -> None:
        gh_api.side_effect = [
            [],
            [],
            [
                {"sha": "accepted-head"},
                {
                    "sha": "human-head",
                    "author": {"login": "reviewer", "type": "User"},
                },
                {
                    "sha": "bot-head",
                    "author": {"login": "automation[bot]", "type": "Bot"},
                },
            ],
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
        return_value={"headRefOid": "reviewer-head"},
    )
    @patch.object(process_author_follow_ups, "gh_api")
    def test_current_human_activity_uses_latest_human_commit(
        self,
        gh_api,
        _gh_pr_view,
        _fetch_pr_review_data,
    ) -> None:
        gh_api.side_effect = [
            [],
            [],
            [
                {"sha": "accepted-head"},
                {
                    "sha": "author-head",
                    "author": {"login": "alice", "type": "User"},
                },
                {
                    "sha": "reviewer-head",
                    "author": {"login": "reviewer", "type": "User"},
                },
            ],
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
        return_value={"1": {"general_nudged_at": ""}},
    )
    def test_retry_snapshot_preserves_nudge_state(
        self,
        _load_author_follow_ups,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot = Path(temp_dir) / "author-follow-up-state.json"
            snapshot.write_text(
                json.dumps({
                    "version": AUTHOR_FOLLOW_UP_STATE_VERSION,
                    "prs": {"1": {"general_nudged_at": "2026-07-17T00:00:00Z"}},
                }),
                encoding="utf-8",
            )

            previous = process_author_follow_ups.previous_author_follow_ups(snapshot)

        self.assertEqual(previous, {
            "1": {"general_nudged_at": "2026-07-17T00:00:00Z"}
        })

    @patch.object(process_author_follow_ups, "save_author_follow_ups")
    @patch.object(process_author_follow_ups, "author_follow_up_state_path")
    @patch.object(process_author_follow_ups, "next_author_follow_ups")
    @patch.object(process_author_follow_ups, "previous_author_follow_ups")
    @patch.object(process_author_follow_ups, "load_author_follow_ups")
    @patch.object(process_author_follow_ups, "results_from_dashboard_state", return_value={})
    @patch.object(
        process_author_follow_ups,
        "list_open_prs",
        return_value=[],
    )
    @patch.object(
        process_author_follow_ups,
        "load_dashboard_state_cache",
        return_value={},
    )
    def test_retry_persists_snapshot_state_to_accepted_checkout(
        self,
        _load_dashboard_state_cache,
        _list_open_prs,
        _results_from_dashboard_state,
        load_author_follow_ups,
        previous_author_follow_ups,
        next_author_follow_ups,
        author_follow_up_state_path,
        save_author_follow_ups,
    ) -> None:
        checkout_state = {"1": {"general_nudged_at": ""}}
        retry_state = {"1": {"general_nudged_at": "2026-07-17T00:00:00Z"}}
        load_author_follow_ups.return_value = checkout_state
        previous_author_follow_ups.return_value = retry_state
        next_author_follow_ups.return_value = retry_state
        author_follow_up_state_path.return_value.exists.return_value = True

        process_author_follow_ups.process_author_follow_ups(
            "open-telemetry/example",
            now=NOW,
            refreshed_pr_numbers={1},
            retry_snapshot_path=Path("retry-state.json"),
        )

        next_author_follow_ups.assert_called_once()
        save_author_follow_ups.assert_called_once_with(retry_state)

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
            process_author_follow_ups.GENERAL_NUDGE_MARKER_PREFIX,
            CYCLE_ID,
        )
        lifecycle_comments.return_value = [{
            "body": marker,
            "created_at": "2026-07-08T01:00:00Z",
        }]
        updated = follow_up_entry(general_nudged_at="2026-07-17T00:00:00+00:00")

        result = process_author_follow_ups.execute_action(
            "general-nudge",
            "open-telemetry/example",
            1,
            author_result(),
            None,
            updated,
            NOW,
        )

        self.assertEqual(result["general_nudged_at"], "2026-07-08T01:00:00Z")
        ensure_status_comment.assert_not_called()
        post_comment.assert_not_called()

    @patch.object(process_author_follow_ups, "post_comment")
    @patch.object(
        process_author_follow_ups,
        "ensure_status_comment",
        return_value="https://github.com/open-telemetry/example/pull/1#issuecomment-1",
    )
    @patch.object(
        process_author_follow_ups,
        "current_human_activity",
        return_value=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )
    @patch.object(process_author_follow_ups, "current_author_route", return_value=True)
    @patch.object(
        process_author_follow_ups,
        "issue_details",
        return_value={"state": "open", "pull_request": {"url": "pulls/1"}},
    )
    @patch.object(process_author_follow_ups, "lifecycle_comments", return_value=[])
    def test_new_nudge_links_status_comment(
        self,
        _lifecycle_comments,
        _issue_details,
        _current_author_route,
        _current_human_activity,
        _ensure_status_comment,
        post_comment,
    ) -> None:
        process_author_follow_ups.execute_action(
            "general-nudge",
            "open-telemetry/example",
            1,
            author_result(),
            None,
            follow_up_entry(general_nudged_at=""),
            NOW,
        )

        body = post_comment.call_args.args[2]
        self.assertIn("@alice, this pull request has been waiting on your follow-up", body)
        self.assertIn("[dashboard status comment]", body)
        self.assertIn(process_author_follow_ups.GENERAL_NUDGE_MARKER_PREFIX, body)

    @patch.object(process_author_follow_ups, "post_comment")
    @patch.object(process_author_follow_ups, "ensure_status_comment")
    @patch.object(process_author_follow_ups, "current_human_activity")
    @patch.object(process_author_follow_ups, "current_author_route", return_value=True)
    @patch.object(
        process_author_follow_ups,
        "issue_details",
        return_value={"state": "closed", "pull_request": {}},
    )
    @patch.object(process_author_follow_ups, "lifecycle_comments", return_value=[])
    def test_nudge_is_cancelled_when_pr_closed(
        self,
        _lifecycle_comments,
        _issue_details,
        current_author_route,
        current_human_activity,
        ensure_status_comment,
        post_comment,
    ) -> None:
        result = process_author_follow_ups.execute_action(
            "general-nudge",
            "open-telemetry/example",
            1,
            author_result(),
            follow_up_entry(),
            follow_up_entry(),
            NOW,
        )

        self.assertIsNone(result)
        current_author_route.assert_not_called()
        current_human_activity.assert_not_called()
        ensure_status_comment.assert_not_called()
        post_comment.assert_not_called()

    @patch.object(process_author_follow_ups, "post_comment")
    @patch.object(process_author_follow_ups, "ensure_status_comment")
    @patch.object(process_author_follow_ups, "current_human_activity")
    @patch.object(process_author_follow_ups, "current_author_route", return_value=False)
    @patch.object(
        process_author_follow_ups,
        "issue_details",
        return_value={"state": "open", "pull_request": {"url": "pulls/1"}},
    )
    @patch.object(process_author_follow_ups, "lifecycle_comments", return_value=[])
    def test_nudge_is_cancelled_when_author_route_departed(
        self,
        _lifecycle_comments,
        _issue_details,
        _current_author_route,
        current_human_activity,
        ensure_status_comment,
        post_comment,
    ) -> None:
        result = process_author_follow_ups.execute_action(
            "general-nudge",
            "open-telemetry/example",
            1,
            author_result(),
            follow_up_entry(),
            follow_up_entry(),
            NOW,
        )

        self.assertIsNone(result)
        current_human_activity.assert_not_called()
        ensure_status_comment.assert_not_called()
        post_comment.assert_not_called()

    @patch.object(process_author_follow_ups, "post_comment")
    @patch.object(process_author_follow_ups, "ensure_status_comment")
    @patch.object(
        process_author_follow_ups,
        "current_human_activity",
        return_value=datetime(2026, 7, 16, tzinfo=timezone.utc),
    )
    @patch.object(process_author_follow_ups, "current_author_route", return_value=True)
    @patch.object(
        process_author_follow_ups,
        "issue_details",
        return_value={"state": "open", "pull_request": {"url": "pulls/1"}},
    )
    @patch.object(process_author_follow_ups, "lifecycle_comments", return_value=[])
    def test_nudge_is_deferred_for_new_human_activity(
        self,
        _lifecycle_comments,
        _issue_details,
        _current_author_route,
        _current_human_activity,
        ensure_status_comment,
        post_comment,
    ) -> None:
        accepted = author_result()
        accepted["facts"]["last_author_activity_at"] = "2026-07-16T00:00:00Z"
        accepted["facts"]["observed_at"] = "2026-07-16T00:00:00Z"
        previous = follow_up_entry(general_nudged_at="")
        updated = follow_up_entry(general_nudged_at="2026-07-17T00:00:00+00:00")

        result = process_author_follow_ups.execute_action(
            "general-nudge",
            "open-telemetry/example",
            1,
            accepted,
            previous,
            updated,
            NOW,
        )

        self.assertEqual(result, previous)
        ensure_status_comment.assert_not_called()
        post_comment.assert_not_called()

    def test_general_nudge_links_status_comment_and_marker(self) -> None:
        body = process_author_follow_ups.render_nudge(
            author_result(),
            "https://github.com/open-telemetry/example/pull/1#issuecomment-1",
            CYCLE_ID,
        )

        self.assertIn("waiting on your follow-up for one week", body)
        self.assertIn("[dashboard status comment]", body)
        self.assertIn(process_author_follow_ups.GENERAL_NUDGE_MARKER_PREFIX, body)

    def test_transient_dashboard_failure_preserves_cycle(self) -> None:
        previous = follow_up_entry()

        updated = process_author_follow_ups.next_author_follow_ups(
            "open-telemetry/example",
            {1: {"failed": True, "route": "transient-failure", "facts": {}}},
            {"1": previous},
            NOW,
        )

        self.assertEqual(updated, {"1": previous})

    def test_unrefreshed_result_preserves_cycle_without_action(self) -> None:
        previous = follow_up_entry()

        updated = process_author_follow_ups.next_author_follow_ups(
            "open-telemetry/example",
            {1: author_result()},
            {"1": previous},
            NOW,
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
            {"1": previous},
            NOW,
            refreshed_pr_numbers={1},
            reset_only=True,
        )

        self.assertEqual(updated, {"1": previous})
        execute_action.assert_not_called()

    def test_targeted_route_departure_preserves_nudge_marker(self) -> None:
        updated = process_author_follow_ups.next_author_follow_ups(
            "open-telemetry/example",
            {1: {"route": "approver", "facts": {}}},
            {"1": follow_up_entry()},
            NOW,
            refreshed_pr_numbers={1},
            reset_only=True,
        )

        self.assertEqual(
            updated,
            {"1": {"general_nudged_at": "2026-07-08T00:00:00+00:00"}},
        )

    def test_targeted_run_preserves_unselected_missing_cycle(self) -> None:
        selected = follow_up_entry()
        unselected = follow_up_entry()

        updated = process_author_follow_ups.next_author_follow_ups(
            "open-telemetry/example",
            {1: author_result()},
            {"1": selected, "2": unselected},
            NOW,
            refreshed_pr_numbers={1},
            reset_only=True,
        )

        self.assertEqual(updated, {"1": selected, "2": unselected})


if __name__ == "__main__":
    unittest.main()