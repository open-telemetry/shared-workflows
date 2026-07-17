from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
import unittest
from unittest.mock import patch

import publish_dashboard


class PublishablePrsTest(unittest.TestCase):
    def test_omits_uncached_non_draft_prs_and_retains_drafts(self) -> None:
        prs = [
            {"number": 1, "isDraft": False},
            {"number": 2, "isDraft": False},
            {"number": 3, "isDraft": True},
        ]

        self.assertEqual(
            publish_dashboard.publishable_prs(prs, {1: {"route": "author"}}),
            [prs[0], prs[2]],
        )

    @patch.object(publish_dashboard, "publish_dashboard")
    @patch.object(publish_dashboard, "render_dashboard_markdown", return_value=Path("dashboard.md"))
    @patch.object(publish_dashboard, "set_state_dir")
    def test_publishes_from_read_only_accepted_state(
        self,
        set_state_dir: object,
        render_dashboard_markdown: object,
        publish: object,
    ) -> None:
        checkout_dir = Path("checkout")
        with patch.object(
            publish_dashboard.state_branch,
            "accepted_state_dir",
            return_value=nullcontext(checkout_dir),
        ) as accepted_state_dir:
            publish_dashboard.publish_accepted_dashboard(
                "open-telemetry/example",
                "state-branch",
                True,
            )

        accepted_state_dir.assert_called_once_with("state-branch", required=True)
        set_state_dir.assert_called_once_with(checkout_dir / "example")
        render_dashboard_markdown.assert_called_once_with("open-telemetry/example", True)
        publish.assert_called_once_with("open-telemetry/example", Path("dashboard.md"))


if __name__ == "__main__":
    unittest.main()