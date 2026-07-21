from __future__ import annotations

import unittest
from unittest.mock import call, patch

import dashboard_override


class DashboardOverrideTest(unittest.TestCase):
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

    def test_renders_unauthorized_and_unknown_command_replies(self) -> None:
        unauthorized = dashboard_override.render_command_reply(
            {"comment_id": 2, "kind": "unauthorized", "user": "outsider", "subcommand": "route:reviewers"}
        )
        unknown = dashboard_override.render_command_reply(
            {"comment_id": 3, "kind": "unknown_command", "user": "reviewer", "subcommand": "frobnicate"}
        )

        self.assertIn(dashboard_override.command_reply_marker(2), unauthorized)
        self.assertIn(
            "@outsider only the pull request author or a member of an approving "
            "team can use `/dashboard route:reviewers`.",
            unauthorized,
        )
        self.assertIn(dashboard_override.command_reply_marker(3), unknown)
        self.assertIn(
            "`/dashboard frobnicate` is not a recognized dashboard command.",
            unknown,
        )

    def test_renders_already_routed_replies_per_route(self) -> None:
        cases = {
            "approver": "already waiting on reviewers",
            "maintainer": "already past review and waiting on maintainers",
            "external": "waiting on an external dependency, not on you",
            "copilot": "waiting on an automated Copilot review",
        }
        for route, phrase in cases.items():
            with self.subTest(route=route):
                body = dashboard_override.render_command_reply(
                    {"comment_id": 7, "kind": "already_routed", "user": "author", "route": route}
                )
                self.assertIn(dashboard_override.command_reply_marker(7), body)
                self.assertIn(f"@author this pull request is {phrase}, so", body)
                self.assertIn("`/dashboard route:reviewers` had no effect", body)

    def test_appends_already_routed_reply_for_author_noop(self) -> None:
        facts = {
            "author": "author",
            "dashboard_override_noop": True,
            "dashboard_override_command_id": 12,
        }

        dashboard_override.append_route_noop_reply({"issue_comments": []}, facts, "approver")

        self.assertEqual(
            [{"comment_id": 12, "kind": "already_routed", "user": "author", "route": "approver"}],
            facts["dashboard_command_replies"],
        )

    def test_no_already_routed_reply_when_command_applies(self) -> None:
        facts = {
            "author": "author",
            "dashboard_override_noop": False,
            "dashboard_override_command_id": 12,
        }

        dashboard_override.append_route_noop_reply({"issue_comments": []}, facts, "author")

        self.assertNotIn("dashboard_command_replies", facts)

    def test_already_routed_reply_deduped_by_existing_marker(self) -> None:
        facts = {
            "author": "author",
            "dashboard_override_noop": True,
            "dashboard_override_command_id": 12,
        }
        raw = {
            "issue_comments": [
                {"body": dashboard_override.command_reply_marker(12) + "\n@author ..."},
            ]
        }

        dashboard_override.append_route_noop_reply(raw, facts, "approver")

        self.assertNotIn("dashboard_command_replies", facts)

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

    def test_command_stays_pending_until_label_is_observed(self) -> None:
        raw = {
            "issue_comments": [
                {"id": 3, "user": {"login": "author"}, "body": "/dashboard route:reviewers"},
            ]
        }

        first = dashboard_override.dashboard_override_facts(raw, "author", set(), None)
        retry = dashboard_override.dashboard_override_facts(raw, "author", set(), first)
        acknowledged = dashboard_override.dashboard_override_facts(
            raw,
            "author",
            {"dashboard:route-overridden"},
            retry,
        )
        removed = dashboard_override.dashboard_override_facts(
            raw,
            "author",
            set(),
            acknowledged,
        )

        self.assertTrue(first["dashboard_override_requested"])
        self.assertTrue(retry["dashboard_override_requested"])
        self.assertTrue(acknowledged["dashboard_override_label_applied"])
        self.assertFalse(acknowledged["dashboard_override_requested"])
        self.assertFalse(removed["dashboard_override"])

    def test_newer_command_reapplies_removed_override(self) -> None:
        previous = {
            "dashboard_override": False,
            "dashboard_override_command_id": 3,
            "dashboard_override_requested": False,
        }
        raw = {
            "issue_comments": [
                {"id": 3, "user": {"login": "author"}, "body": "/dashboard route:reviewers"},
                {"id": 5, "user": {"login": "author"}, "body": "/dashboard route:reviewers"},
            ]
        }

        facts = dashboard_override.dashboard_override_facts(
            raw,
            "author",
            set(),
            previous,
        )

        self.assertTrue(facts["dashboard_override_requested"])
        self.assertEqual(5, facts["dashboard_override_command_id"])

    def test_command_only_overrides_author_route(self) -> None:
        for route, expected_route, expected_pending in (
            ("author", "approver", True),
            ("approver", "approver", False),
            ("maintainer", "maintainer", False),
            ("external", "external", False),
        ):
            with self.subTest(route=route):
                facts = {
                    "dashboard_override_label_applied": False,
                    "dashboard_override_requested": True,
                }

                actual = dashboard_override.apply_dashboard_override(facts, route)

                self.assertEqual(expected_route, actual)
                self.assertEqual(expected_pending, facts["dashboard_override_requested"])

    def test_label_always_routes_to_reviewers(self) -> None:
        facts = {
            "dashboard_override_label_applied": True,
            "dashboard_override_requested": False,
        }

        route = dashboard_override.apply_dashboard_override(facts, "maintainer")

        self.assertEqual("approver", route)
        self.assertTrue(facts["dashboard_override"])

    def test_releases_label_once_natural_route_reaches_reviewers(self) -> None:
        facts = {
            "dashboard_override_label_applied": True,
            "dashboard_override_requested": False,
        }

        route = dashboard_override.apply_dashboard_override(facts, "approver")

        self.assertEqual("approver", route)
        self.assertTrue(facts["dashboard_override_release_requested"])

    def test_does_not_release_label_while_override_holds_author_route(self) -> None:
        facts = {
            "dashboard_override_label_applied": True,
            "dashboard_override_requested": False,
        }

        route = dashboard_override.apply_dashboard_override(facts, "author")

        self.assertEqual("approver", route)
        self.assertFalse(facts["dashboard_override_release_requested"])

    @patch.object(dashboard_override, "run_gh")
    @patch.object(
        dashboard_override,
        "load_dashboard_state_cache",
        return_value={
            "prs": {
                "9": {"facts": {"dashboard_override_release_requested": True}},
            }
        },
    )
    def test_delivery_removes_released_override_label(self, _load_state, run_gh) -> None:
        errors = dashboard_override.deliver_dashboard_override_requests(
            "open-telemetry/example"
        )

        self.assertEqual([], errors)
        self.assertEqual(
            call([
                "gh", "api", "--method", "DELETE",
                "repos/open-telemetry/example/issues/9/labels/dashboard%3Aroute-overridden",
            ]),
            run_gh.call_args_list[-1],
        )

    @patch.object(dashboard_override, "run_gh")
    @patch.object(
        dashboard_override,
        "load_dashboard_state_cache",
        return_value={"prs": {}},
    )
    def test_creates_label_without_pending_command(self, _load_state, run_gh) -> None:
        errors = dashboard_override.deliver_dashboard_override_requests(
            "open-telemetry/example"
        )

        self.assertEqual([], errors)
        run_gh.assert_called_once_with([
            "gh", "label", "create", "dashboard:route-overridden",
            "--repo", "open-telemetry/example",
            "--color", dashboard_override.DASHBOARD_OVERRIDE_LABEL_COLOR,
            "--description", dashboard_override.DASHBOARD_OVERRIDE_LABEL_DESCRIPTION,
            "--force",
        ])

    @patch.object(dashboard_override, "run_gh")
    @patch.object(
        dashboard_override,
        "load_dashboard_state_cache",
        return_value={
            "prs": {
                "7": {"facts": {"dashboard_override_requested": True}},
                "8": {"facts": {"dashboard_override_requested": False}},
            }
        },
    )
    def test_delivers_pending_override_label(self, _load_state, run_gh) -> None:
        errors = dashboard_override.deliver_dashboard_override_requests(
            "open-telemetry/example"
        )

        self.assertEqual([], errors)
        self.assertEqual(
            [
                call([
                    "gh", "label", "create", "dashboard:route-overridden",
                    "--repo", "open-telemetry/example",
                    "--color", dashboard_override.DASHBOARD_OVERRIDE_LABEL_COLOR,
                    "--description", dashboard_override.DASHBOARD_OVERRIDE_LABEL_DESCRIPTION,
                    "--force",
                ]),
                call([
                    "gh", "api", "--method", "POST",
                    "repos/open-telemetry/example/issues/7/labels",
                    "-f", "labels[]=dashboard:route-overridden",
                ]),
            ],
            run_gh.call_args_list,
        )


if __name__ == "__main__":
    unittest.main()