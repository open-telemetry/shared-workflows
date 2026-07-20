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


if __name__ == "__main__":
    unittest.main()