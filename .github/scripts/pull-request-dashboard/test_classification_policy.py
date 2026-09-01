from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import unittest
from unittest.mock import patch

from classification_policy import (
    MAX_PROMPT_CHARS,
    ActionDecision,
    AuthorCommentDecision,
    AuthorCommentDiscussionPlan,
    AuthorCommentModelRequest,
    CandidateFeedback,
    ClassificationDeferred,
    ClassificationDiagnostics,
    ClassificationDiscussion,
    ClassificationFailure,
    ClassificationSuccess,
    DiscussionAction,
    DiscussionComment,
    DiscussionIdentity,
    DiscussionKind,
    FeedbackOutcome,
    RawModelResponse,
    Verdict,
    VerdictContract,
    VerdictDecision,
    VerdictModelRequest,
    cached_classification_record,
    classification_result_from_cache_record,
    classification_result_to_record,
    combine_author_comment_results,
    discussion_cache_key,
    make_author_comment_request,
    map_verdict_result,
    parse_author_comment_decision,
    prepare_author_comment_discussion,
    prepare_author_comment_requests,
    prepare_praise_candidates,
    render_prompt_inputs,
    render_verdict_prompt,
    resolve_author_comment_response,
    resolve_review_thread_policy,
    resolve_verdict_response,
    select_author_comment_requests,
)


def discussion(
    discussion_id: str,
    kind: DiscussionKind,
    body: str,
    *,
    actor_role: str = "approver",
    requester: str = "",
    pr_author: str = "",
    candidate_feedback: tuple[tuple[str, str], ...] = (),
) -> ClassificationDiscussion:
    return ClassificationDiscussion(
        DiscussionIdentity(discussion_id, kind),
        (
            DiscussionComment(
                "2026-01-02T03:04:05Z",
                actor_role,
                body,
            ),
        ),
        requester=requester,
        pr_author=pr_author,
        candidate_feedback=tuple(
            CandidateFeedback(feedback_id, feedback_body)
            for feedback_id, feedback_body in candidate_feedback
        ),
    )


class PromptCompatibilityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.review = discussion(
            "feedback-1",
            DiscussionKind.TOP_LEVEL_FEEDBACK,
            "@Maintainer, please check this",
            requester="alice",
            pr_author="bob",
        )
        self.reply = discussion(
            "reply-1",
            DiscussionKind.TOP_LEVEL_AUTHOR_REPLY,
            "Fixed the first item",
            candidate_feedback=(
                ("feedback-1", "Please fix one"),
                ("feedback-2", "Please fix two"),
            ),
        )
        self.thread = discussion(
            "thread-1",
            DiscussionKind.REVIEW_THREAD,
            "LGTM",
        )

    def test_prompt_bytes_match_the_pre_extraction_prompts(self) -> None:
        prompts = {
            "review": render_verdict_prompt(
                [self.review],
                VerdictContract.REVIEWER_FEEDBACK,
            ),
            "author": make_author_comment_request([self.reply]).prompt,
            "praise": render_verdict_prompt(
                [self.thread],
                VerdictContract.PRAISE,
            ),
        }

        self.assertEqual(
            {
                name: (
                    len(prompt),
                    hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                )
                for name, prompt in prompts.items()
            },
            {
                "review": (
                    5359,
                    "ea4f5a86153173cf98f1d3d21a097e18dd513ce52c8c119f563b016559376653",
                ),
                "author": (
                    3372,
                    "96720c58f7509a58e65b13f4aecfd1f82c7670088dcf438df2e8aed1c26dae5d",
                ),
                "praise": (
                    2116,
                    "35032fc11bd7a88a3471d3dd740712920ec61fae66346fab727402449b6fb127",
                ),
            },
        )

    def test_cache_keys_match_the_pre_extraction_keys(self) -> None:
        self.assertEqual(
            discussion_cache_key(
                self.review,
                "gpt-test",
                verdict_contract=VerdictContract.REVIEWER_FEEDBACK,
            ),
            "c78a48290b695bf839eed8ba25a1481a95a7565309c86fd57bd15ce8395ec58b",
        )
        self.assertEqual(
            discussion_cache_key(
                self.reply,
                "gpt-test",
                author_comment=True,
            ),
            "e1ec5c0d70adb0981a7bfecdc7f8f5bd7b1a6a6d7ea597ea1cd512fe3de3e864",
        )
        self.assertEqual(
            discussion_cache_key(
                self.thread,
                "gpt-test",
                verdict_contract=VerdictContract.PRAISE,
            ),
            "76e534a013fc212856acbebd3c1897aa2c27daa6a69c6c8bee02e2d11b7bb2fd",
        )

    def test_author_comment_prompt_supports_empty_input(self) -> None:
        prompt = make_author_comment_request(
            [],
            max_prompt_chars=MAX_PROMPT_CHARS,
        ).prompt

        self.assertIn(
            "---BEGIN AUTHOR FOLLOW-UPS---\n[]\n"
            "---END AUTHOR FOLLOW-UPS---",
            prompt,
        )

    def test_author_comment_prompt_does_not_apply_batching(self) -> None:
        discussions = [
            replace(
                self.reply,
                identity=DiscussionIdentity(
                    f"author-reply-{index}",
                    DiscussionKind.TOP_LEVEL_AUTHOR_REPLY,
                ),
            )
            for index in range(11)
        ]

        prompt = make_author_comment_request(
            discussions,
            max_prompt_chars=MAX_PROMPT_CHARS,
        ).prompt

        self.assertIn('"discussion_id": "author-reply-10"', prompt)

    def test_unknown_discussion_kind_names_the_record(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "discussion 'feedback-1' has unknown "
            "discussion_kind 'unknown-kind'",
        ):
            ClassificationDiscussion.from_record({
                "discussion_id": "feedback-1",
                "discussion_kind": "unknown-kind",
            })

    @patch(
        "classification_policy.make_author_comment_request",
        wraps=make_author_comment_request,
    )
    def test_author_comment_chunking_uses_few_prompt_probes(
        self,
        make_request,
    ) -> None:
        reply = discussion(
            "reply-1",
            DiscussionKind.TOP_LEVEL_AUTHOR_REPLY,
            "Fixed the requested items.",
            candidate_feedback=tuple(
                (f"feedback-{index}", f"Request {index}.")
                for index in range(64)
            ),
        )

        requests = prepare_author_comment_requests(
            [reply],
            max_prompt_chars=100_000,
        )

        self.assertEqual(len(requests), 1)
        self.assertLess(make_request.call_count, 20)

    def test_author_comment_prompt_errors_name_the_actual_limit(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "author-comment prompt exceeds max_prompt_chars=1",
        ):
            prepare_author_comment_requests(
                [replace(self.reply, candidate_feedback=())],
                max_prompt_chars=1,
            )

        with self.assertRaisesRegex(
            ValueError,
            "max_prompt_chars=1 is too small "
            "for one author-comment candidate",
        ):
            prepare_author_comment_requests(
                [self.reply],
                max_prompt_chars=1,
            )

    def test_rendered_prompt_must_fit_after_truncation(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "rendered prompt exceeds max_prompt_chars=10 after truncation",
        ):
            render_prompt_inputs(
                [],
                "xxxxxxxxxxxxxxxxxxxx{discussions}",
                max_prompt_chars=10,
            )

    def test_author_comment_request_planning_reuses_rendered_requests(
        self,
    ) -> None:
        discussions = [
            replace(
                self.reply,
                identity=DiscussionIdentity(
                    f"reply-{index}",
                    DiscussionKind.TOP_LEVEL_AUTHOR_REPLY,
                ),
                candidate_feedback=(),
            )
            for index in range(3)
        ]

        with patch(
            "classification_policy.make_author_comment_request",
            wraps=make_author_comment_request,
        ) as make_request:
            requests = prepare_author_comment_requests(
                discussions,
                batch_size=2,
                max_prompt_chars=100_000,
            )

        self.assertEqual(len(requests), 2)
        self.assertEqual(make_request.call_count, 4)


class AuthorCommentBudgetSelectionTest(unittest.TestCase):
    @staticmethod
    def plan(
        discussion_id: str,
        request_count: int,
    ) -> AuthorCommentDiscussionPlan:
        item = discussion(
            discussion_id,
            DiscussionKind.TOP_LEVEL_AUTHOR_REPLY,
            f"Reply {discussion_id}",
        )
        return AuthorCommentDiscussionPlan(
            item,
            tuple(
                AuthorCommentModelRequest(
                    (item,),
                    f"prompt-{discussion_id}-{index}",
                    (),
                )
                for index in range(request_count)
            ),
        )

    def test_skips_overflow_and_spends_remaining_budget_on_later_items(
        self,
    ) -> None:
        plans = (
            self.plan("expensive-1", 10),
            self.plan("expensive-2", 10),
            self.plan("overflow-1", 3),
            self.plan("cheap", 2),
            self.plan("overflow-2", 2),
        )

        selection = select_author_comment_requests(
            plans,
            max_model_calls=20,
        )

        self.assertEqual(
            [
                item.identity.discussion_id
                for item in selection.admitted
            ],
            ["expensive-1", "expensive-2", "cheap"],
        )
        self.assertEqual(
            [
                item.identity.discussion_id
                for item in selection.deferred
            ],
            ["overflow-1", "overflow-2"],
        )
        self.assertEqual(selection.request_count, 20)
        self.assertEqual(
            [
                [
                    item.identity.discussion_id
                    for item in request.discussions
                ]
                for batch in selection.batches
                for request in batch.requests
            ],
            (
                [["expensive-1"]] * 9
                + [["expensive-1", "expensive-2"]]
                + [["expensive-2"]] * 8
                + [["expensive-2", "cheap"], ["cheap"]]
            ),
        )

    def test_oversized_first_item_does_not_block_later_items(
        self,
    ) -> None:
        selection = select_author_comment_requests(
            (
                self.plan("oversized", 21),
                self.plan("cheap-1", 1),
                self.plan("cheap-2", 1),
            ),
            max_model_calls=20,
        )

        self.assertEqual(
            [
                item.identity.discussion_id
                for item in selection.admitted
            ],
            ["cheap-1", "cheap-2"],
        )
        self.assertEqual(
            [
                item.identity.discussion_id
                for item in selection.deferred
            ],
            ["oversized"],
        )
        self.assertEqual(selection.request_count, 1)
        self.assertEqual(
            [
                item.identity.discussion_id
                for item in selection.batches[0].requests[0].discussions
            ],
            ["cheap-1", "cheap-2"],
        )

    def test_preparation_retains_the_requests_used_for_execution(self) -> None:
        item = discussion(
            "reply",
            DiscussionKind.TOP_LEVEL_AUTHOR_REPLY,
            "Done",
        )

        plan = prepare_author_comment_discussion(item)
        selection = select_author_comment_requests(
            (plan,),
            max_model_calls=20,
        )

        self.assertIs(selection.batches[0].requests[0], plan.requests[0])


class ResultProjectionCompatibilityTest(unittest.TestCase):
    identity = DiscussionIdentity(
        "feedback-1",
        DiscussionKind.TOP_LEVEL_FEEDBACK,
    )

    def test_success_projection_matches_the_dashboard_record(self) -> None:
        result = ClassificationSuccess(
            self.identity,
            ActionDecision(DiscussionAction.AUTHOR, "Needs a reply."),
            cli_call=True,
            since="2026-01-02T03:04:05Z",
            ignored_last_comment=True,
        )

        self.assertEqual(
            classification_result_to_record(result),
            {
                "discussion_id": "feedback-1",
                "discussion_kind": "top-level-feedback",
                "failed": False,
                "decision": {
                    "discussion_action": "author",
                    "reason": "Needs a reply.",
                },
                "_copilot_cli_call": True,
                "since": "2026-01-02T03:04:05Z",
                "ignored_last_comment": True,
            },
        )

    def test_failure_and_cache_projections_preserve_the_old_shape(self) -> None:
        result = ClassificationFailure(
            self.identity,
            VerdictDecision(Verdict.AUTHOR_ACTION, "Unreadable."),
            ClassificationDiagnostics(
                error="model failed",
                response_text="bad output",
                stderr="trace",
            ),
            cli_call=True,
        )

        self.assertEqual(
            classification_result_to_record(result),
            {
                "discussion_id": "feedback-1",
                "discussion_kind": "top-level-feedback",
                "failed": True,
                "decision": {
                    "verdict": "author_action",
                    "reason": "Unreadable.",
                },
                "_copilot_cli_call": True,
                "error": "model failed",
                "response_text": "bad output",
                "stderr": "trace",
            },
        )
        self.assertEqual(
            cached_classification_record(result),
            {
                "discussion_id": "feedback-1",
                "discussion_kind": "top-level-feedback",
                "failed": True,
                "decision": {
                    "verdict": "author_action",
                    "reason": "Unreadable.",
                },
            },
        )

    def test_deferred_author_comment_keeps_its_reason(self) -> None:
        result = ClassificationDeferred(
            DiscussionIdentity(
                "reply-1",
                DiscussionKind.TOP_LEVEL_AUTHOR_REPLY,
            ),
            AuthorCommentDecision(
                reason="Deferred by per-PR classification limit"
            ),
        )

        self.assertEqual(
            classification_result_to_record(result)["decision"],
            {
                "feedback_outcomes": [],
                "reason": "Deferred by per-PR classification limit",
            },
        )

    def test_cache_round_trip_preserves_result_state(self) -> None:
        discussion = self._discussion()
        deferred = ClassificationDeferred(
            discussion.identity,
            AuthorCommentDecision(
                reason="Deferred by per-PR classification limit"
            ),
            since="2026-01-02T03:04:05Z",
        )
        failure = ClassificationFailure(
            discussion.identity,
            VerdictDecision(Verdict.AUTHOR_ACTION, "Unreadable."),
            ClassificationDiagnostics(error="model failed"),
        )

        restored_deferred = classification_result_from_cache_record(
            cached_classification_record(deferred),
            discussion,
            author_comment=True,
        )
        restored_failure = classification_result_from_cache_record(
            cached_classification_record(failure),
            discussion,
            verdict_contract=VerdictContract.REVIEWER_FEEDBACK,
        )

        self.assertEqual(restored_deferred, deferred)
        self.assertIsInstance(restored_failure, ClassificationFailure)

    def _discussion(self) -> ClassificationDiscussion:
        return discussion(
            "feedback-1",
            DiscussionKind.TOP_LEVEL_FEEDBACK,
            "Please update this.",
        )


class PreparationAndResolutionTest(unittest.TestCase):
    def test_missing_author_comment_partials_fail(self) -> None:
        reply = discussion(
            "reply-1",
            DiscussionKind.TOP_LEVEL_AUTHOR_REPLY,
            "Fixed it.",
        )

        result = combine_author_comment_results([reply], {})[0]

        self.assertIsInstance(result, ClassificationFailure)
        assert isinstance(result, ClassificationFailure)
        self.assertEqual(
            result.diagnostics.error,
            "missing partial results for discussion_id 'reply-1'",
        )

    def test_invalid_feedback_keys_report_the_contract_field(self) -> None:
        _decision, errors = parse_author_comment_decision(
            json.dumps({
                "feedback_outcomes": [
                    {
                        "feedback_id": "legacy-id",
                        "discussion_action": "none",
                    },
                    {
                        "feedback_key": 42,
                        "discussion_action": "none",
                    },
                ]
            }),
            {"f0001": "feedback-1"},
        )

        self.assertIn("missing feedback_key", errors[0])
        self.assertIn(
            "unexpected feedback_id field 'legacy-id'",
            errors[0],
        )
        self.assertIn("feedback_key is not a string: 42", errors[1])

    def test_invalid_feedback_key_diagnostics_are_sorted(self) -> None:
        _decision, errors = parse_author_comment_decision(
            json.dumps({
                "feedback_outcomes": [
                    {
                        "feedback_key": "unknown",
                        "discussion_action": "none",
                    }
                ]
            }),
            {
                "f0002": "feedback-2",
                "f0001": "feedback-1",
            },
        )

        self.assertIn("expected keys ['f0001', 'f0002']", errors[0])
        self.assertIn(
            "canonical candidate IDs ['feedback-1', 'feedback-2']",
            errors[0],
        )

    def test_unknown_feedback_key_diagnostics_are_truncated(self) -> None:
        _decision, errors = parse_author_comment_decision(
            json.dumps({
                "feedback_outcomes": [
                    {
                        "feedback_key": "unknown",
                        "discussion_action": "none",
                    }
                ]
            }),
            {
                f"f{index + 1:04d}": f"feedback-{index}"
                for index in range(12)
            },
        )

        self.assertEqual(errors[0].count("(showing 10 of 12)"), 2)
        self.assertIn("expected keys ['f0001', 'f0002',", errors[0])
        self.assertIn("'f0010'] (showing 10 of 12)", errors[0])
        self.assertNotIn("'f0011'", errors[0])
        self.assertIn(
            "canonical candidate IDs ['feedback-0', 'feedback-1',",
            errors[0],
        )
        self.assertIn("'feedback-9'] (showing 10 of 12)", errors[0])
        self.assertNotIn("'feedback-10'", errors[0])

    def test_long_reviewer_comment_needs_no_model_request(self) -> None:
        thread = discussion(
            "thread-1",
            DiscussionKind.REVIEW_THREAD,
            "Could this be deterministic without relying on sleep? "
            "The current approach is flaky on slower machines.",
        )

        self.assertEqual(prepare_praise_candidates([thread]), ())
        plan = resolve_review_thread_policy([thread], {})

        self.assertEqual(plan.author_replies, ())
        result = plan.resolved[0]
        self.assertIsInstance(result, ClassificationSuccess)
        assert isinstance(result.decision, ActionDecision)
        self.assertEqual(result.decision.action, DiscussionAction.AUTHOR)

    def test_praise_and_author_reply_shortcuts_preserve_handoffs(self) -> None:
        thread = discussion(
            "thread-1",
            DiscussionKind.REVIEW_THREAD,
            "LGTM",
        )
        praise = map_verdict_result(
            ClassificationSuccess(
                thread.identity,
                VerdictDecision(Verdict.PRAISE, "Only praise."),
            ),
            VerdictContract.PRAISE,
        )

        plan = resolve_review_thread_policy(
            [thread],
            {"thread-1": praise},
        )

        result = plan.resolved[0]
        assert isinstance(result.decision, ActionDecision)
        self.assertEqual(result.decision.action, DiscussionAction.NONE)
        self.assertTrue(result.ignored_last_comment)

    def test_author_comment_request_resolves_canonical_feedback_ids(self) -> None:
        reply = discussion(
            "reply-1",
            DiscussionKind.TOP_LEVEL_AUTHOR_REPLY,
            "Fixed it.",
            actor_role="author",
            candidate_feedback=(("feedback-1", "Please fix it."),),
        )
        request = make_author_comment_request([reply])
        response = RawModelResponse(
            0,
            json.dumps({
                "items": [
                    {
                        "discussion_id": "reply-1",
                        "feedback_outcomes": [
                            {
                                "feedback_key": "f0001",
                                "discussion_action": "none",
                                "reason": "Completed.",
                            }
                        ],
                    }
                ]
            }),
        )

        self.assertIs(
            request.feedback_ids_for("reply-1"),
            request.feedback_ids_for("reply-1"),
        )
        self.assertEqual(
            request.feedback_ids_for("reply-1"),
            {"f0001": "feedback-1"},
        )
        result = resolve_author_comment_response(request, response)[0]

        self.assertIsInstance(result, ClassificationSuccess)
        assert isinstance(result.decision, AuthorCommentDecision)
        self.assertEqual(
            result.decision.feedback_outcomes,
            (
                FeedbackOutcome(
                    "feedback-1",
                    DiscussionAction.NONE,
                    "Completed.",
                ),
            ),
        )

    def test_author_comment_requests_are_hard_bounded_and_keep_all_feedback(
        self,
    ) -> None:
        reply = discussion(
            "reply-1",
            DiscussionKind.TOP_LEVEL_AUTHOR_REPLY,
            "Fixed the requested items.",
            actor_role="author",
            candidate_feedback=tuple(
                (
                    f"feedback-{index}",
                    f"Request {index}: " + "x" * 1000,
                )
                for index in range(30)
            ),
        )

        requests = prepare_author_comment_requests(
            [reply],
            max_prompt_chars=5000,
        )

        self.assertGreater(len(requests), 1)
        self.assertTrue(
            all(len(request.prompt) <= 5000 for request in requests)
        )
        self.assertEqual(
            [
                feedback_id
                for request in requests
                for _discussion_id, feedback in request.feedback_ids
                for _feedback_key, feedback_id in feedback
            ],
            [f"feedback-{index}" for index in range(30)],
        )


class AuthorCommentMalformedResponseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.first = discussion(
            "reply-1",
            DiscussionKind.TOP_LEVEL_AUTHOR_REPLY,
            "Fixed the first item.",
            actor_role="author",
            candidate_feedback=(("feedback-1", "Please fix one."),),
        )
        self.second = discussion(
            "reply-2",
            DiscussionKind.TOP_LEVEL_AUTHOR_REPLY,
            "Fixed the second item.",
            actor_role="author",
            candidate_feedback=(("feedback-2", "Please fix two."),),
        )

    def test_duplicate_and_missing_discussion_ids_fail(self) -> None:
        request = make_author_comment_request([self.first, self.second])
        item = {
            "discussion_id": "reply-1",
            "feedback_outcomes": [
                {
                    "feedback_key": "f0001",
                    "discussion_action": "none",
                    "reason": "Completed.",
                }
            ],
        }

        results = resolve_author_comment_response(
            request,
            RawModelResponse(
                0,
                json.dumps({"items": [item, item]}),
            ),
        )

        self.assertIsInstance(results[0], ClassificationFailure)
        self.assertIn(
            "duplicate discussion_id",
            results[0].diagnostics.error,
        )
        self.assertIsInstance(results[1], ClassificationFailure)
        self.assertIn(
            "this discussion_id",
            results[1].diagnostics.error,
        )

    def test_feedback_keys_cannot_cross_discussions(self) -> None:
        request = make_author_comment_request([self.first, self.second])
        response = RawModelResponse(
            0,
            json.dumps({
                "items": [
                    {
                        "discussion_id": "reply-1",
                        "feedback_outcomes": [
                            {
                                "feedback_key": "f0002",
                                "discussion_action": "none",
                                "reason": "Wrong discussion.",
                            }
                        ],
                    },
                    {
                        "discussion_id": "reply-2",
                        "feedback_outcomes": [
                            {
                                "feedback_key": "f0002",
                                "discussion_action": "none",
                                "reason": "Completed.",
                            }
                        ],
                    },
                ]
            }),
        )

        first, second = resolve_author_comment_response(request, response)

        self.assertIsInstance(first, ClassificationFailure)
        self.assertIn("expected keys ['f0001']", first.diagnostics.error)
        self.assertIsInstance(second, ClassificationSuccess)
        assert isinstance(second.decision, AuthorCommentDecision)
        self.assertEqual(
            second.decision.feedback_outcomes[0].feedback_id,
            "feedback-2",
        )

    def test_duplicate_feedback_key_and_invalid_action_fail(self) -> None:
        request = make_author_comment_request([self.first])
        for name, outcomes, expected in (
            (
                "duplicate",
                [
                    {
                        "feedback_key": "f0001",
                        "discussion_action": "none",
                        "reason": "Completed.",
                    },
                    {
                        "feedback_key": "f0001",
                        "discussion_action": "author",
                        "reason": "Duplicate.",
                    },
                ],
                "duplicate feedback_key 'f0001'",
            ),
            (
                "invalid action",
                [
                    {
                        "feedback_key": "f0001",
                        "discussion_action": "reviewer",
                        "reason": "Invalid.",
                    }
                ],
                "invalid discussion_action 'reviewer'",
            ),
        ):
            with self.subTest(name=name):
                result = resolve_author_comment_response(
                    request,
                    RawModelResponse(
                        0,
                        json.dumps({
                            "items": [
                                {
                                    "discussion_id": "reply-1",
                                    "feedback_outcomes": outcomes,
                                }
                            ]
                        }),
                    ),
                )[0]

                self.assertIsInstance(result, ClassificationFailure)
                self.assertIn(expected, result.diagnostics.error)


class MalformedResponseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.discussion = discussion(
            "feedback-1",
            DiscussionKind.TOP_LEVEL_FEEDBACK,
            "Please fix this.",
            requester="reviewer",
            pr_author="author",
        )
        self.request = VerdictModelRequest(
            (self.discussion,),
            VerdictContract.REVIEWER_FEEDBACK,
            "prompt",
        )

    def test_malformed_response_uses_the_fail_safe_verdict(self) -> None:
        result = resolve_verdict_response(
            self.request,
            RawModelResponse(0, "not json", ""),
        )[0]

        self.assertIsInstance(result, ClassificationFailure)
        assert isinstance(result, ClassificationFailure)
        assert isinstance(result.decision, VerdictDecision)
        self.assertEqual(result.decision.verdict, Verdict.AUTHOR_ACTION)
        self.assertIn(
            "did not return a valid verdict",
            result.diagnostics.error,
        )

    def test_duplicate_id_fails_even_when_both_items_are_valid(self) -> None:
        item = {
            "discussion_id": "feedback-1",
            "verdict": "no_author_action",
            "reason": "Done.",
        }
        result = resolve_verdict_response(
            self.request,
            RawModelResponse(
                0,
                json.dumps({"items": [item, item]}),
                "",
            ),
        )[0]

        self.assertIsInstance(result, ClassificationFailure)
        assert isinstance(result, ClassificationFailure)
        self.assertIn("duplicate discussion_id", result.diagnostics.error)

    def test_missing_id_fails_only_the_requested_discussion(self) -> None:
        result = resolve_verdict_response(
            self.request,
            RawModelResponse(0, json.dumps({"items": []}), ""),
        )[0]

        self.assertIsInstance(result, ClassificationFailure)
        self.assertTrue(result.cli_call)

    def test_nonzero_exit_with_valid_verdict_reports_only_the_exit(self) -> None:
        result = resolve_verdict_response(
            self.request,
            RawModelResponse(
                1,
                json.dumps({
                    "items": [
                        {
                            "discussion_id": "feedback-1",
                            "verdict": "no_author_action",
                            "reason": "Done.",
                        }
                    ]
                }),
                "stderr",
            ),
        )[0]

        self.assertIsInstance(result, ClassificationFailure)
        self.assertIn("exited with status 1", result.diagnostics.error)
        self.assertNotIn(
            "did not return a valid verdict",
            result.diagnostics.error,
        )


if __name__ == "__main__":
    unittest.main()
