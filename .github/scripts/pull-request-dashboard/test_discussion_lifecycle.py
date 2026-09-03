from __future__ import annotations

import unittest

from classification_policy import (
    ActionDecision,
    AuthorCommentDecision,
    ClassificationDeferred,
    ClassificationDiagnostics,
    ClassificationDiscussion,
    ClassificationFailure,
    ClassificationSuccess,
    DiscussionAction,
    DiscussionClassifications,
    DiscussionIdentity,
    DiscussionKind,
    FeedbackOutcome,
    reviewer_feedback_prompt_input,
)
from discussion_lifecycle import (
    DiscussionInput,
    LifecycleMode,
    PreparedDiscussions,
    prepare_discussions,
    reviewer_handoff_feedback,
    resolve_discussions,
)
from pull_request_source import normalize_review_threads


ROOT_TIMESTAMP = "2026-07-14T01:00:00Z"


def top_level_item(
    discussion_id: str,
    timestamp: str = ROOT_TIMESTAMP,
) -> dict:
    return {
        "discussion_id": discussion_id,
        "discussion_kind": "top-level-feedback",
        "root_timestamp": timestamp,
        "comments": [
            {
                "timestamp": timestamp,
                "actor": "reviewer",
                "actor_role": "approver",
                "body": "Please update this.",
            }
        ],
    }


def review_thread(
    discussion_id: str,
    timestamp: str = ROOT_TIMESTAMP,
) -> dict:
    return {
        "discussion_id": discussion_id,
        "discussion_kind": "review-comment-thread",
        "comments": [
            {
                "timestamp": timestamp,
                "actor": "reviewer",
                "actor_role": "approver",
                "body": "Please update this.",
            }
        ],
    }


def author_reply(
    source_id: int,
    timestamp: str,
    *feedback_ids: str,
) -> dict:
    return {
        "discussion_id": f"pr-author-reply-{source_id}",
        "discussion_kind": "top-level-author-reply",
        "source_id": source_id,
        "candidate_feedback": [
            {"discussion_id": feedback_id, "body": "Please update this."}
            for feedback_id in feedback_ids
        ],
        "comments": [
            {
                "timestamp": timestamp,
                "actor": "author",
                "actor_role": "author",
                "body": "I handled this.",
            }
        ],
    }


def classification(
    discussion_id: str,
    action: str,
) -> ClassificationSuccess:
    kind = (
        DiscussionKind.REVIEW_THREAD
        if discussion_id.startswith("thread-")
        else DiscussionKind.TOP_LEVEL_FEEDBACK
    )
    return ClassificationSuccess(
        DiscussionIdentity(discussion_id, kind),
        ActionDecision(
            DiscussionAction(action),
            "Test classification.",
        ),
    )


def author_reply_classification(
    source_id: int,
    *feedback_actions: tuple[str, str],
    deferred: bool = False,
) -> ClassificationSuccess | ClassificationDeferred:
    identity = DiscussionIdentity(
        f"pr-author-reply-{source_id}",
        DiscussionKind.TOP_LEVEL_AUTHOR_REPLY,
    )
    decision = AuthorCommentDecision(tuple(
        FeedbackOutcome(
            feedback_id,
            DiscussionAction(action),
            "Test reply classification.",
        )
        for feedback_id, action in feedback_actions
    ))
    if deferred:
        return ClassificationDeferred(identity, decision)
    return ClassificationSuccess(identity, decision)


class PrepareDiscussionsTest(unittest.TestCase):
    def test_prepares_threads_feedback_and_author_replies(self) -> None:
        prepared = prepare_discussions(
            DiscussionInput(
                normalize_review_threads((
                    {
                        "id": "thread-1",
                        "isResolved": False,
                        "isOutdated": False,
                        "path": "src/example.py",
                        "line": 7,
                        "comments": {
                            "nodes": [
                                {
                                    "url": "https://example.test/thread/first",
                                    "body": "Fixed it.",
                                    "createdAt": "2026-07-14T03:00:00Z",
                                    "author": {"login": "author"},
                                },
                                {
                                    "url": "https://example.test/thread/root",
                                    "body": "Please fix this.",
                                    "createdAt": ROOT_TIMESTAMP,
                                    "author": {"login": "copilot"},
                                },
                            ]
                        },
                    },
                )),
                (
                    {
                        "kind": "issue-comment",
                        "source_id": 201,
                        "created_timestamp": "2026-07-14T02:00:00Z",
                        "timestamp": "2026-07-14T05:00:00Z",
                        "actor": "reviewer",
                        "actor_role": "approver",
                        "body": "Please update the description.",
                        "discussion_url": "https://example.test/comment/201",
                    },
                    {
                        "kind": "issue-comment",
                        "source_id": 301,
                        "created_timestamp": "2026-07-14T04:00:00Z",
                        "timestamp": "2026-07-14T06:00:00Z",
                        "actor": "author",
                        "actor_role": "author",
                        "body": "I updated the description.",
                    },
                    {
                        "kind": "issue-comment",
                        "source_id": 202,
                        "created_timestamp": "2026-07-14T05:00:00Z",
                        "timestamp": "2026-07-14T07:00:00Z",
                        "actor": "reviewer",
                        "actor_role": "approver",
                        "body": "Please add another test.",
                        "discussion_url": "https://example.test/comment/202",
                    },
                ),
                "author",
                frozenset({"reviewer"}),
                "no",
            )
        )

        self.assertEqual(
            [thread["discussion_id"] for thread in prepared.review_threads],
            ["thread-1"],
        )
        self.assertEqual(
            [comment["body"] for comment in prepared.review_threads[0]["comments"]],
            ["Please fix this.", "Fixed it."],
        )
        self.assertEqual(
            prepared.review_threads[0]["discussion_url"],
            "https://example.test/thread/root",
        )
        self.assertTrue(prepared.review_threads[0]["strict_author_action"])
        self.assertEqual(
            [item["discussion_id"] for item in prepared.top_level_items],
            ["pr-issue-comment-201", "pr-issue-comment-202"],
        )
        self.assertEqual(
            prepared.top_level_author_comment_items[0]["candidate_feedback"],
            [
                {
                    "discussion_id": "pr-issue-comment-201",
                    "body": "Please update the description.",
                }
            ],
        )

    def test_orders_discussions_by_creation_time_not_edit_time(self) -> None:
        prepared = prepare_discussions(
            DiscussionInput(
                normalize_review_threads((
                    {
                        "id": "later-thread",
                        "isResolved": False,
                        "isOutdated": False,
                        "comments": {
                            "nodes": [
                                {
                                    "url": "https://example.test/thread/later",
                                    "body": "Later request.",
                                    "createdAt": "2026-07-14T04:00:00Z",
                                    "updatedAt": "2026-07-14T04:00:00Z",
                                    "author": {"login": "reviewer"},
                                }
                            ]
                        },
                    },
                    {
                        "id": "earlier-thread",
                        "isResolved": False,
                        "isOutdated": False,
                        "comments": {
                            "nodes": [
                                {
                                    "url": "https://example.test/thread/earlier",
                                    "body": "Earlier request edited later.",
                                    "createdAt": ROOT_TIMESTAMP,
                                    "updatedAt": "2026-07-14T07:00:00Z",
                                    "author": {"login": "reviewer"},
                                }
                            ]
                        },
                    },
                )),
                (
                    {
                        "kind": "issue-comment",
                        "source_id": 102,
                        "created_timestamp": "2026-07-14T03:00:00Z",
                        "timestamp": "2026-07-14T03:00:00Z",
                        "actor": "reviewer",
                        "actor_role": "approver",
                        "body": "Newer feedback.",
                    },
                    {
                        "kind": "issue-comment",
                        "source_id": 101,
                        "created_timestamp": "2026-07-14T02:00:00Z",
                        "timestamp": "2026-07-14T08:00:00Z",
                        "actor": "reviewer",
                        "actor_role": "approver",
                        "body": "Older feedback edited later.",
                    },
                ),
                "author",
                frozenset({"reviewer"}),
                "no",
            )
        )

        self.assertEqual(
            [thread["discussion_id"] for thread in prepared.review_threads],
            ["earlier-thread", "later-thread"],
        )
        self.assertEqual(
            [item["discussion_id"] for item in prepared.top_level_items],
            ["pr-issue-comment-101", "pr-issue-comment-102"],
        )
        self.assertEqual(
            [item["root_timestamp"] for item in prepared.top_level_items],
            ["2026-07-14T02:00:00Z", "2026-07-14T03:00:00Z"],
        )

    def test_edited_author_response_uses_its_content_timestamp(self) -> None:
        prepared = prepare_discussions(
            DiscussionInput(
                (),
                (
                    {
                        "kind": "issue-comment",
                        "source_id": 201,
                        "created_timestamp": "2026-07-14T02:00:00Z",
                        "timestamp": "2026-07-14T02:00:00Z",
                        "actor": "reviewer",
                        "actor_role": "approver",
                        "body": "Please update the description.",
                    },
                    {
                        "kind": "issue-comment",
                        "source_id": 301,
                        "created_timestamp": "2026-07-14T03:00:00Z",
                        "timestamp": "2026-07-14T06:00:00Z",
                        "actor": "author",
                        "actor_role": "author",
                        "body": "Both requests are complete.",
                    },
                    {
                        "kind": "issue-comment",
                        "source_id": 202,
                        "created_timestamp": "2026-07-14T05:00:00Z",
                        "timestamp": "2026-07-14T05:00:00Z",
                        "actor": "reviewer",
                        "actor_role": "approver",
                        "body": "Please add another test.",
                    },
                ),
                "author",
                frozenset({"reviewer"}),
                "no",
            )
        )

        reply = prepared.top_level_author_comment_items[0]
        self.assertEqual("2026-07-14T06:00:00Z", reply["comments"][0]["timestamp"])
        self.assertEqual(
            ["pr-issue-comment-201", "pr-issue-comment-202"],
            [
                feedback["discussion_id"]
                for feedback in reply["candidate_feedback"]
            ],
        )

    def test_author_response_excludes_feedback_edited_after_it(self) -> None:
        prepared = prepare_discussions(
            DiscussionInput(
                (),
                (
                    {
                        "kind": "issue-comment",
                        "source_id": 201,
                        "created_timestamp": "2026-07-14T02:00:00Z",
                        "timestamp": "2026-07-14T07:00:00Z",
                        "actor": "reviewer",
                        "actor_role": "approver",
                        "body": "Please also update the tests.",
                    },
                    {
                        "kind": "issue-comment",
                        "source_id": 301,
                        "created_timestamp": "2026-07-14T03:00:00Z",
                        "timestamp": "2026-07-14T06:00:00Z",
                        "actor": "author",
                        "actor_role": "author",
                        "body": "The original request is complete.",
                    },
                ),
                "author",
                frozenset({"reviewer"}),
                "no",
            )
        )

        self.assertEqual(
            [],
            prepared.top_level_author_comment_items[0]["candidate_feedback"],
        )

    def test_top_level_cutoff_retires_only_unchanged_old_feedback(self) -> None:
        prepared = prepare_discussions(
            DiscussionInput(
                normalize_review_threads(({
                    "id": "old-thread",
                    "isResolved": False,
                    "isOutdated": False,
                    "comments": {
                        "nodes": [{
                            "url": "https://example.test/thread/old",
                            "body": "Please update the implementation.",
                            "createdAt": "2026-07-14T01:00:00Z",
                            "author": {"login": "reviewer"},
                        }],
                    },
                },)),
                (
                    {
                        "kind": "issue-comment",
                        "source_id": 201,
                        "created_timestamp": "2026-07-14T01:00:00Z",
                        "timestamp": "2026-07-14T01:00:00Z",
                        "actor": "reviewer",
                        "actor_role": "approver",
                        "body": "Old top-level request.",
                    },
                    {
                        "kind": "issue-comment",
                        "source_id": 202,
                        "created_timestamp": "2026-07-14T02:00:00Z",
                        "timestamp": "2026-07-14T05:00:00Z",
                        "actor": "reviewer",
                        "actor_role": "approver",
                        "body": "Edited top-level request.",
                    },
                    {
                        "kind": "review-state",
                        "source_id": 203,
                        "created_timestamp": "2026-07-14T03:00:00Z",
                        "timestamp": "2026-07-14T03:00:00Z",
                        "content_timestamp": "2026-07-14T05:00:00Z",
                        "actor": "reviewer",
                        "actor_role": "approver",
                        "state": "COMMENTED",
                        "body": "Edited review summary.",
                    },
                    {
                        "kind": "issue-comment",
                        "source_id": 204,
                        "created_timestamp": "2026-07-14T04:00:00Z",
                        "timestamp": "2026-07-14T04:00:00Z",
                        "actor": "reviewer",
                        "actor_role": "approver",
                        "body": "Same-second top-level request.",
                    },
                    {
                        "kind": "issue-comment",
                        "source_id": 301,
                        "created_timestamp": "2026-07-14T06:00:00Z",
                        "timestamp": "2026-07-14T06:00:00Z",
                        "actor": "author",
                        "actor_role": "author",
                        "body": "I addressed the current requests.",
                    },
                ),
                "author",
                frozenset({"reviewer"}),
                "no",
            ),
            top_level_feedback_cutoff="2026-07-14T04:00:00Z",
        )

        self.assertEqual(
            ["old-thread"],
            [thread["discussion_id"] for thread in prepared.review_threads],
        )
        self.assertEqual(
            ["pr-issue-comment-202", "pr-review-203"],
            [item["discussion_id"] for item in prepared.top_level_items],
        )
        self.assertEqual(
            ["pr-issue-comment-202", "pr-review-203"],
            [
                item["discussion_id"]
                for item in prepared.top_level_author_comment_items[0][
                    "candidate_feedback"
                ]
            ],
        )

    def test_ignores_author_only_review_threads(self) -> None:
        prepared = prepare_discussions(
            DiscussionInput(
                normalize_review_threads((
                    {
                        "id": "author-note",
                        "isResolved": False,
                        "isOutdated": False,
                        "comments": {
                            "nodes": [
                                {
                                    "url": "https://example.test/thread/note",
                                    "body": "Todo: automate this later.",
                                    "createdAt": ROOT_TIMESTAMP,
                                    "author": {"login": "author"},
                                }
                            ]
                        },
                    },
                )),
                (),
                "author",
                frozenset({"reviewer"}),
                "no",
            )
        )

        self.assertEqual(prepared.review_threads, ())


class ResolveDiscussionsTest(unittest.TestCase):
    def test_invalid_review_decision_names_the_classification(self) -> None:
        invalid = ClassificationSuccess(
            DiscussionIdentity(
                "thread-invalid",
                DiscussionKind.REVIEW_THREAD,
            ),
            AuthorCommentDecision(),
        )

        with self.assertRaises(TypeError) as caught:
            resolve_discussions(
                PreparedDiscussions(
                    (review_thread("thread-invalid"),),
                    (),
                    (),
                ),
                DiscussionClassifications((invalid,), (), ()),
            )

        self.assertEqual(
            str(caught.exception),
            "review-thread classification 'thread-invalid' "
            "(review-comment-thread) requires ActionDecision, "
            "got AuthorCommentDecision",
        )

    def test_invalid_top_level_decision_names_the_classification(
        self,
    ) -> None:
        invalid = ClassificationSuccess(
            DiscussionIdentity(
                "feedback-invalid",
                DiscussionKind.TOP_LEVEL_FEEDBACK,
            ),
            AuthorCommentDecision(),
        )

        with self.assertRaises(TypeError) as caught:
            resolve_discussions(
                PreparedDiscussions(
                    (),
                    (top_level_item("feedback-invalid"),),
                    (),
                ),
                DiscussionClassifications((), (invalid,), ()),
            )

        self.assertEqual(
            str(caught.exception),
            "top-level classification 'feedback-invalid' "
            "(top-level-feedback) requires ActionDecision, "
            "got AuthorCommentDecision",
        )

    def test_projects_pending_actions_for_each_classification(self) -> None:
        prepared = PreparedDiscussions(
            (
                review_thread("thread-author"),
                review_thread("thread-unclear"),
                review_thread("thread-none"),
            ),
            (
                top_level_item("feedback-author"),
                top_level_item("feedback-unclear"),
                top_level_item("feedback-none"),
            ),
            (),
        )
        outcome = resolve_discussions(
            prepared,
            DiscussionClassifications(
                (
                    classification("thread-author", "author"),
                    classification("thread-unclear", "unclear"),
                    classification("thread-none", "none"),
                ),
                (
                    classification("feedback-author", "author"),
                    classification("feedback-unclear", "unclear"),
                    classification("feedback-none", "none"),
                ),
                (),
            ),
        )

        self.assertEqual(
            outcome.pending_actions,
            {
                "thread-author": {
                    "action": "author",
                    "since": ROOT_TIMESTAMP,
                },
                "thread-unclear": {
                    "action": "author",
                    "since": ROOT_TIMESTAMP,
                },
                "feedback-author": {
                    "action": "author",
                    "since": ROOT_TIMESTAMP,
                },
                "feedback-unclear": {
                    "action": "author",
                    "since": ROOT_TIMESTAMP,
                },
            },
        )

    def test_completed_author_reply_records_history(self) -> None:
        prepared = PreparedDiscussions(
            (),
            (top_level_item("question"),),
            (author_reply(102, "2026-07-14T03:00:00Z", "question"),),
        )
        outcome = resolve_discussions(
            prepared,
            DiscussionClassifications(
                (),
                (classification("question", "author"),),
                (author_reply_classification(102, ("question", "none")),),
            ),
        )

        self.assertEqual(outcome.pending_actions, {})
        self.assertEqual(
            outcome.top_level_history,
            {
                "question": {
                    "evidence": {"reply": "2026-07-14T03:00:00Z"},
                    "reply_source_id": 102,
                }
            },
        )

    def test_invalid_author_reply_decision_names_the_classification(
        self,
    ) -> None:
        invalid = ClassificationSuccess(
            DiscussionIdentity(
                "pr-author-reply-102",
                DiscussionKind.TOP_LEVEL_AUTHOR_REPLY,
            ),
            ActionDecision(
                DiscussionAction.AUTHOR,
                "Wrong decision type.",
            ),
        )

        with self.assertRaises(TypeError) as caught:
            resolve_discussions(
                PreparedDiscussions(
                    (),
                    (),
                    (author_reply(102, ROOT_TIMESTAMP),),
                ),
                DiscussionClassifications((), (), (invalid,)),
            )

        self.assertEqual(
            str(caught.exception),
            "author-comment classification 'pr-author-reply-102' "
            "(top-level-author-reply) requires AuthorCommentDecision, "
            "got ActionDecision",
        )

    def test_author_reply_applies_each_feedback_outcome(self) -> None:
        prepared = PreparedDiscussions(
            (),
            (top_level_item("first"), top_level_item("second")),
            (
                author_reply(
                    102,
                    "2026-07-14T03:00:00Z",
                    "first",
                    "second",
                ),
            ),
        )
        outcome = resolve_discussions(
            prepared,
            DiscussionClassifications(
                (),
                (
                    classification("first", "author"),
                    classification("second", "author"),
                ),
                (
                    author_reply_classification(
                        102,
                        ("first", "none"),
                        ("second", "author"),
                    ),
                ),
            ),
        )

        self.assertEqual(
            outcome.pending_actions,
            {
                "second": {
                    "action": "author",
                    "since": "2026-07-14T03:00:00Z",
                }
            },
        )
        self.assertEqual(
            outcome.top_level_history["first"],
            {
                "evidence": {"reply": "2026-07-14T03:00:00Z"},
                "reply_source_id": 102,
            },
        )

    def test_review_state_does_not_change_action_lifecycle(self) -> None:
        for review_state in ("CHANGES_REQUESTED", "APPROVED"):
            with self.subTest(review_state=review_state):
                discussion = top_level_item("code")
                discussion["review_state"] = review_state
                open_outcome = resolve_discussions(
                    PreparedDiscussions((), (discussion,), ()),
                    DiscussionClassifications(
                        (),
                        (classification("code", "author"),),
                        (),
                    ),
                )
                closed_outcome = resolve_discussions(
                    PreparedDiscussions(
                        (),
                        (discussion,),
                        (author_reply(102, "2026-07-14T03:00:00Z", "code"),),
                    ),
                    DiscussionClassifications(
                        (),
                        (classification("code", "author"),),
                        (author_reply_classification(102, ("code", "none")),),
                    ),
                )

                self.assertEqual(
                    open_outcome.pending_actions["code"]["action"],
                    "author",
                )
                self.assertNotIn("code", open_outcome.top_level_history)
                self.assertEqual(closed_outcome.pending_actions, {})
                self.assertEqual(
                    closed_outcome.top_level_history["code"]["evidence"],
                    {"reply": "2026-07-14T03:00:00Z"},
                )

    def test_author_reply_closes_unclear_items(self) -> None:
        outcome = resolve_discussions(
            PreparedDiscussions(
                (),
                (top_level_item("unclear"),),
                (author_reply(102, "2026-07-14T03:00:00Z", "unclear"),),
            ),
            DiscussionClassifications(
                (),
                (classification("unclear", "unclear"),),
                (author_reply_classification(102, ("unclear", "none")),),
            ),
        )

        self.assertEqual(outcome.pending_actions, {})
        self.assertEqual(
            outcome.top_level_history["unclear"]["evidence"],
            {"reply": "2026-07-14T03:00:00Z"},
        )

    def test_later_reviewer_acknowledgement_does_not_address_older_item(self) -> None:
        outcome = resolve_discussions(
            PreparedDiscussions(
                (),
                (
                    top_level_item("request"),
                    top_level_item("ack", "2026-07-14T02:00:00Z"),
                ),
                (),
            ),
            DiscussionClassifications(
                (),
                (
                    classification("request", "author"),
                    classification("ack", "none"),
                ),
                (),
            ),
        )

        self.assertEqual(
            outcome.pending_actions,
            {"request": {"action": "author", "since": ROOT_TIMESTAMP}},
        )
        self.assertEqual(outcome.top_level_history, {})

    def test_renewed_author_handoff_supersedes_completion(self) -> None:
        prepared = PreparedDiscussions(
            (),
            (top_level_item("dependency"),),
            (
                author_reply(102, "2026-07-14T02:00:00Z", "dependency"),
                author_reply(103, "2026-07-14T03:00:00Z", "dependency"),
                author_reply(104, "2026-07-14T04:00:00Z", "dependency"),
            ),
        )
        outcome = resolve_discussions(
            prepared,
            DiscussionClassifications(
                (),
                (classification("dependency", "author"),),
                (
                    author_reply_classification(102, ("dependency", "author")),
                    author_reply_classification(103, ("dependency", "none")),
                    author_reply_classification(104, ("dependency", "author")),
                ),
            ),
        )

        self.assertEqual(
            outcome.pending_actions,
            {
                "dependency": {
                    "action": "author",
                    "since": "2026-07-14T04:00:00Z",
                }
            },
        )
        self.assertEqual(outcome.top_level_history, {})

    def test_unclear_reply_preserves_the_earlier_author_handoff(self) -> None:
        prepared = PreparedDiscussions(
            (),
            (top_level_item("dependency"),),
            (
                author_reply(102, "2026-07-14T02:00:00Z", "dependency"),
                author_reply(103, "2026-07-14T03:00:00Z", "dependency"),
            ),
        )
        outcome = resolve_discussions(
            prepared,
            DiscussionClassifications(
                (),
                (classification("dependency", "author"),),
                (
                    author_reply_classification(102, ("dependency", "author")),
                    author_reply_classification(103, ("dependency", "unclear")),
                ),
            ),
        )

        self.assertEqual(
            outcome.pending_actions,
            {
                "dependency": {
                    "action": "author",
                    "since": "2026-07-14T02:00:00Z",
                }
            },
        )
        self.assertEqual(outcome.top_level_history, {})

    def test_equal_timestamps_use_source_order(self) -> None:
        prepared = PreparedDiscussions(
            (),
            (top_level_item("question"),),
            (
                author_reply(102, "2026-07-14T03:00:00Z", "question"),
                author_reply(103, "2026-07-14T03:00:00Z", "question"),
            ),
        )
        outcome = resolve_discussions(
            prepared,
            DiscussionClassifications(
                (),
                (classification("question", "author"),),
                (
                    author_reply_classification(102, ("question", "none")),
                    author_reply_classification(103, ("question", "author")),
                ),
            ),
        )

        self.assertEqual(
            outcome.pending_actions["question"],
            {"action": "author", "since": "2026-07-14T03:00:00Z"},
        )
        self.assertEqual(outcome.top_level_history, {})


class HistoryRestorationTest(unittest.TestCase):
    history = {
        "question": {
            "evidence": {"reply": "2026-07-14T03:00:00Z"},
            "reply_source_id": 102,
        }
    }

    def test_restores_cached_reply_while_source_awaits_classification(self) -> None:
        outcome = resolve_discussions(
            PreparedDiscussions(
                (),
                (top_level_item("question"),),
                (author_reply(102, "2026-07-14T03:00:00Z", "question"),),
            ),
            DiscussionClassifications(
                (),
                (classification("question", "author"),),
                (),
            ),
            self.history,
        )

        self.assertEqual(outcome.pending_actions, {})
        self.assertEqual(outcome.top_level_history, self.history)

    def test_restores_cached_reply_after_deferred_classification(self) -> None:
        outcome = resolve_discussions(
            PreparedDiscussions(
                (),
                (top_level_item("question"),),
                (author_reply(102, "2026-07-14T03:00:00Z", "question"),),
            ),
            DiscussionClassifications(
                (),
                (classification("question", "author"),),
                (
                    author_reply_classification(
                        102,
                        deferred=True,
                    ),
                ),
            ),
            self.history,
        )

        self.assertEqual(outcome.pending_actions, {})
        self.assertEqual(outcome.top_level_history, self.history)

    def test_drops_cached_reply_when_source_was_deleted(self) -> None:
        outcome = resolve_discussions(
            PreparedDiscussions((), (top_level_item("question"),), ()),
            DiscussionClassifications(
                (),
                (classification("question", "author"),),
                (),
            ),
            self.history,
        )

        self.assertEqual(
            outcome.pending_actions,
            {"question": {"action": "author", "since": ROOT_TIMESTAMP}},
        )
        self.assertEqual(outcome.top_level_history, {})

    def test_reclassification_replaces_cached_completion_with_handoff(self) -> None:
        outcome = resolve_discussions(
            PreparedDiscussions(
                (),
                (top_level_item("question"),),
                (author_reply(102, "2026-07-14T03:00:00Z", "question"),),
            ),
            DiscussionClassifications(
                (),
                (classification("question", "author"),),
                (author_reply_classification(102, ("question", "author")),),
            ),
            self.history,
        )

        self.assertEqual(
            outcome.pending_actions,
            {
                "question": {
                    "action": "author",
                    "since": "2026-07-14T03:00:00Z",
                }
            },
        )
        self.assertEqual(outcome.top_level_history, {})

    def test_restores_legacy_history_without_source_id(self) -> None:
        legacy_history = {
            "question": {
                "evidence": {"reply": "2026-07-14T03:00:00Z"},
            }
        }
        outcome = resolve_discussions(
            PreparedDiscussions((), (top_level_item("question"),), ()),
            DiscussionClassifications(
                (),
                (classification("question", "author"),),
                (),
            ),
            legacy_history,
        )

        self.assertEqual(outcome.pending_actions, {})
        self.assertEqual(outcome.top_level_history, legacy_history)

    def test_edited_feedback_invalidates_legacy_history_without_source_id(
        self,
    ) -> None:
        prepared = prepare_discussions(
            DiscussionInput(
                (),
                ({
                    "kind": "issue-comment",
                    "source_id": 201,
                    "created_timestamp": "2026-07-14T07:00:00Z",
                    "timestamp": "2026-07-14T09:00:00Z",
                    "actor": "reviewer",
                    "actor_role": "approver",
                    "body": "Please update this.",
                },),
                "author",
                frozenset({"reviewer"}),
                "no",
            )
        )
        outcome = resolve_discussions(
            prepared,
            DiscussionClassifications(
                (),
                (classification("pr-issue-comment-201", "author"),),
                (),
            ),
            {
                "pr-issue-comment-201": {
                    "evidence": {"reply": "2026-07-14T07:30:00Z"},
                }
            },
        )

        self.assertEqual(
            outcome.pending_actions,
            {
                "pr-issue-comment-201": {
                    "action": "author",
                    "since": "2026-07-14T07:00:00Z",
                }
            },
        )
        self.assertEqual(outcome.top_level_history, {})

    def test_recovers_source_id_for_legacy_history(self) -> None:
        outcome = resolve_discussions(
            PreparedDiscussions(
                (),
                (top_level_item("question"),),
                (author_reply(102, "2026-07-14T03:00:00Z", "question"),),
            ),
            DiscussionClassifications(
                (),
                (classification("question", "author"),),
                (author_reply_classification(102, ("question", "none")),),
            ),
            {
                "question": {
                    "evidence": {"reply": "2026-07-14T03:00:00Z"},
                }
            },
        )

        self.assertEqual(
            outcome.top_level_history["question"]["reply_source_id"],
            102,
        )

    def test_new_handoff_supersedes_legacy_history(self) -> None:
        outcome = resolve_discussions(
            PreparedDiscussions(
                (),
                (top_level_item("question"),),
                (author_reply(103, "2026-07-14T04:00:00Z", "question"),),
            ),
            DiscussionClassifications(
                (),
                (classification("question", "author"),),
                (author_reply_classification(103, ("question", "author")),),
            ),
            {
                "question": {
                    "evidence": {"reply": "2026-07-14T03:00:00Z"},
                }
            },
        )

        self.assertEqual(
            outcome.pending_actions["question"],
            {"action": "author", "since": "2026-07-14T04:00:00Z"},
        )
        self.assertEqual(outcome.top_level_history, {})


class LifecycleProjectionTest(unittest.TestCase):
    def test_failed_classification_omits_pending_actions_and_history(self) -> None:
        failed = ClassificationFailure(
            DiscussionIdentity(
                "question",
                DiscussionKind.TOP_LEVEL_FEEDBACK,
            ),
            ActionDecision(
                DiscussionAction.AUTHOR,
                "Test classification.",
            ),
            ClassificationDiagnostics(error="model failed"),
        )
        outcome = resolve_discussions(
            PreparedDiscussions((), (top_level_item("question"),), ()),
            DiscussionClassifications((), (failed,), ()),
            {
                "question": {
                    "evidence": {"reply": "2026-07-14T03:00:00Z"},
                }
            },
        )

        self.assertEqual(outcome.pending_actions, {})
        self.assertEqual(outcome.top_level_history, {})
        self.assertEqual(outcome.failed_classifications, (failed,))
        fields = outcome.dashboard_fields()
        self.assertNotIn("pending_actions", fields)
        self.assertNotIn("top_level_history", fields)
        self.assertEqual(
            set(fields),
            {
                "review_threads",
                "top_level_items",
                "top_level_author_comment_items",
                "review_thread_classifications",
                "top_level_classifications",
                "top_level_author_comment_classifications",
            },
        )

    def test_reviewer_handoff_skips_classifications_and_preserves_history(self) -> None:
        prepared = PreparedDiscussions(
            (review_thread("thread"),),
            (top_level_item("question"),),
            (author_reply(102, "2026-07-14T03:00:00Z", "question"),),
        )
        history = {
            "question": {
                "evidence": {"reply": "2026-07-14T03:00:00Z"},
                "reply_source_id": 102,
            }
        }
        outcome = resolve_discussions(
            prepared,
            None,
            history,
            mode=LifecycleMode.REVIEWER_HANDOFF,
        )

        self.assertEqual(outcome.mode, LifecycleMode.REVIEWER_HANDOFF)
        self.assertEqual(outcome.classifications, DiscussionClassifications.empty())
        self.assertEqual(outcome.pending_actions, {})
        self.assertEqual(outcome.top_level_history, history)
        self.assertEqual(outcome.failed_classifications, ())
        self.assertEqual(
            outcome.dashboard_fields(),
            {
                "review_threads": list(prepared.review_threads),
                "top_level_items": list(prepared.top_level_items),
                "top_level_author_comment_items": list(
                    prepared.top_level_author_comment_items
                ),
                "review_thread_classifications": [],
                "top_level_classifications": [],
                "top_level_author_comment_classifications": [],
                "pending_actions": {},
                "top_level_history": history,
            },
        )

    def test_handoff_feedback_keeps_only_post_command_human_feedback(self) -> None:
        old_thread = review_thread("old-thread", "2026-07-14T01:00:00Z")
        reopened_thread = review_thread(
            "reopened-thread", "2026-07-14T01:00:00Z"
        )
        reopened_thread["comments"].extend([
            {
                "timestamp": "2026-07-14T03:00:00Z",
                "actor": "author",
                "actor_role": "author",
                "body": "I handled this.",
            },
            {
                "timestamp": "2026-07-14T05:00:00Z",
                "actor": "reviewer",
                "actor_role": "approver",
                "body": "Thanks.",
            },
        ])
        bot_thread = review_thread("bot-thread", "2026-07-14T01:00:00Z")
        bot_thread["comments"].append({
            "timestamp": "2026-07-14T05:00:00Z",
            "actor": "reviewer[bot]",
            "actor_role": "bot",
            "body": "Automated feedback.",
        })
        author_item = top_level_item(
            "author-comment",
            "2026-07-14T05:00:00Z",
        )
        author_item["comments"][0].update({
            "actor": "author",
            "actor_role": "author",
        })
        bot_item = top_level_item(
            "bot-comment",
            "2026-07-14T05:00:00Z",
        )
        bot_item["comments"][0].update({
            "actor": "reviewer[bot]",
            "actor_role": "bot",
        })
        follow_up_item = top_level_item(
            "follow-up-feedback",
            "2026-07-14T01:00:00Z",
        )
        follow_up_item["comments"].append({
            "timestamp": "2026-07-14T05:00:00Z",
            "actor": "reviewer",
            "actor_role": "approver",
            "body": "Please also update the tests.",
        })
        prepared = PreparedDiscussions(
            (old_thread, reopened_thread, bot_thread),
            (
                top_level_item("old-feedback", "2026-07-14T01:00:00Z"),
                author_item,
                bot_item,
                follow_up_item,
                top_level_item("new-feedback", "2026-07-14T05:00:00Z"),
            ),
            (
                author_reply(
                    102,
                    "2026-07-14T06:00:00Z",
                    "old-feedback",
                    "new-feedback",
                ),
            ),
        )

        filtered = reviewer_handoff_feedback(
            prepared,
            "2026-07-14T04:00:00Z",
            "author",
        )

        self.assertEqual(
            ["reopened-thread"],
            [thread["discussion_id"] for thread in filtered.review_threads],
        )
        self.assertEqual(
            ["Thanks."],
            [
                comment["body"]
                for comment in filtered.review_threads[0]["comments"]
            ],
        )
        self.assertEqual(
            {
                "latest_comment_role": "approver",
                "current_conflicts": "unknown",
            },
            filtered.review_threads[0]["discussion_facts"],
        )
        self.assertEqual("reviewer", filtered.review_threads[0]["requester"])
        self.assertEqual("author", filtered.review_threads[0]["pr_author"])
        self.assertEqual(
            ["follow-up-feedback", "new-feedback"],
            [item["discussion_id"] for item in filtered.top_level_items],
        )
        self.assertEqual(
            ["Please also update the tests."],
            [
                comment["body"]
                for comment in filtered.top_level_items[0]["comments"]
            ],
        )
        self.assertEqual((), filtered.top_level_author_comment_items)

    def test_handoff_feedback_keeps_same_second_feedback_out_of_scope(self) -> None:
        prepared = PreparedDiscussions(
            (),
            (top_level_item("same-second", "2026-07-14T04:00:00+00:00"),),
            (),
        )

        filtered = reviewer_handoff_feedback(
            prepared,
            "2026-07-14T04:00:00Z",
            "author",
        )

        self.assertEqual(PreparedDiscussions((), (), ()), filtered)

    def test_handoff_feedback_uses_effective_content_timestamps(self) -> None:
        source = DiscussionInput(
            normalize_review_threads(({
                "id": "edited-thread",
                "isResolved": False,
                "isOutdated": False,
                "comments": {
                    "nodes": [{
                        "url": "https://example.test/thread/edited",
                        "body": "Please also update the tests.",
                        "createdAt": "2026-07-14T02:00:00Z",
                        "lastEditedAt": "2026-07-14T06:00:00Z",
                        "author": {"login": "reviewer"},
                    }],
                },
            },)),
            ({
                "kind": "issue-comment",
                "source_id": 201,
                "created_timestamp": "2026-07-14T03:00:00Z",
                "timestamp": "2026-07-14T05:00:00Z",
                "actor": "reviewer",
                "actor_role": "approver",
                "body": "The test is still failing.",
            },),
            "author",
            frozenset({"reviewer"}),
            "no",
        )

        filtered = reviewer_handoff_feedback(
            prepare_discussions(source),
            "2026-07-14T04:00:00Z",
            "author",
        )

        self.assertEqual(
            ["edited-thread"],
            [item["discussion_id"] for item in filtered.review_threads],
        )
        self.assertEqual(
            ["pr-issue-comment-201"],
            [item["discussion_id"] for item in filtered.top_level_items],
        )

    def test_handoff_feedback_preserves_conversation_order_after_edit(self) -> None:
        source = DiscussionInput(
            normalize_review_threads(({
                "id": "edited-thread",
                "isResolved": False,
                "isOutdated": False,
                "comments": {
                    "nodes": [
                        {
                            "url": "https://example.test/thread/edited",
                            "body": "Root request, edited later.",
                            "createdAt": "2026-07-14T02:00:00Z",
                            "lastEditedAt": "2026-07-14T06:00:00Z",
                            "author": {"login": "root-reviewer"},
                        },
                        {
                            "url": "https://example.test/thread/follow-up",
                            "body": "Newer follow-up.",
                            "createdAt": "2026-07-14T05:00:00Z",
                            "author": {"login": "follow-up-reviewer"},
                        },
                    ],
                },
            },)),
            (),
            "author",
            frozenset({"root-reviewer"}),
            "no",
        )

        filtered = reviewer_handoff_feedback(
            prepare_discussions(source),
            "2026-07-14T04:00:00Z",
            "author",
        )

        discussion = filtered.review_threads[0]
        prompt_input = reviewer_feedback_prompt_input(
            ClassificationDiscussion.from_record(discussion)
        )
        self.assertEqual(
            "Root request, edited later.\n\nNewer follow-up.",
            prompt_input["body"],
        )
        self.assertEqual("root-reviewer", prompt_input["requester"])
        self.assertEqual(
            "approver",
            discussion["discussion_facts"]["latest_comment_role"],
        )


if __name__ == "__main__":
    unittest.main()
