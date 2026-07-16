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

    def test_reviewer_legend_includes_top_level_feedback(self) -> None:
        markdown = render_pr_tables([], {})

        self.assertIn(
            "💬 open discussion · 📌 author action pending · 🔴 changes requested.",
            markdown,
        )


if __name__ == "__main__":
    unittest.main()