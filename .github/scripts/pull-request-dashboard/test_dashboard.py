from __future__ import annotations

from argparse import Namespace
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import ANY, Mock, call, patch

from classification import discussion_prompt_input
from copilot_review import apply_copilot_review_gate
from dashboard import (
    BACKFILL_RECORDED_FAILURE_STATUS,
    DashboardUpdate,
    add_wait_age_facts,
    apply_targeted_dashboard_update,
    author_action_discussion_urls,
    backfill_failed_pr_numbers,
    complete_initial_backfill_if_ready,
    compute_facts,
    fetch_pr_raw,
    group_review_threads,
    main,
    remove_cached_dashboard_prs,
    route_pr,
    set_backfill_pr_failed,
    update_dashboard_for_backfill,
    write_initial_backfill_output,
)


class FetchPrRawTest(unittest.TestCase):
    def test_uses_graphql_issue_comments_without_rest_join(self) -> None:
        issue_comments = [{"id": 101, "body": "GraphQL comment"}]
        rest_payloads = {
            "/repos/owner/repo/pulls/7/comments?per_page=100": [
                {"id": 201, "body": "Review comment"}
            ],
            "/repos/owner/repo/pulls/7/commits?per_page=100": [
                {"sha": "abcdef123456"}
            ],
        }

        def gh_api(path: str, paginate: bool) -> list[dict]:
            self.assertTrue(paginate)
            return rest_payloads[path]

        with (
            patch(
                "dashboard.gh_pr_view",
                return_value={"id": "PR_node", "baseRefName": "main"},
            ),
            patch(
                "dashboard.fetch_pr_issue_comments",
                return_value=issue_comments,
            ) as fetch_issue_comments,
            patch("dashboard.gh_api", side_effect=gh_api) as rest_api,
            patch("dashboard.fetch_review_threads", return_value=[]),
            patch(
                "dashboard.fetch_pr_review_data",
                return_value={"reviews": [], "pr_metadata": {}},
            ),
            patch(
                "dashboard.gh_pr_check_rollup",
                return_value={"required": [], "non_blocking_failures": []},
            ),
            patch("dashboard.gh_required_check_contexts", return_value=[]),
            patch("dashboard.include_missing_required_checks", return_value=[]),
        ):
            raw = fetch_pr_raw(
                "owner/repo",
                "owner",
                "repo",
                {"number": 7},
                [],
            )

        self.assertEqual(raw["issue_comments"], issue_comments)
        fetch_issue_comments.assert_called_once_with("owner", "repo", 7)
        self.assertEqual(rest_api.call_count, 2)
        self.assertEqual(
            {call.args[0] for call in rest_api.call_args_list},
            set(rest_payloads),
        )


class ReviewThreadDiscussionUrlTest(unittest.TestCase):
    def test_group_review_threads_ignores_author_only_annotations(self) -> None:
        thread = {
            "id": "thread-1",
            "isResolved": False,
            "isOutdated": False,
            "comments": {
                "nodes": [
                    {
                        "url": "https://example.test/discussion/1",
                        "body": "todo: automate this later",
                        "createdAt": "2026-07-14T01:00:00Z",
                        "author": {"login": "author"},
                    },
                ],
            },
        }

        self.assertEqual(
            group_review_threads(
                {"review_threads": [thread]},
                "author",
                {"reviewer"},
                {"conflicts": "no"},
            ),
            [],
        )

        thread["comments"]["nodes"].append({
            "url": "https://example.test/discussion/2",
            "body": "Please handle this in the current PR.",
            "createdAt": "2026-07-14T02:00:00Z",
            "author": {"login": "reviewer"},
        })

        self.assertEqual(
            len(group_review_threads(
                {"review_threads": [thread]},
                "author",
                {"reviewer"},
                {"conflicts": "no"},
            )),
            1,
        )

    def test_group_review_threads_stores_first_comment_url_on_thread(self) -> None:
        threads = group_review_threads(
            {
                "review_threads": [
                    {
                        "id": "thread-1",
                        "isResolved": False,
                        "isOutdated": False,
                        "comments": {
                            "nodes": [
                                {
                                    "url": "https://example.test/discussion/1",
                                    "body": "first",
                                    "createdAt": "2026-07-14T02:00:00Z",
                                    "author": {"login": "reviewer"},
                                },
                                {
                                    "url": "https://example.test/discussion/2",
                                    "body": "second",
                                    "createdAt": "2026-07-14T01:00:00Z",
                                    "author": {"login": "author"},
                                },
                            ],
                        },
                    },
                ],
            },
            "author",
            {"reviewer"},
            {"conflicts": "no"},
        )

        self.assertEqual("https://example.test/discussion/1", threads[0]["discussion_url"])
        self.assertEqual("second", threads[0]["comments"][0]["body"])
        self.assertNotIn("url", threads[0]["comments"][0])

    def test_author_action_urls_use_thread_url_and_deduplicate(self) -> None:
        discussions = [
            {"discussion_id": "thread-1", "discussion_url": "https://example.test/discussion/1"},
            {"discussion_id": "thread-2", "discussion_url": "https://example.test/discussion/1"},
            {"discussion_id": "top-level-1", "discussion_url": "https://example.test/discussion/2"},
        ]
        pending_actions = {
            "thread-1": {"action": "author"},
            "thread-2": {"action": "author"},
            "top-level-1": {"action": "author"},
        }

        self.assertEqual(
            ["https://example.test/discussion/1", "https://example.test/discussion/2"],
            author_action_discussion_urls(discussions, pending_actions),
        )


class CopilotReviewGateTest(unittest.TestCase):
    def test_current_head_matches_latest_clean_copilot_review(self) -> None:
        facts = compute_facts(
            {
                "pr": {
                    "updatedAt": "2026-07-20T03:00:00Z",
                    "createdAt": "2026-07-20T01:00:00Z",
                    "author": {"login": "author"},
                    "assignees": [],
                    "reviewRequests": [
                        {"login": "copilot-pull-request-reviewer"},
                    ],
                    "mergeStateStatus": "CLEAN",
                    "mergeable": "MERGEABLE",
                },
                "reviews": [
                    {
                        "id": 10,
                        "commit_id": "old-head",
                        "finding_count": 1,
                        "user": {"login": "copilot-pull-request-reviewer[bot]"},
                        "submitted_at": "2026-07-20T01:30:00Z",
                    },
                    {
                        "id": 20,
                        "commit_id": "current-head",
                        "finding_count": 0,
                        "user": {"login": "copilot-pull-request-reviewer"},
                        "submitted_at": "2026-07-20T02:30:00Z",
                    },
                ],
                "commits": [{"sha": "old-head"}, {"sha": "current-head"}],
                "review_comments": [
                    {"pull_request_review_id": 10},
                ],
                "checks": [],
            },
            "author",
            [],
        )

        self.assertTrue(facts["copilot_review_requested"])
        self.assertTrue(facts["copilot_review_exists"])
        self.assertFalse(facts["copilot_review_needed"])

    def test_late_stale_review_does_not_replace_clean_current_head_review(self) -> None:
        facts = compute_facts(
            {
                "pr": {
                    "updatedAt": "2026-07-20T03:00:00Z",
                    "createdAt": "2026-07-20T01:00:00Z",
                    "author": {"login": "author"},
                    "assignees": [],
                    "reviewRequests": [],
                    "mergeStateStatus": "CLEAN",
                    "mergeable": "MERGEABLE",
                },
                "reviews": [
                    {
                        "id": 10,
                        "commit_id": "current-head",
                        "finding_count": 0,
                        "user": {"login": "copilot"},
                        "submitted_at": "2026-07-20T02:30:00Z",
                    },
                    {
                        "id": 20,
                        "commit_id": "old-head",
                        "finding_count": 1,
                        "user": {"login": "copilot"},
                        "submitted_at": "2026-07-20T03:00:00Z",
                    },
                ],
                "commits": [{"sha": "old-head"}, {"sha": "current-head"}],
                "review_comments": [{"pull_request_review_id": 20}],
                "checks": [],
            },
            "author",
            [],
        )

        self.assertFalse(facts["copilot_review_needed"])

    def test_push_since_latest_clean_copilot_review_needs_rereview(self) -> None:
        facts = compute_facts(
            {
                "pr": {
                    "updatedAt": "2026-07-20T03:00:00Z",
                    "createdAt": "2026-07-20T01:00:00Z",
                    "author": {"login": "author"},
                    "assignees": [],
                    "reviewRequests": [],
                    "mergeStateStatus": "CLEAN",
                    "mergeable": "MERGEABLE",
                },
                "reviews": [
                    {
                        "id": 20,
                        "commit_id": "reviewed-head",
                        "finding_count": 0,
                        "user": {"login": "copilot"},
                        "submitted_at": "2026-07-20T02:30:00Z",
                    },
                ],
                "commits": [{"sha": "reviewed-head"}, {"sha": "current-head"}],
                "review_comments": [],
                "checks": [],
            },
            "author",
            [],
        )

        self.assertFalse(facts["copilot_review_requested"])
        self.assertTrue(facts["copilot_review_needed"])

    def test_latest_findings_review_replaces_clean_review_on_same_head(self) -> None:
        facts = compute_facts(
            {
                "pr": {
                    "updatedAt": "2026-07-20T03:00:00Z",
                    "createdAt": "2026-07-20T01:00:00Z",
                    "author": {"login": "author"},
                    "assignees": [],
                    "reviewRequests": [],
                    "mergeStateStatus": "CLEAN",
                    "mergeable": "MERGEABLE",
                },
                "reviews": [
                    {
                        "id": 10,
                        "commit_id": "current-head",
                        "finding_count": 0,
                        "user": {"login": "copilot"},
                        "submitted_at": "2026-07-20T01:30:00Z",
                    },
                    {
                        "id": 20,
                        "commit_id": "current-head",
                        "finding_count": 1,
                        "user": {"login": "copilot"},
                        "submitted_at": "2026-07-20T02:30:00Z",
                    },
                ],
                "commits": [{"sha": "current-head"}],
                "review_comments": [{"pull_request_review_id": 20}],
                "checks": [],
            },
            "author",
            [],
        )

        self.assertTrue(facts["copilot_review_needed"])

    def test_findings_only_history_needs_rereview(self) -> None:
        facts = compute_facts(
            {
                "pr": {
                    "updatedAt": "2026-07-20T03:00:00Z",
                    "createdAt": "2026-07-20T01:00:00Z",
                    "author": {"login": "author"},
                    "assignees": [],
                    "reviewRequests": [],
                    "mergeStateStatus": "CLEAN",
                    "mergeable": "MERGEABLE",
                },
                "reviews": [
                    {
                        "id": 20,
                        "commit_id": "reviewed-head",
                        "finding_count": 1,
                        "user": {"login": "copilot"},
                        "submitted_at": "2026-07-20T02:30:00Z",
                    },
                ],
                "commits": [{"sha": "reviewed-head"}, {"sha": "current-head"}],
                "review_comments": [{"pull_request_review_id": 20}],
                "checks": [],
            },
            "author",
            [],
        )

        self.assertTrue(facts["copilot_review_needed"])

    def test_waits_for_automatic_initial_copilot_review(self) -> None:
        facts = compute_facts(
            {
                "pr": {
                    "updatedAt": "2026-07-20T03:00:00Z",
                    "createdAt": "2026-07-20T01:00:00Z",
                    "author": {"login": "author"},
                    "assignees": [],
                    "reviewRequests": [],
                    "mergeStateStatus": "CLEAN",
                    "mergeable": "MERGEABLE",
                },
                "reviews": [],
                "commits": [{"sha": "current-head"}],
                "review_comments": [],
                "checks": [],
            },
            "author",
            [],
        )

        self.assertFalse(facts["copilot_review_exists"])
        self.assertFalse(facts["copilot_review_needed"])

    def test_initial_automatic_review_blocks_human_handoff(self) -> None:
        facts = {
            "copilot_review_requested": True,
            "copilot_review_exists": False,
            "copilot_review_needed": False,
        }

        route = apply_copilot_review_gate(
            facts,
            "approver",
            enabled=True,
        )

        self.assertEqual(route, "copilot")
        self.assertFalse(facts["copilot_review_request_needed"])

    def test_marks_re_review_needed_after_push_since_clean_review(self) -> None:
        facts = {
            "copilot_review_requested": False,
            "copilot_review_exists": True,
            "copilot_review_needed": True,
        }

        route = apply_copilot_review_gate(
            facts,
            "maintainer",
            enabled=True,
        )

        self.assertEqual(route, "copilot")
        self.assertTrue(facts["copilot_review_request_needed"])

    def test_marks_re_review_needed_before_reviewer_handoff(self) -> None:
        facts = {
            "copilot_review_requested": False,
            "copilot_review_exists": True,
            "copilot_review_needed": True,
        }

        route = apply_copilot_review_gate(
            facts,
            "approver",
            enabled=True,
        )

        self.assertEqual(route, "copilot")
        self.assertTrue(facts["copilot_review_request_needed"])

    def test_pending_re_review_waits_without_duplicate_request(self) -> None:
        facts = {
            "copilot_review_requested": True,
            "copilot_review_exists": True,
            "copilot_review_needed": True,
        }

        route = apply_copilot_review_gate(
            facts,
            "maintainer",
            enabled=True,
        )

        self.assertEqual(route, "copilot")
        self.assertFalse(facts["copilot_review_request_needed"])

    def test_current_head_clean_review_moves_to_maintainers(self) -> None:
        facts = {
            "copilot_review_requested": False,
            "copilot_review_exists": True,
            "copilot_review_needed": False,
        }

        route = apply_copilot_review_gate(
            facts,
            "maintainer",
            enabled=True,
        )

        self.assertEqual(route, "maintainer")
        self.assertFalse(facts["copilot_review_request_needed"])

    def test_disabled_gate_preserves_maintainer_route(self) -> None:
        facts = {
            "copilot_review_requested": False,
            "copilot_review_exists": True,
            "copilot_review_needed": True,
        }

        route = apply_copilot_review_gate(
            facts,
            "maintainer",
            enabled=False,
        )

        self.assertEqual(route, "maintainer")
        self.assertFalse(facts["copilot_review_request_needed"])

    def test_discussion_url_is_excluded_from_classifier_input(self) -> None:
        prompt_input = discussion_prompt_input({
            "discussion_id": "thread-1",
            "discussion_kind": "review-comment-thread",
            "discussion_url": "https://example.test/discussion/1",
            "comments": [],
        })

        self.assertNotIn("discussion_url", prompt_input)


class InitialBackfillCompletionTest(unittest.TestCase):
    def test_marks_complete_only_after_all_open_prs_are_cached(self) -> None:
        state = {"initial_backfill_complete": False, "prs": {"1": {}}}

        self.assertFalse(complete_initial_backfill_if_ready(state, {1, 2}))
        self.assertFalse(state["initial_backfill_complete"])

        state["prs"]["2"] = {}
        self.assertTrue(complete_initial_backfill_if_ready(state, {1, 2}))
        self.assertTrue(state["initial_backfill_complete"])
        self.assertFalse(complete_initial_backfill_if_ready(state, {1, 2}))

    def test_empty_repository_completes_initial_backfill(self) -> None:
        state = {"prs": {}}

        self.assertTrue(complete_initial_backfill_if_ready(state, set()))
        self.assertTrue(state["initial_backfill_complete"])

    def test_writes_initial_backfill_status_to_github_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            for name, state, expected in (
                ("incomplete", None, "false"),
                ("complete", {"initial_backfill_complete": True, "prs": {}}, "true"),
            ):
                with self.subTest(name=name):
                    output_path = Path(temp_dir) / name
                    with patch("dashboard.load_dashboard_state_cache", return_value=state):
                        write_initial_backfill_output(output_path)

                    self.assertEqual(
                        f"initial_backfill_complete={expected}\n",
                        output_path.read_text(encoding="utf-8"),
                    )

class StatusCommentQueueTest(unittest.TestCase):
    @patch("dashboard.record_copilot_review_observation")
    @patch("dashboard.record_author_nudge_observation")
    @patch("dashboard.save_dashboard_update_state", return_value=0)
    @patch("dashboard.enqueue_status_comment_update")
    @patch(
        "dashboard.load_dashboard_state_cache",
        return_value={"prs": {"12": {}, "34": {}, "56": {}}},
    )
    def test_removed_dashboard_results_enqueue_status_comments(
        self,
        _load_state: Mock,
        enqueue_update: Mock,
        save_state: Mock,
        record_nudge: Mock,
        _record_copilot: Mock,
    ) -> None:
        args = Namespace(pr_number=None)

        status = remove_cached_dashboard_prs(args, {12, 34})

        self.assertEqual(0, status)
        self.assertEqual(
            [call(12), call(34)],
            sorted(enqueue_update.call_args_list, key=lambda call: call.args[0]),
        )
        saved_state = save_state.call_args.args[1]
        self.assertEqual({"56": {}}, saved_state["prs"])
        self.assertEqual(
            [call(12, None, ANY), call(34, None, ANY)],
            sorted(record_nudge.call_args_list, key=lambda value: value.args[0]),
        )

    @patch("dashboard.record_copilot_review_observation")
    @patch("dashboard.record_author_nudge_observation")
    @patch("dashboard.clear_backfill_pr_failure")
    @patch("dashboard.save_dashboard_update_state", return_value=0)
    @patch("dashboard.enqueue_status_comment_update")
    @patch("dashboard.merge_dashboard_update_with_latest_state")
    def test_targeted_state_change_enqueues_status_comment(
        self,
        merge_update: Mock,
        enqueue_update: Mock,
        _save_state: Mock,
        _clear_backfill_failure: Mock,
        record_nudge: Mock,
        _record_copilot: Mock,
    ) -> None:
        accepted_result = {"route": "author"}
        calculation = DashboardUpdate(
            results={},
            dashboard_state={"prs": {"12": accepted_result}},
            trigger_pr_result={"route": "approver"},
        )
        merge_update.return_value = (calculation, False)

        status = apply_targeted_dashboard_update(
            Namespace(pr_number=12, prepare_author_nudges=True),
            calculation,
        )

        self.assertEqual(0, status)
        enqueue_update.assert_called_once_with(12)
        record_nudge.assert_called_once_with(
            12,
            accepted_result,
            ANY,
            prepare_due=True,
        )

    @patch("dashboard.record_copilot_review_observation")
    @patch("dashboard.record_author_nudge_observation")
    @patch("dashboard.clear_backfill_pr_failure")
    @patch("dashboard.save_dashboard_update_state", return_value=0)
    @patch("dashboard.enqueue_status_comment_update")
    @patch("dashboard.merge_dashboard_update_with_latest_state")
    def test_unchanged_targeted_state_does_not_enqueue_status_comment(
        self,
        merge_update: Mock,
        enqueue_update: Mock,
        _save_state: Mock,
        _clear_backfill_failure: Mock,
        record_nudge: Mock,
        _record_copilot: Mock,
    ) -> None:
        accepted_result = {"route": "approver"}
        calculation = DashboardUpdate(
            results={},
            dashboard_state={"prs": {"12": accepted_result}},
            trigger_pr_result={"route": "author"},
        )
        merge_update.return_value = (calculation, True)

        status = apply_targeted_dashboard_update(Namespace(pr_number=12), calculation)

        self.assertEqual(0, status)
        enqueue_update.assert_not_called()
        record_nudge.assert_called_once_with(
            12,
            accepted_result,
            ANY,
            prepare_due=False,
        )


class RequiredCiRoutingTest(unittest.TestCase):
    def test_non_blocking_check_failures_use_deterministic_casefold_tiebreaker(self) -> None:
        facts = compute_facts(
            {
                "pr": {
                    "updatedAt": "2026-07-14T03:00:00Z",
                    "createdAt": "2026-07-14T01:00:00Z",
                    "author": {"login": "author"},
                    "assignees": [],
                    "mergeStateStatus": "CLEAN",
                    "mergeable": "MERGEABLE",
                },
                "checks": [],
                "non_blocking_check_failures": [
                    {"name": "codeql", "bucket": "fail"},
                    {"name": "CodeQL", "bucket": "fail"},
                ],
            },
            "author",
            [],
        )

        self.assertEqual(
            ["CodeQL", "codeql"],
            facts["non_blocking_check_failures"],
        )

    def test_required_check_buckets_control_ci_facts_and_author_routing(self) -> None:
        cases = (
            ("TIMED_OUT", "fail", 1, 0, "author"),
            ("ACTION_REQUIRED", "fail", 1, 0, "author"),
            ("STARTUP_FAILURE", "fail", 1, 0, "author"),
            ("CANCELLED", "cancel", 1, 0, "author"),
            ("IN_PROGRESS", "pending", 0, 1, "approver"),
            ("SKIPPED", "skipping", 0, 0, "approver"),
            ("SUCCESS", "pass", 0, 0, "approver"),
        )
        for state, bucket, failing, pending, route in cases:
            with self.subTest(state=state, bucket=bucket):
                facts = compute_facts(
                    {
                        "pr": {
                            "updatedAt": "2026-07-14T03:00:00Z",
                            "createdAt": "2026-07-14T01:00:00Z",
                            "author": {"login": "author"},
                            "assignees": [],
                            "mergeStateStatus": "CLEAN",
                            "mergeable": "MERGEABLE",
                        },
                        "checks": [{"state": state, "bucket": bucket}],
                        "non_blocking_check_failures": [
                            {"name": "workflow-notification", "bucket": "fail"},
                        ],
                    },
                    "author",
                    [],
                )

                self.assertEqual(failing, facts["ci_failing_count"])
                self.assertEqual(pending, facts["ci_pending_count"])
                self.assertEqual(
                    ["workflow-notification"],
                    facts["non_blocking_check_failures"],
                )
                self.assertEqual(route, route_pr(facts, {}, 1))

    def test_required_ci_failure_routes_to_author_before_approval_state(self) -> None:
        facts = {
            "approval_count": 1,
            "ci_failing_count": 1,
            "is_maintenance_bot": False,
        }

        self.assertEqual("author", route_pr(facts, {}, 1))

    def test_required_ci_failure_preserves_maintenance_bot_routing(self) -> None:
        for approval_count, expected_route in ((0, "approver"), (1, "maintainer")):
            with self.subTest(approval_count=approval_count):
                facts = {
                    "approval_count": approval_count,
                    "ci_failing_count": 1,
                    "is_maintenance_bot": True,
                }

                self.assertEqual(expected_route, route_pr(facts, {}, 2))

    def test_required_ci_failure_waits_since_first_current_failure(self) -> None:
        facts = compute_facts(
            {
                "pr": {
                    "updatedAt": "2026-07-17T03:00:00Z",
                    "createdAt": "2026-07-14T01:00:00Z",
                    "author": {"login": "author"},
                    "assignees": [],
                    "mergeStateStatus": "CLEAN",
                    "mergeable": "MERGEABLE",
                },
                "checks": [
                    {
                        "bucket": "fail",
                        "completed_at": "2026-07-17T02:00:00Z",
                    },
                    {
                        "bucket": "cancel",
                        "completed_at": "2026-07-17T01:00:00Z",
                    },
                ],
            },
            "author",
            [
                {
                    "actor_role": "author",
                    "kind": "issue-comment",
                    "body": "old activity",
                    "timestamp": "2026-07-14T02:00:00Z",
                }
            ],
        )

        cases = (
            ("2026-07-17T03:00:00Z", "2026-07-17T01:00:00+00:00", "ci_failure"),
            ("2026-07-16T23:00:00Z", "2026-07-16T23:00:00+00:00", "oldest_pending_thread"),
        )
        for discussion_since, waiting_since, basis in cases:
            with self.subTest(discussion_since=discussion_since):
                current_facts = dict(facts)
                add_wait_age_facts(
                    current_facts,
                    "author",
                    {"thread": {"action": "author", "since": discussion_since}},
                )

                self.assertEqual(waiting_since, current_facts["waiting_since"])
                self.assertEqual(basis, current_facts["waiting_age_basis"])


class BackfillFailureIsolationTest(unittest.TestCase):
    def test_failed_pr_does_not_block_later_backfill_progress(self) -> None:
        args = Namespace(
            repo="repo",
            approver_team=["approvers"],
            state_branch="state",
            model="model",
            required_approvals=1,
            non_blocking_check_pattern=[],
        )
        dashboard_state = {
            "initial_backfill_complete": False,
            "prs": {},
        }
        backfill_state = {"cursor": {}}
        refreshed_pr_numbers: list[int] = []

        def load_dashboard_state() -> dict:
            return deepcopy(dashboard_state)

        def load_backfill_state() -> dict:
            return deepcopy(backfill_state)

        def save_backfill_state(state: dict) -> None:
            backfill_state.clear()
            backfill_state.update(deepcopy(state))

        def build_update(*call_args) -> DashboardUpdate:
            pr_number = call_args[5]
            starting_state = call_args[9]
            refreshed_pr_numbers.append(pr_number)
            result = {
                "pr_number": pr_number,
                "failed": pr_number == 1,
                "route": "unknown" if pr_number == 1 else "reviewer",
            }
            updated_state = deepcopy(starting_state)
            updated_state["prs"][str(pr_number)] = result
            return DashboardUpdate(
                results={pr_number: result},
                dashboard_state=updated_state,
                trigger_pr_result=result,
            )

        def save_dashboard_state(_args, state: dict, unchanged: bool) -> int:
            if not unchanged:
                dashboard_state.clear()
                dashboard_state.update(deepcopy(state))
            return 0

        def push_state_changes(_state_dir, _message, update_state, **_kwargs) -> int:
            return update_state()

        with (
            patch("dashboard.list_open_prs", return_value=[{"number": 1}, {"number": 2}]),
            patch("dashboard.prune_classification_cache"),
            patch("dashboard.load_reviewer_set", return_value={"reviewer"}),
            patch("dashboard.load_dashboard_state_cache", side_effect=load_dashboard_state),
            patch("dashboard.load_backfill_state", side_effect=load_backfill_state),
            patch("dashboard.save_backfill_state", side_effect=save_backfill_state),
            patch("dashboard.build_dashboard_update_for_pr", side_effect=build_update),
            patch(
                "dashboard.merge_dashboard_update_with_latest_state",
                side_effect=lambda calculation, *_args: (calculation, False),
            ),
            patch(
                "dashboard.reject_failed_dashboard_result",
                side_effect=lambda result: result["failed"],
            ),
            patch("dashboard.save_dashboard_update_state", side_effect=save_dashboard_state),
            patch("dashboard.record_author_nudge_observation") as record_nudge,
            patch("dashboard.state_branch.configure_git"),
            patch("dashboard.state_branch.checkout_state"),
            patch("dashboard.state_branch.remove_existing_state_dir"),
            patch("dashboard.state_branch.push_state_changes", side_effect=push_state_changes),
        ):
            status = update_dashboard_for_backfill(args, Path("state"))

        self.assertEqual(refreshed_pr_numbers, [1, 2])
        record_nudge.assert_called_once_with(2, ANY, ANY, prepare_due=False)
        self.assertEqual(status, BACKFILL_RECORDED_FAILURE_STATUS)
        self.assertEqual(dashboard_state["prs"], {"2": {"pr_number": 2, "failed": False, "route": "reviewer"}})
        self.assertTrue(dashboard_state["initial_backfill_complete"])
        self.assertEqual(backfill_state["cursor"], {"last_pr_number": 2})
        self.assertEqual(backfill_failed_pr_numbers(backfill_state), {1})

    def test_successful_retry_clears_recorded_failure(self) -> None:
        state = {"failed_pr_numbers": [1, 2]}

        self.assertEqual(set_backfill_pr_failed(state, 1, False), {2})
        self.assertEqual(state["failed_pr_numbers"], [2])

    def test_successful_targeted_update_clears_recorded_failure(self) -> None:
        args = Namespace(pr_number=1)
        calculation = DashboardUpdate(
            results={},
            dashboard_state={"prs": {"1": {"pr_number": 1}}},
            trigger_pr_result={"pr_number": 1, "failed": False},
        )
        backfill_state = {
            "cursor": {"last_pr_number": 7},
            "failed_pr_numbers": [1, 2],
        }
        saved_backfill_state: dict = {}

        with (
            patch(
                "dashboard.merge_dashboard_update_with_latest_state",
                return_value=(calculation, False),
            ),
            patch("dashboard.load_backfill_state", return_value=deepcopy(backfill_state)),
            patch(
                "dashboard.save_backfill_state",
                side_effect=lambda state: saved_backfill_state.update(deepcopy(state)),
            ),
            patch("dashboard.save_dashboard_update_state", return_value=0) as save_dashboard,
        ):
            status = apply_targeted_dashboard_update(args, calculation)

        self.assertEqual(status, 0)
        self.assertEqual(saved_backfill_state["cursor"], {"last_pr_number": 7})
        self.assertEqual(saved_backfill_state["failed_pr_numbers"], [2])
        save_dashboard.assert_called_once_with(args, calculation.dashboard_state, False)

    def test_emits_initial_backfill_status_only_for_accepted_state_outcomes(self) -> None:
        for status, should_emit in (
            (0, True),
            (BACKFILL_RECORDED_FAILURE_STATUS, True),
            (1, False),
        ):
            with (
                self.subTest(status=status),
                tempfile.TemporaryDirectory() as temp_dir,
                patch(
                    "sys.argv",
                    [
                        "dashboard.py",
                        "--state-branch",
                        "state",
                        "--repo",
                        "repo",
                        "--approver-team",
                        "approvers",
                        "--github-output",
                        str(Path(temp_dir) / "output"),
                    ],
                ),
                patch("dashboard.state_branch.temporary_state_dir") as temporary_state_dir,
                patch("dashboard.update_dashboard_via_state_branch", return_value=status),
                patch("dashboard.write_initial_backfill_output") as write_output,
            ):
                temporary_state_dir.return_value.__enter__.return_value = Path(temp_dir)

                self.assertEqual(main(), status)

            if should_emit:
                write_output.assert_called_once_with(Path(temp_dir) / "output")
            else:
                write_output.assert_not_called()


if __name__ == "__main__":
    unittest.main()