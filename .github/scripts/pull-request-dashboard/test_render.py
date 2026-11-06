from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import re
import unittest
from unittest.mock import patch

from dashboard_test_support import (
    dashboard_facts,
    stored_dashboard_result,
)
from render import (
    ISSUE_BODY_MAX_CHARS,
    author_cell_text,
    ci_cell,
    render_draft_pr_section,
    render_pr_tables,
    reviewers_cell_text,
)
from route_presentation import ROUTE_ORDER, route_label


class DashboardSizingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.enterContext(patch("render.activity_age", return_value="1d"))

    def test_more_than_100_rows_and_drafts_are_shown_when_the_body_fits(self) -> None:
        prs = [
            {"number": number, "title": "Feature", "isDraft": False}
            for number in range(1, 151)
        ] + [{"number": 151, "title": "Draft", "isDraft": True}]
        results = tuple(stored_dashboard_result(number) for number in range(1, 151))

        full = render_pr_tables(prs, results, max_chars=None)
        markdown = render_pr_tables(prs, results)

        self.assertEqual(full, markdown)
        self.assertLess(len(markdown), ISSUE_BODY_MAX_CHARS)
        self.assertEqual(151, len(re.findall(r"^\| #\d+ ", markdown, re.MULTILINE)))
        self.assertNotIn("[!WARNING]", markdown)
        self.assertNotIn("not shown", markdown)

    def test_actual_character_limit_with_unicode_and_escaped_markdown(self) -> None:
        for offset in (-1, 0, 1):
            with self.subTest(offset=offset):
                prs = [{
                    "number": 1,
                    "title": "日本語 🧪 & | [x]",
                    "isDraft": False,
                }]
                results = (stored_dashboard_result(1),)
                full = render_pr_tables(prs, results, max_chars=None)
                prs[0]["title"] += "x" * (ISSUE_BODY_MAX_CHARS + offset - len(full))
                full = render_pr_tables(prs, results, max_chars=None)
                markdown = render_pr_tables(prs, results)

                self.assertEqual(ISSUE_BODY_MAX_CHARS + offset, len(full))
                self.assertGreater(len(full.encode("utf-8")), ISSUE_BODY_MAX_CHARS)
                self.assertLessEqual(len(markdown), ISSUE_BODY_MAX_CHARS)
                if offset <= 0:
                    self.assertEqual(full, markdown)
                    self.assertNotIn("[!WARNING]", markdown)
                else:
                    self.assertNotIn("| #1 ", markdown)
                    self.assertIn("## Waiting on authors\n\n_More 1 PR not shown_", markdown)
                    self.assertIn("[!WARNING]", markdown)

    def test_trims_sections_and_rows_in_reverse_display_order(self) -> None:
        prs = []
        results = []
        routes = [route for route in ROUTE_ORDER if route != "transient-failure"]
        for index, route in enumerate(routes):
            for number in (index * 2 + 1, index * 2 + 2):
                prs.append({
                    "number": number,
                    "title": "x" * 1000,
                    "isDraft": False,
                })
                if route != "unknown":
                    results.append(stored_dashboard_result(
                        number,
                        route,
                        facts=dashboard_facts(
                            waiting_since=f"2026-07-{number:02d}T00:00:00Z",
                        ),
                    ))
        prs.extend([
            {
                "number": number,
                "title": "x" * 1000,
                "isDraft": True,
                "draftSince": f"2026-07-{number:02d}T00:00:00Z",
            }
            for number in (9, 10)
        ])
        prs.reverse()
        original_prs = deepcopy(prs)
        full = render_pr_tables(prs, results, max_chars=None)
        visible_counts = set()

        for budget in range(1500, len(full) + 500, 250):
            with self.subTest(budget=budget):
                markdown = render_pr_tables(prs, results, max_chars=budget)
                visible = [
                    int(number)
                    for number in re.findall(r"^\| #(\d+) ", markdown, re.MULTILINE)
                ]
                visible_counts.add(len(visible))
                self.assertEqual([1, 2, 3, 4, 5, 6, 8, 7, 9, 10][:len(visible)], visible)
                self.assertLessEqual(len(markdown), budget)
                for route in routes:
                    self.assertIn(f"## {route_label(route)}", markdown)
                self.assertIn("## Draft pull requests", markdown)
                omitted = sum(
                    int(count)
                    for count in re.findall(r"_More (\d+) PRs? not shown_", markdown)
                )
                self.assertEqual(10 - len(visible), omitted)
                self.assertEqual(len(visible) < 10, "[!WARNING]" in markdown)
        self.assertEqual(set(range(11)), visible_counts)
        self.assertEqual(original_prs, prs)

    def test_partial_draft_section_keeps_the_oldest_time_in_draft(self) -> None:
        prs = [
            {
                "number": 1,
                "title": "x" * 1000,
                "isDraft": True,
                "draftSince": "2026-07-10T00:00:00Z",
                "createdAt": "2026-07-01T00:00:00Z",
            },
            {
                "number": 2,
                "title": "x" * 1000,
                "isDraft": True,
                "draftSince": "2026-07-01T00:00:00Z",
                "createdAt": "2026-07-02T00:00:00Z",
            },
        ]
        full = render_pr_tables(prs, (), max_chars=None)

        markdown = render_pr_tables(prs, (), max_chars=len(full) - 1)

        self.assertNotIn("| #1 ", markdown)
        self.assertIn("| #2 ", markdown)
        self.assertIn("_More 1 PR not shown_", markdown)
        self.assertNotIn("All drafts are omitted", markdown)
        self.assertLessEqual(len(markdown), len(full) - 1)

    def test_fully_omitted_drafts_keep_their_count_without_an_empty_table(self) -> None:
        prs = [
            {"number": number, "title": "x" * 1000, "isDraft": True}
            for number in range(1, 13)
        ]

        markdown = render_pr_tables(prs, (), max_chars=1500)

        self.assertIn("All drafts are omitted to fit GitHub's issue-body limit.", markdown)
        self.assertIn("## Draft pull requests\n\n_More 12 PRs not shown_", markdown)
        self.assertNotIn("| PR | Author | Draft age |", markdown)
        self.assertNotIn("| #", markdown)
        self.assertLessEqual(len(markdown), 1500)

    def test_waiting_age_fallback_ties_and_unknown_ages_preserve_sorting(self) -> None:
        prs = [
            {"number": number, "title": "x" * 1000, "isDraft": False}
            for number in range(1, 6)
        ]
        results = (
            stored_dashboard_result(1, facts=dashboard_facts(
                waiting_since="2026-07-01T00:00:00Z",
                last_activity_at="2026-07-03T00:00:00Z",
            )),
            stored_dashboard_result(2, facts=dashboard_facts(
                waiting_since="2026-07-01T00:00:00Z",
            )),
            stored_dashboard_result(3, facts=dashboard_facts(
                waiting_since="2026-07-02T00:00:00Z",
                last_activity_at="2026-06-01T00:00:00Z",
            )),
            stored_dashboard_result(4),
            stored_dashboard_result(5, facts=dashboard_facts(
                last_activity_at="2026-06-30T00:00:00Z",
            )),
        )
        now = datetime(2026, 11, 3, tzinfo=timezone.utc)
        with patch(
            "render.seconds_since",
            side_effect=lambda ts: int((now - ts).total_seconds()) if ts else None,
        ):
            full = render_pr_tables(prs, results, max_chars=None)
            markdown = render_pr_tables(prs, results, max_chars=len(full) - 1100)

        self.assertEqual(
            ["5", "2", "1"],
            re.findall(r"^\| #(\d+) ", markdown, re.MULTILINE),
        )
        self.assertIn("_More 2 PRs not shown_", markdown)
        self.assertLessEqual(len(markdown), len(full) - 1100)

    def test_warning_and_footer_overhead_is_included_in_the_budget(self) -> None:
        prs = [
            {"number": number, "title": "Small", "isDraft": False}
            for number in range(1, 21)
        ]
        results = tuple(stored_dashboard_result(number) for number in range(1, 21))
        full = render_pr_tables(prs, results, max_chars=None)

        markdown = render_pr_tables(prs, results, max_chars=len(full) - 1)

        self.assertLessEqual(len(markdown), len(full) - 1)
        self.assertIn("[!WARNING]", markdown)
        self.assertGreater(
            int(re.search(r"_More (\d+) PRs not shown_", markdown).group(1)),
            1,
        )

    def test_impossible_budget_fails_explicitly(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "headings and omission notices exceed"):
            render_pr_tables([], (), max_chars=1)
        for budget in (0, -1):
            with self.subTest(budget=budget):
                with self.assertRaisesRegex(ValueError, "max_chars must be positive"):
                    render_pr_tables([], (), max_chars=budget)


class RenderTest(unittest.TestCase):
    def test_permission_owned_check_action_has_a_distinct_ci_icon(self) -> None:
        self.assertEqual(
            "🔐",
            ci_cell(dashboard_facts(
                ci_failing_count=0,
                ci_maintainer_action_required_count=1,
                ci_pending_count=0,
            )),
        )

    def test_pending_check_and_permission_action_show_both_icons(self) -> None:
        self.assertEqual(
            "⏳ 🔐",
            ci_cell(dashboard_facts(
                ci_failing_count=0,
                ci_maintainer_action_required_count=1,
                ci_pending_count=1,
            )),
        )

    def test_mixed_failure_and_permission_action_shows_both_icons(self) -> None:
        self.assertEqual(
            "❌ 🔐",
            ci_cell(dashboard_facts(
                ci_failing_count=1,
                ci_maintainer_action_required_count=1,
                ci_pending_count=0,
            )),
        )

    def test_ci_legend_explains_write_access_icon(self) -> None:
        markdown = render_pr_tables([], ())

        self.assertIn(
            "CI column: ✅ passing · ⏳ running · ❌ failing · "
            "🔐 workflow action required.",
            markdown,
        )

    def test_reviewer_legend_includes_top_level_feedback(self) -> None:
        markdown = render_pr_tables([], ())

        self.assertIn(
            "⏳ review pending · 💬 unresolved feedback from reviewer · "
            "📌 top-level feedback needs author action · 🔴 changes requested.",
            markdown,
        )

    def test_human_rereview_replaces_the_previous_approval_badge(self) -> None:
        cell = reviewers_cell_text(dashboard_facts(
            reviewers=[{
                "login": "reviewer",
                "approved": True,
                "pending_review": True,
            }],
        ))

        self.assertEqual("reviewer&nbsp;⏳", cell)

    def test_human_rereview_keeps_the_changes_requested_badge(self) -> None:
        cell = reviewers_cell_text(dashboard_facts(
            reviewers=[{
                "login": "reviewer",
                "changes_requested": True,
                "pending_review": True,
            }],
        ))

        self.assertEqual("reviewer&nbsp;⏳\u2060🔴", cell)

    def test_copilot_without_unresolved_feedback_is_hidden(self) -> None:
        for login in (
            "copilot",
            "Copilot",
            "copilot[bot]",
            "copilot-pull-request-reviewer",
            "copilot-pull-request-reviewer[bot]",
        ):
            for status in (
                {},
                {"approved": True},
                {"approved_non_team": True},
                {"changes_requested": True},
                {"top_level_feedback": True},
                {"pending_review": True},
            ):
                with self.subTest(login=login, status=status):
                    cell = reviewers_cell_text(dashboard_facts(
                        reviewers=[
                            {"login": login, **status},
                            {"login": "reviewer"},
                        ],
                        copilot_review_exists=True,
                    ))

                    self.assertEqual("reviewer", cell)

    def test_copilot_with_unresolved_feedback_remains_visible(self) -> None:
        for login in (
            "copilot",
            "Copilot",
            "copilot[bot]",
            "copilot-pull-request-reviewer",
            "copilot-pull-request-reviewer[bot]",
        ):
            with self.subTest(login=login):
                cell = reviewers_cell_text(dashboard_facts(
                    reviewers=[{"login": login, "unresolved_thread": True}],
                ))

                self.assertEqual("Copilot&nbsp;💬", cell)

    def test_copilot_is_hidden_after_its_last_thread_is_resolved(self) -> None:
        cell = reviewers_cell_text(dashboard_facts(
            reviewers=[{"login": "copilot-pull-request-reviewer"}],
            copilot_review_exists=True,
            copilot_review_outstanding=True,
        ))

        self.assertEqual("", cell)

    def test_requested_copilot_review_is_listed_as_pending(self) -> None:
        cell = reviewers_cell_text(dashboard_facts(
            reviewers=[{"login": "reviewer", "approved": True}],
            copilot_review_outstanding=True,
            copilot_review_requested=True,
        ))

        self.assertEqual("Copilot&nbsp;⏳<br>reviewer&nbsp;✅", cell)

    def test_requested_copilot_review_keeps_its_entry_without_open_feedback(self) -> None:
        cell = reviewers_cell_text(dashboard_facts(
            reviewers=[{"login": "Copilot"}],
            copilot_review_exists=True,
            copilot_review_outstanding=True,
            copilot_review_requested=True,
        ))

        self.assertEqual("Copilot&nbsp;⏳", cell)

    def test_requested_copilot_review_on_an_ungated_branch_is_not_pending(self) -> None:
        # Nothing holds the pull request there, and a requested human reviewer
        # who has not responded is left off the row, so Copilot gets no row
        # either. The gate is what makes the wait someone's turn.
        cell = reviewers_cell_text(dashboard_facts(
            reviewers=[
                {"login": "copilot-pull-request-reviewer"},
                {"login": "reviewer", "approved": True},
            ],
            copilot_review_outstanding=False,
            copilot_review_requested=True,
        ))

        self.assertEqual("reviewer&nbsp;✅", cell)

    def test_requested_copilot_review_marks_its_existing_entry(self) -> None:
        cell = reviewers_cell_text(dashboard_facts(
            reviewers=[{
                "login": "copilot-pull-request-reviewer",
                "unresolved_thread": True,
            }],
            copilot_review_outstanding=True,
            copilot_review_requested=True,
        ))

        self.assertEqual("Copilot&nbsp;⏳\u2060💬", cell)

    def test_requested_copilot_review_marks_its_short_login_entry(self) -> None:
        cell = reviewers_cell_text(dashboard_facts(
            reviewers=[{"login": "copilot", "unresolved_thread": True}],
            copilot_review_outstanding=True,
            copilot_review_requested=True,
        ))

        self.assertEqual("Copilot&nbsp;⏳\u2060💬", cell)

    def test_requested_copilot_review_marks_its_api_cased_entry(self) -> None:
        cell = reviewers_cell_text(dashboard_facts(
            reviewers=[{"login": "Copilot", "unresolved_thread": True}],
            copilot_review_outstanding=True,
            copilot_review_requested=True,
        ))

        self.assertEqual("Copilot&nbsp;⏳\u2060💬", cell)

    def test_stale_copilot_review_without_a_request_is_not_pending(self) -> None:
        # Nothing is in flight, so the row would otherwise claim a wait that no
        # one is serving.
        cell = reviewers_cell_text(dashboard_facts(
            reviewers=[{"login": "reviewer", "approved": True}],
            copilot_review_outstanding=True,
            copilot_review_exists=True,
            copilot_review_requested=False,
        ))

        self.assertEqual("reviewer&nbsp;✅", cell)

    def test_held_pr_awaiting_the_first_review_request_is_pending(self) -> None:
        cell = reviewers_cell_text(dashboard_facts(
            reviewers=[{"login": "reviewer", "approved": True}],
            copilot_review_requested=False,
            copilot_review_exists=False,
            copilot_review_outstanding=True,
            route_held_for_gates=True,
        ))

        self.assertEqual("Copilot&nbsp;⏳<br>reviewer&nbsp;✅", cell)

    def test_unheld_pr_awaiting_the_first_review_is_not_pending(self) -> None:
        cell = reviewers_cell_text(dashboard_facts(
            reviewers=[{"login": "reviewer", "approved": True}],
            copilot_review_requested=False,
            copilot_review_exists=False,
            copilot_review_outstanding=True,
            route_held_for_gates=False,
        ))

        self.assertEqual("reviewer&nbsp;✅", cell)

    def test_pr_held_only_by_unsettled_checks_is_not_copilot_pending(self) -> None:
        # Unsettled checks hold a route too, so the hold alone would put Copilot
        # on every row of a repository the gate does not cover.
        cell = reviewers_cell_text(dashboard_facts(
            reviewers=[{"login": "reviewer", "approved": True}],
            copilot_review_requested=False,
            copilot_review_exists=False,
            copilot_review_outstanding=False,
            required_checks_settled=False,
            route_held_for_gates=True,
        ))

        self.assertEqual("reviewer&nbsp;✅", cell)

    def test_clean_copilot_review_is_not_listed_as_pending(self) -> None:
        cell = reviewers_cell_text(dashboard_facts(
            reviewers=[{"login": "reviewer", "approved": True}],
            copilot_review_requested=False,
            copilot_review_exists=True,
        ))

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
        results = (
            stored_dashboard_result(123),
            stored_dashboard_result(124),
        )

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
            (),
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

    def test_author_legend_explains_badges(self) -> None:
        markdown = render_pr_tables([], ())

        self.assertIn(
            "Author column: 🆕 first-time contributor · 🔭 open-telemetry member · "
            "🛠️ approver or maintainer · 🤖 bot.",
            markdown,
        )

    def test_author_badges(self) -> None:
        # Approver team listings include members of child maintainer teams.
        approvers = frozenset({"maintainer", "approver"})
        cases = [
            ("first-time contributor", {"login": "new"}, "FIRST_TIME_CONTRIBUTOR", "new", "new&nbsp;🆕"),
            ("first-time GitHub user", {"login": "new"}, "FIRST_TIMER", "new", "new&nbsp;🆕"),
            ("organization member", {"login": "member"}, "MEMBER", "member", "member&nbsp;🔭"),
            ("maintainer", {"login": "Maintainer"}, "MEMBER", "Maintainer", "Maintainer&nbsp;🛠️"),
            ("approver", {"login": "approver"}, "MEMBER", "approver", "approver&nbsp;🛠️"),
            ("approver without org association", {"login": "approver"}, "CONTRIBUTOR", "approver", "approver&nbsp;🛠️"),
            ("bot account", {"login": "renovate[bot]", "type": "Bot"}, "CONTRIBUTOR", "renovate[bot]", "renovate[bot]&nbsp;🤖"),
            ("bot app login", {"login": "renovate[bot]", "type": "Bot"}, "NONE", "app/renovate", "app/renovate&nbsp;🤖"),
            ("bot user account", {"login": "opentelemetrybot"}, "MEMBER", "opentelemetrybot", "opentelemetrybot&nbsp;🤖"),
            ("Copilot without a human", {"login": "Copilot", "type": "Bot"}, "NONE", "Copilot", "Copilot&nbsp;🤖"),
            ("returning contributor", {"login": "contributor"}, "CONTRIBUTOR", "contributor", "contributor"),
            ("no association", {"login": "outsider"}, None, "outsider", "outsider"),
            ("unknown author", {}, None, "", ""),
        ]
        for name, pr_author, association, author, expected in cases:
            with self.subTest(name):
                pr = {"author": pr_author, "authorAssociation": association}
                self.assertEqual(expected, author_cell_text(pr, author, approvers))

    def test_copilot_pr_human_author_does_not_inherit_copilot_association(self) -> None:
        pr = {
            "author": {"login": "Copilot", "type": "Bot"},
            "authorAssociation": "FIRST_TIME_CONTRIBUTOR",
        }

        self.assertEqual("human", author_cell_text(pr, "human"))
        self.assertEqual(
            "maintainer&nbsp;🛠️",
            author_cell_text(pr, "maintainer", frozenset({"maintainer"})),
        )

    def test_renders_author_badges_in_route_and_draft_tables(self) -> None:
        markdown = render_pr_tables(
            [
                {
                    "number": 127,
                    "title": "Feature",
                    "author": {"login": "member"},
                    "authorAssociation": "MEMBER",
                    "isDraft": False,
                },
                {
                    "number": 128,
                    "title": "Work in progress",
                    "author": {"login": "maintainer"},
                    "authorAssociation": "MEMBER",
                    "isDraft": True,
                },
            ],
            (stored_dashboard_result(127),),
            approver_logins=frozenset({"maintainer"}),
        )

        self.assertIn("| #127 Feature | member&nbsp;🔭 |", markdown)
        self.assertIn("| #128 Work in progress | maintainer&nbsp;🛠️ |", markdown)

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
        results = (stored_dashboard_result(126),)

        self.assertEqual(
            render_pr_tables(prs, results),
            render_pr_tables(prs, results, labels_to_display=[]),
        )
        self.assertNotIn("<code>", render_pr_tables(prs, results))


if __name__ == "__main__":
    unittest.main()
