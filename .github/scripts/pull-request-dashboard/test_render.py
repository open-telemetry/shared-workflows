from __future__ import annotations

import unittest

from render import render_diagnostics_section, render_pr_tables


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
                        "action": "external",
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
            "pr-author-reply-102 -> dependency:external, pending:external ",
            markdown,
        )
        self.assertIn(
            "pr-author-reply-102 -> ambiguous:unclear, no-action ",
            markdown,
        )

    def test_reviewer_legend_includes_top_level_feedback(self) -> None:
        markdown = render_pr_tables([], {})

        self.assertIn(
            "💬 open review thread · 📌 top-level feedback needs author action · 🔴 changes requested.",
            markdown,
        )

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
