from __future__ import annotations

import unittest

from render import render_pr_tables


class RenderTest(unittest.TestCase):
    def test_reviewer_legend_includes_top_level_action(self) -> None:
        markdown = render_pr_tables([], {})

        self.assertIn(
            "💬 open discussion · 📌 tracked top-level action · 🔴 changes requested.",
            markdown,
        )


if __name__ == "__main__":
    unittest.main()