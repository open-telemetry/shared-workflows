from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import publish_dashboard


class PublishablePrsTest(unittest.TestCase):
    def test_parses_labels_to_display_json(self) -> None:
        self.assertEqual(
            ["size/*", "breaking change"],
            publish_dashboard.parse_labels_to_display_json(
                '["size/*", "breaking change"]'
            ),
        )

    def test_rejects_invalid_labels_to_display_json(self) -> None:
        invalid_values = (
            ("{", "must be valid JSON"),
            ("{}", "must be a JSON array of non-blank strings"),
            ('["size/*", 1]', "must be a JSON array of non-blank strings"),
            ('[""]', "must be a JSON array of non-blank strings"),
            ('["   "]', "must be a JSON array of non-blank strings"),
        )
        for raw, message in invalid_values:
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(RuntimeError, message):
                    publish_dashboard.parse_labels_to_display_json(raw)

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
                ["size/*"],
            )

        accepted_state_dir.assert_called_once_with("state-branch", required=True)
        set_state_dir.assert_called_once_with(checkout_dir / "example")
        render_dashboard_markdown.assert_called_once_with(
            "open-telemetry/example",
            True,
            ["size/*"],
        )
        publish.assert_called_once_with("open-telemetry/example", Path("dashboard.md"))

    def test_passes_labels_to_renderer(self) -> None:
        prs = [
            {
                "number": 1,
                "isDraft": False,
                "labels": ["size/L"],
            },
        ]
        results = {1: {"route": "unknown"}}
        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "dashboard.md"
            with (
                patch.object(
                    publish_dashboard,
                    "load_dashboard_state_cache",
                    return_value={"prs": {}},
                ),
                patch.object(publish_dashboard, "list_open_prs", return_value=prs),
                patch.object(
                    publish_dashboard,
                    "results_from_dashboard_state",
                    return_value=results,
                ),
                patch.object(
                    publish_dashboard,
                    "render_pr_tables",
                    return_value="dashboard\n",
                ) as render_pr_tables,
                patch.object(
                    publish_dashboard,
                    "dashboard_markdown_path",
                    return_value=output_path,
                ),
            ):
                self.assertEqual(
                    output_path,
                    publish_dashboard.render_dashboard_markdown(
                        "open-telemetry/example",
                        False,
                        ["size/*"],
                    ),
                )

        render_pr_tables.assert_called_once_with(
            prs,
            results,
            max_rows_per_section=None,
            skip_drafts=False,
            labels_to_display=["size/*"],
        )


if __name__ == "__main__":
    unittest.main()
