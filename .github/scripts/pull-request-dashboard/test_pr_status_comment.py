from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse

import pr_status_comment
from dashboard_contracts import (
    DashboardRoute,
    EvaluationFailure,
    StoredDashboardResult,
)
from dashboard_test_support import (
    dashboard_facts,
    dashboard_state,
    evaluation_failure,
    stored_dashboard_result,
)
from dashboard_status import status_reviewer_handoff_clearance


def status_result(
    route: DashboardRoute | str = DashboardRoute.APPROVER,
    **facts: object,
) -> StoredDashboardResult | EvaluationFailure:
    route = DashboardRoute(route)
    if route.is_failure:
        return evaluation_failure(
            route=route,
            facts=dashboard_facts(**facts),
        )
    return stored_dashboard_result(
        route=route,
        facts=dashboard_facts(**facts),
    )


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
            status_result(
                DashboardRoute.AUTHOR,
                author="alice",
                author_nudge_episode_id="abc123",
                author_action_review_thread_urls=(
                    "https://github.com/open-telemetry/example/pull/1#discussion_r1",
                ),
                author_action_top_level_feedback_urls=(
                    "https://github.com/open-telemetry/example/pull/1#pullrequestreview-2",
                ),
            ),
        )

        self.assertIn("**Waiting on the author** · refreshed ", body)
        self.assertIn(f"Respond to 2 review items {pr_status_comment.RESPONSE_EXAMPLES}:", body)
        self.assertIn(
            f"<!-- pull-request-dashboard-status-revision:{pr_status_comment.STATUS_COMMENT_REVISION} -->",
            body,
        )
        self.assertIn(
            pr_status_comment.author_nudge_episode_marker("abc123"),
            body,
        )
        self.assertNotIn("### Review feedback", body)
        self.assertIn("- **Inline threads:** [1]", body)
        self.assertIn("- **Top-level threads:** [2]", body)
        self.assertIn(
            "- **Should this be with reviewers?** Comment "
            "`/dashboard route:reviewers` to route it to them.",
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

    def test_persists_feedback_cleared_handoff_in_status_comment(self) -> None:
        body = pr_status_comment.render_status_comment(
            self.pr(),
            status_result(
                DashboardRoute.AUTHOR,
                dashboard_override_bound_command_id=12,
                dashboard_override_head_sha="abcdef123456",
                dashboard_override_cleared_by_feedback=True,
            ),
        )
        comment = {
            "user": {"login": "opentelemetry-pr-dashboard[bot]"},
            "body": body,
        }

        self.assertIn(
            "<!-- pull-request-dashboard-reviewer-handoff-cleared:"
            "12:abcdef123456 -->",
            body,
        )
        self.assertIn(
            "<!-- pull-request-dashboard-override-ack:"
            "12:abcdef123456 -->",
            body,
        )
        self.assertEqual(
            (98, "no-status-marker"),
            status_reviewer_handoff_clearance([
                {
                    "user": {"login": "alice"},
                    "body": (
                        f"{pr_status_comment.STATUS_MARKER}\n"
                        "<!-- pull-request-dashboard-reviewer-handoff-cleared:"
                        "99:forged -->"
                    ),
                },
                {
                    "user": {"login": "opentelemetry-pr-dashboard[bot]"},
                    "body": (
                        "<!-- pull-request-dashboard-reviewer-handoff-cleared:"
                        "98:no-status-marker -->"
                    ),
                },
                comment,
            ]),
        )

    def test_optional_status_markers_have_stable_order(self) -> None:
        body = pr_status_comment.render_status_comment(
            self.pr(),
            status_result(
                DashboardRoute.AUTHOR,
                author_nudge_episode_id="abc123",
                dashboard_override_bound_command_id=12,
                dashboard_override_head_sha="abcdef123456",
                dashboard_override_cleared_by_feedback=True,
            ),
        )

        self.assertEqual(
            [
                pr_status_comment.STATUS_MARKER,
                (
                    "<!-- pull-request-dashboard-status-revision:"
                    f"{pr_status_comment.STATUS_COMMENT_REVISION} -->"
                ),
                pr_status_comment.author_nudge_episode_marker("abc123"),
                (
                    "<!-- pull-request-dashboard-override-ack:"
                    "12:abcdef123456 -->"
                ),
                (
                    "<!-- pull-request-dashboard-reviewer-handoff-cleared:"
                    "12:abcdef123456 -->"
                ),
                "## Pull request dashboard status",
            ],
            body.splitlines()[:6],
        )

    def test_clearance_recovery_prefers_the_latest_marker_for_a_command(self) -> None:
        def status_comment(head: str, updated_at: str) -> dict[str, object]:
            return {
                "user": {"login": "opentelemetry-pr-dashboard[bot]"},
                "updated_at": updated_at,
                "body": (
                    f"{pr_status_comment.STATUS_MARKER}\n"
                    "<!-- pull-request-dashboard-reviewer-handoff-cleared:"
                    f"12:{head} -->"
                ),
            }

        self.assertEqual(
            (12, "current-head"),
            status_reviewer_handoff_clearance([
                status_comment("current-head", "2026-08-25T09:00:00Z"),
                status_comment("old-head", "2026-08-25T08:00:00Z"),
            ]),
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
        first_body = pr_status_comment.render_status_comment(
            self.pr(),
            status_result(),
        )
        second_body = pr_status_comment.render_status_comment(
            self.pr(),
            status_result(),
        )

        self.assertIn("**Waiting on reviewers** · refreshed 2026-07-18 12:34 UTC", first_body)
        self.assertIn("**Waiting on reviewers** · refreshed 2026-07-18 12:35 UTC", second_body)
        self.assertNotEqual(first_body, second_body)

    def test_accuracy_note_prefills_central_issue_for_every_status(self) -> None:
        cases = (
            (self.pr(), status_result()),
            (self.pr(draft=True), None),
            (self.pr(merged=True), None),
            (self.pr(state="closed"), None),
            (self.pr(), None),
        )

        for pr, result in cases:
            with self.subTest(pr=pr, result=result):
                body = pr_status_comment.render_status_comment(pr, result)

                self.assertIn("[Report it](", body)
                self.assertIn("with what you expected", body)
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
            status_result(),
        )

        status_comment, footer = body.split("\n\n<details>", maxsplit=1)
        report_url = footer.split("[Report it](", maxsplit=1)[1].split(
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
            status_result(
                DashboardRoute.AUTHOR,
                ci_failing_count=1,
                non_blocking_check_failures=tuple(
                        "&" * pr_status_comment.NON_BLOCKING_CHECK_FAILURE_NAME_LIMIT
                        for _ in range(
                            pr_status_comment.NON_BLOCKING_CHECK_FAILURE_LIMIT
                        )
                ),
                author_action_review_thread_urls=tuple(
                        "https://github.com/open-telemetry/example/pull/1"
                        "#discussion_r1234567890"
                        for _ in range(
                            pr_status_comment.AUTHOR_ACTION_FEEDBACK_LINK_LIMIT
                        )
                ),
            ),
        )

        report_url = body.split("[Report it](", maxsplit=1)[1].split(
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
            status_result(
                DashboardRoute.AUTHOR,
                author="alice",
                ci_failing_count=1,
            ),
        )

        self.assertIn("**Waiting on the author** · refreshed ", body)
        self.assertIn("Investigate required status check failures.", body)
        self.assertNotIn("### Review feedback", body)
        self.assertNotIn(pr_status_comment.RESPONSE_EXAMPLES, body)

    def test_waiting_on_author_names_merge_conflicts(self) -> None:
        body = pr_status_comment.render_status_comment(
            self.pr(),
            status_result(
                DashboardRoute.AUTHOR,
                author="alice",
                conflicts="yes",
                ci_failing_count=1,
            ),
        )

        self.assertIn("**Waiting on the author** · refreshed ", body)
        self.assertIn("Resolve merge conflicts.", body)
        self.assertIn("Investigate required status check failures.", body)
        self.assertIn("Should this be with reviewers?", body)

    def test_conflicted_held_pr_names_the_outstanding_gate(self) -> None:
        body = pr_status_comment.render_status_comment(
            self.pr(),
            status_result(
                DashboardRoute.AUTHOR,
                author="alice",
                conflicts="yes",
                route_held_for_gates=True,
                required_checks_settled=False,
                copilot_review_outstanding=False,
            ),
        )

        self.assertIn("Resolve merge conflicts.", body)
        self.assertIn("Wait for the required status checks to report;", body)

    def test_waiting_on_maintainers_keeps_route_and_names_merge_conflicts(self) -> None:
        body = pr_status_comment.render_status_comment(
            self.pr(),
            status_result(
                DashboardRoute.MAINTAINER,
                author="alice",
                conflicts="yes",
            ),
        )

        self.assertIn("**Waiting on maintainers** · refreshed ", body)
        self.assertIn("Resolve merge conflicts, then merge when ready.", body)

    def test_held_pr_names_only_the_outstanding_check_gate(self) -> None:
        body = pr_status_comment.render_status_comment(
            self.pr(),
            status_result(
                DashboardRoute.AUTHOR,
                author="alice",
                route_held_for_gates=True,
                required_checks_settled=False,
                copilot_review_outstanding=False,
            ),
        )

        self.assertIn("Wait for the required status checks to report;", body)

    def test_held_pr_names_only_the_outstanding_copilot_gate(self) -> None:
        body = pr_status_comment.render_status_comment(
            self.pr(),
            status_result(
                DashboardRoute.AUTHOR,
                author="alice",
                route_held_for_gates=True,
                required_checks_settled=True,
                copilot_review_outstanding=True,
            ),
        )

        self.assertIn("Wait for the Copilot review to report;", body)

    def test_a_pr_released_from_a_stalled_gate_says_so(self) -> None:
        body = pr_status_comment.render_status_comment(
            self.pr(),
            status_result(
                author="alice",
                route_held_for_gates=False,
                route_hold_expired=True,
                required_checks_settled=True,
                copilot_review_outstanding=True,
                copilot_review_unreported=True,
            ),
        )

        self.assertIn(
            "The dashboard stopped waiting for the Copilot review to report, "
            "and routed this pull request anyway.",
            body,
        )

    def test_waiting_on_author_combines_ci_and_review_feedback_reasons(self) -> None:
        body = pr_status_comment.render_status_comment(
            self.pr(),
            status_result(
                DashboardRoute.AUTHOR,
                author="alice",
                ci_failing_count=2,
                author_action_review_thread_urls=(
                    "https://github.com/open-telemetry/example/pull/1#discussion_r1",
                ),
            ),
        )

        self.assertIn("Two things need attention:", body)
        self.assertIn("- **Required checks are failing** — investigate the failures.", body)
        self.assertIn("- **1 review item** — respond to each {}:".format(pr_status_comment.RESPONSE_EXAMPLES), body)
        self.assertIn("  - **Inline threads:** [1]", body)

    def test_required_ci_action_notes_configured_non_blocking_failures(self) -> None:
        body = pr_status_comment.render_status_comment(
            self.pr(),
            status_result(
                DashboardRoute.AUTHOR,
                ci_failing_count=2,
                non_blocking_check_failures=(
                    "CodeQL",
                    "workflow-notification",
                ),
            ),
        )

        self.assertIn(
            "Investigate required status check failures. "
            "Note: CodeQL and workflow-notification are also failing but are not required checks.",
            body,
        )

    def test_required_ci_action_escapes_non_blocking_failure_names(self) -> None:
        body = pr_status_comment.render_status_comment(
            self.pr(),
            status_result(
                DashboardRoute.AUTHOR,
                ci_failing_count=1,
                non_blocking_check_failures=(
                    "[CodeQL] <script>\n@maintainers",
                    r"pipe|slash\check & more",
                ),
            ),
        )

        self.assertIn(
            "Note: \\[CodeQL\\] &lt;script&gt; &#64;maintainers and "
            "pipe\\|slash\\\\check &amp; more are also failing but are not required checks.",
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
            status_result(
                DashboardRoute.AUTHOR,
                ci_failing_count=1,
                non_blocking_check_failures=tuple(failures),
            ),
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
            status_result(non_blocking_check_failures=("codecov/patch",)),
        )

        self.assertIn("**Waiting on reviewers** · refreshed ", body)
        self.assertIn(
            "**Non-blocking check failure:** codecov/patch",
            body.splitlines(),
        )

    def test_non_author_routes_also_name_required_ci_failures(self) -> None:
        cases = (
            (
                "maintainer",
                1,
                "Waiting on maintainers",
                ["CodeQL"],
                "1 required status check is failing.",
                "**Non-blocking check failure:** CodeQL",
            ),
            (
                "approver",
                2,
                "Waiting on reviewers",
                ["CodeQL", "workflow-notification"],
                "2 required status checks are failing.",
                "**Non-blocking check failures:** CodeQL and workflow-notification",
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
                    status_result(
                        route,
                        ci_failing_count=failing_count,
                        non_blocking_check_failures=tuple(non_blocking_failures),
                    ),
                )

                self.assertIn(f"**{waiting_on}** · refreshed ", body)
                self.assertIn(f"**Also blocked by:** {blocked_by}", body)
                self.assertIn(non_blocking_line, body.splitlines())

    def test_reviewer_handoff_still_reports_a_check_failure_as_blocking(self) -> None:
        body = pr_status_comment.render_status_comment(
            self.pr(),
            status_result(ci_failing_count=1),
        )

        self.assertIn("**Also blocked by:** 1 required status check is failing.", body)

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
            status_result(
                DashboardRoute.AUTHOR,
                author="alice",
                author_action_review_thread_urls=tuple(review_thread_urls),
                author_action_top_level_feedback_urls=tuple(
                    top_level_feedback_urls
                ),
            ),
        )

        self.assertIn("- **Inline threads:**", body)
        self.assertIn("- **Top-level threads:** [20]", body)
        self.assertIn(
            "- _Showing 20 of 22 feedback links; "
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
            status_result(
                DashboardRoute.AUTHOR,
                author_action_review_thread_urls=tuple(review_thread_urls),
                author_action_top_level_feedback_urls=(
                    "https://github.com/open-telemetry/example/pull/1#issuecomment-1",
                ),
            ),
        )

        self.assertNotIn("Top-level threads", body)
        self.assertIn(
            "- _Showing 20 of 21 feedback links; "
            "resolve the remaining items from the pull request's conversation._",
            body,
        )

    def test_draft_waits_on_author(self) -> None:
        body = pr_status_comment.render_status_comment(self.pr(draft=True), None)

        self.assertIn("**Waiting on the author** · refreshed ", body)
        self.assertIn("Move out of draft to request review.", body)

    def test_merged_pr_has_no_author_guidance(self) -> None:
        body = pr_status_comment.render_status_comment(
            self.pr(state="closed", merged=True),
            None,
        )

        self.assertIn("**Merged** · refreshed ", body)
        self.assertNotIn(pr_status_comment.RESPONSE_EXAMPLES, body)

    def test_terminal_pr_has_no_author_feedback_links(self) -> None:
        result = status_result(
            DashboardRoute.AUTHOR,
            author="alice",
            author_action_review_thread_urls=(
                "https://github.com/open-telemetry/example/pull/1#discussion_r1",
            ),
            author_action_top_level_feedback_urls=(
                "https://github.com/open-telemetry/example/pull/1#pullrequestreview-2",
            ),
        )

        for overrides in ({"state": "closed"}, {"state": "closed", "merged": True}):
            with self.subTest(overrides=overrides):
                body = pr_status_comment.render_status_comment(self.pr(**overrides), result)

                self.assertNotIn("### Review feedback", body)
                self.assertNotIn("- **Inline threads", body)
                self.assertNotIn("- **Top-level threads", body)

    def test_author_login_is_not_mentioned(self) -> None:
        body = pr_status_comment.render_status_comment(
            self.pr(),
            status_result(DashboardRoute.AUTHOR, author="alice"),
        )

        self.assertIn("**Waiting on the author** · refreshed ", body)
        self.assertNotIn("@alice", body)

    def test_routes_render_one_status_sentence(self) -> None:
        expected_summaries = {
            "approver": ("Waiting on reviewers", "Review the latest changes."),
            "maintainer": ("Waiting on maintainers", "Merge when ready."),
            "transient-failure": ("Waiting on the pull request dashboard maintainers", "Determine the next action."),
            "unknown": ("Waiting on the pull request dashboard maintainers", "Determine the next action."),
        }

        for route, (headline, next_step) in expected_summaries.items():
            with self.subTest(route=route):
                body = pr_status_comment.render_status_comment(
                    self.pr(),
                    status_result(route),
                )

                self.assertIn(f"**{headline}** · refreshed ", body)
                self.assertIn(next_step, body)
                self.assertNotIn("**Status:**", body)
                self.assertNotIn(pr_status_comment.RESPONSE_EXAMPLES, body)


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
            {
                "id": 7,
                "performed_via_github_app": {
                    "slug": pr_status_comment.DASHBOARD_APP_SLUG,
                },
                "body": "<!-- pull-request-dashboard-status --> old",
            },
            {
                "id": 8,
                "performed_via_github_app": {
                    "slug": pr_status_comment.DASHBOARD_APP_SLUG,
                },
                "body": (
                    "<!-- pull-request-dashboard-status -->\n"
                    "<!-- pull-request-dashboard-override-ack:"
                    "12:bound-head:2026-08-16T08:00:00Z -->\n"
                    "<!-- pull-request-dashboard-reviewer-handoff-cleared:"
                    "12:bound-head -->"
                ),
            },
        ],
    )
    def test_updates_comment_and_deletes_duplicates(self, _comments: object) -> None:
        pr_status_comment.upsert_status_comment(
            "open-telemetry/example",
            1,
            "body",
            preserve_clearance=True,
        )

        self.assertEqual(["PATCH", "DELETE"], [command[3] for command in self.commands])
        self.assertIn(
            "<!-- pull-request-dashboard-override-ack:"
            "12:bound-head:2026-08-16T08:00:00Z -->",
            self.commands[0][-1],
        )
        self.assertIn(
            "<!-- pull-request-dashboard-reviewer-handoff-cleared:"
            "12:bound-head -->",
            self.commands[0][-1],
        )

    @patch.object(pr_status_comment, "managed_status_comments", return_value=[])
    def test_does_not_create_comment_when_creation_is_disabled(
        self, _comments: object
    ) -> None:
        pr_status_comment.upsert_status_comment(
            "open-telemetry/example", 1, "body", create=False
        )

        self.assertEqual([], self.commands)

    @patch.object(pr_status_comment, "managed_status_comments", return_value=[])
    def test_locked_pr_without_status_comment_has_nothing_to_defer(
        self, _comments: object
    ) -> None:
        pr_status_comment.upsert_status_comment(
            "open-telemetry/example",
            1,
            "body",
            create=False,
            locked=True,
        )

        self.assertEqual([], self.commands)

    @patch.object(
        pr_status_comment,
        "managed_status_comments",
        return_value=[{"id": 7, "body": "<!-- pull-request-dashboard-status --> old"}],
    )
    def test_still_updates_existing_comment_when_creation_is_disabled(
        self, _comments: object
    ) -> None:
        pr_status_comment.upsert_status_comment(
            "open-telemetry/example", 1, "body", create=False
        )

        self.assertEqual(["PATCH"], [command[3] for command in self.commands])

    @patch.object(
        pr_status_comment,
        "managed_status_comments",
        return_value=[{
            "id": 7,
            "performed_via_github_app": {
                "slug": pr_status_comment.DASHBOARD_APP_SLUG,
            },
            "body": (
                f"{pr_status_comment.STATUS_MARKER}\n"
                "<!-- pull-request-dashboard-override-ack:"
                "12:bound-head:2026-08-16T08:00:00Z -->\n"
                "<!-- pull-request-dashboard-reviewer-handoff-cleared:"
                "12:bound-head -->\n"
                "old status"
            ),
        }],
    )
    def test_status_update_preserves_handoff_clearance(
        self, _comments: object
    ) -> None:
        body = (
            f"{pr_status_comment.STATUS_MARKER}\n"
            "<!-- pull-request-dashboard-status-revision:4 -->\n"
            "## Pull request dashboard status"
        )

        pr_status_comment.upsert_status_comment(
            "open-telemetry/example",
            1,
            body,
            create=False,
            preserve_clearance=True,
        )

        self.assertIn(
            "body="
            f"{pr_status_comment.STATUS_MARKER}\n"
            "<!-- pull-request-dashboard-status-revision:4 -->\n"
            "<!-- pull-request-dashboard-override-ack:"
            "12:bound-head:2026-08-16T08:00:00Z -->\n"
            "<!-- pull-request-dashboard-reviewer-handoff-cleared:"
            "12:bound-head -->\n"
            "## Pull request dashboard status",
            self.commands[0],
        )

    @patch.object(
        pr_status_comment,
        "managed_status_comments",
        return_value=[{"id": 7, "body": "<!-- pull-request-dashboard-status --> old"}],
    )
    def test_locked_pr_defers_existing_comment_update(self, _comments: object) -> None:
        with self.assertRaisesRegex(
            pr_status_comment.StatusCommentDeferred,
            "PR #1 is locked",
        ):
            pr_status_comment.upsert_status_comment(
                "open-telemetry/example",
                1,
                "body",
                create=False,
                locked=True,
            )

        self.assertEqual([], self.commands)


class PublishPrStatusTest(unittest.TestCase):
    @patch.object(pr_status_comment, "upsert_status_comment")
    @patch.object(
        pr_status_comment,
        "gh_api",
        return_value={
            "number": 1,
            "state": "closed",
            "merged": True,
            "locked": True,
        },
    )
    def test_locked_terminal_pr_disables_creation_and_marks_update_locked(
        self, _gh_api: Mock, upsert: Mock
    ) -> None:
        pr_status_comment.publish_pr_status(
            "open-telemetry/example", 1, dashboard_state()
        )

        self.assertFalse(upsert.call_args.kwargs["create"])
        self.assertTrue(upsert.call_args.kwargs["locked"])
        self.assertTrue(upsert.call_args.kwargs["preserve_clearance"])

    @patch.object(pr_status_comment, "upsert_status_comment")
    @patch.object(pr_status_comment, "gh_api")
    def test_terminal_pr_never_creates_a_status_comment(
        self, gh_api: Mock, upsert: Mock
    ) -> None:
        for pr in (
            {"number": 1, "state": "closed", "merged": True},
            {"number": 1, "state": "closed", "merged": False},
        ):
            with self.subTest(merged=pr["merged"]):
                gh_api.return_value = pr

                pr_status_comment.publish_pr_status(
                    "open-telemetry/example", 1, dashboard_state()
                )

                self.assertFalse(upsert.call_args.kwargs["create"])

    @patch.object(pr_status_comment, "upsert_status_comment")
    @patch.object(
        pr_status_comment,
        "gh_api",
        return_value={"number": 1, "state": "open", "merged": False},
    )
    def test_open_pr_still_creates_a_status_comment(
        self, _gh_api: Mock, upsert: Mock
    ) -> None:
        pr_status_comment.publish_pr_status(
            "open-telemetry/example",
            1,
            dashboard_state(),
        )

        self.assertTrue(upsert.call_args.kwargs["create"])
        self.assertTrue(upsert.call_args.kwargs["preserve_clearance"])


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
                "body": "no marker",
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

        self.assertEqual([2], [comment["id"] for comment in comments])


class RolloutStateTest(unittest.TestCase):
    @patch.object(pr_status_comment, "STATUS_COMMENT_ROLLOUT_BATCH_SIZE", 2)
    @patch.object(pr_status_comment, "save_status_comment_rollout_state")
    @patch.object(pr_status_comment, "publish_pr_status")
    @patch.object(
        pr_status_comment,
        "managed_status_comments",
        side_effect=[
            [{"id": 34, "body": pr_status_comment.STATUS_MARKER}],
            [],
        ],
    )
    @patch.object(
        pr_status_comment,
        "load_dashboard_state_cache",
        return_value=dashboard_state(),
    )
    @patch.object(
        pr_status_comment,
        "load_status_comment_rollout_state",
        return_value={
            "target_revision": pr_status_comment.STATUS_COMMENT_REVISION,
            "completed_revision": pr_status_comment.STATUS_COMMENT_REVISION,
            "pending_pr_numbers": [],
            "draft_reconciliation_cursor": 12,
        },
    )
    def test_hourly_reconciliation_queues_only_missing_draft_comments(
        self,
        _load_rollout: object,
        _load_dashboard: object,
        managed_comments: Mock,
        publish_pr_status: Mock,
        save_rollout: Mock,
    ) -> None:
        errors = pr_status_comment.update_status_comments_from_state(
            "open-telemetry/example",
            {12, 34, 56},
            open_draft_pr_numbers={12, 34, 56},
        )

        self.assertEqual([], errors)
        self.assertEqual(
            [34, 56],
            [call.args[1] for call in managed_comments.call_args_list],
        )
        publish_pr_status.assert_called_once_with(
            "open-telemetry/example",
            56,
            dashboard_state(),
        )
        saved_state = save_rollout.call_args.args[0]
        self.assertEqual([], saved_state["pending_pr_numbers"])
        self.assertEqual(56, saved_state["draft_reconciliation_cursor"])

    @patch.object(pr_status_comment, "STATUS_COMMENT_ROLLOUT_BATCH_SIZE", 3)
    @patch.object(pr_status_comment, "save_status_comment_rollout_state")
    @patch.object(pr_status_comment, "publish_pr_status")
    @patch.object(
        pr_status_comment,
        "managed_status_comments",
        side_effect=[
            RuntimeError("gh api failed"),
            [],
            [{"id": 56, "body": pr_status_comment.STATUS_MARKER}],
        ],
    )
    @patch.object(
        pr_status_comment,
        "load_dashboard_state_cache",
        return_value=dashboard_state(),
    )
    @patch.object(
        pr_status_comment,
        "load_status_comment_rollout_state",
        return_value={
            "target_revision": pr_status_comment.STATUS_COMMENT_REVISION,
            "completed_revision": pr_status_comment.STATUS_COMMENT_REVISION,
            "pending_pr_numbers": [],
            "draft_reconciliation_cursor": 0,
        },
    )
    def test_one_failed_draft_lookup_does_not_stop_the_rollout(
        self,
        _load_rollout: object,
        _load_dashboard: object,
        managed_comments: Mock,
        publish_pr_status: Mock,
        save_rollout: Mock,
    ) -> None:
        errors = pr_status_comment.update_status_comments_from_state(
            "open-telemetry/example",
            {12, 34, 56},
            open_draft_pr_numbers={12, 34, 56},
        )

        self.assertEqual(
            ["PR #12: draft comment lookup failed: gh api failed"],
            errors,
        )
        self.assertEqual(
            [12, 34, 56],
            [call.args[1] for call in managed_comments.call_args_list],
        )
        publish_pr_status.assert_called_once_with(
            "open-telemetry/example",
            34,
            dashboard_state(),
        )
        saved_state = save_rollout.call_args.args[0]
        self.assertEqual([], saved_state["pending_pr_numbers"])
        self.assertEqual(56, saved_state["draft_reconciliation_cursor"])

    @patch.object(pr_status_comment, "save_status_comment_rollout_state")
    @patch.object(
        pr_status_comment,
        "publish_pr_status",
        side_effect=pr_status_comment.StatusCommentDeferred("PR #34 is locked"),
    )
    @patch.object(
        pr_status_comment,
        "load_dashboard_state_cache",
        return_value=dashboard_state(),
    )
    @patch.object(
        pr_status_comment,
        "load_status_comment_rollout_state",
        return_value={
            "target_revision": 12,
            "completed_revision": 11,
            "pending_pr_numbers": [34],
        },
    )
    def test_targeted_update_retains_deferred_locked_pr(
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
        save_rollout.assert_not_called()

    @patch.object(pr_status_comment, "save_status_comment_rollout_state")
    @patch.object(pr_status_comment, "publish_pr_status")
    @patch.object(
        pr_status_comment,
        "load_dashboard_state_cache",
        return_value=dashboard_state(),
    )
    @patch.object(
        pr_status_comment,
        "load_status_comment_rollout_state",
        return_value={
            "target_revision": 0,
            "completed_revision": 0,
            "pending_pr_numbers": [56, 34, 12],
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
        publish_pr_status.assert_called_once_with(
            "open-telemetry/example",
            34,
            dashboard_state(),
        )
        saved_state = save_rollout.call_args.args[0]
        self.assertEqual(0, saved_state["target_revision"])
        self.assertEqual([56, 12], saved_state["pending_pr_numbers"])

    @patch.object(pr_status_comment, "save_status_comment_rollout_state")
    @patch.object(pr_status_comment, "publish_pr_status")
    @patch.object(
        pr_status_comment,
        "load_dashboard_state_cache",
        return_value=dashboard_state(),
    )
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
    @patch.object(
        pr_status_comment,
        "load_dashboard_state_cache",
        return_value=dashboard_state(),
    )
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
    @patch.object(
        pr_status_comment,
        "load_dashboard_state_cache",
        return_value=dashboard_state(),
    )
    @patch.object(
        pr_status_comment,
        "load_status_comment_rollout_state",
        return_value={
            "target_revision": pr_status_comment.STATUS_COMMENT_REVISION,
            "completed_revision": 0,
            "pending_pr_numbers": [7, 8],
        },
    )
    def test_rollout_retains_excluded_pr_without_publishing_it(
        self,
        _load_rollout: object,
        _load_dashboard: object,
        publish_pr_status: Mock,
        save_rollout: Mock,
    ) -> None:
        errors = pr_status_comment.update_status_comments_from_state(
            "open-telemetry/example",
            {7, 8},
            {7},
        )

        self.assertEqual([], errors)
        publish_pr_status.assert_called_once_with(
            "open-telemetry/example",
            8,
            dashboard_state(),
        )
        saved_state = save_rollout.call_args.args[0]
        self.assertEqual([7], saved_state["pending_pr_numbers"])
        self.assertEqual(0, saved_state["completed_revision"])

    @patch.object(pr_status_comment, "save_status_comment_rollout_state")
    @patch.object(pr_status_comment, "publish_pr_status")
    @patch.object(
        pr_status_comment,
        "load_dashboard_state_cache",
        return_value=dashboard_state(),
    )
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
    @patch.object(
        pr_status_comment,
        "load_dashboard_state_cache",
        return_value=dashboard_state(),
    )
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
        self.assertEqual(
            [12, 34],
            [call.args[1] for call in publish_pr_status.call_args_list],
        )
        saved_state = save_rollout.call_args.args[0]
        self.assertEqual([12], saved_state["pending_pr_numbers"])
        self.assertEqual(0, saved_state["completed_revision"])

    @patch.object(pr_status_comment, "save_status_comment_rollout_state")
    @patch.object(
        pr_status_comment,
        "publish_pr_status",
        side_effect=[
            pr_status_comment.StatusCommentDeferred("PR #12 is locked"),
            None,
        ],
    )
    @patch.object(
        pr_status_comment,
        "load_dashboard_state_cache",
        return_value=dashboard_state(),
    )
    @patch.object(
        pr_status_comment,
        "load_status_comment_rollout_state",
        return_value={
            "target_revision": 0,
            "completed_revision": 0,
            "pending_pr_numbers": [],
        },
    )
    def test_deferred_locked_pr_stays_pending_without_delivery_error(
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

        self.assertEqual([], errors)
        self.assertEqual(
            [12, 34],
            [call.args[1] for call in publish_pr_status.call_args_list],
        )
        saved_state = save_rollout.call_args.args[0]
        self.assertEqual([12], saved_state["pending_pr_numbers"])
        self.assertEqual(0, saved_state["completed_revision"])

    @patch.object(pr_status_comment, "STATUS_COMMENT_ROLLOUT_BATCH_SIZE", 2)
    @patch.object(
        pr_status_comment,
        "publish_pr_status",
        side_effect=[
            pr_status_comment.StatusCommentDeferred("PR #12 is locked"),
            pr_status_comment.StatusCommentDeferred("PR #34 is locked"),
        ],
    )
    @patch.object(
        pr_status_comment,
        "load_dashboard_state_cache",
        return_value=dashboard_state(),
    )
    def test_deferred_locked_prs_rotate_behind_unattempted_prs(
        self,
        _load_dashboard: object,
        publish_pr_status: Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "state._state_dir", Path(temp_dir)
        ):
            pr_status_comment.save_status_comment_rollout_state({
                "target_revision": pr_status_comment.STATUS_COMMENT_REVISION,
                "completed_revision": 0,
                "pending_pr_numbers": [12, 34, 56],
            })

            errors = pr_status_comment.update_status_comments_from_state(
                "open-telemetry/example",
                {12, 34, 56},
            )

            self.assertEqual([], errors)
            self.assertEqual(
                [12, 34],
                [call.args[1] for call in publish_pr_status.call_args_list],
            )
            saved_state = pr_status_comment.load_status_comment_rollout_state()
            self.assertEqual([56, 12, 34], saved_state["pending_pr_numbers"])
            self.assertEqual(0, saved_state["completed_revision"])

            publish_pr_status.reset_mock()
            publish_pr_status.side_effect = [
                None,
                pr_status_comment.StatusCommentDeferred("PR #12 is locked"),
            ]

            errors = pr_status_comment.update_status_comments_from_state(
                "open-telemetry/example",
                {12, 34, 56},
            )

            self.assertEqual([], errors)
            self.assertEqual(
                [56, 12],
                [call.args[1] for call in publish_pr_status.call_args_list],
            )
            self.assertEqual(
                [34, 12],
                pr_status_comment.load_status_comment_rollout_state()[
                    "pending_pr_numbers"
                ],
            )


if __name__ == "__main__":
    unittest.main()