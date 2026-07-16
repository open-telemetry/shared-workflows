from __future__ import annotations

import unittest

from render import render_diagnostics_section, render_pr_tables


class RenderTest(unittest.TestCase):
    def test_diagnostics_render_pending_actions_and_closed_items(self) -> None:
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
                "mainline_action_classifications": [
                    {
                        "discussion_id": "mainline",
                        "decision": {
                            "discussion_action": "author",
                            "reason": "Confirmed",
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
        self.assertIn("mainline -> author, closed", markdown)

    def test_reviewer_legend_includes_top_level_action(self) -> None:
        markdown = render_pr_tables([], {})

        self.assertIn(
            "💬 open discussion · 📌 tracked top-level action · 🔴 changes requested.",
            markdown,
        )


if __name__ == "__main__":
    unittest.main()