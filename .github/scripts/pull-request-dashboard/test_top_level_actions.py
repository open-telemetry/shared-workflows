from __future__ import annotations

import unittest

from classification_policy import (
    ActionDecision,
    AuthorCommentDecision,
    ClassificationDiscussion,
    ClassificationSuccess,
    DiscussionAction,
    DiscussionClassifications,
    DiscussionIdentity,
    DiscussionKind,
    FeedbackOutcome,
    leading_mentions,
    reviewer_feedback_prompt_input,
)
from dashboard_contracts import DashboardRoute
from dashboard_test_support import dashboard_facts
from discussion_lifecycle import (
    DiscussionInput,
    PreparedDiscussions,
    prepare_discussions,
    resolve_discussions,
)
from pull_request_activity import ActivityInput, build_activity_timeline
from pull_request_source import normalize_pull_request_source
from routing_decision import RoutingInput, resolve_routing


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
    def test_top_level_prompt_input_ignores_review_state(self) -> None:
        discussion = top_level_item("change-request")
        discussion["review_state"] = "CHANGES_REQUESTED"
        discussion["comments"] = [{"body": "Please update the implementation."}]

        self.assertEqual(
            reviewer_feedback_prompt_input(
                ClassificationDiscussion.from_record(discussion)
            ),
            {
                "discussion_id": "change-request",
                "feedback_kind": "top_level_comment",
                "requester": "reviewer",
                "pr_author": "author",
                "addressed_to": [],
                "body": "Please update the implementation.",
            },
        )

    def test_review_body_is_identified_as_a_review_summary(self) -> None:
        discussion = top_level_item(
            "review-summary",
            source_kind="review-state",
        )
        discussion["comments"] = [
            {"body": "Looks good, just one comment about naming."}
        ]

        self.assertEqual(
            reviewer_feedback_prompt_input(
                ClassificationDiscussion.from_record(discussion)
            )["feedback_kind"],
            "review_summary",
        )

    def test_top_level_prompt_input_reports_who_a_comment_addresses(self) -> None:
        discussion = top_level_item("addressed")
        discussion["comments"] = [
            {
                "body": (
                    "@maintainer, @open-telemetry/java-approvers "
                    "could we reuse #123 here?"
                )
            }
        ]

        prompt_input = reviewer_feedback_prompt_input(
            ClassificationDiscussion.from_record(discussion)
        )
        self.assertEqual(
            prompt_input["addressed_to"],
            ["maintainer", "open-telemetry/java-approvers"],
        )

    def test_leading_mentions_only_reads_the_opening_run(self) -> None:
        self.assertEqual(
            leading_mentions("@trask In #19459 @someone did this"),
            ["trask"],
        )
        self.assertEqual(leading_mentions("Please rebase, @author"), [])
        self.assertEqual(
            leading_mentions("\n  @first @second\nplease look"),
            ["first", "second"],
        )
        self.assertEqual(
            leading_mentions("@first\n@second\nplease look"),
            ["first", "second"],
        )
        self.assertEqual(
            leading_mentions("@First @Open-Telemetry/Java"),
            ["first", "open-telemetry/java"],
        )
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
            [
                "https://example.test/issue-comment/101",
                "https://example.test/review/202",
            ],
        )
        self.assertEqual([item["pr_author"] for item in items], ["author", "author"])
        self.assertEqual(
            items[1]["root_timestamp"],
            "2026-07-14T02:00:00Z",
        )

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

        self.assertEqual(top_level_items_from_raw(raw), [])

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

        self.assertEqual(top_level_items_from_raw(raw), [])

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
