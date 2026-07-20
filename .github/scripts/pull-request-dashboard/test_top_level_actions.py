from __future__ import annotations

import json
import unittest
from subprocess import CompletedProcess
from unittest.mock import patch

from dashboard import (
    AuthorCommentOutcome,
    add_wait_age_facts,
    add_reviewers,
    advance_top_level_actions,
    build_dashboard_update_for_pr,
    build_review_thread_pending_actions,
    derive_top_level_author_comment_items,
    derive_top_level_items,
    normalize_events,
    requires_title_edit_lookup,
    route_pr,
    top_level_author_comment_outcomes,
    top_level_author_comment_source_state,
)
from classification import (
    classify_discussion_domains,
    discussion_prompt,
    parse_discussion_decision,
    run_llm_for_top_level_author_comment_batch,
    run_llm_for_top_level_reviewer_feedback_batch,
    top_level_reviewer_feedback_batch_prompt,
    top_level_reviewer_feedback_prompt_input,
)
from notifications import reviewer_logins_for_notification
from render import reviewer_icon


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


def classification(discussion_id: str, *evidence_kinds: str) -> dict:
    return {
        "discussion_id": discussion_id,
        "discussion_kind": "top-level-feedback",
        "decision": {
            "discussion_action": "author",
            "required_evidence_kinds": list(evidence_kinds),
            "reason": "action requested",
        },
    }


def author_comment_decision(*feedback_actions: tuple[str, str]) -> dict:
    return {
        "feedback_outcomes": [
            {
                "feedback_id": feedback_id,
                "discussion_action": action,
                "reason": "Test author-comment outcome.",
            }
            for feedback_id, action in feedback_actions
        ]
    }


def top_level_batch_result(
    discussion_id: str,
    action: str = "none",
    evidence_kinds: list[str] | None = None,
    reason: str = "No action",
) -> dict:
    return {
        "discussion_id": discussion_id,
        "discussion_action": action,
        "required_evidence_kinds": evidence_kinds or [],
        "reason": reason,
    }


def copilot_batch_response(*items: dict) -> CompletedProcess[str]:
    return CompletedProcess(
        args=[],
        returncode=0,
        stdout=json.dumps({"items": items}),
        stderr="",
    )


def event(kind: str, timestamp: str, actor: str, actor_role: str, **values: object) -> dict:
    return {
        "kind": kind,
        "timestamp": timestamp,
        "actor": actor,
        "actor_role": actor_role,
        "body": values.pop("body", kind),
        "state": values.pop("state", None),
        "is_merge_from_base_by_non_author": False,
        **values,
    }


def author_comment_outcome(
    feedback_id: str,
    timestamp: str,
    source_id: int = 102,
) -> AuthorCommentOutcome:
    return {
        "source_id": source_id,
        "action": "none",
        "timestamp": timestamp,
        "feedback_id": feedback_id,
    }


def top_level_history_record(kind: str, timestamp: str) -> dict:
    return {
        "evidence": {kind: timestamp},
    }


def top_level_items_from_raw(
    raw: dict,
    conflicts: str = "no",
) -> list[dict]:
    events = normalize_events(
        {
            "commits": [],
            "issue_comments": raw.get("issue_comments") or [],
            "review_comments": [],
            "reviews": raw.get("reviews") or [],
        },
        "author",
        {"reviewer"},
    )
    return derive_top_level_items(
        events,
        {"author": "author", "conflicts": conflicts},
    )


def classify_feedback_domains(
    number: int,
    review_threads: list[dict],
    top_level_items: list[dict],
    model: str,
) -> tuple[list[dict], list[dict]]:
    review_classifications, top_level_classifications, _ = (
        classify_discussion_domains(
            number,
            review_threads,
            top_level_items,
            [],
            model,
        )
    )
    return review_classifications, top_level_classifications


class TopLevelActionLedgerTest(unittest.TestCase):
    def test_inline_prompt_treats_author_inability_as_completed_reply(self) -> None:
        discussion = review_thread_discussion("inline")
        discussion["comments"] = [
            {
                "timestamp": "2026-07-17T18:57:50Z",
                "actor": "reviewer",
                "actor_role": "approver",
                "body": "any chance to make it deterministic without relying on sleep?",
            },
            {
                "timestamp": "2026-07-17T20:56:50Z",
                "actor": "author",
                "actor_role": "author",
                "body": "I couldn't find a good way",
            },
        ]

        prompt = discussion_prompt(discussion)

        self.assertIn("Require an explicit statement", prompt)
        self.assertIn("I couldn't find a good way", prompt)
        self.assertIn("is a completed reply and maps to reviewer", prompt)

    def test_review_thread_pending_actions_include_since_and_omit_closed(self) -> None:
        review_threads = [
            {
                "discussion_id": "open",
                "comments": [{"timestamp": ROOT_TIMESTAMP}],
            },
            {
                "discussion_id": "unclear",
                "comments": [{"timestamp": ROOT_TIMESTAMP}],
            },
            {
                "discussion_id": "closed",
                "comments": [{"timestamp": ROOT_TIMESTAMP}],
            },
        ]
        classifications = [
            {
                "discussion_id": "open",
                "decision": {"discussion_action": "author"},
            },
            {
                "discussion_id": "unclear",
                "decision": {"discussion_action": "unclear"},
            },
            {
                "discussion_id": "closed",
                "decision": {"discussion_action": "none"},
            },
        ]

        pending_actions = build_review_thread_pending_actions(
            review_threads, classifications
        )

        self.assertEqual(
            pending_actions,
            {
                "open": {"action": "author", "since": ROOT_TIMESTAMP},
                "unclear": {"action": "reviewer", "since": ROOT_TIMESTAMP},
            },
        )

    @patch("classification.print_copilot_otel_file")
    @patch("classification.subprocess.run")
    def test_top_level_batch_fails_invalid_results_without_retry(
        self,
        run_copilot,
        _print_otel,
    ) -> None:
        run_copilot.side_effect = [
            copilot_batch_response(
                top_level_batch_result("second"),
                top_level_batch_result(
                    "first",
                    "author",
                    ["commit", "description"],
                    "Change requested",
                ),
                top_level_batch_result(
                    "invalid",
                    "author",
                    reason="Missing required evidence",
                ),
                top_level_batch_result("second", reason="Duplicate"),
            ),
        ]
        discussions = [
            top_level_item("first"),
            top_level_item("second"),
            top_level_item("missing"),
            top_level_item("invalid"),
        ]

        records = run_llm_for_top_level_reviewer_feedback_batch(discussions, "model")

        self.assertEqual(
            [record["discussion_id"] for record in records],
            ["first", "second", "missing", "invalid"],
        )
        self.assertEqual(
            records[0]["decision"]["required_evidence_kinds"],
            ["commit", "description"],
        )
        self.assertEqual(records[0]["decision"]["reason"], "Change requested")
        self.assertFalse(records[0]["failed"])
        self.assertTrue(records[1]["failed"])
        self.assertIn("duplicate discussion_id", records[1]["error"])
        self.assertTrue(records[2]["failed"])
        self.assertIn("this discussion_id", records[2]["error"])
        self.assertTrue(records[3]["failed"])
        self.assertIn("this discussion_id", records[3]["error"])
        self.assertEqual(run_copilot.call_count, 1)
        prompt = run_copilot.call_args.args[0][2]
        self.assertIn("Return exactly 4 items", prompt)
        self.assertIn(
            '["first", "second", "missing", "invalid"]',
            prompt,
        )
        self.assertIn("Never merge, deduplicate", prompt)

    @patch("classification.print_copilot_otel_file")
    @patch("classification.subprocess.run")
    def test_top_level_batch_rejects_missing_result(
        self,
        run_copilot,
        _print_otel,
    ) -> None:
        run_copilot.return_value = copilot_batch_response()

        records = run_llm_for_top_level_reviewer_feedback_batch(
            [top_level_item("missing")], "model"
        )

        self.assertEqual(run_copilot.call_count, 1)
        self.assertTrue(records[0]["failed"])
        self.assertIn("this discussion_id", records[0]["error"])

    @patch("classification.MAX_PROMPT_CHARS", 5000)
    @patch("classification.print_copilot_otel_file")
    @patch("classification.subprocess.run")
    def test_reviewer_feedback_prompts_are_hard_bounded(
        self,
        run_copilot,
        _print_otel,
    ) -> None:
        def respond(args, **_kwargs):
            prompt = args[2]
            prompt_items = json.loads(
                prompt.split("---BEGIN TOP-LEVEL FEEDBACK---\n", 1)[1].split(
                    "\n---END TOP-LEVEL FEEDBACK---", 1
                )[0]
            )
            return copilot_batch_response(*[
                top_level_batch_result(item["discussion_id"])
                for item in prompt_items
            ])

        run_copilot.side_effect = respond
        discussions = [
            {
                **top_level_item(f"feedback-{index}"),
                "comments": [{"body": "x" * 6000}],
            }
            for index in range(4)
        ]

        records = run_llm_for_top_level_reviewer_feedback_batch(
            discussions, "model"
        )

        self.assertGreater(run_copilot.call_count, 1)
        prompts = [call.args[0][2] for call in run_copilot.call_args_list]
        self.assertTrue(all(len(prompt) <= 5000 for prompt in prompts))
        self.assertEqual(
            [record["discussion_id"] for record in records],
            [f"feedback-{index}" for index in range(4)],
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
                        "feedback_id": "question",
                        "discussion_action": "none",
                        "reason": "The author answered the question.",
                    },
                    {
                        "feedback_id": "test-request",
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
                "discussion_id": "question",
                "body": "Why is this branch necessary?",
            },
            {
                "discussion_id": "test-request",
                "body": "Please test this before merging.",
            }
        ]

        records = run_llm_for_top_level_author_comment_batch(
            [discussion], "model"
        )

        self.assertFalse(records[0]["failed"])
        self.assertEqual(
            records[0]["decision"]["feedback_outcomes"],
            [
                {
                    "feedback_id": "question",
                    "discussion_action": "none",
                    "reason": "The author answered the question.",
                },
                {
                    "feedback_id": "test-request",
                    "discussion_action": "author",
                    "reason": "The author will add the test later.",
                },
            ],
        )
        self.assertNotIn("required_evidence_kinds", records[0]["decision"])
        prompt = run_copilot.call_args.args[0][2]
        self.assertIn("Please test this before merging.", prompt)

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
                            "feedback_id": feedback["discussion_id"],
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

        self.assertFalse(records[0]["failed"])
        self.assertGreater(run_copilot.call_count, 1)
        prompts = [call.args[0][2] for call in run_copilot.call_args_list]
        self.assertTrue(all(len(prompt) <= 5000 for prompt in prompts))
        combined_prompts = "\n".join(prompts)
        for index in range(30):
            self.assertIn(f'"discussion_id": "feedback-{index}"', combined_prompts)
        self.assertEqual(
            [
                outcome["feedback_id"]
                for outcome in records[0]["decision"]["feedback_outcomes"]
            ],
            [f"feedback-{index}" for index in range(30)],
        )

    @patch("classification.print_copilot_otel_file")
    @patch("classification.subprocess.run")
    def test_author_comment_batch_rejects_unknown_feedback_id(
        self,
        run_copilot,
        _print_otel,
    ) -> None:
        run_copilot.return_value = copilot_batch_response(
            {
                "discussion_id": "author-reply",
                "feedback_outcomes": [
                    {
                        "feedback_id": "not-a-candidate",
                        "discussion_action": "external",
                        "reason": "Blocked upstream.",
                    }
                ],
            }
        )
        discussion = review_thread_discussion("author-reply")
        discussion["discussion_kind"] = "top-level-author-reply"
        discussion["candidate_feedback"] = [
            {
                "discussion_id": "actual-candidate",
                "body": "Please update the implementation.",
            }
        ]

        records = run_llm_for_top_level_author_comment_batch(
            [discussion], "model"
        )

        self.assertTrue(records[0]["failed"])
        self.assertEqual(records[0]["decision"]["feedback_outcomes"], [])
        self.assertIn("valid classification", records[0]["error"])

    @patch("classification.save_classification_cache")
    @patch("classification.load_classification_cache", return_value={})
    @patch("classification.run_llm_for_top_level_reviewer_feedback_batch")
    @patch("classification.run_llm_for_discussion")
    def test_discussion_domains_use_separate_classification_pipelines(
        self,
        run_inline,
        run_batch,
        _load_cache,
        save_cache,
    ) -> None:
        run_inline.side_effect = lambda discussion, _model: {
            "discussion_id": discussion["discussion_id"],
            "discussion_kind": discussion["discussion_kind"],
            "failed": False,
            "decision": {"discussion_action": "reviewer", "reason": "Author replied"},
        }
        run_batch.side_effect = lambda discussions, _model: [
            {
                "discussion_id": discussion["discussion_id"],
                "discussion_kind": discussion["discussion_kind"],
                "failed": False,
                "decision": {
                    "discussion_action": "author",
                    "required_evidence_kinds": ["reply"],
                    "reason": "Reviewer asked a question",
                },
            }
            for discussion in discussions
        ]
        review_threads = [review_thread_discussion("inline")]
        top_level_items = [top_level_item("top-level")]

        review_thread_classifications, top_level_classifications = (
            classify_feedback_domains(123, review_threads, top_level_items, "model")
        )

        self.assertEqual(run_inline.call_args.args[0]["discussion_id"], "inline")
        self.assertEqual(
            [discussion["discussion_id"] for discussion in run_batch.call_args.args[0]],
            ["top-level"],
        )
        self.assertEqual(
            [record["discussion_id"] for record in review_thread_classifications],
            ["inline"],
        )
        self.assertEqual(
            [record["discussion_id"] for record in top_level_classifications],
            ["top-level"],
        )
        self.assertEqual(len(save_cache.call_args.args[1]), 2)

    @patch("classification.save_classification_cache")
    @patch("classification.load_classification_cache", return_value={})
    @patch("classification.run_llm_for_top_level_reviewer_feedback_batch", return_value=[])
    @patch("classification.run_llm_for_top_level_author_comment_batch")
    def test_author_replies_use_discussion_classification_cache(
        self,
        run_author_batch,
        _run_batch,
        _load_cache,
        save_cache,
    ) -> None:
        run_author_batch.side_effect = lambda discussions, _model: [
            {
                "discussion_id": discussion["discussion_id"],
                "discussion_kind": discussion["discussion_kind"],
                "failed": False,
                "decision": author_comment_decision(("feedback", "author")),
            }
            for discussion in discussions
        ]
        author_reply = review_thread_discussion("author-reply")
        author_reply["discussion_kind"] = "top-level-author-reply"
        author_reply["candidate_feedback"] = [
            {"discussion_id": "feedback", "body": "Please add a test."}
        ]

        review_classifications, top_level_classifications, reply_classifications = (
            classify_discussion_domains(
                123,
                [],
                [],
                [author_reply],
                "model",
            )
        )

        self.assertEqual(review_classifications, [])
        self.assertEqual(top_level_classifications, [])
        self.assertEqual(reply_classifications[0]["discussion_id"], "author-reply")
        self.assertEqual(
            reply_classifications[0]["decision"]["feedback_outcomes"][0][
                "discussion_action"
            ],
            "author",
        )
        self.assertEqual(len(save_cache.call_args.args[1]), 1)

    @patch("classification.MAX_TOP_LEVEL_CLASSIFICATIONS_PER_PR", 20)
    @patch("classification.TOP_LEVEL_CLASSIFICATION_BATCH_SIZE", 10)
    @patch("classification.save_classification_cache")
    @patch("classification.load_classification_cache", return_value={})
    @patch("classification.run_llm_for_top_level_reviewer_feedback_batch", return_value=[])
    @patch("classification.run_llm_for_top_level_author_comment_batch")
    def test_author_reply_classification_is_batched_and_bounded(
        self,
        run_author_batch,
        _run_top_level_batch,
        _load_cache,
        _save_cache,
    ) -> None:
        run_author_batch.side_effect = lambda discussions, _model: [
            {
                "discussion_id": discussion["discussion_id"],
                "discussion_kind": discussion["discussion_kind"],
                "failed": False,
                "decision": {"feedback_outcomes": []},
            }
            for discussion in discussions
        ]
        author_replies = [
            {
                **review_thread_discussion(f"author-reply-{index}"),
                "discussion_kind": "top-level-author-reply",
            }
            for index in range(23)
        ]

        _review, _top_level, classifications = (
            classify_discussion_domains(
                123,
                [],
                [],
                author_replies,
                "model",
            )
        )

        self.assertEqual(run_author_batch.call_count, 2)
        self.assertEqual(
            [len(call.args[0]) for call in run_author_batch.call_args_list],
            [10, 10],
        )
        self.assertEqual(
            [record["decision"] for record in classifications[:20]],
            [{"feedback_outcomes": []}] * 20,
        )
        self.assertEqual(
            [record["decision"] for record in classifications[20:]],
            [
                {
                    "feedback_outcomes": [],
                    "reason": "Deferred by per-PR classification limit",
                }
            ] * 3,
        )
        self.assertEqual(
            [record.get("deferred") for record in classifications],
            [None] * 20 + [True] * 3,
        )

    @patch("classification.save_classification_cache")
    @patch("classification.load_classification_cache", return_value={})
    @patch("classification.run_llm_for_top_level_reviewer_feedback_batch")
    def test_later_run_classifies_only_failed_top_level_item(
        self,
        run_batch,
        load_cache,
        save_cache,
    ) -> None:
        valid = top_level_item("valid")
        missing = top_level_item("missing")
        run_batch.return_value = [
            classification("valid", "commit"),
            {
                "discussion_id": "missing",
                "discussion_kind": "top-level-feedback",
                "failed": True,
                "decision": {
                    "discussion_action": "unclear",
                    "required_evidence_kinds": [],
                    "reason": "Missing result",
                },
            },
        ]

        classify_feedback_domains(123, [], [valid, missing], "model")

        cached = save_cache.call_args.args[1]
        self.assertEqual(len(cached), 1)
        load_cache.return_value = cached
        run_batch.reset_mock()
        run_batch.return_value = [classification("missing", "commit")]

        classify_feedback_domains(123, [], [valid, missing], "model")

        self.assertEqual(
            [discussion["discussion_id"] for discussion in run_batch.call_args.args[0]],
            ["missing"],
        )

    @patch("classification.save_classification_cache")
    @patch("classification.load_classification_cache", return_value={})
    @patch("classification.run_llm_for_top_level_reviewer_feedback_batch")
    def test_top_level_cache_ignores_mutable_facts_but_includes_body(
        self,
        run_batch,
        load_cache,
        save_cache,
    ) -> None:
        run_batch.side_effect = lambda discussions, _model: [
            classification(discussion["discussion_id"], "reply")
            for discussion in discussions
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
    @patch("classification.run_llm_for_top_level_reviewer_feedback_batch")
    def test_uncached_top_level_classification_is_batched_and_bounded(
        self,
        run_batch,
        _load_cache,
        save_cache,
    ) -> None:
        run_batch.side_effect = lambda discussions, _model: [
            {
                "discussion_id": discussion["discussion_id"],
                "discussion_kind": discussion["discussion_kind"],
                "failed": False,
                "decision": {
                    "discussion_action": "none",
                    "required_evidence_kinds": [],
                    "reason": "No action",
                },
            }
            for discussion in discussions
        ]
        discussions = [top_level_item(f"item-{index}") for index in range(23)]

        _review_thread_classifications, classifications = classify_feedback_domains(
            123, [], discussions, "model"
        )

        self.assertEqual(run_batch.call_count, 2)
        self.assertEqual([len(call.args[0]) for call in run_batch.call_args_list], [10, 10])
        self.assertEqual(len(classifications), 23)
        self.assertEqual(
            [record["decision"]["discussion_action"] for record in classifications],
            ["none"] * 20 + ["unclear"] * 3,
        )
        self.assertFalse(any(record.get("failed") for record in classifications))
        self.assertEqual(len(save_cache.call_args.args[1]), 20)

        _load_cache.return_value = save_cache.call_args.args[1]
        run_batch.reset_mock()

        _review_thread_classifications, classifications = classify_feedback_domains(
            123, [], discussions, "model"
        )

        self.assertEqual(run_batch.call_count, 1)
        self.assertEqual(len(run_batch.call_args.args[0]), 3)
        self.assertEqual(
            [record["decision"]["discussion_action"] for record in classifications],
            ["none"] * 23,
        )
        self.assertEqual(len(save_cache.call_args.args[1]), 23)

    @patch("classification.MAX_TOP_LEVEL_CLASSIFICATIONS_PER_PR", 0)
    @patch("classification.save_classification_cache")
    @patch("classification.load_classification_cache", return_value={})
    @patch("classification.run_llm_for_top_level_reviewer_feedback_batch")
    def test_deferred_changes_requested_uses_normal_fallback(
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
        self.assertEqual(
            classifications[0]["decision"],
            {
                "discussion_action": "unclear",
                "required_evidence_kinds": [],
                "reason": "Deferred by per-PR classification limit",
            },
        )

    def test_top_level_prompt_input_ignores_review_state(self) -> None:
        discussion = top_level_item("change-request")
        discussion["review_state"] = "CHANGES_REQUESTED"
        discussion["comments"] = [{"body": "Please update the implementation."}]

        self.assertEqual(
            top_level_reviewer_feedback_prompt_input(discussion),
            {
                "discussion_id": "change-request",
                "pr_author": "author",
                "body": "Please update the implementation.",
            },
        )

    def test_top_level_prompt_distinguishes_other_participants_from_author(self) -> None:
        discussion = top_level_item("review-request")
        discussion["comments"] = [
            {"body": "@reviewer should we put the effort into merging this?"}
        ]

        prompt = top_level_reviewer_feedback_batch_prompt([discussion])

        self.assertIn('"pr_author": "author"', prompt)
        self.assertIn("directed to other reviewers", prompt)
        self.assertIn("which implementation or code option", prompt)

    def test_unclear_item_sets_reviewer_wait_age(self) -> None:
        pending_actions = {
            "unclear": {"action": "reviewer", "since": ROOT_TIMESTAMP},
        }
        facts = {
            "last_author_activity_at": "2026-07-14T04:00:00Z",
            "created_at": "2026-07-13T01:00:00Z",
        }

        add_wait_age_facts(facts, "approver", pending_actions)

        self.assertEqual(facts["waiting_since"], "2026-07-14T01:00:00+00:00")
        self.assertEqual(facts["waiting_age_basis"], "oldest_pending_thread")

    @patch("dashboard.build_pr_result")
    def test_dashboard_refresh_reuses_stored_top_level_history(self, build_result) -> None:
        build_result.return_value = None
        previous_state = {
            "pr-review-456": top_level_history_record("commit", "2026-07-14T03:00:00Z"),
        }

        build_dashboard_update_for_pr(
            "open-telemetry/example",
            "open-telemetry",
            "example",
            {123},
            {"reviewer"},
            123,
            "model",
            1,
            {
                "prs": {
                    "123": {
                        "pr_number": 123,
                        "top_level_history": previous_state,
                    }
                }
            },
        )

        self.assertEqual(
            build_result.call_args.kwargs["previous_top_level_history"],
            previous_state,
        )

    def test_top_level_decision_requires_matching_action_and_evidence(self) -> None:
        for action in ("reviewer", "approver"):
            with self.subTest(action=action):
                _, valid = parse_discussion_decision(
                    json.dumps({
                        "discussion_action": action,
                        "required_evidence_kinds": [],
                        "reason": "unsupported top-level action",
                    }),
                    require_evidence_kinds=True,
                )

                self.assertFalse(valid)
        _, mismatched_valid = parse_discussion_decision(
            '{"discussion_action":"author","required_evidence_kinds":[],"reason":"invalid evidence"}',
            require_evidence_kinds=True,
        )
        external, external_valid = parse_discussion_decision(
            '{"discussion_action":"external","required_evidence_kinds":[],"reason":"blocked elsewhere"}',
            require_evidence_kinds=True,
        )

        self.assertFalse(mismatched_valid)
        self.assertTrue(external_valid)
        self.assertEqual(external["discussion_action"], "external")

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

        events = normalize_events(
            {
                "commits": [],
                "issue_comments": raw["issue_comments"],
                "review_comments": [],
                "reviews": raw["reviews"],
            },
            "author",
            {"reviewer"},
        )
        items = derive_top_level_items(
            events,
            {"author": "author", "conflicts": "no"},
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
        self.assertEqual(items[1]["root_timestamp"], "2026-07-14T03:00:00Z")
        review_event = next(event for event in events if event["kind"] == "review-state")
        self.assertEqual(review_event["timestamp"], "2026-07-14T02:00:00Z")
        self.assertEqual(review_event["updated_timestamp"], "2026-07-14T03:00:00Z")

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

    def test_commit_advances_only_commit_action(self) -> None:
        discussions = [top_level_item("code"), top_level_item("description")]
        classifications = [
            classification("code", "commit"),
            classification("description", "description"),
        ]
        events = [event("commit", "2026-07-14T03:00:00Z", "author", "author")]

        pending_actions, top_level_history = advance_top_level_actions(
            discussions, classifications, events, {}, "author", None, []
        )

        self.assertNotIn("code", pending_actions)
        self.assertEqual(
            top_level_history["code"]["evidence"],
            {"commit": "2026-07-14T03:00:00Z"},
        )
        self.assertEqual(classifications[0]["decision"]["discussion_action"], "author")
        self.assertEqual(pending_actions["description"]["action"], "author")
        self.assertEqual(
            top_level_history["description"]["evidence"],
            {"commit": "2026-07-14T03:00:00Z"},
        )
        self.assertEqual(classifications[1]["decision"]["discussion_action"], "author")

    def test_commit_after_final_feedback_edit_addresses_code_choice(self) -> None:
        discussion = top_level_item("code-choice")
        discussion["root_timestamp"] = "2023-12-18T09:20:36Z"
        events = [
            event(
                "issue-comment",
                "2023-12-18T09:20:07Z",
                "author",
                "author",
                created_timestamp="2023-12-18T09:20:07Z",
            ),
            event("commit", "2023-12-18T13:14:32Z", "author", "author"),
        ]

        pending_actions, history = advance_top_level_actions(
            [discussion],
            [classification("code-choice", "commit")],
            events,
            {},
            "author",
            None,
            [],
        )

        self.assertEqual(pending_actions, {})
        self.assertEqual(
            history["code-choice"]["evidence"],
            {"commit": "2023-12-18T13:14:32Z"},
        )

    def test_author_reply_advances_all_author_actions(self) -> None:
        discussions = [
            top_level_item("code"),
            top_level_item("description"),
            top_level_item("reply"),
        ]
        classifications = [
            classification("code", "commit"),
            classification("description", "description"),
            classification("reply", "reply"),
        ]
        events = [
            event("issue-comment", "2026-07-14T03:00:00Z", "author", "author"),
        ]

        pending_actions, top_level_history = advance_top_level_actions(
            discussions,
            classifications,
            events,
            {},
            "author",
            None,
            [
                author_comment_outcome(
                    discussion["discussion_id"], "2026-07-14T03:00:00Z"
                )
                for discussion in discussions
            ],
        )

        self.assertEqual(pending_actions, {})
        self.assertEqual(top_level_history["code"]["evidence"], {"reply": "2026-07-14T03:00:00Z"})
        self.assertEqual(
            top_level_history["description"]["evidence"],
            {"reply": "2026-07-14T03:00:00Z"},
        )
        self.assertEqual(top_level_history["reply"]["evidence"], {"reply": "2026-07-14T03:00:00Z"})

    def test_author_self_deferral_does_not_close_top_level_feedback(self) -> None:
        discussion = top_level_item("test-request")
        events = [
            event(
                "issue-comment",
                "2026-07-14T03:00:00Z",
                "author",
                "author",
                source_id=102,
                created_timestamp="2026-07-14T03:00:00Z",
                body="Thanks, I'll have time this week to test and validate.",
            ),
        ]
        author_reply_items = derive_top_level_author_comment_items(
            events,
            [discussion],
            {"conflicts": "no"},
        )

        pending_actions, history = advance_top_level_actions(
            [discussion],
            [classification("test-request", "reply")],
            events,
            {},
            "author",
            {
                "test-request": {
                    "evidence": {"reply": "2026-07-14T03:00:00Z"},
                }
            },
            author_comment_outcomes=top_level_author_comment_outcomes(
                author_reply_items,
                [
                    {
                        "discussion_id": author_reply_items[0]["discussion_id"],
                        "decision": author_comment_decision(
                            ("test-request", "author")
                        ),
                    }
                ],
            ),
        )

        self.assertEqual(pending_actions["test-request"]["action"], "author")
        self.assertNotIn("test-request", history)

    def test_author_comment_candidates_include_only_earlier_feedback(self) -> None:
        earlier = top_level_item("earlier")
        earlier["comments"] = [{"body": "Please update the implementation."}]
        later = top_level_item("later")
        later["root_timestamp"] = "2026-07-14T04:00:00Z"
        later["comments"] = [{"body": "Please add another test."}]
        events = [
            event(
                "issue-comment",
                "2026-07-14T03:00:00Z",
                "author",
                "author",
                source_id=102,
                created_timestamp="2026-07-14T03:00:00Z",
            ),
        ]

        items = derive_top_level_author_comment_items(
            events,
            [earlier, later],
            {"conflicts": "no"},
        )

        self.assertEqual(
            items[0]["candidate_feedback"],
            [
                {
                    "discussion_id": "earlier",
                    "body": "Please update the implementation.",
                }
            ],
        )

    def test_completed_author_reply_closes_top_level_feedback(self) -> None:
        discussion = top_level_item("question")
        events = [
            event(
                "issue-comment",
                "2026-07-14T03:00:00Z",
                "author",
                "author",
                source_id=102,
                created_timestamp="2026-07-14T03:00:00Z",
                body="I tested this and confirmed it works.",
            ),
        ]
        author_reply_items = derive_top_level_author_comment_items(
            events,
            [discussion],
            {"conflicts": "no"},
        )

        pending_actions, history = advance_top_level_actions(
            [discussion],
            [classification("question", "reply")],
            events,
            {},
            "author",
            previous_history=None,
            author_comment_outcomes=top_level_author_comment_outcomes(
                author_reply_items,
                [
                    {
                        "discussion_id": author_reply_items[0]["discussion_id"],
                        "decision": author_comment_decision(("question", "none")),
                    }
                ],
            ),
        )

        self.assertEqual(pending_actions, {})
        self.assertEqual(
            history["question"],
            {
                "evidence": {"reply": "2026-07-14T03:00:00Z"},
                "reply_source_id": 102,
            },
        )

    def test_cached_author_reply_survives_missing_classification(self) -> None:
        discussion = top_level_item("question")

        pending_actions, history = advance_top_level_actions(
            [discussion],
            [classification("question", "reply")],
            [],
            {},
            "author",
            previous_history={
                "question": {
                    "evidence": {"reply": "2026-07-14T03:00:00Z"},
                    "reply_source_id": 102,
                },
            },
            author_comment_outcomes=[],
        )

        self.assertEqual(pending_actions, {})
        self.assertEqual(
            history["question"],
            {
                "evidence": {"reply": "2026-07-14T03:00:00Z"},
                "reply_source_id": 102,
            },
        )

    def test_cached_author_reply_is_removed_with_its_source(self) -> None:
        discussion = top_level_item("question")

        pending_actions, history = advance_top_level_actions(
            [discussion],
            [classification("question", "reply")],
            [],
            {},
            "author",
            previous_history={
                "question": {
                    "evidence": {"reply": "2026-07-14T03:00:00Z"},
                    "reply_source_id": 102,
                },
            },
            author_comment_outcomes=[],
            author_comment_source_state={"current": set(), "classified": set()},
        )

        self.assertEqual(
            pending_actions,
            {"question": {"action": "author", "since": ROOT_TIMESTAMP}},
        )
        self.assertEqual(history, {})

    def test_cached_author_reply_is_recomputed_after_classification(self) -> None:
        discussion = top_level_item("question")

        pending_actions, history = advance_top_level_actions(
            [discussion],
            [classification("question", "reply")],
            [],
            {},
            "author",
            previous_history={
                "question": {
                    "evidence": {"reply": "2026-07-14T03:00:00Z"},
                    "reply_source_id": 102,
                },
            },
            author_comment_outcomes=[],
            author_comment_source_state={"current": {102}, "classified": {102}},
        )

        self.assertEqual(
            pending_actions,
            {"question": {"action": "author", "since": ROOT_TIMESTAMP}},
        )
        self.assertEqual(history, {})

    def test_cached_author_reply_survives_failed_classification(self) -> None:
        discussion = top_level_item("question")
        author_comment_items = [{"discussion_id": "reply", "source_id": 102}]
        source_state = top_level_author_comment_source_state(
            author_comment_items,
            [{"discussion_id": "reply", "failed": True}],
        )

        pending_actions, history = advance_top_level_actions(
            [discussion],
            [classification("question", "reply")],
            [],
            {},
            "author",
            previous_history={
                "question": {
                    "evidence": {"reply": "2026-07-14T03:00:00Z"},
                    "reply_source_id": 102,
                },
            },
            author_comment_outcomes=[],
            author_comment_source_state=source_state,
        )

        self.assertEqual(pending_actions, {})
        self.assertEqual(
            history["question"],
            {
                "evidence": {"reply": "2026-07-14T03:00:00Z"},
                "reply_source_id": 102,
            },
        )

    def test_cached_author_reply_survives_deferred_classification(self) -> None:
        discussion = top_level_item("question")
        source_state = top_level_author_comment_source_state(
            [{"discussion_id": "reply", "source_id": 102}],
            [{"discussion_id": "reply", "failed": False, "deferred": True}],
        )

        pending_actions, history = advance_top_level_actions(
            [discussion],
            [classification("question", "reply")],
            [],
            {},
            "author",
            previous_history={
                "question": {
                    "evidence": {"reply": "2026-07-14T03:00:00Z"},
                    "reply_source_id": 102,
                },
            },
            author_comment_outcomes=[],
            author_comment_source_state=source_state,
        )

        self.assertEqual(pending_actions, {})
        self.assertEqual(
            history["question"],
            {
                "evidence": {"reply": "2026-07-14T03:00:00Z"},
                "reply_source_id": 102,
            },
        )

    def test_legacy_cached_author_reply_survives_missing_classification(self) -> None:
        discussion = top_level_item("question")

        pending_actions, history = advance_top_level_actions(
            [discussion],
            [classification("question", "reply")],
            [],
            {},
            "author",
            previous_history={
                "question": {
                    "evidence": {"reply": "2026-07-14T03:00:00Z"},
                },
            },
            author_comment_outcomes=[],
        )

        self.assertEqual(pending_actions, {})
        self.assertEqual(
            history["question"],
            {"evidence": {"reply": "2026-07-14T03:00:00Z"}},
        )

    def test_legacy_cached_author_reply_recovers_source_id(self) -> None:
        discussion = top_level_item("question")

        pending_actions, history = advance_top_level_actions(
            [discussion],
            [classification("question", "reply")],
            [],
            {},
            "author",
            previous_history={
                "question": {
                    "evidence": {"reply": "2026-07-14T03:00:00Z"},
                },
            },
            author_comment_outcomes=[
                author_comment_outcome(
                    "question", "2026-07-14T03:00:00Z", source_id=102
                ),
            ],
        )

        self.assertEqual(pending_actions, {})
        self.assertEqual(
            history["question"],
            {
                "evidence": {"reply": "2026-07-14T03:00:00Z"},
                "reply_source_id": 102,
            },
        )

    def test_newer_handoff_supersedes_legacy_cached_reply(self) -> None:
        discussion = top_level_item("question")

        pending_actions, history = advance_top_level_actions(
            [discussion],
            [classification("question", "reply")],
            [],
            {},
            "author",
            previous_history={
                "question": {
                    "evidence": {"reply": "2026-07-14T02:00:00Z"},
                },
            },
            author_comment_outcomes=[
                {
                    "source_id": 103,
                    "action": "external",
                    "timestamp": "2026-07-14T03:00:00Z",
                    "feedback_id": "question",
                },
            ],
        )

        self.assertEqual(
            pending_actions,
            {
                "question": {
                    "action": "external",
                    "since": "2026-07-14T03:00:00Z",
                },
            },
        )
        self.assertEqual(history, {})

    def test_newer_author_handoff_supersedes_cached_reply(self) -> None:
        discussion = top_level_item("question")

        pending_actions, history = advance_top_level_actions(
            [discussion],
            [classification("question", "reply")],
            [],
            {},
            "author",
            previous_history={
                "question": {
                    "evidence": {"reply": "2026-07-14T02:00:00Z"},
                    "reply_source_id": 102,
                },
            },
            author_comment_outcomes=[
                {
                    "source_id": 103,
                    "action": "external",
                    "timestamp": "2026-07-14T03:00:00Z",
                    "feedback_id": "question",
                },
            ],
        )

        self.assertEqual(
            pending_actions,
            {
                "question": {
                    "action": "external",
                    "since": "2026-07-14T03:00:00Z",
                },
            },
        )
        self.assertEqual(history, {})

    def test_reclassified_author_reply_supersedes_cached_reply(self) -> None:
        discussion = top_level_item("question")

        pending_actions, history = advance_top_level_actions(
            [discussion],
            [classification("question", "reply")],
            [],
            {},
            "author",
            previous_history={
                "question": {
                    "evidence": {"reply": "2026-07-14T03:00:00Z"},
                    "reply_source_id": 102,
                },
            },
            author_comment_outcomes=[
                {
                    "source_id": 102,
                    "action": "author",
                    "timestamp": "2026-07-14T03:00:00Z",
                    "feedback_id": "question",
                },
            ],
        )

        self.assertEqual(
            pending_actions,
            {
                "question": {
                    "action": "author",
                    "since": "2026-07-14T03:00:00Z",
                },
            },
        )
        self.assertEqual(history, {})

    def test_author_reply_uses_source_id_to_break_timestamp_tie(self) -> None:
        discussion = top_level_item("question")
        events = [
            event(
                "issue-comment",
                "2026-07-14T03:00:00Z",
                "author",
                "author",
                source_id=102,
                created_timestamp="2026-07-14T03:00:00Z",
                body="I tested this and confirmed it works.",
            ),
            event(
                "issue-comment",
                "2026-07-14T03:00:00Z",
                "author",
                "author",
                source_id=103,
                created_timestamp="2026-07-14T03:00:00Z",
                body="I'll make another change later.",
            ),
        ]
        author_reply_items = derive_top_level_author_comment_items(
            events,
            [discussion],
            {"conflicts": "no"},
        )
        classifications = [
            {
                "discussion_id": author_reply_items[0]["discussion_id"],
                "decision": author_comment_decision(("question", "none")),
            },
            {
                "discussion_id": author_reply_items[1]["discussion_id"],
                "decision": author_comment_decision(("question", "author")),
            },
        ]

        pending_actions, history = advance_top_level_actions(
            [discussion],
            [classification("question", "reply")],
            events,
            {},
            "author",
            previous_history=None,
            author_comment_outcomes=top_level_author_comment_outcomes(
                author_reply_items,
                classifications,
            ),
        )

        self.assertEqual(
            pending_actions,
            {
                "question": {
                    "action": "author",
                    "since": "2026-07-14T03:00:00Z",
                },
            },
        )
        self.assertEqual(history, {})

    def test_author_comment_applies_each_feedback_outcome_independently(self) -> None:
        discussions = [
            top_level_item("first-request"),
            top_level_item("second-request"),
        ]
        classifications = [
            classification("first-request", "commit"),
            classification("second-request", "reply"),
        ]
        events = [
            event(
                "issue-comment",
                "2026-07-14T03:00:00Z",
                "author",
                "author",
                source_id=102,
                created_timestamp="2026-07-14T03:00:00Z",
            ),
        ]
        author_comment_items = derive_top_level_author_comment_items(
            events,
            discussions,
            {"conflicts": "no"},
        )
        author_comment_outcomes = top_level_author_comment_outcomes(
            author_comment_items,
            [
                {
                    "discussion_id": author_comment_items[0]["discussion_id"],
                    "decision": {
                        "feedback_outcomes": [
                            {
                                "feedback_id": "first-request",
                                "discussion_action": "none",
                                "reason": "The author answered the first request.",
                            },
                            {
                                "feedback_id": "second-request",
                                "discussion_action": "author",
                                "reason": "The author will address the second request.",
                            },
                        ],
                    },
                }
            ],
        )

        pending_actions, history = advance_top_level_actions(
            discussions,
            classifications,
            events,
            {},
            "author",
            previous_history=None,
            author_comment_outcomes=author_comment_outcomes,
        )

        self.assertEqual(
            pending_actions,
            {
                "second-request": {
                    "action": "author",
                    "since": "2026-07-14T03:00:00Z",
                },
            },
        )
        self.assertEqual(
            history,
            {
                "first-request": {
                    "evidence": {"reply": "2026-07-14T03:00:00Z"},
                    "reply_source_id": 102,
                },
            },
        )

    def test_external_author_reply_routes_feedback_external(self) -> None:
        discussion = top_level_item("dependency")
        events = [
            event(
                "issue-comment",
                "2026-07-14T03:00:00Z",
                "author",
                "author",
                source_id=102,
                created_timestamp="2026-07-14T03:00:00Z",
                body="This is blocked on an upstream specification decision.",
            ),
        ]
        author_reply_items = derive_top_level_author_comment_items(
            events,
            [discussion],
            {"conflicts": "no"},
        )

        pending_actions, history = advance_top_level_actions(
            [discussion],
            [classification("dependency", "reply")],
            events,
            {},
            "author",
            previous_history=None,
            author_comment_outcomes=top_level_author_comment_outcomes(
                author_reply_items,
                [
                    {
                        "discussion_id": author_reply_items[0]["discussion_id"],
                        "decision": author_comment_decision(
                            ("dependency", "external")
                        ),
                    }
                ],
            ),
        )

        self.assertEqual(
            pending_actions["dependency"],
            {"action": "external", "since": "2026-07-14T03:00:00Z"},
        )
        self.assertNotIn("dependency", history)

    def test_later_external_reply_supersedes_author_self_deferral(self) -> None:
        discussion = top_level_item("dependency")
        events = [
            event(
                "issue-comment",
                "2026-07-14T02:00:00Z",
                "author",
                "author",
                source_id=102,
                created_timestamp="2026-07-14T02:00:00Z",
                body="I'll investigate this.",
            ),
            event(
                "issue-comment",
                "2026-07-14T03:00:00Z",
                "author",
                "author",
                source_id=103,
                created_timestamp="2026-07-14T03:00:00Z",
                body="This is blocked on an upstream specification decision.",
            ),
        ]
        author_comment_items = derive_top_level_author_comment_items(
            events,
            [discussion],
            {"conflicts": "no"},
        )

        pending_actions, history = advance_top_level_actions(
            [discussion],
            [classification("dependency", "reply")],
            events,
            {},
            "author",
            previous_history=None,
            author_comment_outcomes=top_level_author_comment_outcomes(
                author_comment_items,
                [
                    {
                        "discussion_id": author_comment_items[0]["discussion_id"],
                        "decision": author_comment_decision(
                            ("dependency", "author")
                        ),
                    },
                    {
                        "discussion_id": author_comment_items[1]["discussion_id"],
                        "decision": author_comment_decision(
                            ("dependency", "external")
                        ),
                    },
                ],
            ),
        )

        self.assertEqual(
            pending_actions["dependency"],
            {"action": "external", "since": "2026-07-14T03:00:00Z"},
        )
        self.assertNotIn("dependency", history)

    def test_later_author_self_deferral_supersedes_external_reply(self) -> None:
        discussion = top_level_item("dependency")
        events = [
            event(
                "issue-comment",
                "2026-07-14T02:00:00Z",
                "author",
                "author",
                source_id=102,
                created_timestamp="2026-07-14T02:00:00Z",
                body="This is blocked on an upstream specification decision.",
            ),
            event(
                "issue-comment",
                "2026-07-14T03:00:00Z",
                "author",
                "author",
                source_id=103,
                created_timestamp="2026-07-14T03:00:00Z",
                body="I'll update this in the current PR.",
            ),
        ]
        author_comment_items = derive_top_level_author_comment_items(
            events,
            [discussion],
            {"conflicts": "no"},
        )
        author_comment_outcomes = top_level_author_comment_outcomes(
            author_comment_items,
            [
                {
                    "discussion_id": author_comment_items[0]["discussion_id"],
                    "decision": author_comment_decision(
                        ("dependency", "external")
                    ),
                },
                {
                    "discussion_id": author_comment_items[1]["discussion_id"],
                    "decision": author_comment_decision(("dependency", "author")),
                },
            ],
        )

        for original_action in ("external", "unclear"):
            with self.subTest(original_action=original_action):
                original_classification = classification("dependency", "reply")
                original_classification["decision"]["discussion_action"] = original_action

                pending_actions, history = advance_top_level_actions(
                    [discussion],
                    [original_classification],
                    events,
                    {},
                    "author",
                    previous_history=None,
                    author_comment_outcomes=author_comment_outcomes,
                )

                self.assertEqual(
                    pending_actions["dependency"],
                    {"action": "author", "since": "2026-07-14T03:00:00Z"},
                )
                self.assertNotIn("dependency", history)

    def test_external_reply_uses_creation_order_after_older_comment_edit(self) -> None:
        discussion = top_level_item("dependency")
        events = [
            event(
                "issue-comment",
                "2026-07-14T05:00:00Z",
                "author",
                "author",
                source_id=103,
                created_timestamp="2026-07-14T04:00:00Z",
                body="This is blocked on a second upstream decision.",
            ),
            event(
                "issue-comment",
                "2026-07-14T06:00:00Z",
                "author",
                "author",
                source_id=102,
                created_timestamp="2026-07-14T02:00:00Z",
                body="This is blocked on the first upstream decision.",
            ),
        ]
        author_comment_items = derive_top_level_author_comment_items(
            events,
            [discussion],
            {"conflicts": "no"},
        )
        author_comment_outcomes = top_level_author_comment_outcomes(
            author_comment_items,
            [
                {
                    "discussion_id": item["discussion_id"],
                    "decision": author_comment_decision(
                        ("dependency", "external")
                    ),
                }
                for item in author_comment_items
            ],
        )

        self.assertEqual(
            [
                (outcome["source_id"], outcome["timestamp"])
                for outcome in author_comment_outcomes
            ],
            [
                (102, "2026-07-14T02:00:00Z"),
                (103, "2026-07-14T04:00:00Z"),
            ],
        )

        pending_actions, history = advance_top_level_actions(
            [discussion],
            [classification("dependency", "reply")],
            events,
            {},
            "author",
            previous_history=None,
            author_comment_outcomes=author_comment_outcomes,
        )

        self.assertEqual(
            pending_actions["dependency"],
            {"action": "external", "since": "2026-07-14T02:00:00Z"},
        )
        self.assertNotIn("dependency", history)

    def test_completed_reply_restarts_later_external_handoff_age(self) -> None:
        discussion = top_level_item("dependency")
        author_comment_outcomes = [
            {
                "source_id": 102,
                "action": "external",
                "timestamp": "2026-07-14T02:00:00Z",
                "feedback_id": "dependency",
            },
            author_comment_outcome(
                "dependency", "2026-07-14T03:00:00Z", source_id=103
            ),
            {
                "source_id": 104,
                "action": "external",
                "timestamp": "2026-07-14T04:00:00Z",
                "feedback_id": "dependency",
            },
        ]

        pending_actions, history = advance_top_level_actions(
            [discussion],
            [classification("dependency", "reply")],
            [],
            {},
            "author",
            previous_history=None,
            author_comment_outcomes=author_comment_outcomes,
        )

        self.assertEqual(
            pending_actions["dependency"],
            {"action": "external", "since": "2026-07-14T04:00:00Z"},
        )
        self.assertEqual(history, {})

    def test_unclear_reply_preserves_explicit_external_handoff(self) -> None:
        discussion = top_level_item("dependency")
        author_comment_outcomes = [
            {
                "source_id": 102,
                "action": "external",
                "timestamp": "2026-07-14T02:00:00Z",
                "feedback_id": "dependency",
            },
            {
                "source_id": 103,
                "action": "unclear",
                "timestamp": "2026-07-14T03:00:00Z",
                "feedback_id": "dependency",
            },
        ]

        pending_actions, history = advance_top_level_actions(
            [discussion],
            [classification("dependency", "reply")],
            [],
            {},
            "author",
            previous_history=None,
            author_comment_outcomes=author_comment_outcomes,
        )

        self.assertEqual(
            pending_actions["dependency"],
            {"action": "external", "since": "2026-07-14T02:00:00Z"},
        )
        self.assertEqual(history, {})

    def test_satisfied_evidence_is_not_reopened_by_external_reply(self) -> None:
        discussion = top_level_item("code")
        events = [
            event("commit", "2026-07-14T02:00:00Z", "author", "author"),
            event(
                "issue-comment",
                "2026-07-14T03:00:00Z",
                "author",
                "author",
                source_id=102,
                created_timestamp="2026-07-14T03:00:00Z",
                body="This is blocked on an upstream decision.",
            ),
        ]
        author_comment_items = derive_top_level_author_comment_items(
            events,
            [discussion],
            {"conflicts": "no"},
        )

        pending_actions, history = advance_top_level_actions(
            [discussion],
            [classification("code", "commit")],
            events,
            {},
            "author",
            previous_history=None,
            author_comment_outcomes=top_level_author_comment_outcomes(
                author_comment_items,
                [
                    {
                        "discussion_id": author_comment_items[0]["discussion_id"],
                        "decision": author_comment_decision(("code", "external")),
                    }
                ],
            ),
        )

        self.assertEqual(pending_actions, {})
        self.assertEqual(
            history["code"]["evidence"],
            {"commit": "2026-07-14T02:00:00Z"},
        )

    def test_later_satisfying_evidence_clears_external_reply(self) -> None:
        discussion = top_level_item("code")
        events = [
            event(
                "issue-comment",
                "2026-07-14T02:00:00Z",
                "author",
                "author",
                source_id=102,
                created_timestamp="2026-07-14T02:00:00Z",
                body="This is blocked on an upstream decision.",
            ),
            event("commit", "2026-07-14T03:00:00Z", "author", "author"),
        ]
        author_comment_items = derive_top_level_author_comment_items(
            events,
            [discussion],
            {"conflicts": "no"},
        )

        pending_actions, history = advance_top_level_actions(
            [discussion],
            [classification("code", "commit")],
            events,
            {},
            "author",
            previous_history=None,
            author_comment_outcomes=top_level_author_comment_outcomes(
                author_comment_items,
                [
                    {
                        "discussion_id": author_comment_items[0]["discussion_id"],
                        "decision": author_comment_decision(("code", "external")),
                    }
                ],
            ),
        )

        self.assertEqual(pending_actions, {})
        self.assertEqual(
            history["code"]["evidence"],
            {"commit": "2026-07-14T03:00:00Z"},
        )

    def test_non_content_updates_do_not_reopen_replied_to_feedback(self) -> None:
        events = normalize_events(
            {
                "commits": [],
                "issue_comments": [
                    {
                        "id": 101,
                        "created_at": "2026-05-27T01:29:41Z",
                        "updated_at": "2026-05-30T22:09:49Z",
                        "content_updated_at": "2026-05-27T01:29:41Z",
                        "user": {"login": "reviewer"},
                        "body": "Can you mark this as resolving the issue?",
                    },
                    {
                        "id": 102,
                        "created_at": "2026-05-27T12:07:12Z",
                        "updated_at": "2026-05-30T22:09:46Z",
                        "content_updated_at": "2026-05-27T12:07:12Z",
                        "user": {"login": "author"},
                        "body": "This PR resolves it only partly.",
                    },
                ],
                "review_comments": [],
                "reviews": [],
            },
            "author",
            {"reviewer"},
        )
        discussion = top_level_item("request")
        discussion["root_timestamp"] = events[0]["timestamp"]

        pending_actions, history = advance_top_level_actions(
            [discussion],
            [classification("request", "description")],
            events,
            {},
            "author",
            None,
            [
                author_comment_outcome(
                    "request", "2026-05-27T12:07:12Z", source_id=102
                )
            ],
        )

        self.assertEqual(pending_actions, {})
        self.assertEqual(
            history["request"]["evidence"],
            {"reply": "2026-05-27T12:07:12Z"},
        )

    def test_normalized_events_use_creation_order_not_edit_order(self) -> None:
        events = normalize_events(
            {
                "commits": [],
                "issue_comments": [
                    {
                        "id": 101,
                        "created_at": "2026-07-14T01:00:00Z",
                        "updated_at": "2026-07-14T05:00:00Z",
                        "content_updated_at": "2026-07-14T05:00:00Z",
                        "user": {"login": "author"},
                        "body": "Older comment edited later.",
                    },
                    {
                        "id": 102,
                        "created_at": "2026-07-14T02:00:00Z",
                        "updated_at": "2026-07-14T02:00:00Z",
                        "content_updated_at": "2026-07-14T02:00:00Z",
                        "user": {"login": "author"},
                        "body": "Newer comment.",
                    },
                ],
                "review_comments": [],
                "reviews": [],
            },
            "author",
            {"reviewer"},
        )

        self.assertEqual([event["source_id"] for event in events], [101, 102])
        self.assertEqual(events[0]["timestamp"], "2026-07-14T05:00:00Z")
        self.assertEqual(events[0]["created_timestamp"], "2026-07-14T01:00:00Z")

    def test_edited_old_author_comment_does_not_count_as_reply(self) -> None:
        events = normalize_events(
            {
                "commits": [],
                "issue_comments": [
                    {
                        "id": 101,
                        "created_at": "2026-07-14T00:00:00Z",
                        "updated_at": "2026-07-14T03:00:00Z",
                        "content_updated_at": "2026-07-14T03:00:00Z",
                        "user": {"login": "author"},
                        "body": "An earlier comment edited later.",
                    }
                ],
                "review_comments": [],
                "reviews": [],
            },
            "author",
            {"reviewer"},
        )

        pending_actions, top_level_history = advance_top_level_actions(
            [top_level_item("code")],
            [classification("code", "reply")],
            events,
            {},
            "author",
            None,
            [],
        )

        self.assertEqual(events[0]["timestamp"], "2026-07-14T03:00:00Z")
        self.assertEqual(events[0]["created_timestamp"], "2026-07-14T00:00:00Z")
        self.assertEqual(pending_actions["code"]["action"], "author")
        self.assertNotIn("code", top_level_history)

    def test_compound_action_waits_for_all_required_evidence(self) -> None:
        discussions = [top_level_item("compound")]
        classifications = [classification("compound", "commit", "description")]

        pending_actions, first_history = advance_top_level_actions(
            discussions,
            classifications,
            [event("commit", "2026-07-14T02:00:00Z", "author", "author")],
            {},
            "author",
            None,
            [],
        )

        self.assertEqual(pending_actions["compound"]["action"], "author")
        self.assertEqual(
            first_history["compound"]["evidence"],
            {"commit": "2026-07-14T02:00:00Z"},
        )

        pending_actions, history = advance_top_level_actions(
            discussions,
            classifications,
            [],
            {
                "lastEditedAt": "2026-07-14T03:00:00Z",
                "editor": {"login": "author"},
            },
            "author",
            first_history,
            [],
        )

        self.assertEqual(pending_actions, {})
        self.assertEqual(
            history["compound"]["evidence"],
            {
                "commit": "2026-07-14T02:00:00Z",
                "description": "2026-07-14T03:00:00Z",
            },
        )

    def test_title_edit_addresses_title_action(self) -> None:
        pending_actions, history = advance_top_level_actions(
            [top_level_item("title")],
            [classification("title", "title")],
            [],
            {
                "titleEdits": [
                    {
                        "actor": {"login": "author"},
                        "createdAt": "2026-07-14T03:00:00Z",
                    },
                    {
                        "actor": {"login": "maintainer"},
                        "createdAt": "2026-07-14T02:00:00Z",
                    },
                ],
            },
            "author",
            None,
            [],
        )

        self.assertEqual(pending_actions, {})
        self.assertEqual(
            history["title"]["evidence"],
            {"title": "2026-07-14T03:00:00Z"},
        )

    def test_title_lookup_requires_title_classification_without_cached_title(self) -> None:
        discussion = top_level_item("title")
        title_classification = classification("title", "commit", "title")

        self.assertTrue(
            requires_title_edit_lookup(
                [discussion],
                [title_classification],
                None,
                [],
            )
        )
        self.assertFalse(
            requires_title_edit_lookup(
                [discussion],
                [classification("title", "commit")],
                None,
                [],
            )
        )
        self.assertFalse(
            requires_title_edit_lookup(
                [discussion],
                [title_classification],
                {
                    "title": {
                        "evidence": {"reply": "2026-07-14T03:00:00Z"},
                    },
                },
                [],
            )
        )
        self.assertFalse(
            requires_title_edit_lookup(
                [discussion],
                [title_classification],
                {
                    "title": top_level_history_record(
                        "title", "2026-07-14T03:00:00Z"
                    ),
                },
                [],
            )
        )
        self.assertFalse(
            requires_title_edit_lookup(
                [discussion],
                [title_classification],
                {
                    "title": {
                        "evidence": {"reply": "2026-07-14T03:00:00Z"},
                        "reply_source_id": 102,
                    },
                },
                [author_comment_outcome("title", "2026-07-14T03:00:00Z")],
            )
        )
        cached_reply = {
            "title": {
                "evidence": {"reply": "2026-07-14T03:00:00Z"},
                "reply_source_id": 102,
            },
        }
        self.assertTrue(
            requires_title_edit_lookup(
                [discussion],
                [title_classification],
                cached_reply,
                [],
                {"current": set(), "classified": set()},
            )
        )
        self.assertFalse(
            requires_title_edit_lookup(
                [discussion],
                [title_classification],
                cached_reply,
                [],
                {"current": {102}, "classified": set()},
            )
        )
        self.assertTrue(
            requires_title_edit_lookup(
                [discussion],
                [title_classification],
                {
                    "title": {
                        "evidence": {"reply": "2026-07-14T00:00:00Z"},
                        "reply_source_id": 102,
                    },
                },
                [author_comment_outcome("title", "2026-07-14T00:00:00Z")],
            )
        )
        self.assertFalse(
            requires_title_edit_lookup(
                [discussion],
                [title_classification],
                None,
                [author_comment_outcome("title", "2026-07-14T03:00:00Z")],
            )
        )
        self.assertTrue(
            requires_title_edit_lookup(
                [discussion],
                [title_classification],
                None,
                [author_comment_outcome("title", "2026-07-14T00:00:00Z")],
            )
        )

    def test_maintainer_cherry_pick_uses_original_author_date(self) -> None:
        events = normalize_events(
            {
                "commits": [
                    {
                        "sha": "abcdef123456",
                        "author": {"login": "author"},
                        "committer": {"login": "maintainer"},
                        "commit": {
                            "author": {
                                "name": "Author",
                                "date": "2026-07-13T03:00:00Z",
                            },
                            "committer": {"date": "2026-07-14T03:00:00Z"},
                            "message": "Cherry-pick requested change",
                        },
                        "parents": [{}],
                    }
                ],
                "issue_comments": [],
                "review_comments": [],
                "reviews": [],
            },
            "author",
            {"reviewer"},
        )

        self.assertEqual(events[0]["actor"], "author")
        self.assertEqual(events[0]["timestamp"], "2026-07-13T03:00:00Z")

        classifications = [classification("code", "commit")]
        pending_actions, top_level_history = advance_top_level_actions(
            [top_level_item("code")],
            classifications,
            events,
            {},
            "author",
            None,
            [],
        )

        self.assertEqual(pending_actions["code"]["action"], "author")
        self.assertNotIn("code", top_level_history)

    def test_cherry_pick_by_author_is_author_evidence(self) -> None:
        events = normalize_events(
            {
                "commits": [
                    {
                        "sha": "abcdef123456",
                        "author": {"login": "original-author"},
                        "committer": {"login": "author"},
                        "commit": {
                            "author": {
                                "name": "Original Author",
                                "date": "2026-07-13T03:00:00Z",
                            },
                            "committer": {"date": "2026-07-14T03:00:00Z"},
                            "message": "Cherry-pick requested change",
                        },
                        "parents": [{}],
                    }
                ],
                "issue_comments": [],
                "review_comments": [],
                "reviews": [],
            },
            "author",
            {"reviewer"},
        )

        self.assertEqual(events[0]["actor"], "author")
        self.assertEqual(events[0]["actor_role"], "author")
        self.assertEqual(events[0]["timestamp"], "2026-07-14T03:00:00Z")

    def test_author_commit_without_committer_date_uses_author_date(self) -> None:
        events = normalize_events(
            {
                "commits": [
                    {
                        "sha": "abcdef123456",
                        "author": {"login": "author"},
                        "committer": {"login": "author"},
                        "commit": {
                            "author": {
                                "name": "Author",
                                "date": "2026-07-14T03:00:00Z",
                            },
                            "committer": {},
                            "message": "Address requested change",
                        },
                        "parents": [{}],
                    }
                ],
                "issue_comments": [],
                "review_comments": [],
                "reviews": [],
            },
            "author",
            {"reviewer"},
        )

        self.assertEqual(events[0]["actor"], "author")
        self.assertEqual(events[0]["timestamp"], "2026-07-14T03:00:00Z")

    def test_description_edit_advances_description_action(self) -> None:
        discussions = [top_level_item("description")]
        classifications = [classification("description", "description")]
        metadata = {
            "lastEditedAt": "2026-07-14T03:00:00Z",
            "editor": {"login": "author"},
        }

        pending_actions, top_level_history = advance_top_level_actions(
            discussions, classifications, [], metadata, "author", None, []
        )

        self.assertNotIn("description", pending_actions)
        self.assertEqual(
            top_level_history["description"]["evidence"],
            {"description": "2026-07-14T03:00:00Z"},
        )
        self.assertEqual(classifications[0]["decision"]["discussion_action"], "author")

    def test_description_evidence_survives_later_non_author_edit(self) -> None:
        discussions = [top_level_item("description")]
        first_classifications = [classification("description", "description")]
        _first_pending_actions, first_history = advance_top_level_actions(
            discussions,
            first_classifications,
            [],
            {
                "lastEditedAt": "2026-07-14T03:00:00Z",
                "editor": {"login": "author"},
            },
            "author",
            None,
            [],
        )
        classifications = [classification("description", "description")]

        pending_actions, top_level_history = advance_top_level_actions(
            discussions,
            classifications,
            [],
            {
                "lastEditedAt": "2026-07-14T04:00:00Z",
                "editor": {"login": "maintainer"},
            },
            "author",
            first_history,
            [],
        )

        self.assertNotIn("description", pending_actions)
        self.assertEqual(
            top_level_history["description"]["evidence"],
            {"description": "2026-07-14T03:00:00Z"},
        )

    def test_unclear_item_preserves_description_evidence_for_later_classification(self) -> None:
        discussions = [top_level_item("description")]
        unclear_classification = classification("description", "description")
        unclear_classification["decision"]["discussion_action"] = "unclear"

        pending_actions, first_history = advance_top_level_actions(
            discussions,
            [unclear_classification],
            [],
            {
                "lastEditedAt": "2026-07-14T03:00:00Z",
                "editor": {"login": "author"},
            },
            "author",
            None,
            [],
        )

        self.assertEqual(pending_actions["description"]["action"], "reviewer")
        self.assertEqual(
            first_history["description"]["evidence"],
            {"description": "2026-07-14T03:00:00Z"},
        )

        refreshed_pending_actions, refreshed_history = advance_top_level_actions(
            discussions,
            [classification("description", "description")],
            [],
            {
                "lastEditedAt": "2026-07-14T04:00:00Z",
                "editor": {"login": "maintainer"},
            },
            "author",
            first_history,
            [],
        )

        self.assertNotIn("description", refreshed_pending_actions)
        self.assertEqual(refreshed_history, first_history)

    def test_persisted_description_evidence_keeps_item_addressed(self) -> None:
        discussions = [top_level_item("description")]
        classifications = [classification("description", "description")]
        previous_state = {
            "description": top_level_history_record("description", "2026-07-14T03:00:00Z"),
        }
        pending_actions, top_level_history = advance_top_level_actions(
            discussions,
            classifications,
            [],
            {},
            "author",
            previous_state,
            [],
        )

        self.assertNotIn("description", pending_actions)
        self.assertEqual(
            top_level_history["description"]["evidence"],
            {"description": "2026-07-14T03:00:00Z"},
        )

        refreshed_classifications = [classification("description", "description")]
        refreshed_pending_actions, refreshed_history = advance_top_level_actions(
            discussions,
            refreshed_classifications,
            [],
            {
                "lastEditedAt": "2026-07-14T05:00:00Z",
                "editor": {"login": "maintainer"},
            },
            "author",
            top_level_history,
            [],
        )

        self.assertNotIn("description", refreshed_pending_actions)
        self.assertEqual(
            refreshed_history["description"]["evidence"],
            {"description": "2026-07-14T03:00:00Z"},
        )

    def test_same_refresh_description_evidence_is_persisted(self) -> None:
        discussions = [top_level_item("description")]
        classifications = [classification("description", "description")]
        confirmation = event(
            "review-state",
            "2026-07-14T04:00:00Z",
            "reviewer",
            "approver",
            state="APPROVED",
        )

        pending_actions, top_level_history = advance_top_level_actions(
            discussions,
            classifications,
            [confirmation],
            {
                "lastEditedAt": "2026-07-14T03:00:00Z",
                "editor": {"login": "author"},
            },
            "author",
            None,
            [],
        )

        self.assertNotIn("description", pending_actions)
        self.assertEqual(
            top_level_history["description"]["evidence"],
            {"description": "2026-07-14T03:00:00Z"},
        )

        refreshed_classifications = [classification("description", "description")]
        refreshed_pending_actions, refreshed_history = advance_top_level_actions(
            discussions,
            refreshed_classifications,
            [],
            {
                "lastEditedAt": "2026-07-14T05:00:00Z",
                "editor": {"login": "maintainer"},
            },
            "author",
            top_level_history,
            [],
        )

        self.assertNotIn("description", refreshed_pending_actions)
        self.assertEqual(
            refreshed_history["description"]["evidence"],
            {"description": "2026-07-14T03:00:00Z"},
        )

    def test_evidence_before_edited_root_is_not_reused(self) -> None:
        discussions = [top_level_item("description")]
        classifications = [classification("description", "description")]

        pending_actions, top_level_history = advance_top_level_actions(
            discussions,
            classifications,
            [],
            {},
            "author",
            {
                "description": top_level_history_record("description", "2026-07-14T00:00:00Z"),
            },
            [],
        )

        self.assertEqual(pending_actions["description"]["action"], "author")
        self.assertNotIn("description", top_level_history)

    def test_stored_other_evidence_does_not_satisfy_current_decision(self) -> None:
        classifications = [classification("code", "commit")]

        pending_actions, top_level_history = advance_top_level_actions(
            [top_level_item("code")],
            classifications,
            [],
            {},
            "author",
            {
                "code": top_level_history_record("description", "2026-07-14T03:00:00Z"),
            },
            [],
        )

        self.assertEqual(pending_actions["code"]["action"], "author")
        self.assertEqual(
            top_level_history["code"]["evidence"],
            {"description": "2026-07-14T03:00:00Z"},
        )

    def test_review_state_does_not_change_action_lifecycle(self) -> None:
        evidence_events = {
            "commit": event("commit", "2026-07-14T03:00:00Z", "author", "author"),
            "reply": event(
                "issue-comment",
                "2026-07-14T03:00:00Z",
                "author",
                "author",
                created_timestamp="2026-07-14T03:00:00Z",
            ),
        }
        for evidence_kind, evidence_event in evidence_events.items():
            with self.subTest(evidence_kind=evidence_kind):
                discussion = top_level_item("code")
                discussion["review_state"] = "CHANGES_REQUESTED"
                pending_actions, top_level_history = advance_top_level_actions(
                    [discussion],
                    [classification("code", "commit")],
                    [evidence_event],
                    {},
                    "author",
                    None,
                    (
                        [author_comment_outcome("code", "2026-07-14T03:00:00Z")]
                        if evidence_kind == "reply"
                        else []
                    ),
                )

                self.assertEqual(pending_actions, {})
                self.assertEqual(
                    top_level_history["code"]["evidence"],
                    {evidence_kind: "2026-07-14T03:00:00Z"},
                )

    def test_reviewer_activity_does_not_close_ordinary_item_without_author_evidence(self) -> None:
        events = normalize_events(
            {
                "commits": [],
                "issue_comments": [],
                "review_comments": [],
                "reviews": [
                    {
                        "id": 202,
                        "submitted_at": "2026-07-14T00:00:00Z",
                        "updated_at": "2026-07-14T03:00:00Z",
                        "user": {"login": "reviewer"},
                        "state": "COMMENTED",
                        "body": "This is addressed.",
                    }
                ],
            },
            "author",
            {"reviewer"},
        )
        classifications = [classification("code", "commit")]

        pending_actions, top_level_history = advance_top_level_actions(
            [top_level_item("code")],
            classifications,
            events,
            {},
            "author",
            None,
            [],
        )

        self.assertEqual(events[0]["timestamp"], "2026-07-14T00:00:00Z")
        self.assertEqual(pending_actions["code"]["action"], "author")
        self.assertNotIn("code", top_level_history)

    def test_approval_does_not_close_ordinary_item_without_author_evidence(self) -> None:
        discussions = [top_level_item("code")]
        classifications = [classification("code", "commit")]
        events = [
            event(
                "review-state",
                "2026-07-14T03:00:00Z",
                "reviewer",
                "approver",
                state="APPROVED",
            ),
        ]

        pending_actions, top_level_history = advance_top_level_actions(
            discussions, classifications, events, {}, "author", None, []
        )

        self.assertEqual(pending_actions["code"]["action"], "author")
        self.assertNotIn("code", top_level_history)
        self.assertEqual(classifications[0]["decision"]["discussion_action"], "author")

    def test_later_author_evidence_closes_ordinary_item(self) -> None:
        discussions = [top_level_item("code")]
        confirmation = event(
            "review-state",
            "2026-07-14T03:00:00Z",
            "reviewer",
            "approver",
            state="APPROVED",
        )
        _first_pending_actions, first_history = advance_top_level_actions(
            discussions,
            [classification("code", "commit")],
            [confirmation],
            {},
            "author",
            None,
            [],
        )

        refreshed_pending_actions, refreshed_history = advance_top_level_actions(
            discussions,
            [classification("code", "commit")],
            [confirmation, event("commit", "2026-07-14T04:00:00Z", "author", "author")],
            {},
            "author",
            first_history,
            [],
        )

        self.assertNotIn("code", refreshed_pending_actions)
        self.assertEqual(
            refreshed_history["code"]["evidence"],
            {"commit": "2026-07-14T04:00:00Z"},
        )

    def test_later_actionable_request_does_not_confirm_older_item(self) -> None:
        discussions = [
            top_level_item("first", source_kind="issue-comment", source_id=101),
            top_level_item("second", source_kind="issue-comment", source_id=102),
        ]
        classifications = [
            classification("first", "commit"),
            classification("second", "commit"),
        ]
        events = [
            event(
                "issue-comment",
                "2026-07-14T03:00:00Z",
                "reviewer",
                "approver",
                source_id=102,
            ),
        ]

        pending_actions, top_level_history = advance_top_level_actions(
            discussions, classifications, events, {}, "author", None, []
        )

        self.assertEqual(pending_actions["first"]["action"], "author")
        self.assertNotIn("first", top_level_history)
        self.assertEqual(classifications[0]["decision"]["discussion_action"], "author")

    def test_filtered_conflict_request_does_not_confirm_older_item(self) -> None:
        discussions = [
            top_level_item("request", source_kind="issue-comment", source_id=101),
        ]
        classifications = [classification("request", "commit")]
        events = [
            event("commit", "2026-07-14T02:00:00Z", "author", "author"),
            event(
                "issue-comment",
                "2026-07-14T03:00:00Z",
                "reviewer",
                "approver",
                source_id=102,
                body="Please resolve the merge conflict.",
            ),
        ]

        pending_actions, top_level_history = advance_top_level_actions(
            discussions, classifications, events, {}, "author", None, []
        )

        self.assertNotIn("request", pending_actions)
        self.assertEqual(classifications[0]["decision"]["discussion_action"], "author")
        self.assertEqual(top_level_history["request"]["evidence"], {"commit": "2026-07-14T02:00:00Z"})

    def test_later_reviewer_acknowledgement_does_not_address_older_item(self) -> None:
        discussions = [
            top_level_item("request", source_kind="issue-comment", source_id=101),
            top_level_item("ack", source_kind="issue-comment", source_id=102),
        ]
        classifications = [
            classification("request", "commit"),
            {
                "discussion_id": "ack",
                "discussion_kind": "top-level-feedback",
                "decision": {"discussion_action": "none", "required_evidence_kinds": []},
            },
        ]
        events = [
            event(
                "issue-comment",
                "2026-07-14T03:00:00Z",
                "reviewer",
                "approver",
                source_id=102,
            ),
        ]

        pending_actions, top_level_history = advance_top_level_actions(
            discussions, classifications, events, {}, "author", None, []
        )

        self.assertEqual(pending_actions["request"]["action"], "author")
        self.assertNotIn("request", top_level_history)
        self.assertEqual(classifications[0]["decision"]["discussion_action"], "author")

    def test_repeated_changes_requested_review_does_not_close_item(self) -> None:
        discussions = [top_level_item("code")]
        discussions[0]["review_state"] = "CHANGES_REQUESTED"
        classifications = [classification("code", "commit")]
        events = [
            event(
                "review-state",
                "2026-07-14T03:00:00Z",
                "reviewer",
                "approver",
                state="CHANGES_REQUESTED",
            ),
        ]

        pending_actions, top_level_history = advance_top_level_actions(
            discussions, classifications, events, {}, "author", None, []
        )

        self.assertEqual(pending_actions["code"]["action"], "author")
        self.assertNotIn("code", top_level_history)
        self.assertEqual(classifications[0]["decision"]["discussion_action"], "author")

    def test_review_state_does_not_block_routing_after_author_evidence(self) -> None:
        discussions = [top_level_item("code")]
        discussions[0]["review_state"] = "CHANGES_REQUESTED"
        classifications = [classification("code", "commit")]
        events = [
            event("commit", "2026-07-14T02:00:00Z", "author", "author"),
            event(
                "review-state",
                "2026-07-14T03:00:00Z",
                "reviewer",
                "approver",
                state="COMMENTED",
            ),
            event(
                "review-state",
                "2026-07-14T04:00:00Z",
                "other-reviewer",
                "approver",
                state="APPROVED",
            ),
        ]
        facts = {"approval_count": 1, "is_maintenance_bot": False}

        pending_actions, top_level_history = advance_top_level_actions(
            discussions, classifications, events, {}, "author", None, []
        )

        self.assertEqual(pending_actions, {})
        self.assertIn("evidence", top_level_history["code"])
        self.assertEqual(route_pr(facts, pending_actions, 1), "maintainer")

    def test_reviewer_activity_does_not_close_external_and_unclear_items(self) -> None:
        events = [
            event(
                "issue-comment",
                "2026-07-14T03:00:00Z",
                "reviewer",
                "approver",
            ),
        ]
        for action in ("external", "unclear"):
            with self.subTest(action=action):
                discussions = [top_level_item(action)]
                classifications = [classification(action, action)]
                classifications[0]["decision"]["discussion_action"] = action

                pending_actions, top_level_history = advance_top_level_actions(
                    discussions, classifications, events, {}, "author", None, []
                )

                expected_action = "external" if action == "external" else "reviewer"
                self.assertEqual(pending_actions[action]["action"], expected_action)
                self.assertNotIn(action, top_level_history)
                self.assertEqual(classifications[0]["decision"]["discussion_action"], action)

    def test_author_reply_closes_external_and_unclear_items(self) -> None:
        events = [
            event(
                "issue-comment",
                "2026-07-14T03:00:00Z",
                "author",
                "author",
                created_timestamp="2026-07-14T03:00:00Z",
            ),
        ]
        for action in ("external", "unclear"):
            with self.subTest(action=action):
                discussions = [top_level_item(action)]
                classifications = [classification(action, action)]
                classifications[0]["decision"]["discussion_action"] = action

                pending_actions, top_level_history = advance_top_level_actions(
                    discussions,
                    classifications,
                    events,
                    {},
                    "author",
                    None,
                    [author_comment_outcome(action, "2026-07-14T03:00:00Z")],
                )

                self.assertNotIn(action, pending_actions)
                self.assertEqual(
                    top_level_history[action]["evidence"],
                    {"reply": "2026-07-14T03:00:00Z"},
                )

    def test_changes_requested_is_visual_only_after_action_clears(self) -> None:
        discussions = [top_level_item("code")]
        discussions[0]["review_state"] = "CHANGES_REQUESTED"
        discussions[0]["comments"] = [
            event("issue-comment", ROOT_TIMESTAMP, "reviewer", "approver"),
        ]
        classifications = [classification("code", "commit")]
        pending_actions = {}
        facts = {"approval_count": 1, "is_maintenance_bot": False, "assignees": []}
        events = [
            event(
                "review-state",
                ROOT_TIMESTAMP,
                "reviewer",
                "approver",
                state="CHANGES_REQUESTED",
            )
        ]

        self.assertEqual(route_pr(facts, pending_actions, 1), "maintainer")
        add_reviewers(facts, events, [], discussions, pending_actions)
        reviewer = facts["reviewers"][0]
        self.assertFalse(reviewer["top_level_feedback"])
        self.assertFalse(reviewer["open_thread"])
        self.assertEqual(reviewer_icon(reviewer), "🔴")
        self.assertEqual(reviewer_logins_for_notification(facts), ["reviewer"])

    def test_outsider_changes_requested_reviewer_remains_visible(self) -> None:
        discussions = [top_level_item("code", requester="outsider")]
        discussions[0]["review_state"] = "CHANGES_REQUESTED"
        pending_actions = {}
        facts = {"approval_count": 0, "is_maintenance_bot": False, "assignees": []}
        events = [
            event(
                "review-state",
                ROOT_TIMESTAMP,
                "outsider",
                "outsider",
                state="CHANGES_REQUESTED",
            )
        ]

        self.assertEqual(route_pr(facts, pending_actions, 1), "approver")
        add_reviewers(facts, events, [], discussions, pending_actions)

        reviewer = facts["reviewers"][0]
        self.assertEqual(reviewer["login"], "outsider")
        self.assertTrue(reviewer["changes_requested"])
        self.assertFalse(reviewer["top_level_feedback"])
        self.assertEqual(reviewer_icon(reviewer), "🔴")
        self.assertEqual(reviewer_logins_for_notification(facts), ["outsider"])

    def test_inline_and_top_level_feedback_keep_both_badges(self) -> None:
        top_level = top_level_item("top_level")
        top_level["comments"] = [
            event("issue-comment", ROOT_TIMESTAMP, "reviewer", "approver"),
        ]
        inline = {
            "discussion_id": "inline",
            "discussion_kind": "review-comment-thread",
            "comments": [
                event("review-comment", ROOT_TIMESTAMP, "reviewer", "approver"),
            ],
        }
        classifications = [classification("top_level", "commit")]
        classifications.append(
            {
                "discussion_id": "inline",
                "discussion_kind": "review-comment-thread",
                "decision": {"discussion_action": "author", "reason": "inline request"},
            }
        )
        facts = {"assignees": []}
        pending_actions = {
            "top_level": {"action": "author", "since": ROOT_TIMESTAMP},
            "inline": {"action": "author", "since": ROOT_TIMESTAMP},
        }

        add_reviewers(facts, [], [inline], [top_level], pending_actions)

        reviewer = facts["reviewers"][0]
        self.assertTrue(reviewer["top_level_feedback"])
        self.assertTrue(reviewer["open_thread"])
        self.assertEqual(reviewer_icon(reviewer), "💬\u2060📌")


if __name__ == "__main__":
    unittest.main()