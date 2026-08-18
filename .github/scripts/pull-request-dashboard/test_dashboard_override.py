from __future__ import annotations

import unittest
from unittest.mock import call, patch

import dashboard_override


class DashboardOverrideTest(unittest.TestCase):
    def test_override_guidance_matches_pre_review_route(self) -> None:
        self.assertIn(
            "waiting on the author to waiting on reviewers",
            dashboard_override.author_override_guidance(),
        )

    def test_dashboard_command_body_remainder(self) -> None:
        self.assertIsNone(
            dashboard_override.dashboard_command_body_remainder(
                {"body": "just a normal comment"}
            )
        )
        self.assertEqual(
            "",
            dashboard_override.dashboard_command_body_remainder(
                {"body": "/dashboard route:reviewers"}
            ),
        )
        self.assertEqual(
            "I addressed everything by doing X.",
            dashboard_override.dashboard_command_body_remainder(
                {"body": "/dashboard route:reviewers\n\nI addressed everything by doing X."}
            ),
        )
        self.assertEqual(
            "I addressed the feedback.\nAdditional context follows.",
            dashboard_override.dashboard_command_body_remainder({
                "body": (
                    "/dashboard route:reviewers I addressed the feedback.\n"
                    "Additional context follows."
                )
            }),
        )

    def test_latest_authorized_command_accepts_author_and_approvers(self) -> None:
        raw = {
            "issue_comments": [
                {"id": 1, "user": {"login": "outsider"}, "body": "/dashboard route:reviewers"},
                {"id": 2, "user": {"login": "author"}, "body": "text /dashboard route:reviewers"},
                {"id": 3, "user": {"login": "author"}, "body": "/dashboard route:reviewers\nplease review"},
                {"id": 4, "user": {"login": "Approver"}, "body": "/dashboard route:reviewers"},
            ]
        }

        self.assertEqual(
            (4, "Approver"),
            dashboard_override.latest_authorized_command(raw, "author", {"approver"}),
        )

    def test_latest_authorized_command_ignores_app_acknowledged_command(self) -> None:
        raw = {
            "issue_comments": [
                {"id": 3, "user": {"login": "author"}, "body": "/dashboard route:reviewers"},
                {
                    "id": 4,
                    "user": {"login": "opentelemetry-pr-dashboard[bot]"},
                    "body": dashboard_override.override_ack_marker(3),
                },
            ]
        }

        self.assertEqual(
            (0, ""),
            dashboard_override.latest_authorized_command(raw, "author", set()),
        )

    def test_is_authorized_commander_matches_author_or_approver(self) -> None:
        self.assertTrue(
            dashboard_override.is_authorized_commander("Author", "author", set())
        )
        self.assertTrue(
            dashboard_override.is_authorized_commander("Approver", "author", {"approver"})
        )
        self.assertFalse(
            dashboard_override.is_authorized_commander("outsider", "author", {"approver"})
        )

    def test_pending_replies_for_unauthorized_and_unknown_commands(self) -> None:
        raw = {
            "issue_comments": [
                {"id": 1, "user": {"login": "author"}, "body": "/dashboard route:reviewers"},
                {"id": 2, "user": {"login": "outsider"}, "body": "/dashboard route:reviewers"},
                {"id": 3, "user": {"login": "reviewer"}, "body": "/dashboard frobnicate"},
                {"id": 4, "user": {"login": "author"}, "body": "/dashboard"},
                {"id": 5, "user": {"login": "reviewer"}, "body": "looks good to me"},
                {"id": 6, "user": {"login": "approver"}, "body": "/dashboard route:reviewers"},
            ]
        }

        replies = dashboard_override.pending_command_replies(raw, "author", {"approver"})

        self.assertEqual(
            [
                {"comment_id": 2, "kind": "unauthorized", "user": "outsider", "subcommand": "route:reviewers"},
                {"comment_id": 3, "kind": "unknown_command", "user": "reviewer", "subcommand": "frobnicate"},
                {"comment_id": 4, "kind": "unknown_command", "user": "author", "subcommand": ""},
            ],
            replies,
        )

    def test_already_replied_commands_are_not_repeated(self) -> None:
        raw = {
            "issue_comments": [
                {"id": 2, "user": {"login": "outsider"}, "body": "/dashboard route:reviewers"},
                {
                    "id": 9,
                    "user": {"login": "opentelemetry-pr-dashboard[bot]"},
                    "body": dashboard_override.command_reply_marker(2) + "\n@outsider ...",
                },
            ]
        }

        self.assertEqual([], dashboard_override.pending_command_replies(raw, "author"))

    def test_forged_marker_from_non_app_user_does_not_suppress_reply(self) -> None:
        raw = {
            "issue_comments": [
                {"id": 2, "user": {"login": "outsider"}, "body": "/dashboard route:reviewers"},
                {
                    "id": 9,
                    "user": {"login": "outsider"},
                    "body": dashboard_override.command_reply_marker(2) + "\nnothing to see here",
                },
            ]
        }

        replies = dashboard_override.pending_command_replies(raw, "author")

        self.assertEqual(
            [{"comment_id": 2, "kind": "unauthorized", "user": "outsider", "subcommand": "route:reviewers"}],
            replies,
        )

    def test_renders_command_replies(self) -> None:
        unauthorized = dashboard_override.render_command_reply(
            {"comment_id": 2, "kind": "unauthorized", "user": "outsider", "subcommand": "route:reviewers"}
        )
        unknown = dashboard_override.render_command_reply(
            {"comment_id": 3, "kind": "unknown_command", "user": "reviewer", "subcommand": "frobnicate"}
        )
        routed = dashboard_override.render_command_reply(
            {"comment_id": 4, "kind": "routed", "user": "author"}
        )
        gate_held = dashboard_override.render_command_reply({
            "comment_id": 5,
            "kind": "routed",
            "held_gates": "the required status checks",
            "user": "author",
        })
        maintainer = dashboard_override.render_command_reply({
            "comment_id": 6,
            "kind": "routed",
            "route": "maintainer",
            "user": "author",
        })

        self.assertIn(dashboard_override.command_reply_marker(2), unauthorized)
        self.assertIn(
            "@outsider, only the pull request author or a member of an approving "
            "team can use `/dashboard route:reviewers`.",
            unauthorized,
        )
        self.assertIn(dashboard_override.command_reply_marker(3), unknown)
        self.assertIn(
            "`/dashboard frobnicate` is not a recognized dashboard command.",
            unknown,
        )
        self.assertIn(dashboard_override.command_reply_marker(4), routed)
        self.assertIn(dashboard_override.override_ack_marker(4), routed)
        self.assertIn("@author, this pull request was routed to reviewers.", routed)
        self.assertIn(dashboard_override.command_reply_marker(5), gate_held)
        self.assertIn(
            "@author, your reviewer-routing request was recorded; the reviewer "
            "handoff is waiting on the required status checks.",
            gate_held,
        )
        self.assertIn(dashboard_override.command_reply_marker(6), maintainer)
        self.assertIn(
            "@author, your reviewer-routing request was recorded; this pull request "
            "has the approvals it needs and is now waiting on maintainers.",
            maintainer,
        )

    def test_renders_already_routed_replies_per_route(self) -> None:
        cases = {
            "approver": "already waiting on reviewers",
            "maintainer": "already past review and waiting on maintainers",
        }
        for route, phrase in cases.items():
            with self.subTest(route=route):
                body = dashboard_override.render_command_reply(
                    {"comment_id": 7, "kind": "already_routed", "user": "author", "route": route}
                )
                self.assertIn(dashboard_override.command_reply_marker(7), body)
                self.assertIn(dashboard_override.override_ack_marker(7), body)
                self.assertIn(f"@author, this pull request is {phrase}, so", body)
                self.assertIn("`/dashboard route:reviewers` had no effect", body)

    def test_renders_already_routed_reply_for_a_command_that_cleared_nothing(
        self,
    ) -> None:
        body = dashboard_override.render_command_reply(
            {"comment_id": 7, "kind": "already_routed", "user": "author", "route": "author"}
        )

        self.assertIn(dashboard_override.override_ack_marker(7), body)
        self.assertIn(
            "@author, everything still open on this pull request arrived after "
            "your `/dashboard route:reviewers` command",
            body,
        )

    def test_renders_stale_head_reply(self) -> None:
        body = dashboard_override.render_command_reply(
            {"comment_id": 7, "kind": "stale_head", "user": "author"}
        )

        self.assertIn(dashboard_override.override_ack_marker(7), body)
        self.assertIn(
            "@author, the dashboard observed a new head and could not bind your "
            "`/dashboard route:reviewers` command to it",
            body,
        )
        self.assertIn("Run the command again", body)

    def test_appends_routed_reply_for_break_glass_command_that_cleared_nothing(self) -> None:
        facts = {
            "author": "author",
            "dashboard_override_command_id": 12,
            "copilot_review_bypassed_by_override": True,
        }

        dashboard_override.append_command_ack_reply({"issue_comments": []}, facts, "approver")

        self.assertEqual(
            [{
                "comment_id": 12,
                "kind": "routed",
                "user": "author",
                "route": "approver",
                "held_gates": "",
            }],
            facts["dashboard_command_replies"],
        )

    def test_no_ack_reply_without_a_pending_command(self) -> None:
        facts = {"author": "author", "dashboard_override_command_id": 0}

        dashboard_override.append_command_ack_reply({"issue_comments": []}, facts, "author")

        self.assertNotIn("dashboard_command_replies", facts)

    def test_appends_stale_head_reply_when_push_follows_command(self) -> None:
        facts = {
            "author": "author",
            "dashboard_override_command_id": 12,
            "dashboard_override_command_targets_head": False,
            "copilot_review_bypassed_by_override": False,
        }

        dashboard_override.append_command_ack_reply(
            {"issue_comments": []}, facts, "author"
        )

        self.assertEqual("stale_head", facts["dashboard_command_replies"][0]["kind"])

    def test_already_routed_reply_deduped_by_existing_marker(self) -> None:
        facts = {
            "author": "author",
            "dashboard_override_command_id": 12,
        }
        raw = {
            "issue_comments": [
                {
                    "user": {"login": "opentelemetry-pr-dashboard[bot]"},
                    "body": dashboard_override.command_reply_marker(12) + "\n@author ...",
                },
            ]
        }

        dashboard_override.append_command_ack_reply(raw, facts, "approver")

        self.assertNotIn("dashboard_command_replies", facts)

    def test_forged_marker_does_not_dedupe_already_routed_reply(self) -> None:
        facts = {
            "author": "author",
            "dashboard_override_command_id": 12,
        }
        raw = {
            "issue_comments": [
                {
                    "user": {"login": "outsider"},
                    "body": dashboard_override.command_reply_marker(12) + "\n@author ...",
                },
            ]
        }

        dashboard_override.append_command_ack_reply(raw, facts, "approver")

        self.assertEqual(
            [{
                "comment_id": 12,
                "kind": "already_routed",
                "user": "author",
                "route": "approver",
                "held_gates": "",
            }],
            facts["dashboard_command_replies"],
        )

    @patch.object(dashboard_override, "run_gh")
    @patch.object(dashboard_override, "gh_api", return_value=[])
    @patch.object(
        dashboard_override,
        "load_dashboard_state_cache",
        return_value={
            "prs": {
                "5": {
                    "facts": {
                        "dashboard_command_replies": [
                            {"comment_id": 2, "kind": "unauthorized", "user": "outsider", "subcommand": "route:reviewers"},
                        ]
                    }
                }
            }
        },
    )
    def test_delivers_pending_command_reply(self, _load_state, gh_api, run_gh) -> None:
        errors = dashboard_override.deliver_dashboard_command_replies(
            "open-telemetry/example"
        )

        self.assertEqual([], errors)
        gh_api.assert_called_once_with(
            "/repos/open-telemetry/example/issues/5/comments?per_page=100",
            paginate=True,
        )
        posted = run_gh.call_args.args[0]
        self.assertEqual(posted[:5], ["gh", "api", "--method", "POST", "repos/open-telemetry/example/issues/5/comments"])
        self.assertIn(dashboard_override.command_reply_marker(2), posted[-1])

    @patch.object(dashboard_override, "run_gh")
    @patch.object(
        dashboard_override,
        "gh_api",
        return_value=[
            {
                "performed_via_github_app": {"slug": "opentelemetry-pr-dashboard"},
                "body": "<!-- pull-request-dashboard-command-reply:2 --> already replied",
            }
        ],
    )
    @patch.object(
        dashboard_override,
        "load_dashboard_state_cache",
        return_value={
            "prs": {
                "5": {
                    "facts": {
                        "dashboard_command_replies": [
                            {"comment_id": 2, "kind": "unauthorized", "user": "outsider", "subcommand": "route:reviewers"},
                        ]
                    }
                }
            }
        },
    )
    def test_delivery_skips_already_replied_command(self, _load_state, _gh_api, run_gh) -> None:
        errors = dashboard_override.deliver_dashboard_command_replies(
            "open-telemetry/example"
        )

        self.assertEqual([], errors)
        run_gh.assert_not_called()

    def test_command_stays_pending_until_app_acknowledges_it(self) -> None:
        raw = {
            "issue_comments": [
                {"id": 3, "user": {"login": "author"}, "body": "/dashboard route:reviewers"},
            ]
        }

        first = dashboard_override.dashboard_override_facts(raw, "author")
        retry = dashboard_override.dashboard_override_facts(raw, "author")
        acknowledged_raw = {
            "issue_comments": [
                *raw["issue_comments"],
                {
                    "id": 4,
                    "user": {"login": "opentelemetry-pr-dashboard[bot]"},
                    "body": dashboard_override.override_ack_marker(3),
                },
            ]
        }
        acknowledged = dashboard_override.dashboard_override_facts(
            acknowledged_raw,
            "author",
        )

        self.assertEqual(3, first["dashboard_override_command_id"])
        self.assertEqual(3, retry["dashboard_override_command_id"])
        self.assertEqual(0, acknowledged["dashboard_override_command_id"])

    def test_newer_command_reapplies_removed_override(self) -> None:
        raw = {
            "issue_comments": [
                {"id": 3, "user": {"login": "author"}, "body": "/dashboard route:reviewers"},
                {
                    "id": 4,
                    "user": {"login": "opentelemetry-pr-dashboard[bot]"},
                    "body": dashboard_override.override_ack_marker(3),
                },
                {"id": 5, "user": {"login": "author"}, "body": "/dashboard route:reviewers"},
            ]
        }

        facts = dashboard_override.dashboard_override_facts(raw, "author")

        self.assertEqual(5, facts["dashboard_override_command_id"])

    def test_rebuilds_unacknowledged_already_routed_reply_across_refreshes(self) -> None:
        raw = {
            "issue_comments": [
                {"id": 5, "user": {"login": "author"}, "body": "/dashboard route:reviewers"},
            ]
        }

        for _ in range(2):
            facts = dashboard_override.dashboard_override_facts(raw, "author")
            dashboard_override.append_command_ack_reply(raw, facts, "approver")

            self.assertEqual(
                [{
                    "comment_id": 5,
                    "kind": "already_routed",
                    "user": "author",
                    "route": "approver",
                    "held_gates": "",
                }],
                facts["dashboard_command_replies"],
            )

    def test_acknowledged_command_does_not_replay_after_cache_eviction(self) -> None:
        raw = {
            "issue_comments": [
                {"id": 5, "user": {"login": "author"}, "body": "/dashboard route:reviewers"},
                {
                    "id": 9,
                    "user": {"login": "opentelemetry-pr-dashboard[bot]"},
                    "body": dashboard_override.override_ack_marker(5) + "\n@author ...",
                },
            ]
        }

        facts = dashboard_override.dashboard_override_facts(raw, "author")

        self.assertEqual(0, facts["dashboard_override_command_id"])
        self.assertEqual([], facts["dashboard_command_replies"])

    def test_newest_acknowledgement_consumes_older_authorized_commands(self) -> None:
        raw = {
            "issue_comments": [
                {"id": 3, "user": {"login": "author"}, "body": "/dashboard route:reviewers"},
                {"id": 5, "user": {"login": "author"}, "body": "/dashboard route:reviewers"},
                {
                    "id": 9,
                    "user": {"login": "opentelemetry-pr-dashboard[bot]"},
                    "body": dashboard_override.override_ack_marker(5),
                },
            ]
        }

        self.assertEqual(
            (0, ""),
            dashboard_override.latest_authorized_command(raw, "author", set()),
        )

    def test_watermark_survives_acknowledgement(self) -> None:
        raw = {
            "issue_comments": [
                {
                    "id": 3,
                    "user": {"login": "author"},
                    "created_at": "2026-07-30T12:00:00Z",
                    "body": "/dashboard route:reviewers",
                },
                {
                    "id": 4,
                    "user": {"login": "opentelemetry-pr-dashboard[bot]"},
                    "created_at": "2026-07-30T12:05:00Z",
                    "body": dashboard_override.override_ack_marker(3),
                },
            ]
        }

        facts = dashboard_override.dashboard_override_facts(raw, "author")

        self.assertEqual(0, facts["dashboard_override_command_id"])
        self.assertEqual("2026-07-30T12:00:00Z", facts["dashboard_override_since"])

    def test_watermark_survives_the_commander_leaving_the_approver_team(self) -> None:
        raw = {
            "issue_comments": [
                {
                    "id": 3,
                    "user": {"login": "former-approver"},
                    "created_at": "2026-07-30T12:00:00Z",
                    "body": "/dashboard route:reviewers",
                },
                {
                    "id": 4,
                    "user": {"login": "opentelemetry-pr-dashboard[bot]"},
                    "created_at": "2026-07-30T12:05:00Z",
                    "body": dashboard_override.override_ack_marker(3),
                },
            ]
        }

        facts = dashboard_override.dashboard_override_facts(raw, "author", set())

        self.assertEqual("2026-07-30T12:00:00Z", facts["dashboard_override_since"])

    def test_unacknowledged_command_needs_current_authorization(self) -> None:
        raw = {
            "issue_comments": [
                {
                    "id": 3,
                    "user": {"login": "former-approver"},
                    "created_at": "2026-07-30T12:00:00Z",
                    "body": "/dashboard route:reviewers",
                },
            ]
        }

        facts = dashboard_override.dashboard_override_facts(raw, "author", set())

        self.assertEqual("", facts["dashboard_override_since"])

    def test_command_that_cleared_nothing_is_acknowledged_where_it_is_routed(self) -> None:
        raw = {
            "issue_comments": [
                {"id": 5, "user": {"login": "author"}, "body": "/dashboard route:reviewers"},
            ]
        }
        facts = dashboard_override.dashboard_override_facts(raw, "author")

        dashboard_override.append_command_ack_reply(raw, facts, "author")

        self.assertEqual(
            [{
                "comment_id": 5,
                "kind": "already_routed",
                "user": "author",
                "route": "author",
                "held_gates": "",
            }],
            facts["dashboard_command_replies"],
        )

    def test_conflict_does_not_defer_override_acknowledgement(self) -> None:
        raw = {
            "issue_comments": [
                {"id": 5, "user": {"login": "author"}, "body": "/dashboard route:reviewers"},
            ]
        }
        facts = dashboard_override.dashboard_override_facts(raw, "author")
        facts["conflicts"] = "yes"
        facts["copilot_review_bypassed_by_override"] = True

        dashboard_override.append_command_ack_reply(raw, facts, "approver")

        self.assertEqual(
            [{
                "comment_id": 5,
                "kind": "routed",
                "user": "author",
                "route": "approver",
                "held_gates": "",
            }],
            facts["dashboard_command_replies"],
        )

    @patch.object(dashboard_override, "run_gh")
    @patch.object(dashboard_override, "gh_api", return_value=[])
    @patch.object(
        dashboard_override,
        "load_dashboard_state_cache",
        return_value={
            "prs": {
                "7": {
                    "facts": {
                        "dashboard_command_replies": [
                            {
                                "comment_id": 3,
                                "kind": "routed",
                                "route": "author",
                                "held_gates": "the Copilot review",
                                "user": "author",
                            },
                        ]
                    }
                },
            }
        },
    )
    def test_delivers_command_acknowledgement(self, _load_state, _gh_api, run_gh) -> None:
        errors = dashboard_override.deliver_dashboard_command_replies(
            "open-telemetry/example"
        )

        self.assertEqual([], errors)
        self.assertEqual(
            [
                call([
                    "gh", "api", "--method", "POST",
                    "repos/open-telemetry/example/issues/7/comments",
                    "-f", "body=<!-- pull-request-dashboard-command-reply:3 -->\n<!-- pull-request-dashboard-override-ack:3 -->\n@author, your reviewer-routing request was recorded; the reviewer handoff is waiting on the Copilot review.\n",
                ]),
            ],
            run_gh.call_args_list,
        )


if __name__ == "__main__":
    unittest.main()