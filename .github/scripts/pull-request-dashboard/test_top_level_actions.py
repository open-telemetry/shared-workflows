from __future__ import annotations

import json
import unittest
from subprocess import CompletedProcess
from unittest.mock import patch

from classification import (
    classify_discussion_domains,
    classify_review_threads,
    run_llm_for_verdict_batch,
    run_llm_for_top_level_author_comment_batch,
    top_level_reviewer_feedback_prompt_input,
)
from classification_policy import (
    PRAISE_VERDICTS,
    REVIEWER_FEEDBACK_VERDICTS,
    ActionDecision,
    AuthorCommentDecision,
    ClassificationDiagnostics,
    ClassificationFailure,
    ClassificationResult,
    ClassificationSuccess,
    DiscussionAction,
    DiscussionClassifications,
    DiscussionIdentity,
    DiscussionKind,
    FeedbackOutcome,
    Verdict,
    VerdictDecision,
    leading_mentions,
)
from discussion_lifecycle import (
    DiscussionInput,
    PreparedDiscussions,
    prepare_discussions,
    resolve_discussions,
)
from dashboard_contracts import DashboardRoute
from dashboard_test_support import dashboard_facts
from reviewer_state import (
    ReviewerDiscussionInput,
    ReviewerInput,
    prepare_reviewers,
    resolve_reviewers,
)
from routing_decision import RoutingInput, resolve_routing
from pull_request_activity import ActivityInput, build_activity_timeline
from pull_request_source import normalize_pull_request_source


ROOT_TIMESTAMP = "2026-07-14T01:00:00Z"


def top_level_item(
    discussion_id: str,
    requester: str = "reviewer",
    source_kind: str | None = None,
    source_id: int | None = None,
) -> dict:
    discussion = {
        "discussion_id": discussion_id,
        "discussion_kind": "top-level-feedback",
        "pr_author": "author",
        "requester": requester,
        "root_timestamp": ROOT_TIMESTAMP,
        "comments": [],
    }
    if source_kind is not None:
        discussion["source_kind"] = source_kind
    if source_id is not None:
        discussion["source_id"] = source_id
    return discussion


def review_thread_discussion(discussion_id: str) -> dict:
    return {
        "discussion_id": discussion_id,
        "discussion_kind": "review-comment-thread",
        "comments": [],
    }


def verdict_record(
    discussion_id: str,
    verdict: str = "author_action",
    kind: DiscussionKind = DiscussionKind.TOP_LEVEL_FEEDBACK,
) -> ClassificationSuccess:
    return ClassificationSuccess(
        DiscussionIdentity(discussion_id, kind),
        VerdictDecision(Verdict(verdict), "action requested"),
    )


def verdict_record_for(
    discussion: dict,
    verdict: str,
) -> ClassificationSuccess:
    return verdict_record(
        discussion["discussion_id"],
        verdict,
        DiscussionKind(discussion["discussion_kind"]),
    )


def failed_verdict_record_for(
    discussion: dict,
    verdict: str,
    error: str,
) -> ClassificationFailure:
    return ClassificationFailure(
        DiscussionIdentity(
            discussion["discussion_id"],
            DiscussionKind(discussion["discussion_kind"]),
        ),
        VerdictDecision(Verdict(verdict), "because"),
        ClassificationDiagnostics(error=error),
    )


def author_comment_decision(
    *feedback_actions: tuple[str, str],
) -> AuthorCommentDecision:
    return AuthorCommentDecision(tuple(
        FeedbackOutcome(
            feedback_id,
            DiscussionAction(action),
            "Test author-comment outcome.",
        )
        for feedback_id, action in feedback_actions
    ))


def author_comment_result(
    discussion: dict,
    *feedback_actions: tuple[str, str],
) -> ClassificationSuccess:
    return ClassificationSuccess(
        DiscussionIdentity(
            discussion["discussion_id"],
            DiscussionKind(discussion["discussion_kind"]),
        ),
        author_comment_decision(*feedback_actions),
    )


def copilot_batch_response(*items: dict) -> CompletedProcess[str]:
    return CompletedProcess(
        args=[],
        returncode=0,
        stdout=json.dumps({"items": items}),
        stderr="",
    )


def top_level_items_from_raw(
    raw: dict,
    conflicts: str = "no",
) -> list[dict]:
    activity = build_activity_timeline(
        ActivityInput(
            normalize_pull_request_source({
                "pr": {},
                "commits": [],
                "issue_comments": raw.get("issue_comments") or [],
                "review_comments": [],
                "reviews": raw.get("reviews") or [],
            }),
            "author",
            frozenset({"reviewer"}),
        )
    )
    return list(
        prepare_discussions(
            DiscussionInput(
                (),
                activity.events,
                "author",
                frozenset({"reviewer"}),
                conflicts,
            )
        ).top_level_items
    )


def review_thread_pending_actions(
    review_threads: list[dict],
    classifications: list[ClassificationResult],
) -> dict[str, dict]:
    return resolve_discussions(
        PreparedDiscussions(tuple(review_threads), (), ()),
        DiscussionClassifications(tuple(classifications), (), ()),
    ).pending_actions


def classify_feedback_domains(
    number: int,
    review_threads: list[dict],
    top_level_items: list[dict],
    model: str,
) -> tuple[
    tuple[ClassificationResult, ...],
    tuple[ClassificationResult, ...],
]:
    classifications = classify_discussion_domains(
        number,
        review_threads,
        top_level_items,
        [],
        model,
    )
    return (
        classifications.review_threads,
        classifications.top_level_items,
    )


class VerdictBatchErrorTest(unittest.TestCase):
    def run_batch(self, returncode: int, stdout: str) -> dict:
        proc = CompletedProcess(["copilot"], returncode, stdout, "")
        with patch("classification.run_copilot", return_value=proc):
            return run_llm_for_verdict_batch(
                [{"discussion_id": "d", "discussion_kind": "review-comment-thread"}],
                "model",
                "prompt",
                ("deferral", "complete"),
            )[0]

    def test_a_nonzero_exit_with_a_usable_verdict_is_not_called_unreadable(self) -> None:
        record = self.run_batch(1, '{"items": [{"discussion_id": "d", "verdict": "complete"}]}')

        self.assertTrue(record.failed)
        assert isinstance(record, ClassificationFailure)
        self.assertIn("exited with status 1", record.diagnostics.error)
        self.assertNotIn(
            "did not return a valid verdict",
            record.diagnostics.error,
        )

    def test_an_unreadable_answer_still_says_so(self) -> None:
        record = self.run_batch(0, "not json")

        self.assertTrue(record.failed)
        assert isinstance(record, ClassificationFailure)
        self.assertIn(
            "did not return a valid verdict",
            record.diagnostics.error,
        )


class IgnoredPraiseWaitAgeTest(unittest.TestCase):
    """Praise must not reset the age of the request it was posted after."""

    def thread(self, *comments: tuple[str, str, str]) -> dict:
        return {
            "discussion_id": "t",
            "discussion_kind": "review-comment-thread",
            "discussion_facts": {"latest_comment_role": comments[-1][0]},
            "comments": [
                {"actor_role": role, "body": body, "timestamp": stamp}
                for role, body, stamp in comments
            ],
        }

    def pending_actions(self, thread: dict, reply: str) -> dict[str, dict]:
        def batch(items, _model, _prompt, verdicts):
            answer = "praise" if verdicts == PRAISE_VERDICTS else reply
            return [
                verdict_record_for(item, answer)
                for item in items
            ]

        with patch("classification.run_llm_for_verdict_batch", side_effect=batch):
            records = classify_review_threads(1, [thread], "model", {}, {})
        return review_thread_pending_actions([thread], list(records.values()))

    def waiting_since(self, thread: dict, reply: str) -> str:
        return self.pending_actions(thread, reply)["t"]["since"]

    def open_thread_reviewers(self, thread: dict) -> list[str]:
        pending = self.pending_actions(thread, "complete")
        self.assertTrue(pending["t"]["ignored_last_comment"])
        return [
            reviewer.login
            for reviewer in resolve_reviewers(
                prepare_reviewers(ReviewerInput((), (), ())),
                ReviewerDiscussionInput((thread,), (), pending),
            )
            if reviewer.open_thread
        ]

    def test_praise_does_not_make_its_author_a_waiting_reviewer(self) -> None:
        thread = self.thread(
            ("approver", "please fix", "2026-03-12T00:00:00Z"),
            ("author", "fixed it", "2026-04-01T00:00:00Z"),
            ("approver", "LGTM", "2026-05-20T00:00:00Z"),
        )
        thread["comments"][0]["actor"] = "alice"
        thread["comments"][2]["actor"] = "bob"

        self.assertEqual(["alice"], self.open_thread_reviewers(thread))

    def test_an_edited_request_still_counts_its_reviewer(self) -> None:
        thread = self.thread(
            ("approver", "please fix", "2026-06-01T00:00:00Z"),
            ("author", "fixed it", "2026-04-01T00:00:00Z"),
            ("approver", "LGTM", "2026-05-20T00:00:00Z"),
        )
        thread["comments"][0]["actor"] = "alice"
        thread["comments"][2]["actor"] = "bob"

        self.assertEqual(["alice"], self.open_thread_reviewers(thread))

    def test_praise_after_a_reviewer_request_keeps_the_request_date(self) -> None:
        thread = self.thread(
            ("reviewer", "please fix", "2026-03-12T00:00:00Z"),
            ("reviewer", "LGTM", "2026-05-20T00:00:00Z"),
        )

        self.assertEqual("2026-03-12T00:00:00Z", self.waiting_since(thread, "complete"))

    def test_praise_after_an_author_reply_keeps_the_reply_date(self) -> None:
        thread = self.thread(
            ("author", "fixed it", "2026-03-12T00:00:00Z"),
            ("reviewer", "LGTM", "2026-05-20T00:00:00Z"),
        )

        self.assertEqual("2026-03-12T00:00:00Z", self.waiting_since(thread, "complete"))


class ReviewThreadPraiseTest(unittest.TestCase):
    def thread(self, *comments: tuple[str, str]) -> dict:
        return {
            "discussion_id": "t",
            "discussion_kind": "review-comment-thread",
            "discussion_facts": {"latest_comment_role": comments[-1][0]},
            "comments": [{"actor_role": role, "body": body} for role, body in comments],
        }

    def answering(self, praise: str, reply: str = "complete"):
        def batch(items, _model, _prompt, verdicts):
            answer = praise if verdicts == PRAISE_VERDICTS else reply
            return [
                verdict_record_for(item, answer)
                for item in items
            ]

        return batch

    @patch("classification.run_llm_for_verdict_batch")
    def test_a_thread_of_nothing_but_praise_needs_nobody(self, run_verdict) -> None:
        run_verdict.side_effect = self.answering("praise")

        records = classify_review_threads(1, [self.thread(("reviewer", "LGTM"))], "model", {}, {})

        decision = records["t"].decision
        assert isinstance(decision, ActionDecision)
        self.assertEqual(decision.action, DiscussionAction.NONE)

    @patch("classification.run_llm_for_verdict_batch")
    def test_praise_falls_back_to_the_comment_before_it(self, run_verdict) -> None:
        run_verdict.side_effect = self.answering("praise")

        records = classify_review_threads(
            1, [self.thread(("reviewer", "please fix"), ("reviewer", "LGTM"))], "model", {}, {}
        )

        decision = records["t"].decision
        assert isinstance(decision, ActionDecision)
        self.assertEqual(decision.action, DiscussionAction.AUTHOR)

    @patch("classification.run_llm_for_verdict_batch")
    def test_praise_after_an_author_reply_hands_the_thread_back(self, run_verdict) -> None:
        run_verdict.side_effect = self.answering("praise", reply="complete")

        records = classify_review_threads(
            1, [self.thread(("author", "fixed it"), ("reviewer", "LGTM"))], "model", {}, {}
        )

        decision = records["t"].decision
        assert isinstance(decision, ActionDecision)
        self.assertEqual(decision.action, DiscussionAction.REVIEWER)

    @patch("classification.run_llm_for_verdict_batch")
    def test_a_failed_praise_call_keeps_the_thread_with_the_author(self, run_verdict) -> None:
        run_verdict.side_effect = lambda items, _m, _p, _v: [
            failed_verdict_record_for(
                item,
                "praise",
                "Copilot CLI exited with status 1",
            )
            for item in items
        ]

        records = classify_review_threads(1, [self.thread(("reviewer", "LGTM"))], "model", {}, {})

        decision = records["t"].decision
        assert isinstance(decision, ActionDecision)
        self.assertEqual(decision.action, DiscussionAction.AUTHOR)
        self.assertTrue(records["t"].failed)

    @patch("classification.run_llm_for_verdict_batch")
    def test_a_failed_deferral_call_keeps_the_thread_with_the_author(self, run_verdict) -> None:
        run_verdict.side_effect = lambda items, _m, _p, _v: [
            failed_verdict_record_for(
                item,
                "complete",
                "Copilot CLI exited with status 1",
            )
            for item in items
        ]

        records = classify_review_threads(
            1,
            [self.thread(("reviewer", "please fix"), ("author", "a much longer reply than the gate"))],
            "model",
            {},
            {},
        )

        decision = records["t"].decision
        assert isinstance(decision, ActionDecision)
        self.assertEqual(decision.action, DiscussionAction.AUTHOR)
        self.assertTrue(records["t"].failed)

    @patch("classification.run_llm_for_verdict_batch")
    def test_only_the_last_comment_is_checked_for_praise(self, run_verdict) -> None:
        run_verdict.side_effect = self.answering("praise")

        records = classify_review_threads(
            1, [self.thread(("reviewer", "Nice"), ("reviewer", "LGTM"))], "model", {}, {}
        )

        decision = records["t"].decision
        assert isinstance(decision, ActionDecision)
        self.assertEqual(decision.action, DiscussionAction.AUTHOR)

    @patch("classification.run_llm_for_verdict_batch")
    def test_a_comment_that_is_not_praise_stays_the_authors(self, run_verdict) -> None:
        run_verdict.side_effect = self.answering("not_praise")

        records = classify_review_threads(
            1, [self.thread(("author", "fixed it"), ("reviewer", "one more thing"))], "model", {}, {}
        )

        decision = records["t"].decision
        assert isinstance(decision, ActionDecision)
        self.assertEqual(decision.action, DiscussionAction.AUTHOR)


class AutomationCommandFeedbackTest(unittest.TestCase):
    def test_automation_command_comments_are_not_top_level_feedback(self) -> None:
        def items(body: str) -> list[dict]:
            return top_level_items_from_raw({
                "issue_comments": [{
                    "id": 1,
                    "user": {"login": "reviewer"},
                    "created_at": "2026-07-14T00:00:00Z",
                    "body": body,
                }],
            })

        accepted = (
            "/workflow-approve",
            "/rerun",
            "/fix:refcache",
            "/workflow-approve\n/rerun",
        )
        for body in accepted:
            with self.subTest(body=body):
                self.assertEqual([], items(body))

        kept = (
            "/rerun please take another look",
            "This needs a /workflow-approve",
            "/label component:exporter",
            "/lgtm cancel",
        )
        for body in kept:
            with self.subTest(body=body):
                self.assertEqual(1, len(items(body)))

    def test_a_second_command_on_the_same_line_stays_feedback(self) -> None:
        item = top_level_items_from_raw({
            "issue_comments": [{
                "id": 1,
                "user": {"login": "reviewer"},
                "created_at": "2026-07-14T00:00:00Z",
                "body": "/rerun /needs-tests",
            }],
        })

        self.assertEqual(1, len(item))


class TopLevelActionLedgerTest(unittest.TestCase):


    @patch("classification.print_copilot_otel_file")
    @patch("classification.subprocess.run")
    def test_author_comment_batch_fails_a_duplicated_discussion_id(
        self,
        run_copilot,
        _print_otel,
    ) -> None:
        outcome = {
            "feedback_key": "f0001",
            "discussion_action": "none",
            "reason": "The author answered this feedback.",
        }
        run_copilot.return_value = copilot_batch_response(
            {"discussion_id": "author-reply", "feedback_outcomes": [outcome]},
            {"discussion_id": "author-reply", "feedback_outcomes": [outcome]},
        )
        discussion = review_thread_discussion("author-reply")
        discussion["discussion_kind"] = "top-level-author-reply"
        discussion["candidate_feedback"] = [
            {"discussion_id": "feedback", "body": "Please update the implementation."}
        ]

        records = run_llm_for_top_level_author_comment_batch(
            [discussion], "model"
        )

        self.assertEqual(run_copilot.call_count, 1)
        self.assertTrue(records[0].failed)
        assert isinstance(records[0], ClassificationFailure)
        self.assertIn(
            "duplicate discussion_id",
            records[0].diagnostics.error,
        )

    @patch("classification.print_copilot_otel_file")
    @patch("classification.subprocess.run")
    def test_author_comment_batch_rejects_missing_result(
        self,
        run_copilot,
        _print_otel,
    ) -> None:
        run_copilot.return_value = copilot_batch_response()
        discussion = review_thread_discussion("author-reply")
        discussion["discussion_kind"] = "top-level-author-reply"
        discussion["candidate_feedback"] = [
            {"discussion_id": "feedback", "body": "Please update the implementation."}
        ]

        records = run_llm_for_top_level_author_comment_batch(
            [discussion], "model"
        )

        self.assertEqual(run_copilot.call_count, 1)
        self.assertTrue(records[0].failed)
        assert isinstance(records[0], ClassificationFailure)
        self.assertIn(
            "this discussion_id",
            records[0].diagnostics.error,
        )

    @patch("classification.print_copilot_otel_file")
    @patch("classification.subprocess.run")
    def test_author_comment_batch_supports_mixed_feedback_outcomes(
        self,
        run_copilot,
        _print_otel,
    ) -> None:
        run_copilot.return_value = copilot_batch_response(
            {
                "discussion_id": "completed-reply",
                "feedback_outcomes": [
                    {
                        "feedback_key": "f0001",
                        "discussion_action": "none",
                        "reason": "The author answered the question.",
                    },
                    {
                        "feedback_key": "f0002",
                        "discussion_action": "author",
                        "reason": "The author will add the test later.",
                    },
                ],
            }
        )
        discussion = review_thread_discussion("completed-reply")
        discussion["discussion_kind"] = "top-level-author-reply"
        discussion["candidate_feedback"] = [
            {
                "discussion_id": "pr-review-3512552586",
                "body": "Why is this branch necessary?",
            },
            {
                "discussion_id": "pr-issue-comment-3578803688",
                "body": "Please test this before merging.",
            }
        ]

        records = run_llm_for_top_level_author_comment_batch(
            [discussion], "model"
        )

        self.assertFalse(records[0].failed)
        decision = records[0].decision
        assert isinstance(decision, AuthorCommentDecision)
        self.assertEqual(
            decision.feedback_outcomes,
            (
                FeedbackOutcome(
                    "pr-review-3512552586",
                    DiscussionAction.NONE,
                    "The author answered the question.",
                ),
                FeedbackOutcome(
                    "pr-issue-comment-3578803688",
                    DiscussionAction.AUTHOR,
                    "The author will add the test later.",
                ),
            ),
        )
        prompt = run_copilot.call_args.args[0][2]
        self.assertIn("Please test this before merging.", prompt)
        self.assertIn('"feedback_key": "f0001"', prompt)
        self.assertIn('"feedback_key": "f0002"', prompt)
        self.assertNotIn("pr-review-3512552586", prompt)
        self.assertNotIn("pr-issue-comment-3578803688", prompt)

    @patch("classification.MAX_PROMPT_CHARS", 5000)
    @patch("classification.print_copilot_otel_file")
    @patch("classification.subprocess.run")
    def test_author_comment_prompts_are_hard_bounded(
        self,
        run_copilot,
        _print_otel,
    ) -> None:
        def respond(args, **_kwargs):
            prompt = args[2]
            prompt_items = json.loads(
                prompt.split("---BEGIN AUTHOR FOLLOW-UPS---\n", 1)[1].split(
                    "\n---END AUTHOR FOLLOW-UPS---", 1
                )[0]
            )
            return copilot_batch_response(*[
                {
                    "discussion_id": item["discussion_id"],
                    "feedback_outcomes": [
                        {
                            "feedback_key": feedback["feedback_key"],
                            "discussion_action": "none",
                            "reason": "Completed response.",
                        }
                        for feedback in item["candidate_feedback"]
                    ],
                }
                for item in prompt_items
            ])

        run_copilot.side_effect = respond
        discussion = review_thread_discussion("author-reply")
        discussion["discussion_kind"] = "top-level-author-reply"
        discussion["candidate_feedback"] = [
            {
                "discussion_id": f"feedback-{index}",
                "body": f"Request {index}: " + "x" * 1000,
            }
            for index in range(30)
        ]

        records = run_llm_for_top_level_author_comment_batch(
            [discussion], "model"
        )

        self.assertFalse(records[0].failed)
        self.assertGreater(run_copilot.call_count, 1)
        prompts = [call.args[0][2] for call in run_copilot.call_args_list]
        self.assertTrue(all(len(prompt) <= 5000 for prompt in prompts))
        combined_prompts = "\n".join(prompts)
        for index in range(30):
            self.assertNotIn(f'"discussion_id": "feedback-{index}"', combined_prompts)
        self.assertEqual(
            [
                outcome.feedback_id
                for outcome in records[0].decision.feedback_outcomes
            ],
            [f"feedback-{index}" for index in range(30)],
        )

    @patch("classification.print_copilot_otel_file")
    @patch("classification.subprocess.run")
    def test_author_comment_batch_rejects_unknown_feedback_key(
        self,
        run_copilot,
        _print_otel,
    ) -> None:
        run_copilot.return_value = copilot_batch_response(
            {
                "discussion_id": "author-reply",
                "feedback_outcomes": [
                    {
                        "feedback_key": "pr-issue-comment-3841040831",
                        "discussion_action": "author",
                        "reason": "Blocked upstream.",
                    }
                ],
            }
        )
        discussion = review_thread_discussion("author-reply")
        discussion["discussion_kind"] = "top-level-author-reply"
        discussion["candidate_feedback"] = [
            {
                "discussion_id": "pr-review-3841040831",
                "body": "Please update the implementation.",
            }
        ]

        records = run_llm_for_top_level_author_comment_batch(
            [discussion], "model"
        )

        self.assertTrue(records[0].failed)
        assert isinstance(records[0], ClassificationFailure)
        decision = records[0].decision
        assert isinstance(decision, AuthorCommentDecision)
        self.assertEqual(decision.feedback_outcomes, ())
        self.assertIn(
            "unknown feedback_key 'pr-issue-comment-3841040831'",
            records[0].diagnostics.error,
        )
        self.assertIn(
            "expected keys ['f0001']",
            records[0].diagnostics.error,
        )
        self.assertIn(
            "canonical candidate IDs ['pr-review-3841040831']",
            records[0].diagnostics.error,
        )

    @patch("classification.print_copilot_otel_file")
    @patch("classification.subprocess.run")
    def test_author_comment_batch_bounds_unknown_feedback_key_diagnostics(
        self,
        run_copilot,
        _print_otel,
    ) -> None:
        run_copilot.return_value = copilot_batch_response(
            {
                "discussion_id": "author-reply",
                "feedback_outcomes": [
                    {
                        "feedback_key": "f9999",
                        "discussion_action": "none",
                        "reason": "Unknown feedback.",
                    }
                ],
            }
        )
        discussion = review_thread_discussion("author-reply")
        discussion["discussion_kind"] = "top-level-author-reply"
        discussion["candidate_feedback"] = [
            {
                "discussion_id": f"feedback-{index}",
                "body": f"Request {index}.",
            }
            for index in range(12)
        ]

        records = run_llm_for_top_level_author_comment_batch(
            [discussion], "model"
        )

        self.assertTrue(records[0].failed)
        assert isinstance(records[0], ClassificationFailure)
        error = records[0].diagnostics.error
        self.assertEqual(error.count("(showing 10 of 12)"), 2)
        self.assertIn("'f0010'", error)
        self.assertNotIn("'f0011'", error)
        self.assertIn("'feedback-9'", error)
        self.assertNotIn("'feedback-10'", error)

    @patch("classification.print_copilot_otel_file")
    @patch("classification.subprocess.run")
    def test_author_comment_batch_rejects_duplicate_feedback_key(
        self,
        run_copilot,
        _print_otel,
    ) -> None:
        run_copilot.return_value = copilot_batch_response(
            {
                "discussion_id": "author-reply",
                "feedback_outcomes": [
                    {
                        "feedback_key": "f0001",
                        "discussion_action": "none",
                        "reason": "The author answered this feedback.",
                    },
                    {
                        "feedback_key": "f0001",
                        "discussion_action": "author",
                        "reason": "Duplicate outcome for the same feedback.",
                    },
                ],
            }
        )
        discussion = review_thread_discussion("author-reply")
        discussion["discussion_kind"] = "top-level-author-reply"
        discussion["candidate_feedback"] = [
            {"discussion_id": "feedback", "body": "Please update the implementation."}
        ]

        records = run_llm_for_top_level_author_comment_batch(
            [discussion], "model"
        )

        self.assertTrue(records[0].failed)
        assert isinstance(records[0], ClassificationFailure)
        self.assertIn(
            "duplicate feedback_key 'f0001'",
            records[0].diagnostics.error,
        )

    @patch("classification.print_copilot_otel_file")
    @patch("classification.subprocess.run")
    def test_author_comment_batch_rejects_invalid_discussion_action(
        self,
        run_copilot,
        _print_otel,
    ) -> None:
        run_copilot.return_value = copilot_batch_response(
            {
                "discussion_id": "author-reply",
                "feedback_outcomes": [
                    {
                        "feedback_key": "f0001",
                        "discussion_action": "reviewer",
                        "reason": "The reviewer has the next action.",
                    }
                ],
            }
        )
        discussion = review_thread_discussion("author-reply")
        discussion["discussion_kind"] = "top-level-author-reply"
        discussion["candidate_feedback"] = [
            {"discussion_id": "feedback", "body": "Please update the implementation."}
        ]

        records = run_llm_for_top_level_author_comment_batch(
            [discussion], "model"
        )

        self.assertTrue(records[0].failed)
        assert isinstance(records[0], ClassificationFailure)
        self.assertIn(
            "invalid discussion_action 'reviewer' for feedback_key 'f0001'",
            records[0].diagnostics.error,
        )

    @patch("classification.print_copilot_otel_file")
    @patch("classification.subprocess.run")
    def test_author_comment_batch_rejects_cross_discussion_feedback_key(
        self,
        run_copilot,
        _print_otel,
    ) -> None:
        run_copilot.return_value = copilot_batch_response(
            {
                "discussion_id": "first-reply",
                "feedback_outcomes": [
                    {
                        "feedback_key": "f0002",
                        "discussion_action": "none",
                        "reason": "Copied from the other discussion.",
                    }
                ],
            },
            {
                "discussion_id": "second-reply",
                "feedback_outcomes": [
                    {
                        "feedback_key": "f0002",
                        "discussion_action": "none",
                        "reason": "The author answered this feedback.",
                    }
                ],
            },
        )
        discussions = [
            {
                **review_thread_discussion("first-reply"),
                "discussion_kind": "top-level-author-reply",
                "candidate_feedback": [
                    {"discussion_id": "first-feedback", "body": "First request."}
                ],
            },
            {
                **review_thread_discussion("second-reply"),
                "discussion_kind": "top-level-author-reply",
                "candidate_feedback": [
                    {"discussion_id": "second-feedback", "body": "Second request."}
                ],
            },
        ]

        records = run_llm_for_top_level_author_comment_batch(discussions, "model")

        self.assertTrue(records[0].failed)
        assert isinstance(records[0], ClassificationFailure)
        self.assertIn(
            "expected keys ['f0001']",
            records[0].diagnostics.error,
        )
        self.assertFalse(records[1].failed)
        decision = records[1].decision
        assert isinstance(decision, AuthorCommentDecision)
        self.assertEqual(
            decision.feedback_outcomes[0].feedback_id,
            "second-feedback",
        )

    @patch("classification.save_classification_cache")
    @patch("classification.load_classification_cache", return_value={})
    @patch("classification.run_llm_for_verdict_batch")
    def test_a_thread_the_author_has_not_answered_needs_no_model(
        self,
        run_batch,
        _load_cache,
        save_cache,
    ) -> None:
        asked = []

        def batch(items, _model, _prompt, verdicts):
            asked.append(verdicts)
            return [verdict_record(item["discussion_id"]) for item in items]

        run_batch.side_effect = batch
        thread = review_thread_discussion("inline")
        thread["discussion_facts"] = {"latest_comment_role": "reviewer"}
        # a real reviewer comment, too long to be a praise candidate, so no binary runs
        thread["comments"] = [
            {
                "timestamp": "2026-07-17T18:57:50Z",
                "actor": "reviewer",
                "actor_role": "approver",
                "body": "any chance to make it deterministic without relying on sleep? "
                        "the current approach is flaky on slower machines",
            },
        ]

        review_thread_classifications, top_level_classifications = (
            classify_feedback_domains(123, [thread], [top_level_item("top-level")], "model")
        )

        self.assertEqual([REVIEWER_FEEDBACK_VERDICTS], asked)
        decision = review_thread_classifications[0].decision
        assert isinstance(decision, ActionDecision)
        self.assertEqual(
            decision.action,
            DiscussionAction.AUTHOR,
        )
        self.assertEqual(
            [
                record.identity.discussion_id
                for record in top_level_classifications
            ],
            ["top-level"],
        )
    @patch("classification.save_classification_cache")
    @patch("classification.load_classification_cache", return_value={})
    @patch("classification.run_llm_for_verdict_batch")
    def test_a_thread_the_author_answered_is_routed_by_the_deferral_binary(
        self,
        run_verdict,
        _load_cache,
        _save_cache,
    ) -> None:
        for verdict, expected in (("deferral", "author"), ("complete", "reviewer")):
            with self.subTest(verdict=verdict):
                run_verdict.side_effect = lambda items, _m, _p, _v, answer=verdict: [
                    verdict_record_for(item, answer)
                    for item in items
                ]
                thread = review_thread_discussion("inline")
                thread["discussion_facts"] = {"latest_comment_role": "author"}
                thread["comments"] = [{"actor_role": "author", "body": "I'll fix this"}]

                review_thread_classifications, _ = classify_feedback_domains(
                    123, [thread], [], "model"
                )

                decision = review_thread_classifications[0].decision
                assert isinstance(decision, ActionDecision)
                self.assertEqual(decision.action.value, expected)

    @patch("classification.save_classification_cache")
    @patch("classification.load_classification_cache", return_value={})
    @patch("classification.run_llm_for_top_level_author_comment_batch")
    def test_author_replies_use_discussion_classification_cache(
        self,
        run_author_batch,
        _load_cache,
        save_cache,
    ) -> None:
        run_author_batch.side_effect = lambda discussions, _model: [
            author_comment_result(
                discussion,
                ("feedback", "author"),
            )
            for discussion in discussions
        ]
        author_reply = review_thread_discussion("author-reply")
        author_reply["discussion_kind"] = "top-level-author-reply"
        author_reply["candidate_feedback"] = [
            {"discussion_id": "feedback", "body": "Please add a test."}
        ]

        domain_classifications = classify_discussion_domains(
            123,
            [],
            [],
            [author_reply],
            "model",
        )
        review_classifications = domain_classifications.review_threads
        top_level_classifications = domain_classifications.top_level_items
        reply_classifications = domain_classifications.top_level_author_comments

        self.assertEqual(review_classifications, ())
        self.assertEqual(top_level_classifications, ())
        self.assertEqual(
            reply_classifications[0].identity.discussion_id,
            "author-reply",
        )
        decision = reply_classifications[0].decision
        assert isinstance(decision, AuthorCommentDecision)
        self.assertEqual(
            decision.feedback_outcomes[0].action,
            DiscussionAction.AUTHOR,
        )
        self.assertEqual(len(save_cache.call_args.args[1]), 1)

    @patch("classification.MAX_TOP_LEVEL_CLASSIFICATIONS_PER_PR", 20)
    @patch("classification.TOP_LEVEL_CLASSIFICATION_BATCH_SIZE", 10)
    @patch("classification.save_classification_cache")
    @patch("classification.load_classification_cache", return_value={})
    @patch("classification.run_llm_for_top_level_author_comment_batch")
    def test_author_reply_classification_is_batched_and_bounded(
        self,
        run_author_batch,
        _load_cache,
        _save_cache,
    ) -> None:
        run_author_batch.side_effect = lambda discussions, _model: [
            author_comment_result(discussion)
            for discussion in discussions
        ]
        author_replies = [
            {
                **review_thread_discussion(f"author-reply-{index}"),
                "discussion_kind": "top-level-author-reply",
            }
            for index in range(23)
        ]

        classifications = classify_discussion_domains(
            123,
            [],
            [],
            author_replies,
            "model",
        ).top_level_author_comments

        self.assertEqual(run_author_batch.call_count, 2)
        self.assertEqual(
            [len(call.args[0]) for call in run_author_batch.call_args_list],
            [10, 10],
        )
        self.assertEqual(
            [record.decision for record in classifications[:20]],
            [AuthorCommentDecision()] * 20,
        )
        self.assertEqual(
            [record.decision for record in classifications[20:]],
            [
                AuthorCommentDecision(
                    reason="Deferred by per-PR classification limit"
                )
            ] * 3,
        )
        self.assertEqual(
            [record.deferred for record in classifications],
            [False] * 20 + [True] * 3,
        )

    @patch("classification.MAX_TOP_LEVEL_AUTHOR_COMMENT_MODEL_CALLS_PER_PR", 2)
    @patch("classification.author_comment_prompt_batches")
    @patch("classification.save_classification_cache")
    @patch("classification.load_classification_cache", return_value={})
    @patch("classification.run_llm_for_top_level_author_comment_batch")
    def test_author_reply_expanded_prompt_calls_are_bounded(
        self,
        run_author_batch,
        _load_cache,
        _save_cache,
        prompt_batches,
    ) -> None:
        prompt_batches.side_effect = lambda discussions: [
            ([discussion], "prompt") for discussion in discussions
        ]
        run_author_batch.side_effect = lambda discussions, _model: [
            author_comment_result(discussion)
            for discussion in discussions
        ]
        author_replies = [
            {
                **review_thread_discussion(f"author-reply-{index}"),
                "discussion_kind": "top-level-author-reply",
            }
            for index in range(3)
        ]

        classifications = classify_discussion_domains(
            123,
            [],
            [],
            author_replies,
            "model",
        ).top_level_author_comments

        self.assertEqual(
            [discussion["discussion_id"] for discussion in run_author_batch.call_args.args[0]],
            ["author-reply-0", "author-reply-1"],
        )
        self.assertFalse(classifications[0].deferred)
        self.assertFalse(classifications[1].deferred)
        self.assertTrue(classifications[2].deferred)
        self.assertEqual(
            classifications[2].decision.reason,
            "Deferred by per-PR classification limit",
        )

    @patch("classification.save_classification_cache")
    @patch("classification.load_classification_cache", return_value={})
    @patch("classification.run_llm_for_verdict_batch")
    def test_later_run_classifies_only_failed_top_level_item(
        self,
        run_batch,
        load_cache,
        save_cache,
    ) -> None:
        valid = top_level_item("valid")
        missing = top_level_item("missing")
        run_batch.return_value = [
            verdict_record("valid"),
            ClassificationFailure(
                DiscussionIdentity(
                    "missing",
                    DiscussionKind.TOP_LEVEL_FEEDBACK,
                ),
                VerdictDecision(
                    Verdict.AUTHOR_ACTION,
                    "Missing result",
                ),
                ClassificationDiagnostics(error="Missing result"),
            ),
        ]

        classify_feedback_domains(123, [], [valid, missing], "model")

        cached = save_cache.call_args.args[1]
        self.assertEqual(len(cached), 1)
        load_cache.return_value = cached
        run_batch.reset_mock()
        run_batch.return_value = [verdict_record("missing")]

        classify_feedback_domains(123, [], [valid, missing], "model")

        self.assertEqual(
            [discussion["discussion_id"] for discussion in run_batch.call_args.args[0]],
            ["missing"],
        )

    @patch("classification.save_classification_cache")
    @patch("classification.load_classification_cache", return_value={})
    @patch("classification.run_llm_for_verdict_batch")
    def test_top_level_cache_ignores_mutable_facts_but_includes_body(
        self,
        run_batch,
        load_cache,
        save_cache,
    ) -> None:
        run_batch.side_effect = lambda items, _m, _p, _v: [
            verdict_record(item["discussion_id"]) for item in items
        ]
        discussion = top_level_item("top-level")
        discussion["comments"] = [{"body": "Could you clarify this?"}]
        discussion["discussion_facts"] = {"current_conflicts": "no"}

        classify_feedback_domains(123, [], [discussion], "model")
        load_cache.return_value = save_cache.call_args.args[1]
        run_batch.reset_mock()

        discussion["discussion_facts"]["current_conflicts"] = "yes"
        classify_feedback_domains(123, [], [discussion], "model")

        run_batch.assert_not_called()

        discussion["comments"][0]["body"] = "Please update the implementation."
        classify_feedback_domains(123, [], [discussion], "model")

        run_batch.assert_called_once()

    @patch("classification.MAX_TOP_LEVEL_CLASSIFICATIONS_PER_PR", 20)
    @patch("classification.TOP_LEVEL_CLASSIFICATION_BATCH_SIZE", 10)
    @patch("classification.save_classification_cache")
    @patch("classification.load_classification_cache", return_value={})
    @patch("classification.run_llm_for_verdict_batch")
    def test_uncached_top_level_classification_is_batched_and_bounded(
        self,
        run_batch,
        _load_cache,
        save_cache,
    ) -> None:
        run_batch.side_effect = lambda items, _m, _p, _v: [
            verdict_record(item["discussion_id"], "no_author_action") for item in items
        ]
        discussions = [top_level_item(f"item-{index}") for index in range(23)]

        _review_thread_classifications, classifications = classify_feedback_domains(
            123, [], discussions, "model"
        )

        self.assertEqual(run_batch.call_count, 2)
        self.assertEqual([len(call.args[0]) for call in run_batch.call_args_list], [10, 10])
        self.assertEqual(len(classifications), 23)
        self.assertEqual(
            [
                record.decision.action.value
                for record in classifications
                if isinstance(record.decision, ActionDecision)
            ],
            ["none"] * 20 + ["author"] * 3,
        )
        self.assertEqual(
            [record.failed for record in classifications],
            [False] * 20 + [True] * 3,
        )
        self.assertEqual(len(save_cache.call_args.args[1]), 20)

        _load_cache.return_value = save_cache.call_args.args[1]
        run_batch.reset_mock()

        _review_thread_classifications, classifications = classify_feedback_domains(
            123, [], discussions, "model"
        )

        self.assertEqual(run_batch.call_count, 1)
        self.assertEqual(len(run_batch.call_args.args[0]), 3)
        self.assertEqual(
            [
                record.decision.action.value
                for record in classifications
                if isinstance(record.decision, ActionDecision)
            ],
            ["none"] * 23,
        )
        self.assertEqual(len(save_cache.call_args.args[1]), 23)

    @patch("classification.MAX_TOP_LEVEL_CLASSIFICATIONS_PER_PR", 0)
    @patch("classification.save_classification_cache")
    @patch("classification.load_classification_cache", return_value={})
    @patch("classification.run_llm_for_verdict_batch")
    def test_over_limit_changes_requested_fails_instead_of_guessing(
        self,
        run_batch,
        _load_cache,
        _save_cache,
    ) -> None:
        discussion = top_level_item("change-request")
        discussion["review_state"] = "CHANGES_REQUESTED"
        discussion["comments"] = [{"body": "Please update the implementation."}]

        _review_thread_classifications, classifications = classify_feedback_domains(
            123, [], [discussion], "model"
        )

        run_batch.assert_not_called()
        self.assertTrue(classifications[0].failed)
        decision = classifications[0].decision
        assert isinstance(decision, ActionDecision)
        self.assertEqual(
            decision,
            ActionDecision(
                DiscussionAction.AUTHOR,
                "Exceeded per-PR classification limit",
            ),
        )

    def test_top_level_prompt_input_ignores_review_state(self) -> None:
        discussion = top_level_item("change-request")
        discussion["review_state"] = "CHANGES_REQUESTED"
        discussion["comments"] = [{"body": "Please update the implementation."}]

        self.assertEqual(
            top_level_reviewer_feedback_prompt_input(discussion),
            {
                "discussion_id": "change-request",
                "requester": "reviewer",
                "pr_author": "author",
                "addressed_to": [],
                "body": "Please update the implementation.",
            },
        )

    def test_top_level_prompt_input_reports_who_a_comment_addresses(self) -> None:
        discussion = top_level_item("addressed")
        discussion["comments"] = [
            {"body": "@maintainer, @open-telemetry/java-approvers could we reuse #123 here?"}
        ]

        self.assertEqual(
            top_level_reviewer_feedback_prompt_input(discussion)["addressed_to"],
            ["maintainer", "open-telemetry/java-approvers"],
        )

    def test_leading_mentions_only_reads_the_opening_run(self) -> None:
        # A mention further in names related work or people, not an addressee.
        self.assertEqual(leading_mentions("@trask In #19459 @someone did this"), ["trask"])
        self.assertEqual(leading_mentions("Please rebase, @author"), [])
        self.assertEqual(leading_mentions("\n  @first @second\nplease look"), ["first", "second"])
        self.assertEqual(leading_mentions("@first\n@second\nplease look"), ["first", "second"])
        self.assertEqual(leading_mentions("@First @Open-Telemetry/Java"), ["first", "open-telemetry/java"])
        self.assertEqual(leading_mentions("@invalid- please look"), [])
        self.assertEqual(leading_mentions(""), [])

    def test_top_level_feedback_gets_stable_individual_items(self) -> None:
        raw = {
            "issue_comments": [
                {
                    "id": 101,
                    "html_url": "https://example.test/issue-comment/101",
                    "created_at": ROOT_TIMESTAMP,
                    "updated_at": ROOT_TIMESTAMP,
                    "user": {"login": "reviewer"},
                    "body": "Please update the code.",
                }
            ],
            "reviews": [
                {
                    "id": 202,
                    "url": "https://example.test/review/202",
                    "submitted_at": "2026-07-14T02:00:00Z",
                    "updated_at": "2026-07-14T03:00:00Z",
                    "user": {"login": "reviewer"},
                    "state": "APPROVED",
                    "body": "Please update the PR description.",
                }
            ],
        }

        activity = build_activity_timeline(
            ActivityInput(
                normalize_pull_request_source({
                    "pr": {},
                    "commits": [],
                    "issue_comments": raw["issue_comments"],
                    "review_comments": [],
                    "reviews": raw["reviews"],
                }),
                "author",
                frozenset({"reviewer"}),
            )
        )
        items = list(
            prepare_discussions(
                DiscussionInput(
                    (),
                    activity.events,
                    "author",
                    frozenset({"reviewer"}),
                    "no",
                )
            ).top_level_items
        )

        self.assertEqual(
            [item["discussion_id"] for item in items],
            ["pr-issue-comment-101", "pr-review-202"],
        )
        self.assertEqual(
            [item["discussion_url"] for item in items],
            ["https://example.test/issue-comment/101", "https://example.test/review/202"],
        )
        self.assertEqual([item["pr_author"] for item in items], ["author", "author"])
        self.assertEqual(items[1]["root_timestamp"], "2026-07-14T02:00:00Z")

    def test_top_level_items_require_github_identity_and_requester(self) -> None:
        raw = {
            "issue_comments": [
                {
                    "created_at": ROOT_TIMESTAMP,
                    "updated_at": ROOT_TIMESTAMP,
                    "user": {"login": "reviewer"},
                    "body": "Missing comment id.",
                },
                {
                    "id": 101,
                    "created_at": ROOT_TIMESTAMP,
                    "updated_at": ROOT_TIMESTAMP,
                    "user": {},
                    "body": "Missing requester.",
                },
            ],
            "reviews": [],
        }

        self.assertEqual(
            top_level_items_from_raw(raw),
            [],
        )

    def test_minimized_issue_comment_is_not_top_level_feedback(self) -> None:
        raw = {
            "issue_comments": [
                {
                    "id": 101,
                    "html_url": "https://example.test/issue-comment/101",
                    "created_at": ROOT_TIMESTAMP,
                    "updated_at": ROOT_TIMESTAMP,
                    "user": {"login": "reviewer"},
                    "body": "Please update the documentation.",
                    "minimized": {"reason": "off-topic"},
                }
            ],
            "reviews": [],
        }

        self.assertEqual(top_level_items_from_raw(raw), [])

    def test_resolved_conflict_review_body_is_not_an_action_item(self) -> None:
        raw = {
            "issue_comments": [],
            "reviews": [
                {
                    "id": 202,
                    "submitted_at": ROOT_TIMESTAMP,
                    "user": {"login": "reviewer"},
                    "state": "COMMENTED",
                    "body": "Please resolve the merge conflict.",
                }
            ],
        }

        self.assertEqual(
            top_level_items_from_raw(raw),
            [],
        )

    def test_changes_requested_conflict_review_remains_an_action_item(self) -> None:
        raw = {
            "issue_comments": [],
            "reviews": [
                {
                    "id": 202,
                    "submitted_at": ROOT_TIMESTAMP,
                    "user": {"login": "reviewer"},
                    "state": "CHANGES_REQUESTED",
                    "body": "Please resolve the merge conflict.",
                }
            ],
        }

        items = top_level_items_from_raw(raw)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["review_state"], "CHANGES_REQUESTED")

    def test_empty_changes_requested_review_is_ignored(self) -> None:
        raw = {
            "issue_comments": [],
            "reviews": [
                {
                    "id": 202,
                    "submitted_at": ROOT_TIMESTAMP,
                    "user": {"login": "reviewer"},
                    "state": "CHANGES_REQUESTED",
                    "body": "",
                }
            ],
        }

        self.assertEqual(top_level_items_from_raw(raw), [])

    def test_review_state_does_not_block_routing_after_author_evidence(self) -> None:
        discussion = top_level_item("code")
        discussion["review_state"] = "CHANGES_REQUESTED"
        author_reply_item = {
            "discussion_id": "pr-author-reply-102",
            "discussion_kind": "top-level-author-reply",
            "source_id": 102,
            "candidate_feedback": [
                {
                    "discussion_id": "code",
                    "body": "Please update this.",
                }
            ],
            "comments": [
                {
                    "timestamp": "2026-07-14T02:00:00Z",
                    "actor": "author",
                    "actor_role": "author",
                    "body": "I handled this.",
                }
            ],
        }
        outcome = resolve_discussions(
            PreparedDiscussions((), (discussion,), (author_reply_item,)),
            DiscussionClassifications(
                (),
                (
                    ClassificationSuccess(
                        DiscussionIdentity(
                            "code",
                            DiscussionKind.TOP_LEVEL_FEEDBACK,
                        ),
                        ActionDecision(
                            DiscussionAction.AUTHOR,
                            "action requested",
                        ),
                    ),
                ),
                (
                    ClassificationSuccess(
                        DiscussionIdentity(
                            "pr-author-reply-102",
                            DiscussionKind.TOP_LEVEL_AUTHOR_REPLY,
                        ),
                        author_comment_decision(("code", "none")),
                    ),
                ),
            ),
        )
        facts = dashboard_facts(
            approval_count=1,
            ci_failing_count=0,
            ci_pending_count=0,
        )

        self.assertEqual(outcome.pending_actions, {})
        self.assertEqual(
            outcome.top_level_history["code"]["evidence"],
            {"reply": "2026-07-14T02:00:00Z"},
        )
        routing = resolve_routing(
            RoutingInput(
                facts=facts,
                pending_actions=outcome.pending_actions,
                previous_route=None,
                previous_facts=dashboard_facts(),
                required_approvals=1,
                require_clean_copilot_review=False,
                manual_reviewer_handoff=False,
                pending_human_reviewer_logins=frozenset(),
            )
        )
        self.assertEqual(routing.route, DashboardRoute.MAINTAINER)

if __name__ == "__main__":
    unittest.main()