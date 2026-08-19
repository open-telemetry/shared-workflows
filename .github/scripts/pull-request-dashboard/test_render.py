from __future__ import annotations

import unittest
from unittest.mock import patch

from render import (
    render_diagnostics_section,
    render_draft_pr_section,
    render_pr_tables,
    reviewers_cell_text,
)


class RenderTest(unittest.TestCase):
    def test_diagnostics_distinguish_addressed_top_level_items(self) -> None:
        lines = render_diagnostics_section({
            123: {
                "review_thread_classifications": [
                    {
                        "discussion_id": "inline",
                        "decision": {
                            "discussion_action": "author",
                            "reason": "Needs revision",
                        },
                    },
                ],
                "top_level_classifications": [
                    {
                        "discussion_id": "top_level",
                        "discussion_kind": "top-level-feedback",
                        "decision": {
                            "discussion_action": "author",
                            "reason": "Confirmed",
                        },
                    },
                    {
                        "discussion_id": "top_level_note",
                        "discussion_kind": "top-level-feedback",
                        "decision": {
                            "discussion_action": "none",
                            "reason": "Informational",
                        },
                    },
                ],
                "pending_actions": {
                    "inline": {
                        "action": "author",
                        "since": "2026-07-14T01:00:00Z",
                    },
                },
            },
        })

        markdown = "\n".join(lines)
        self.assertIn("inline -> author, pending:author", markdown)
        self.assertIn("top_level -> author, addressed", markdown)
        self.assertIn("top_level_note -> none, no-action", markdown)

    def test_diagnostics_render_author_comment_feedback_outcomes(self) -> None:
        lines = render_diagnostics_section({
            123: {
                "top_level_author_comment_classifications": [
                    {
                        "discussion_id": "pr-author-reply-102",
                        "discussion_kind": "top-level-author-reply",
                        "decision": {
                            "feedback_outcomes": [
                                {
                                    "feedback_id": "question",
                                    "discussion_action": "none",
                                    "reason": "The author answered it.",
                                },
                                {
                                    "feedback_id": "test-request",
                                    "discussion_action": "author",
                                    "reason": "The author will add the test.",
                                },
                                {
                                    "feedback_id": "dependency",
                                    "discussion_action": "external",
                                    "reason": "The dependency is blocked upstream.",
                                },
                                {
                                    "feedback_id": "ambiguous",
                                    "discussion_action": "unclear",
                                    "reason": "The response is ambiguous.",
                                },
                            ],
                        },
                    },
                ],
                "pending_actions": {
                    "test-request": {
                        "action": "author",
                        "since": "2026-07-14T01:00:00Z",
                    },
                    "dependency": {
                        "action": "author",
                        "since": "2026-07-14T01:00:00Z",
                    },
                    "ambiguous": {
                        "action": "author",
                        "since": "2026-07-14T01:00:00Z",
                    },
                },
            },
        })

        markdown = "\n".join(lines)
        self.assertIn(
            "pr-author-reply-102 -> question:none, no-action ",
            markdown,
        )
        self.assertIn(
            "pr-author-reply-102 -> test-request:author, pending:author ",
            markdown,
        )
        self.assertIn(
            "pr-author-reply-102 -> dependency:external, pending:author ",
            markdown,
        )
        self.assertIn(
            "pr-author-reply-102 -> ambiguous:unclear, pending:author ",
            markdown,
        )

    def test_reviewer_legend_includes_top_level_feedback(self) -> None:
        markdown = render_pr_tables([], {})

        self.assertIn(
            "⏳ review pending · 💬 open review thread · "
            "📌 top-level feedback needs author action · 🔴 changes requested.",
            markdown,
        )

    def test_human_rereview_replaces_the_previous_approval_badge(self) -> None:
        cell = reviewers_cell_text({
            "reviewers": [{
                "login": "reviewer",
                "approved": True,
                "pending_review": True,
            }],
        })

        self.assertEqual("reviewer&nbsp;⏳", cell)

    def test_human_rereview_keeps_the_changes_requested_badge(self) -> None:
        cell = reviewers_cell_text({
            "reviewers": [{
                "login": "reviewer",
                "changes_requested": True,
                "pending_review": True,
            }],
        })

        self.assertEqual("reviewer&nbsp;⏳\u2060🔴", cell)

    def test_requested_copilot_review_is_listed_as_pending(self) -> None:
        cell = reviewers_cell_text({
            "reviewers": [{"login": "reviewer", "approved": True}],
            "copilot_review_outstanding": True,
            "copilot_review_requested": True,
        })

        self.assertEqual("Copilot&nbsp;⏳<br>reviewer&nbsp;✅", cell)

    def test_requested_copilot_review_on_an_ungated_branch_is_not_pending(self) -> None:
        # Nothing holds the pull request there, and a requested human reviewer
        # who has not responded is left off the row, so Copilot gets no row
        # either. The gate is what makes the wait someone's turn.
        cell = reviewers_cell_text({
            "reviewers": [{"login": "reviewer", "approved": True}],
            "copilot_review_outstanding": False,
            "copilot_review_requested": True,
        })

        self.assertEqual("reviewer&nbsp;✅", cell)

    def test_requested_copilot_review_marks_its_existing_entry(self) -> None:
        cell = reviewers_cell_text({
            "reviewers": [{"login": "copilot-pull-request-reviewer", "open_thread": True}],
            "copilot_review_outstanding": True,
            "copilot_review_requested": True,
        })

        self.assertEqual("Copilot&nbsp;⏳\u2060💬", cell)

    def test_requested_copilot_review_marks_its_short_login_entry(self) -> None:
        cell = reviewers_cell_text({
            "reviewers": [{"login": "copilot", "open_thread": True}],
            "copilot_review_outstanding": True,
            "copilot_review_requested": True,
        })

        self.assertEqual("Copilot&nbsp;⏳\u2060💬", cell)

    def test_requested_copilot_review_marks_its_api_cased_entry(self) -> None:
        cell = reviewers_cell_text({
            "reviewers": [{"login": "Copilot", "open_thread": True}],
            "copilot_review_outstanding": True,
            "copilot_review_requested": True,
        })

        self.assertEqual("Copilot&nbsp;⏳\u2060💬", cell)

    def test_stale_copilot_review_without_a_request_is_not_pending(self) -> None:
        # Nothing is in flight, so the row would otherwise claim a wait that no
        # one is serving.
        cell = reviewers_cell_text({
            "reviewers": [{"login": "reviewer", "approved": True}],
            "copilot_review_outstanding": True,
            "copilot_review_exists": True,
            "copilot_review_requested": False,
        })

        self.assertEqual("reviewer&nbsp;✅", cell)

    def test_held_pr_awaiting_the_automatic_first_review_is_pending(self) -> None:
        # The automatic first review is never requested, so the hold it causes
        # needs the icon to explain the row.
        cell = reviewers_cell_text({
            "reviewers": [{"login": "reviewer", "approved": True}],
            "copilot_review_requested": False,
            "copilot_review_exists": False,
            "copilot_review_outstanding": True,
            "route_held_for_gates": True,
        })

        self.assertEqual("Copilot&nbsp;⏳<br>reviewer&nbsp;✅", cell)

    def test_unheld_pr_awaiting_the_automatic_first_review_is_not_pending(self) -> None:
        cell = reviewers_cell_text({
            "reviewers": [{"login": "reviewer", "approved": True}],
            "copilot_review_requested": False,
            "copilot_review_exists": False,
            "copilot_review_outstanding": True,
            "route_held_for_gates": False,
        })

        self.assertEqual("reviewer&nbsp;✅", cell)

    def test_pr_held_only_by_unsettled_checks_is_not_copilot_pending(self) -> None:
        # Unsettled checks hold a route too, so the hold alone would put Copilot
        # on every row of a repository the gate does not cover.
        cell = reviewers_cell_text({
            "reviewers": [{"login": "reviewer", "approved": True}],
            "copilot_review_requested": False,
            "copilot_review_exists": False,
            "copilot_review_outstanding": False,
            "required_checks_settled": False,
            "route_held_for_gates": True,
        })

        self.assertEqual("reviewer&nbsp;✅", cell)

    def test_clean_copilot_review_is_not_listed_as_pending(self) -> None:
        cell = reviewers_cell_text({
            "reviewers": [{"login": "reviewer", "approved": True}],
            "copilot_review_requested": False,
            "copilot_review_exists": True,
        })

        self.assertEqual("reviewer&nbsp;✅", cell)

    def test_dashboard_does_not_claim_approvers_can_force_refresh(self) -> None:
        markdown = render_pr_tables([], {})

        self.assertNotIn("force a refresh", markdown)

    def test_renders_matching_labels_inline_without_filtering_prs(self) -> None:
        prs = [
            {
                "number": 123,
                "title": "Feature",
                "author": {"login": "author"},
                "isDraft": False,
                "labels": [
                    "size/L",
                    "breaking change",
                    "documentation",
                    "size/L",
                    "Size/XL",
                    "danger | [x] <tag> & @owner",
                ],
            },
            {
                "number": 124,
                "title": "Documentation",
                "author": {"login": "author"},
                "isDraft": False,
                "labels": ["documentation"],
            },
        ]
        results = {
            123: {"route": "unknown", "facts": {}},
            124: {"route": "unknown", "facts": {}},
        }

        markdown = render_pr_tables(
            prs,
            results,
            labels_to_display=["size/*", "size/L", "breaking change", "danger*"],
        )

        self.assertIn(
            "#123 Feature · <code>size/L</code> · <code>breaking change</code> · "
            "<code>danger \\| \\[x\\] &lt;tag&gt; &amp; &#64;owner</code>",
            markdown,
        )
        self.assertEqual(1, markdown.count("<code>size/L</code>"))
        self.assertNotIn("<code>Size/XL</code>", markdown)
        self.assertNotIn("<code>documentation</code>", markdown)
        self.assertIn("#124 Documentation", markdown)

    def test_renders_matching_labels_on_draft_prs(self) -> None:
        markdown = render_pr_tables(
            [
                {
                    "number": 125,
                    "title": "Work in progress",
                    "author": {"login": "author"},
                    "isDraft": True,
                    "labels": ["size/S"],
                },
            ],
            {},
            labels_to_display=["size/*"],
        )

        self.assertIn("| #125 Work in progress · <code>size/S</code> | author |", markdown)

    @patch("render.activity_age", side_effect=lambda value: value.isoformat())
    def test_renders_and_sorts_time_in_draft(self, activity_age) -> None:
        lines = render_draft_pr_section([
            {
                "number": 2,
                "title": "Newer draft",
                "author": {"login": "author"},
                "isDraft": True,
                "createdAt": "2026-07-01T00:00:00Z",
                "draftSince": "2026-07-17T00:00:00Z",
            },
            {
                "number": 1,
                "title": "Older draft",
                "author": {"login": "author"},
                "isDraft": True,
                "createdAt": "2026-07-02T00:00:00Z",
                "draftSince": "2026-07-10T00:00:00Z",
            },
        ])

        markdown = "\n".join(lines)
        self.assertIn("| PR | Author | Draft age |", markdown)
        self.assertLess(markdown.index("#1 Older draft"), markdown.index("#2 Newer draft"))
        self.assertIn("2026-07-10T00:00:00+00:00", markdown)
        self.assertIn("2026-07-17T00:00:00+00:00", markdown)
        self.assertEqual(2, activity_age.call_count)

    def test_omits_labels_when_none_are_configured(self) -> None:
        prs = [
            {
                "number": 126,
                "title": "Feature",
                "author": {"login": "author"},
                "isDraft": False,
                "labels": ["size/L"],
            },
        ]
        results = {126: {"route": "unknown", "facts": {}}}

        self.assertEqual(
            render_pr_tables(prs, results),
            render_pr_tables(prs, results, labels_to_display=[]),
        )
        self.assertNotIn("<code>", render_pr_tables(prs, results))


if __name__ == "__main__":
    unittest.main()
