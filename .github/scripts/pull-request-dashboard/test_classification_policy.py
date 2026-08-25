from __future__ import annotations

import hashlib
import json
import unittest

from classification_policy import (
    ActionDecision,
    AuthorCommentDecision,
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
    classification_result_to_record,
    discussion_cache_key,
    make_author_comment_request,
    map_verdict_result,
    prepare_praise_candidates,
    render_verdict_prompt,
    resolve_author_comment_response,
    resolve_review_thread_policy,
    resolve_verdict_response,
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
                    4573,
                    "786c3605dce43723379e3a0a41502df30a5ab26ff523c7f4e6ff92229eb2cbb9",
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
            "b5facfaf15b876ac43eb58c1ec80d2eb89a6c6f9835e88fe659248cae8c917ba",
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


class PreparationAndResolutionTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
