from __future__ import annotations

from contextlib import nullcontext
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import publish_dashboard
from dashboard_test_support import (
    dashboard_state,
    stored_dashboard_result,
)
from render import AuthorTeams


EXAMPLE_TEAM_SLUGS = publish_dashboard.AuthorTeamSlugs(
    maintainers=("example-maintainers",),
    approvers=("example-approvers",),
    triagers=("example-triagers",),
)


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
            publish_dashboard.publishable_prs(
                prs,
                (stored_dashboard_result(1),),
            ),
            [prs[0], prs[2]],
        )

    @patch.object(publish_dashboard, "fetch_latest_draft_transitions")
    def test_adds_latest_draft_transition_with_creation_fallback(
        self,
        fetch_transitions: object,
    ) -> None:
        fetch_transitions.return_value = {2: "2026-07-17T00:00:00Z"}
        prs = [
            {"number": 1, "isDraft": False, "createdAt": "2026-07-01T00:00:00Z"},
            {"number": 2, "isDraft": True, "createdAt": "2026-07-02T00:00:00Z"},
            {"number": 3, "isDraft": True, "createdAt": "2026-07-03T00:00:00Z"},
        ]

        publish_dashboard.add_draft_since("open-telemetry/example", prs)

        self.assertNotIn("draftSince", prs[0])
        self.assertEqual("2026-07-17T00:00:00Z", prs[1]["draftSince"])
        self.assertEqual("2026-07-03T00:00:00Z", prs[2]["draftSince"])
        fetch_transitions.assert_called_once_with(
            "open-telemetry/example",
            [2, 3],
        )

    @patch.object(publish_dashboard, "gh_api")
    def test_draft_authors_prefer_human_assignees_without_fetching_commits(
        self,
        gh_api,
    ) -> None:
        for login in ("Copilot", "app/copilot-swe-agent", "copilot-swe-agent[bot]"):
            with self.subTest(login=login):
                prs = [{
                    "number": 7,
                    "isDraft": True,
                    "author": {"login": login},
                    "assignees": [
                        {"login": "Copilot"},
                        {"login": "app/copilot-swe-agent"},
                        {"login": "opentelemetrybot"},
                        {"login": "renovate[bot]"},
                        {"login": "human-author"},
                        {"login": "another-human"},
                    ],
                }]

                publish_dashboard.add_draft_authors("open-telemetry/example", prs)

                self.assertEqual("human-author", prs[0]["effectiveAuthor"])
                self.assertEqual({"login": login}, prs[0]["author"])
        gh_api.assert_not_called()

    @patch.object(publish_dashboard, "gh_api")
    def test_draft_author_falls_back_to_first_commit_committer(
        self,
        gh_api,
    ) -> None:
        gh_api.return_value = [{
            "author": {"login": "Copilot"},
            "committer": {"login": "human-committer", "type": "User"},
        }]
        prs = [{
            "number": 7,
            "isDraft": True,
            "author": {"login": "Copilot"},
            "assignees": [{"login": "Copilot"}],
        }]

        publish_dashboard.add_draft_authors("open-telemetry/example", prs)

        self.assertEqual("human-committer", prs[0]["effectiveAuthor"])
        gh_api.assert_called_once_with(
            "/repos/open-telemetry/example/pulls/7/commits?per_page=1"
        )

    @patch.object(publish_dashboard, "gh_api")
    def test_draft_author_keeps_copilot_without_a_human_committer(
        self,
        gh_api,
    ) -> None:
        commits = (
            [],
            [{"committer": None}],
            [{"committer": {"login": "Copilot"}}],
            [{"committer": {"login": "opentelemetrybot"}}],
            [{"committer": {"login": "renovate[bot]"}}],
            [{"committer": {"login": "automation", "type": "Bot"}}],
            [{"committer": {"login": "copilot-pull-request-reviewer"}}],
            [
                {"committer": {"login": "Copilot"}},
                {"committer": {"login": "later-human"}},
            ],
        )
        for value in commits:
            with self.subTest(commits=value):
                gh_api.return_value = value
                prs = [{
                    "number": 7,
                    "isDraft": True,
                    "author": {"login": "Copilot"},
                }]

                publish_dashboard.add_draft_authors("open-telemetry/example", prs)

                self.assertEqual("Copilot", prs[0]["effectiveAuthor"])

    @patch.object(publish_dashboard, "gh_api")
    def test_draft_authors_leave_other_authors_and_non_drafts_unchanged(
        self,
        gh_api,
    ) -> None:
        prs = [
            {
                "number": 7,
                "isDraft": True,
                "author": {"login": "human-author"},
                "assignees": [{"login": "another-human"}],
            },
            {
                "number": 8,
                "isDraft": True,
                "author": {"login": "renovate[bot]"},
            },
            {"number": 9, "isDraft": False, "author": {"login": "Copilot"}},
        ]

        publish_dashboard.add_draft_authors("open-telemetry/example", prs)

        self.assertEqual("human-author", prs[0]["effectiveAuthor"])
        self.assertEqual("renovate[bot]", prs[1]["effectiveAuthor"])
        self.assertNotIn("effectiveAuthor", prs[2])
        gh_api.assert_not_called()

    @patch.object(
        publish_dashboard,
        "gh_api",
        side_effect=RuntimeError("commit fetch failed"),
    )
    def test_draft_author_commit_fetch_failure_is_not_hidden(self, gh_api) -> None:
        prs = [{
            "number": 7,
            "isDraft": True,
            "author": {"login": "Copilot"},
        }]

        with self.assertRaisesRegex(RuntimeError, "commit fetch failed"):
            publish_dashboard.add_draft_authors("open-telemetry/example", prs)

        self.assertNotIn("effectiveAuthor", prs[0])

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
                EXAMPLE_TEAM_SLUGS,
            )

        accepted_state_dir.assert_called_once_with("state-branch", required=True)
        set_state_dir.assert_called_once_with(checkout_dir / "example")
        render_dashboard_markdown.assert_called_once_with(
            "open-telemetry/example",
            True,
            ["size/*"],
            EXAMPLE_TEAM_SLUGS,
        )
        publish.assert_called_once_with("open-telemetry/example", Path("dashboard.md"))

    @patch.object(publish_dashboard, "publish_dashboard")
    @patch.object(
        publish_dashboard,
        "render_dashboard_markdown",
        side_effect=[Path("old.md"), Path("new.md")],
    )
    @patch.object(publish_dashboard, "set_state_dir")
    def test_worker_advancement_does_not_retry_a_pinned_snapshot(
        self,
        _set_state_dir: object,
        _render_dashboard_markdown: object,
        publish: object,
    ) -> None:
        with patch.object(
            publish_dashboard.state_branch,
            "accepted_state_dir",
            side_effect=[nullcontext(Path("old")), nullcontext(Path("new"))],
        ):
            publish_dashboard.publish_accepted_dashboard(
                "open-telemetry/example",
                "state-branch",
                False,
            )

        self.assertEqual(
            [call.args[1] for call in publish.call_args_list],
            [Path("old.md")],
        )

    def test_reads_author_team_slugs_from_the_environment(self) -> None:
        self.assertEqual(
            EXAMPLE_TEAM_SLUGS,
            publish_dashboard.AuthorTeamSlugs.from_environment({
                "MAINTAINER_TEAMS_JSON": '["example-maintainers"]',
                "APPROVER_TEAMS_JSON": '["example-approvers"]',
                "TRIAGER_TEAMS_JSON": '["example-triagers"]',
            }),
        )
        self.assertEqual(
            publish_dashboard.AuthorTeamSlugs(),
            publish_dashboard.AuthorTeamSlugs.from_environment({}),
        )

    def test_rejects_invalid_author_team_slugs(self) -> None:
        for name in ("MAINTAINER_TEAMS_JSON", "APPROVER_TEAMS_JSON", "TRIAGER_TEAMS_JSON"):
            for raw in ("{", '"example-team"', '[""]'):
                with self.subTest(name=name, raw=raw):
                    with self.assertRaisesRegex(RuntimeError, name):
                        publish_dashboard.AuthorTeamSlugs.from_environment({name: raw})

    @patch.object(publish_dashboard, "publish_accepted_dashboard")
    def test_main_passes_author_team_slugs_from_the_environment(
        self,
        publish_accepted: object,
    ) -> None:
        with (
            patch.object(
                sys,
                "argv",
                ["publish_dashboard.py", "--repo", "example", "--state-branch", "state"],
            ),
            patch.dict(
                os.environ,
                {
                    "MAINTAINER_TEAMS_JSON": '["example-maintainers"]',
                    "APPROVER_TEAMS_JSON": '["example-approvers"]',
                    "TRIAGER_TEAMS_JSON": '["example-triagers"]',
                },
            ),
        ):
            self.assertEqual(0, publish_dashboard.main())

        publish_accepted.assert_called_once_with(
            "open-telemetry/example",
            "state",
            False,
            [],
            EXAMPLE_TEAM_SLUGS,
        )

    def test_passes_labels_and_author_teams_to_renderer(self) -> None:
        prs = [
            {
                "number": 1,
                "isDraft": False,
                "labels": ["size/L"],
            },
        ]
        results = (stored_dashboard_result(1),)
        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "dashboard.md"
            with (
                patch.object(
                    publish_dashboard,
                    "load_dashboard_state_cache",
                    return_value=dashboard_state(),
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
                patch.object(
                    publish_dashboard,
                    "load_team_members",
                    side_effect=lambda _org, slugs: {slugs[0].removeprefix("example-")},
                ) as load_team_members,
            ):
                self.assertEqual(
                    output_path,
                    publish_dashboard.render_dashboard_markdown(
                        "open-telemetry/example",
                        False,
                        ["size/*"],
                        EXAMPLE_TEAM_SLUGS,
                    ),
                )

        self.assertEqual(
            [
                (("open-telemetry", ["example-maintainers"]),),
                (("open-telemetry", ["example-approvers"]),),
                (("open-telemetry", ["example-triagers"]),),
            ],
            [(call.args,) for call in load_team_members.call_args_list],
        )
        render_pr_tables.assert_called_once_with(
            prs,
            results,
            max_rows_per_section=None,
            skip_drafts=False,
            labels_to_display=["size/*"],
            author_teams=AuthorTeams(
                maintainers=frozenset({"maintainers"}),
                approvers=frozenset({"approvers"}),
                triagers=frozenset({"triagers"}),
            ),
        )

    def test_skips_team_lookups_without_author_teams(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "dashboard.md"
            with (
                patch.object(
                    publish_dashboard,
                    "load_dashboard_state_cache",
                    return_value=dashboard_state(),
                ),
                patch.object(publish_dashboard, "list_open_prs", return_value=[]),
                patch.object(
                    publish_dashboard,
                    "dashboard_markdown_path",
                    return_value=output_path,
                ),
                patch.object(publish_dashboard, "load_team_members") as load_team_members,
            ):
                publish_dashboard.render_dashboard_markdown(
                    "open-telemetry/example",
                    False,
                )

        load_team_members.assert_not_called()

    def test_renders_effective_draft_authors_from_the_open_pull_listing(self) -> None:
        pulls = [
            {
                "number": 7,
                "title": "Assigned draft",
                "draft": True,
                "user": {"login": "Copilot"},
                "assignees": [{"login": "Copilot"}, {"login": "human-assignee"}],
            },
            {
                "number": 8,
                "title": "Unassigned draft",
                "draft": True,
                "user": {"login": "Copilot"},
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "dashboard.md"
            with (
                patch.object(
                    publish_dashboard,
                    "load_dashboard_state_cache",
                    return_value=dashboard_state(),
                ),
                patch("github_cli._list_open_pulls", return_value=pulls),
                patch.object(
                    publish_dashboard,
                    "gh_api",
                    return_value=[{"committer": {"login": "human-committer"}}],
                ) as gh_api,
                patch.object(
                    publish_dashboard,
                    "fetch_latest_draft_transitions",
                    return_value={},
                ),
                patch.object(
                    publish_dashboard,
                    "dashboard_markdown_path",
                    return_value=output_path,
                ),
            ):
                path = publish_dashboard.render_dashboard_markdown(
                    "open-telemetry/example",
                    False,
                )
                markdown = path.read_text(encoding="utf-8")

        self.assertIn("| #7 Assigned draft | human-assignee |", markdown)
        self.assertIn("| #8 Unassigned draft | human-committer |", markdown)
        self.assertNotIn("| Copilot |", markdown)
        gh_api.assert_called_once_with(
            "/repos/open-telemetry/example/pulls/8/commits?per_page=1"
        )

    def test_large_repo_skips_draft_author_and_age_fetches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "dashboard.md"
            with (
                patch.object(
                    publish_dashboard,
                    "load_dashboard_state_cache",
                    return_value=dashboard_state(),
                ),
                patch.object(
                    publish_dashboard,
                    "list_open_prs",
                    return_value=[{
                        "number": 7,
                        "isDraft": True,
                        "author": {"login": "Copilot"},
                    }],
                ),
                patch.object(publish_dashboard, "add_draft_authors") as add_authors,
                patch.object(publish_dashboard, "add_draft_since") as add_since,
                patch.object(
                    publish_dashboard,
                    "dashboard_markdown_path",
                    return_value=output_path,
                ),
            ):
                path = publish_dashboard.render_dashboard_markdown(
                    "open-telemetry/example",
                    True,
                )
                markdown = path.read_text(encoding="utf-8")

        self.assertNotIn("## Draft pull requests", markdown)
        add_authors.assert_not_called()
        add_since.assert_not_called()


if __name__ == "__main__":
    unittest.main()
