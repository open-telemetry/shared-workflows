from __future__ import annotations

import unittest

from dashboard_contracts import ReviewerSummary
from dashboard_test_support import dashboard_facts
from notifications import reviewer_logins_for_notification
from pull_request_source import normalize_actor, normalize_review_requests
from render import reviewer_icon
from reviewer_state import (
    ReviewerDiscussionInput,
    ReviewerInput,
    prepare_reviewers,
    resolve_reviewers,
)


def review_event(
    login: str,
    state: str,
    *,
    role: str = "approver",
    timestamp: str = "2026-08-01T00:00:00Z",
) -> dict[str, object]:
    return {
        "kind": "review-state",
        "timestamp": timestamp,
        "actor": login,
        "actor_role": role,
        "state": state,
    }


def participation_event(login: str, *, role: str = "approver") -> dict[str, object]:
    return {
        "kind": "issue-comment",
        "timestamp": "2026-08-01T01:00:00Z",
        "actor": login,
        "actor_role": role,
    }


def prepare(
    events: list[dict[str, object]] | None = None,
    review_requests: list[dict[str, object]] | None = None,
    assignees: list[dict[str, object]] | None = None,
):
    return prepare_reviewers(
        ReviewerInput(
            tuple(events or []),
            normalize_review_requests(review_requests),
            tuple(normalize_actor(value) for value in assignees or []),
        )
    )


def resolve(
    prepared,
    review_threads: list[dict[str, object]] | None = None,
    top_level_feedback: list[dict[str, object]] | None = None,
    pending_actions: dict[str, dict[str, object]] | None = None,
):
    return resolve_reviewers(
        prepared,
        ReviewerDiscussionInput(
            tuple(review_threads or []),
            tuple(top_level_feedback or []),
            pending_actions or {},
        ),
    )


class ReviewerStateTest(unittest.TestCase):
    def test_active_team_and_outside_approvals_and_changes_requested(self) -> None:
        prepared = prepare([
            review_event("team-reviewer", "APPROVED"),
            review_event("outside-reviewer", "APPROVED", role="outsider"),
            review_event("blocking-reviewer", "CHANGES_REQUESTED"),
        ])

        reviewers = {
            reviewer.login: reviewer for reviewer in resolve(prepared)
        }

        self.assertEqual(1, prepared.approval_count)
        self.assertTrue(reviewers["team-reviewer"].approved)
        self.assertTrue(reviewers["outside-reviewer"].approved_non_team)
        self.assertTrue(reviewers["blocking-reviewer"].changes_requested)
        blocking = reviewers["blocking-reviewer"]
        self.assertEqual("\U0001f534", reviewer_icon(blocking))
        self.assertEqual(
            ["blocking-reviewer"],
            reviewer_logins_for_notification(
                dashboard_facts(reviewers=(blocking,))
            ),
        )

    def test_outside_changes_requested_reviewer_remains_visible(self) -> None:
        prepared = prepare([
            review_event(
                "outside-reviewer",
                "CHANGES_REQUESTED",
                role="outsider",
            )
        ])

        reviewer = resolve(prepared)[0]

        self.assertEqual("outside-reviewer", reviewer.login)
        self.assertTrue(reviewer.changes_requested)
        self.assertFalse(reviewer.top_level_feedback)
        self.assertEqual("\U0001f534", reviewer_icon(reviewer))
        self.assertEqual(
            ["outside-reviewer"],
            reviewer_logins_for_notification(
                dashboard_facts(reviewers=(reviewer,))
            ),
        )

    def test_individual_rerequest_invalidates_only_that_approval(self) -> None:
        prepared = prepare(
            [
                review_event("active", "APPROVED"),
                review_event("rerequested", "APPROVED"),
            ],
            [{"__typename": "User", "login": "rerequested"}],
        )

        reviewers = {
            reviewer.login: reviewer for reviewer in resolve(prepared)
        }

        self.assertEqual(1, prepared.approval_count)
        self.assertTrue(reviewers["active"].approved)
        self.assertFalse(reviewers["rerequested"].approved)
        self.assertTrue(reviewers["rerequested"].pending_review)
        self.assertEqual(
            frozenset({"rerequested"}),
            prepared.pending_human_reviewer_logins,
        )

    def test_rerequest_preserves_changes_requested(self) -> None:
        prepared = prepare(
            [review_event("reviewer", "CHANGES_REQUESTED")],
            [{"__typename": "User", "login": "reviewer"}],
        )

        reviewer = resolve(prepared)[0]

        self.assertTrue(reviewer.pending_review)
        self.assertTrue(reviewer.changes_requested)

    def test_team_request_does_not_invalidate_individual_approval(self) -> None:
        prepared = prepare(
            [review_event("reviewer", "APPROVED")],
            [{"__typename": "Team", "slug": "example-approvers"}],
        )

        reviewer = resolve(prepared)[0]

        self.assertEqual(1, prepared.approval_count)
        self.assertTrue(reviewer.approved)
        self.assertFalse(reviewer.pending_review)

    def test_first_request_is_not_pending_rereview(self) -> None:
        prepared = prepare(
            review_requests=[{"__typename": "User", "login": "new-reviewer"}]
        )

        self.assertEqual(frozenset(), prepared.pending_human_reviewer_logins)
        self.assertEqual((), resolve(prepared))

    def test_commenting_approver_and_assignee_only_reviewer_remain_visible(self) -> None:
        prepared = prepare(
            [participation_event("commenter")],
            assignees=[{"login": " assigned-reviewer "}],
        )

        reviewers = resolve(prepared)

        self.assertEqual(("assigned-reviewer",), prepared.assignee_logins)
        self.assertEqual(
            ["assigned-reviewer", "commenter"],
            [reviewer.login for reviewer in reviewers],
        )
        self.assertFalse(any(reviewer.approved for reviewer in reviewers))

    def test_inline_ownership_includes_approver_and_outsider(self) -> None:
        prepared = prepare()
        thread = {
            "discussion_id": "inline",
            "comments": [
                {"actor": "approver", "actor_role": "approver"},
                {"actor": "outsider", "actor_role": "outsider"},
            ],
        }

        reviewers = resolve(
            prepared,
            review_threads=[thread],
            pending_actions={"inline": {"action": "reviewer"}},
        )

        self.assertEqual(
            ["approver", "outsider"],
            [reviewer.login for reviewer in reviewers],
        )
        self.assertTrue(all(reviewer.open_thread for reviewer in reviewers))

    def test_ignored_final_praise_does_not_add_its_author(self) -> None:
        prepared = prepare()
        thread = {
            "discussion_id": "inline",
            "comments": [
                {"actor": "requester", "actor_role": "approver"},
                {"actor": "author", "actor_role": "author"},
                {"actor": "praise-author", "actor_role": "approver"},
            ],
        }

        reviewers = resolve(
            prepared,
            review_threads=[thread],
            pending_actions={
                "inline": {
                    "action": "author",
                    "ignored_last_comment": True,
                }
            },
        )

        self.assertEqual(["requester"], [reviewer.login for reviewer in reviewers])

    def test_top_level_owner_is_marked_only_for_author_action(self) -> None:
        prepared = prepare()
        feedback = [
            {"discussion_id": "author-action", "requester": "alice"},
            {"discussion_id": "reviewer-action", "requester": "bob"},
            {"discussion_id": "complete", "requester": "carol"},
        ]

        reviewers = resolve(
            prepared,
            top_level_feedback=feedback,
            pending_actions={
                "author-action": {"action": "author"},
                "reviewer-action": {"action": "reviewer"},
            },
        )

        self.assertEqual(["alice"], [reviewer.login for reviewer in reviewers])
        self.assertTrue(reviewers[0].top_level_feedback)

    def test_combined_badges_projection_and_case_insensitive_ordering(self) -> None:
        prepared = prepare(
            [participation_event("bob")],
            assignees=[{"login": "Zoe"}, {"login": "adam"}],
        )
        thread = {
            "discussion_id": "inline",
            "comments": [{"actor": "bob", "actor_role": "approver"}],
        }
        feedback = [{"discussion_id": "top", "requester": "bob"}]

        reviewers = resolve(
            prepared,
            review_threads=[thread],
            top_level_feedback=feedback,
            pending_actions={
                "inline": {"action": "author"},
                "top": {"action": "author"},
            },
        )

        self.assertEqual(
            ["adam", "bob", "Zoe"],
            [reviewer.login for reviewer in reviewers],
        )
        self.assertEqual(
            ReviewerSummary(
                login="bob",
                open_thread=True,
                top_level_feedback=True,
            ),
            reviewers[1],
        )
        self.assertEqual(
            "\U0001f4ac\u2060\U0001f4cc",
            reviewer_icon(reviewers[1]),
        )


if __name__ == "__main__":
    unittest.main()
