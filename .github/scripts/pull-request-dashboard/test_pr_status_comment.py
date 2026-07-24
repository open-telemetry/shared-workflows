from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse

import pr_status_comment


class RenderStatusCommentTest(unittest.TestCase):
    def pr(self, **overrides: object) -> dict[str, object]:
        pr: dict[str, object] = {
            "state": "open",
            "draft": False,
            "merged": False,
            "html_url": "https://github.com/open-telemetry/example/pull/1",
            "user": {"login": "alice"},
        }
        pr.update(overrides)
        return pr

    def test_waiting_on_author_splits_review_feedback_links(self) -> None:
        body = pr_status_comment.render_status_comment(
            self.pr(),
            {
                "route": "author",
                "facts": {
                    "author": "alice",
                    "author_nudge_episode_id": "abc123",
                    "author_action_review_thread_urls": [
                        "https://github.com/open-telemetry/example/pull/1#discussion_r1",
                    ],
                    "author_action_top_level_feedback_urls": [
                        "https://github.com/open-telemetry/example/pull/1#pullrequestreview-2",
                    ],
                },
            },
        )

        self.assertIn(
            "- **Waiting on:** Author",
            body,
        )
        self.assertIn("- **Next step:** Address or respond to 2 review feedback items:", body)
        self.assertIn(
            f"<!-- pull-request-dashboard-status-revision:{pr_status_comment.STATUS_COMMENT_REVISION} -->",
            body,
        )
        self.assertIn(
            pr_status_comment.author_nudge_episode_marker("abc123"),
            body,
        )
        self.assertNotIn("### Review feedback", body)
        self.assertIn("  - **Inline threads:** [1]", body)
        self.assertIn("  - **Top-level feedback:** [2]", body)
        self.assertIn(f"  - _{pr_status_comment.AUTHOR_GUIDANCE}_", body)
        self.assertIn(
            "If you believe this pull request is incorrectly routed as waiting "
            "on the author, comment `/dashboard route:reviewers` to route it from "
            "waiting on the author to waiting on reviewers. If the last refreshed "
            "time above predates your latest reply or push, the dashboard hasn't "
            "processed it yet.",
            body,
        )

    def test_recovers_episode_only_from_app_authored_status_comment(self) -> None:
        marker = pr_status_comment.author_nudge_episode_marker("abc123")
        comments = [
            {
                "performed_via_github_app": None,
                "body": f"{pr_status_comment.STATUS_MARKER}\n{marker}",
            },
            {
                "performed_via_github_app": {
                    "slug": pr_status_comment.DASHBOARD_APP_SLUG,
                },
                "body": marker,
            },
            {
                "performed_via_github_app": {
                    "slug": pr_status_comment.DASHBOARD_APP_SLUG,
                },
                "body": f"{pr_status_comment.STATUS_MARKER}\n{marker}",
            },
        ]

        self.assertEqual(
            "abc123",
            pr_status_comment.status_author_nudge_episode_id(comments),
        )

    def test_recovers_episode_from_normalized_dashboard_bot_comment(self) -> None:
        marker = pr_status_comment.author_nudge_episode_marker("abc123")

        self.assertEqual(
            "abc123",
            pr_status_comment.status_author_nudge_episode_id([{
                "user": {"login": "opentelemetry-pr-dashboard[bot]"},
                "body": f"{pr_status_comment.STATUS_MARKER}\n{marker}",
            }]),
        )

    def test_does_not_recover_episode_from_other_normalized_authors(self) -> None:
        marker = pr_status_comment.author_nudge_episode_marker("abc123")
        comments = [
            {
                "user": {"login": login},
                "body": f"{pr_status_comment.STATUS_MARKER}\n{marker}",
            }
            for login in ("alice", "another-app[bot]")
        ]

        self.assertEqual(
            "",
            pr_status_comment.status_author_nudge_episode_id(comments),
        )

    @patch.object(
        pr_status_comment,
        "utc_now",
        side_effect=[
            datetime(2026, 7, 18, 12, 34, 56, tzinfo=timezone.utc),
            datetime(2026, 7, 18, 12, 35, 1, tzinfo=timezone.utc),
        ],
    )
    def test_status_last_refreshed_changes_for_identical_status(self, _utc_now: Mock) -> None:
        first_body = pr_status_comment.render_status_comment(self.pr(), {"route": "approver"})
        second_body = pr_status_comment.render_status_comment(self.pr(), {"route": "approver"})

        self.assertIn("_Status last refreshed: 2026-07-18 12:34:56 UTC.", first_body)
        self.assertIn("_Status last refreshed: 2026-07-18 12:35:01 UTC.", second_body)
        self.assertNotEqual(first_body, second_body)

    def test_accuracy_note_prefills_central_issue_for_every_status(self) -> None:
        cases = (
            (self.pr(), {"route": "approver", "facts": {}}),
            (self.pr(draft=True), None),
            (self.pr(merged=True), None),
            (self.pr(state="closed"), None),
            (self.pr(), None),
        )

        for pr, result in cases:
            with self.subTest(pr=pr, result=result):
                body = pr_status_comment.render_status_comment(pr, result)

                self.assertIn(
                    "This automated status or its linked feedback items may be incorrect",
                    body,
                )
                self.assertIn("with the result you expected", body)
                self.assertIn(
                    "https://github.com/open-telemetry/shared-workflows/issues/new?",
                    body,
                )
                self.assertIn(
                    "template=incorrect-pr-dashboard-result.md",
                    body,
                )
                self.assertIn("PR%3A+https%3A%2F%2Fgithub.com%2F", body)
                self.assertIn("What+looks+incorrect", body)
                self.assertNotIn("One+or+more+linked+feedback+items", body)

    def test_accuracy_note_prefills_quoted_live_status_comment(self) -> None:
        body = pr_status_comment.render_status_comment(
            self.pr(),
            {"route": "approver", "facts": {}},
        )

        status_comment, accuracy_note = body.split(
            "\n\n_This automated status or its linked feedback items may be incorrect.",
            maxsplit=1,
        )
        report_url = accuracy_note.split("[report it](", maxsplit=1)[1].split(
            ")", maxsplit=1
        )[0]
        issue_body = parse_qs(urlparse(report_url).query)["body"][0]
        quoted_status_comment = "\n".join(
            f"> {line}" for line in status_comment.splitlines()
        )

        self.assertEqual(
            "PR: https://github.com/open-telemetry/example/pull/1\n\n"
            f"Current live status comment:\n{quoted_status_comment}\n\n"
            "What looks incorrect:\n",
            issue_body,
        )

    def test_accuracy_note_bounds_report_url_for_large_status(self) -> None:
        body = pr_status_comment.render_status_comment(
            self.pr(),
            {
                "route": "author",
                "facts": {
                    "ci_failing_count": 1,
                    "non_blocking_check_failures": [
                        "&" * pr_status_comment.NON_BLOCKING_CHECK_FAILURE_NAME_LIMIT
                        for _ in range(
                            pr_status_comment.NON_BLOCKING_CHECK_FAILURE_LIMIT
                        )
                    ],
                    "author_action_review_thread_urls": [
                        "https://github.com/open-telemetry/example/pull/1"
                        "#discussion_r1234567890"
                        for _ in range(
                            pr_status_comment.AUTHOR_ACTION_FEEDBACK_LINK_LIMIT
                        )
                    ],
                },
            },
        )

        report_url = body.split("[report it](", maxsplit=1)[1].split(
            ")", maxsplit=1
        )[0]
        issue_body = parse_qs(urlparse(report_url).query)["body"][0]

        self.assertLessEqual(
            len(report_url),
            pr_status_comment.STATUS_REPORT_URL_MAX_CHARS,
        )
        self.assertIn(
            f"> {pr_status_comment.STATUS_REPORT_TRUNCATION_NOTICE}",
            issue_body,
        )

    def test_waiting_on_author_names_required_ci_failure(self) -> None:
        body = pr_status_comment.render_status_comment(
            self.pr(),
            {
                "route": "author",
                "facts": {"author": "alice", "ci_failing_count": 1},
            },
        )

        self.assertIn(
            "- **Waiting on:** Author",
            body,
        )
        self.assertIn("- **Next step:** Investigate required status check failures.", body)
        self.assertNotIn("### Review feedback", body)
        self.assertNotIn(pr_status_comment.AUTHOR_GUIDANCE, body)

    def test_waiting_on_author_combines_ci_and_review_feedback_reasons(self) -> None:
        body = pr_status_comment.render_status_comment(
            self.pr(),
            {
                "route": "author",
                "facts": {
                    "author": "alice",
                    "ci_failing_count": 2,
                    "author_action_review_thread_urls": [
                        "https://github.com/open-telemetry/example/pull/1#discussion_r1",
                    ],
                },
            },
        )

        self.assertIn("- **Next steps:**", body)
        self.assertIn("  - Investigate required status check failures.", body)
        self.assertIn("  - Address or respond to 1 review feedback item:", body)
        self.assertIn("    - **Inline threads:** [1]", body)

    def test_required_ci_action_notes_configured_non_blocking_failures(self) -> None:
        body = pr_status_comment.render_status_comment(
            self.pr(),
            {
                "route": "author",
                "facts": {
                    "ci_failing_count": 2,
                    "non_blocking_check_failures": [
                        "CodeQL",
                        "workflow-notification",
                    ],
                },
            },
        )

        self.assertIn(
            "- **Next step:** Investigate required status check failures. "
            "Note: CodeQL and workflow-notification are failing but are not required checks.",
            body,
        )

    def test_required_ci_action_escapes_non_blocking_failure_names(self) -> None:
        body = pr_status_comment.render_status_comment(
            self.pr(),
            {
                "route": "author",
                "facts": {
                    "ci_failing_count": 1,
                    "non_blocking_check_failures": [
                        "[CodeQL] <script>\n@maintainers",
                        r"pipe|slash\check & more",
                    ],
                },
            },
        )

        self.assertIn(
            "Note: \\[CodeQL\\] &lt;script&gt; &#64;maintainers and "
            "pipe\\|slash\\\\check &amp; more are failing but are not required checks.",
            body,
        )

    def test_required_ci_action_limits_non_blocking_failure_names(self) -> None:
        long_name = "x" * (pr_status_comment.NON_BLOCKING_CHECK_FAILURE_NAME_LIMIT + 1)
        failures = [
            long_name,
            *(
                f"check-{index:02d}"
                for index in range(pr_status_comment.NON_BLOCKING_CHECK_FAILURE_LIMIT + 1)
            ),
        ]

        body = pr_status_comment.render_status_comment(
            self.pr(),
            {
                "route": "author",
                "facts": {
                    "ci_failing_count": 1,
                    "non_blocking_check_failures": failures,
                },
            },
        )

        truncated_name = (
            "x" * pr_status_comment.NON_BLOCKING_CHECK_FAILURE_NAME_LIMIT
            + " ...\\[truncated\\]"
        )
        self.assertIn(truncated_name, body)
        self.assertIn("2 additional non-blocking check failures are not shown.", body)
        self.assertNotIn("check-19", body)
        self.assertNotIn("check-20", body)

    def test_non_author_route_names_non_blocking_failure(self) -> None:
        body = pr_status_comment.render_status_comment(
            self.pr(),
            {
                "route": "approver",
                "facts": {
                    "non_blocking_check_failures": ["codecov/patch"],
                },
            },
        )

        self.assertIn("- **Waiting on:** Reviewers", body)
        self.assertIn(
            "- **Non-blocking check failure:** codecov/patch is failing but is not a required check.",
            body,
        )

    def test_non_author_routes_also_name_required_ci_failures(self) -> None:
        cases = (
            (
                "maintainer",
                1,
                "Maintainers",
                ["CodeQL"],
                "1 required status check is failing.",
                "- **Non-blocking check failure:** CodeQL is failing but is not a required check.",
            ),
            (
                "approver",
                2,
                "Reviewers",
                ["CodeQL", "workflow-notification"],
                "2 required status checks are failing.",
                "- **Non-blocking check failures:** CodeQL and workflow-notification are failing but are not required checks.",
            ),
        )
        for (
            route,
            failing_count,
            waiting_on,
            non_blocking_failures,
            blocked_by,
            non_blocking_line,
        ) in cases:
            with self.subTest(route=route):
                body = pr_status_comment.render_status_comment(
                    self.pr(),
                    {
                        "route": route,
                        "facts": {
                            "ci_failing_count": failing_count,
                            "non_blocking_check_failures": non_blocking_failures,
                        },
                    },
                )

                self.assertIn(f"- **Waiting on:** {waiting_on}", body)
                self.assertIn(f"- **Also blocked by:** {blocked_by}", body)
                self.assertIn(non_blocking_line, body)

    def test_waiting_on_author_caps_feedback_links_across_sections(self) -> None:
        review_thread_urls = [
            f"https://github.com/open-telemetry/example/pull/1#discussion_r{index}"
            for index in range(pr_status_comment.AUTHOR_ACTION_FEEDBACK_LINK_LIMIT - 1)
        ]
        top_level_feedback_urls = [
            f"https://github.com/open-telemetry/example/pull/1#pullrequestreview-{index}"
            for index in range(3)
        ]

        body = pr_status_comment.render_status_comment(
            self.pr(),
            {
                "route": "author",
                "facts": {
                    "author": "alice",
                    "author_action_review_thread_urls": review_thread_urls,
                    "author_action_top_level_feedback_urls": top_level_feedback_urls,
                },
            },
        )

        self.assertIn("  - **Inline threads:**", body)
        self.assertIn("  - **Top-level feedback:** [20]", body)
        self.assertIn(
            "  - _Showing 20 of 22 feedback links; "
            "resolve the remaining items from the pull request's conversation._",
            body,
        )
        self.assertNotIn(top_level_feedback_urls[-1], body)

    def test_feedback_group_with_no_remaining_link_slots_still_reads_cleanly(self) -> None:
        review_thread_urls = [
            f"https://github.com/open-telemetry/example/pull/1#discussion_r{index}"
            for index in range(pr_status_comment.AUTHOR_ACTION_FEEDBACK_LINK_LIMIT)
        ]

        body = pr_status_comment.render_status_comment(
            self.pr(),
            {
                "route": "author",
                "facts": {
                    "author_action_review_thread_urls": review_thread_urls,
                    "author_action_top_level_feedback_urls": [
                        "https://github.com/open-telemetry/example/pull/1#issuecomment-1",
                    ],
                },
            },
        )

        self.assertNotIn("Top-level feedback", body)
        self.assertIn(
            "  - _Showing 20 of 21 feedback links; "
            "resolve the remaining items from the pull request's conversation._",
            body,
        )

    def test_draft_waits_on_author(self) -> None:
        body = pr_status_comment.render_status_comment(self.pr(draft=True), None)

        self.assertIn("- **Waiting on:** Author", body)
        self.assertIn(
            "- **Next step:** Move out of draft to request review.",
            body,
        )

    def test_merged_pr_has_no_author_guidance(self) -> None:
        body = pr_status_comment.render_status_comment(
            self.pr(state="closed", merged=True),
            None,
        )

        self.assertIn("**Status:** Merged.", body)
        self.assertNotIn(pr_status_comment.AUTHOR_GUIDANCE, body)

    def test_terminal_pr_has_no_author_feedback_links(self) -> None:
        result = {
            "route": "author",
            "facts": {
                "author": "alice",
                "author_action_review_thread_urls": [
                    "https://github.com/open-telemetry/example/pull/1#discussion_r1",
                ],
                "author_action_top_level_feedback_urls": [
                    "https://github.com/open-telemetry/example/pull/1#pullrequestreview-2",
                ],
            },
        }

        for overrides in ({"state": "closed"}, {"state": "closed", "merged": True}):
            with self.subTest(overrides=overrides):
                body = pr_status_comment.render_status_comment(self.pr(**overrides), result)

                self.assertNotIn("### Review feedback", body)
                self.assertNotIn("- **Inline threads", body)
                self.assertNotIn("- **Top-level feedback", body)

    def test_author_login_is_not_mentioned(self) -> None:
        body = pr_status_comment.render_status_comment(
            self.pr(),
            {"route": "author", "facts": {"author": "alice"}},
        )

        self.assertIn("- **Waiting on:** Author", body)
        self.assertNotIn("@alice", body)

    def test_external_route_advertises_reviewer_override(self) -> None:
        body = pr_status_comment.render_status_comment(
            self.pr(),
            {"route": "external", "facts": {}},
        )

        self.assertIn(
            "waiting on an external dependency or decision, comment "
            "`/dashboard route:reviewers` to route it from waiting on an "
            "external dependency or decision to waiting on reviewers",
            body,
        )

    def test_routes_render_one_status_sentence(self) -> None:
        expected_summaries = {
            "approver": ("Reviewers", "Review the latest changes."),
            "maintainer": ("Maintainers", "Merge when ready."),
            "copilot": ("Copilot", "Wait for the pending review to complete."),
            "external": ("An external dependency or decision", "Resolve it before work can continue."),
            "transient-failure": ("Pull request dashboard maintainers", "Determine the next action."),
            "unknown": ("Pull request dashboard maintainers", "Determine the next action."),
        }

        for route, (waiting_on, next_step) in expected_summaries.items():
            with self.subTest(route=route):
                body = pr_status_comment.render_status_comment(
                    self.pr(),
                    {"route": route, "facts": {}},
                )

                self.assertIn(f"- **Waiting on:** {waiting_on}", body)
                self.assertIn(f"- **Next step:** {next_step}", body)
                self.assertNotIn("**Status:**", body)
                self.assertNotIn(pr_status_comment.AUTHOR_GUIDANCE, body)


class UpsertStatusCommentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.commands: list[list[str]] = []
        self.run_gh_patch = patch.object(
            pr_status_comment,
            "run_gh",
            side_effect=lambda command: self.commands.append(command) or "",
        )
        self.run_gh_patch.start()

    def tearDown(self) -> None:
        self.run_gh_patch.stop()

    @patch.object(pr_status_comment, "managed_status_comments", return_value=[])
    def test_creates_first_status_comment(self, _comments: object) -> None:
        pr_status_comment.upsert_status_comment("open-telemetry/example", 1, "body")

        self.assertEqual("POST", self.commands[0][3])

    @patch.object(
        pr_status_comment,
        "managed_status_comments",
        return_value=[{"id": 7, "body": "body"}],
    )
    def test_does_not_update_unchanged_comment(self, _comments: object) -> None:
        pr_status_comment.upsert_status_comment("open-telemetry/example", 1, "body")

        self.assertEqual([], self.commands)

    @patch.object(
        pr_status_comment,
        "managed_status_comments",
        return_value=[
            {"id": 7, "body": "<!-- review-guidance --> old"},
            {"id": 8, "body": "<!-- pull-request-dashboard-status --> duplicate"},
        ],
    )
    def test_migrates_legacy_comment_and_deletes_duplicates(self, _comments: object) -> None:
        pr_status_comment.upsert_status_comment("open-telemetry/example", 1, "body")

        self.assertEqual(["PATCH", "DELETE"], [command[3] for command in self.commands])


class ManagedStatusCommentsTest(unittest.TestCase):
    @patch.object(
        pr_status_comment,
        "gh_api",
        return_value=[
            {"id": 1, "body": "<!-- pull-request-dashboard-status --> spoofed"},
            {
                "id": 2,
                "body": "<!-- pull-request-dashboard-status --> current",
                "performed_via_github_app": {"slug": "opentelemetry-pr-dashboard"},
            },
            {
                "id": 3,
                "body": "<!-- review-guidance --> legacy",
                "performed_via_github_app": {"slug": "opentelemetry-pr-dashboard"},
            },
            {
                "id": 4,
                "body": "<!-- pull-request-dashboard-status --> other app",
                "performed_via_github_app": {"slug": "other-app"},
            },
        ],
    )
    def test_requires_dashboard_app_identity_and_marker(self, _gh_api: object) -> None:
        comments = pr_status_comment.managed_status_comments("open-telemetry/example", 1)

        self.assertEqual([2, 3], [comment["id"] for comment in comments])


class RolloutStateTest(unittest.TestCase):
    @patch.object(pr_status_comment, "save_status_comment_rollout_state")
    @patch.object(pr_status_comment, "publish_pr_status")
    @patch.object(pr_status_comment, "load_dashboard_state_cache", return_value={"prs": {}})
    @patch.object(
        pr_status_comment,
        "load_status_comment_rollout_state",
        return_value={
            "target_revision": 0,
            "completed_revision": 0,
            "pending_pr_numbers": [12, 34],
        },
    )
    def test_targeted_update_only_drains_triggering_pr(
        self,
        _load_rollout: object,
        _load_dashboard: object,
        publish_pr_status: Mock,
        save_rollout: Mock,
    ) -> None:
        status = pr_status_comment.update_targeted_status_comment_from_state(
            "open-telemetry/example",
            34,
        )

        self.assertEqual([], status)
        publish_pr_status.assert_called_once_with("open-telemetry/example", 34, {"prs": {}})
        saved_state = save_rollout.call_args.args[0]
        self.assertEqual(0, saved_state["target_revision"])
        self.assertEqual([12], saved_state["pending_pr_numbers"])

    @patch.object(pr_status_comment, "save_status_comment_rollout_state")
    @patch.object(pr_status_comment, "publish_pr_status")
    @patch.object(pr_status_comment, "load_dashboard_state_cache", return_value={"prs": {}})
    @patch.object(
        pr_status_comment,
        "load_status_comment_rollout_state",
        return_value={
            "target_revision": 12,
            "completed_revision": 11,
            "pending_pr_numbers": [34],
        },
    )
    def test_targeted_update_completes_drained_rollout(
        self,
        _load_rollout: object,
        _load_dashboard: object,
        _publish_pr_status: Mock,
        save_rollout: Mock,
    ) -> None:
        status = pr_status_comment.update_targeted_status_comment_from_state(
            "open-telemetry/example",
            34,
        )

        self.assertEqual([], status)
        saved_state = save_rollout.call_args.args[0]
        self.assertEqual(12, saved_state["target_revision"])
        self.assertEqual(12, saved_state["completed_revision"])
        self.assertEqual([], saved_state["pending_pr_numbers"])

    def test_new_revision_queues_every_open_pr(self) -> None:
        state = pr_status_comment.prepare_rollout_state(
            {
                "target_revision": 0,
                "completed_revision": 0,
                "pending_pr_numbers": [],
            },
            {12, 34},
        )

        self.assertEqual(pr_status_comment.STATUS_COMMENT_REVISION, state["target_revision"])
        self.assertEqual(0, state["completed_revision"])
        self.assertEqual([12, 34], state["pending_pr_numbers"])

    def test_current_revision_drops_closed_prs_from_queue(self) -> None:
        state = pr_status_comment.prepare_rollout_state(
            {
                "target_revision": pr_status_comment.STATUS_COMMENT_REVISION,
                "completed_revision": 0,
                "pending_pr_numbers": [12, 34],
            },
            {34},
        )

        self.assertEqual([34], state["pending_pr_numbers"])

    @patch.object(pr_status_comment, "save_status_comment_rollout_state")
    @patch.object(pr_status_comment, "publish_pr_status")
    @patch.object(pr_status_comment, "load_dashboard_state_cache", return_value={"prs": {}})
    @patch.object(
        pr_status_comment,
        "load_status_comment_rollout_state",
        return_value={
            "target_revision": pr_status_comment.STATUS_COMMENT_REVISION,
            "completed_revision": 0,
            "pending_pr_numbers": [12, 34],
        },
    )
    def test_rollout_drains_queued_closed_pr(
        self,
        _load_rollout: object,
        _load_dashboard: object,
        publish_pr_status: Mock,
        save_rollout: Mock,
    ) -> None:
        status = pr_status_comment.update_status_comments_from_state(
            "open-telemetry/example",
            {34},
        )

        self.assertEqual([], status)
        self.assertEqual(
            [12, 34],
            [call.args[1] for call in publish_pr_status.call_args_list],
        )
        saved_state = save_rollout.call_args.args[0]
        self.assertEqual([], saved_state["pending_pr_numbers"])
        self.assertEqual(
            pr_status_comment.STATUS_COMMENT_REVISION,
            saved_state["completed_revision"],
        )

    @patch.object(pr_status_comment, "save_status_comment_rollout_state")
    @patch.object(pr_status_comment, "publish_pr_status")
    @patch.object(pr_status_comment, "load_dashboard_state_cache", return_value={"prs": {}})
    @patch.object(
        pr_status_comment,
        "load_status_comment_rollout_state",
        return_value={
            "target_revision": 0,
            "completed_revision": 0,
            "pending_pr_numbers": [],
        },
    )
    def test_rollout_drains_capped_batch(
        self,
        _load_rollout: object,
        _load_dashboard: object,
        publish_pr_status: Mock,
        save_rollout: Mock,
    ) -> None:
        open_pr_numbers = set(range(1, 56))

        status = pr_status_comment.update_status_comments_from_state(
            "open-telemetry/example",
            open_pr_numbers,
        )

        self.assertEqual([], status)
        self.assertEqual(
            list(range(1, 51)),
            [call.args[1] for call in publish_pr_status.call_args_list],
        )
        saved_state = save_rollout.call_args.args[0]
        self.assertEqual([51, 52, 53, 54, 55], saved_state["pending_pr_numbers"])
        self.assertEqual(0, saved_state["completed_revision"])

    @patch.object(pr_status_comment, "save_status_comment_rollout_state")
    @patch.object(
        pr_status_comment,
        "publish_pr_status",
        side_effect=[RuntimeError("failed"), None],
    )
    @patch.object(pr_status_comment, "load_dashboard_state_cache", return_value={"prs": {}})
    @patch.object(
        pr_status_comment,
        "load_status_comment_rollout_state",
        return_value={
            "target_revision": 0,
            "completed_revision": 0,
            "pending_pr_numbers": [],
        },
    )
    def test_failed_comment_write_retains_only_failed_pr_and_continues(
        self,
        _load_rollout: object,
        _load_dashboard: object,
        publish_pr_status: Mock,
        save_rollout: Mock,
    ) -> None:
        errors = pr_status_comment.update_status_comments_from_state(
            "open-telemetry/example",
            {12, 34},
        )

        self.assertEqual(["PR #12: failed"], errors)
        self.assertEqual([12, 34], [call.args[1] for call in publish_pr_status.call_args_list])
        saved_state = save_rollout.call_args.args[0]
        self.assertEqual([12], saved_state["pending_pr_numbers"])
        self.assertEqual(0, saved_state["completed_revision"])


if __name__ == "__main__":
    unittest.main()