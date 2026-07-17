from __future__ import annotations

import unittest

from publish_dashboard import publishable_prs


class PublishablePrsTest(unittest.TestCase):
    def test_omits_uncached_non_draft_prs_and_retains_drafts(self) -> None:
        prs = [
            {"number": 1, "isDraft": False},
            {"number": 2, "isDraft": False},
            {"number": 3, "isDraft": True},
        ]

        self.assertEqual(
            publishable_prs(prs, {1: {"route": "author"}}),
            [prs[0], prs[2]],
        )


if __name__ == "__main__":
    unittest.main()