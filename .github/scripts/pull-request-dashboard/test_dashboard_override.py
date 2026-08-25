from __future__ import annotations

import unittest
from unittest.mock import call, patch

import dashboard_override
import dashboard_override_delivery
from dashboard_contracts import DashboardCommandReply, DashboardRoute
from dashboard_test_support import (
    dashboard_facts,
    dashboard_state,
    stored_dashboard_result,
)


def result_facts(
    override: dashboard_override.DashboardOverrideFacts,
    **changes,
):
    values = {
        "dashboard_override_command_id": override.command_id,
        "dashboard_override_command_user": override.command_user,
        "dashboard_override_head_sha": override.head_sha,
        "dashboard_command_replies": override.command_replies,
    }
    values.update(changes)
    return dashboard_facts(**values)


class DashboardOverrideTest(unittest.TestCase):
    def test_override_guidance_matches_pre_review_route(self) -> None:
        guidance = dashboard_override.author_override_guidance()

        self.assertIn("waiting on the author to waiting on reviewers", guidance)
        self.assertIn("the head it sees when it reads the command", guidance)
        self.assertNotIn("immediately", guidance)

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

    def test_latest_authorized_command_ignores_previously_rejected_command(self) -> None:
        raw = {
            "issue_comments": [
                {
                    "id": 3,
                    "user": {"login": "new-approver"},
                    "body": "/dashboard route:reviewers",
                },
                {
                    "id": 4,
                    "user": {"login": "opentelemetry-pr-dashboard[bot]"},
                    "body": dashboard_override.command_reply_marker(3),
                },
            ]
        }

        self.assertEqual(
            (0, ""),
            dashboard_override.latest_authorized_command(
                raw, "author", {"new-approver"}
            ),
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
            (
                DashboardCommandReply(2, "unauthorized", "outsider", "route:reviewers"),
                DashboardCommandReply(3, "unknown_command", "reviewer", "frobnicate"),
                DashboardCommandReply(4, "unknown_command", "author"),
            ),
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

        self.assertEqual((), dashboard_override.pending_command_replies(raw, "author"))

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
            (
                DashboardCommandReply(
                    2,
                    "unauthorized",
                    "outsider",
                    "route:reviewers",
                ),
            ),
            replies,
        )

    def test_renders_command_replies(self) -> None:
        unauthorized = dashboard_override.render_command_reply(
            DashboardCommandReply(2, "unauthorized", "outsider", "route:reviewers")
        )
        unknown = dashboard_override.render_command_reply(
            DashboardCommandReply(3, "unknown_command", "reviewer", "frobnicate")
        )
        routed = dashboard_override.render_command_reply(
            DashboardCommandReply(
                4,
                "routed",
                "author",
                route=DashboardRoute.APPROVER,
            )
        )
        gate_held = dashboard_override.render_command_reply(
            DashboardCommandReply(
                5,
                "routed",
                "author",
                route=DashboardRoute.APPROVER,
                held_gates="the required status checks",
            )
        )
        maintainer = dashboard_override.render_command_reply(
            DashboardCommandReply(
                6,
                "routed",
                "author",
                route=DashboardRoute.MAINTAINER,
            )
        )

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

    def test_renders_routed_reply_for_a_command_bound_to_an_earlier_head(self) -> None:
        for held_gates in ("", "the Copilot review"):
            with self.subTest(held_gates=held_gates):
                body = dashboard_override.render_command_reply(
                    DashboardCommandReply(
                        7,
                        "routed",
                        "author",
                        route=DashboardRoute.AUTHOR,
                        held_gates=held_gates,
                    )
                )

                self.assertIn(dashboard_override.override_ack_marker(7), body)
                self.assertIn(
                    "@author, your reviewer-routing request is not active for the "
                    "current pull request head; comment `/dashboard route:reviewers` "
                    "again to hand the current head to reviewers.",
                    body,
                )

    def test_ack_marker_records_the_bound_head(self) -> None:
        body = dashboard_override.render_command_reply(
            DashboardCommandReply(
                7,
                "routed",
                "author",
                head_sha="abcdef123456",
                route=DashboardRoute.APPROVER,
            )
        )

        self.assertIn(
            "<!-- pull-request-dashboard-override-ack:7:abcdef123456 -->", body
        )

    def test_pending_command_binds_to_the_head_this_pass_observed(self) -> None:
        raw = {
            "issue_comments": [
                {"id": 5, "user": {"login": "author"}, "body": "/dashboard route:reviewers"},
            ]
        }

        facts = dashboard_override.dashboard_override_facts(
            raw, "author", None, "current-head"
        )

        self.assertEqual(5, facts.command_id)
        self.assertEqual("current-head", facts.head_sha)

    def test_pending_command_keeps_its_first_observed_head(self) -> None:
        raw = {
            "issue_comments": [
                {"id": 5, "user": {"login": "author"}, "body": "/dashboard route:reviewers"},
            ]
        }
        first = dashboard_override.dashboard_override_facts(
            raw, "author", None, "first-head"
        )

        retry = dashboard_override.dashboard_override_facts(
            raw, "author", None, "later-head", result_facts(first)
        )

        self.assertEqual(5, retry.command_id)
        self.assertEqual("first-head", retry.head_sha)

    def test_new_pending_command_binds_to_the_newly_observed_head(self) -> None:
        previous_raw = {
            "issue_comments": [
                {"id": 5, "user": {"login": "author"}, "body": "/dashboard route:reviewers"},
            ]
        }
        previous_facts = dashboard_override.dashboard_override_facts(
            previous_raw, "author", None, "first-head"
        )
        raw = {
            "issue_comments": [
                *previous_raw["issue_comments"],
                {"id": 6, "user": {"login": "author"}, "body": "/dashboard route:reviewers"},
            ]
        }

        facts = dashboard_override.dashboard_override_facts(
            raw, "author", None, "later-head", result_facts(previous_facts)
        )

        self.assertEqual(6, facts.command_id)
        self.assertEqual("later-head", facts.head_sha)

    def test_acknowledged_command_keeps_its_bound_head(self) -> None:
        raw = {
            "issue_comments": [
                {"id": 5, "user": {"login": "author"}, "body": "/dashboard route:reviewers"},
                {
                    "id": 9,
                    "user": {"login": "opentelemetry-pr-dashboard[bot]"},
                    "body": dashboard_override.override_ack_marker(5, "bound-head"),
                },
            ]
        }

        facts = dashboard_override.dashboard_override_facts(
            raw, "author", None, "current-head"
        )

        self.assertEqual(0, facts.command_id)
        self.assertEqual("bound-head", facts.head_sha)

    def test_marker_without_a_head_still_retires_its_command(self) -> None:
        # Acknowledgements written before the dashboard recorded a head have to
        # keep retiring their command, or an old command would run again.
        raw = {
            "issue_comments": [
                {"id": 5, "user": {"login": "author"}, "body": "/dashboard route:reviewers"},
                {
                    "id": 9,
                    "user": {"login": "opentelemetry-pr-dashboard[bot]"},
                    "body": dashboard_override.override_ack_marker(5),
                },
            ]
        }

        facts = dashboard_override.dashboard_override_facts(
            raw, "author", None, "current-head"
        )

        self.assertEqual(0, facts.command_id)
        self.assertEqual("", facts.head_sha)

    def test_forged_acknowledgement_does_not_bind_a_head(self) -> None:
        raw = {
            "issue_comments": [
                {"id": 5, "user": {"login": "author"}, "body": "/dashboard route:reviewers"},
                {
                    "id": 9,
                    "user": {"login": "outsider"},
                    "body": dashboard_override.override_ack_marker(5, "forged-head"),
                },
            ]
        }

        facts = dashboard_override.dashboard_override_facts(
            raw, "author", None, "current-head"
        )

        self.assertEqual(5, facts.command_id)
        self.assertEqual("current-head", facts.head_sha)

    def test_appends_routed_reply_for_break_glass_command_that_cleared_nothing(self) -> None:
        facts = dashboard_facts(
            author="author",
            dashboard_override_command_id=12,
            dashboard_override_head_sha="bound-head",
        )

        facts = dashboard_override.append_command_ack_reply(
            {"issue_comments": []},
            facts,
            DashboardRoute.APPROVER,
        )

        self.assertEqual(
            (
                DashboardCommandReply(
                    12,
                    "routed",
                    "author",
                    head_sha="bound-head",
                    route=DashboardRoute.APPROVER,
                ),
            ),
            facts.dashboard_command_replies,
        )

    def test_no_ack_reply_without_a_pending_command(self) -> None:
        facts = dashboard_facts(author="author")

        updated = dashboard_override.append_command_ack_reply(
            {"issue_comments": []},
            facts,
            DashboardRoute.AUTHOR,
        )

        self.assertEqual((), updated.dashboard_command_replies)

    def test_acknowledges_a_command_even_without_a_bound_head(self) -> None:
        # An unbound head only leaves the handoff inactive. Withholding the
        # acknowledgement would leave the command pending forever instead.
        facts = dashboard_facts(
            author="author",
            dashboard_override_command_id=12,
        )

        facts = dashboard_override.append_command_ack_reply(
            {"issue_comments": []},
            facts,
            DashboardRoute.AUTHOR,
        )

        self.assertEqual(
            (
                DashboardCommandReply(
                    12,
                    "routed",
                    "author",
                    route=DashboardRoute.AUTHOR,
                ),
            ),
            facts.dashboard_command_replies,
        )

    def test_ack_reply_deduped_by_existing_marker(self) -> None:
        facts = dashboard_facts(
            author="author",
            dashboard_override_command_id=12,
            dashboard_override_head_sha="bound-head",
        )
        raw = {
            "issue_comments": [
                {
                    "user": {"login": "opentelemetry-pr-dashboard[bot]"},
                    "body": dashboard_override.command_reply_marker(12) + "\n@author ...",
                },
            ]
        }

        facts = dashboard_override.append_command_ack_reply(
            raw,
            facts,
            DashboardRoute.APPROVER,
        )

        self.assertEqual((), facts.dashboard_command_replies)

    def test_forged_marker_does_not_dedupe_ack_reply(self) -> None:
        facts = dashboard_facts(
            author="author",
            dashboard_override_command_id=12,
            dashboard_override_head_sha="bound-head",
        )
        raw = {
            "issue_comments": [
                {
                    "user": {"login": "outsider"},
                    "body": dashboard_override.command_reply_marker(12) + "\n@author ...",
                },
            ]
        }

        facts = dashboard_override.append_command_ack_reply(
            raw,
            facts,
            DashboardRoute.APPROVER,
        )

        self.assertEqual(
            (
                DashboardCommandReply(
                    12,
                    "routed",
                    "author",
                    head_sha="bound-head",
                    route=DashboardRoute.APPROVER,
                ),
            ),
            facts.dashboard_command_replies,
        )

    @patch.object(dashboard_override_delivery, "run_gh")
    @patch.object(dashboard_override_delivery, "gh_api", return_value=[])
    @patch.object(
        dashboard_override_delivery,
        "load_dashboard_state_cache",
        return_value=dashboard_state(stored_dashboard_result(
            5,
            facts=dashboard_facts(dashboard_command_replies=(
                DashboardCommandReply(
                    2,
                    "unauthorized",
                    "outsider",
                    "route:reviewers",
                ),
            )),
        )),
    )
    def test_delivers_pending_command_reply(self, _load_state, gh_api, run_gh) -> None:
        errors = dashboard_override_delivery.deliver_dashboard_command_replies(
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

    @patch.object(dashboard_override_delivery, "run_gh")
    @patch.object(
        dashboard_override_delivery,
        "gh_api",
        return_value=[
            {
                "performed_via_github_app": {"slug": "opentelemetry-pr-dashboard"},
                "body": "<!-- pull-request-dashboard-command-reply:2 --> already replied",
            }
        ],
    )
    @patch.object(
        dashboard_override_delivery,
        "load_dashboard_state_cache",
        return_value=dashboard_state(stored_dashboard_result(
            5,
            facts=dashboard_facts(dashboard_command_replies=(
                DashboardCommandReply(
                    2,
                    "unauthorized",
                    "outsider",
                    "route:reviewers",
                ),
            )),
        )),
    )
    def test_delivery_skips_already_replied_command(self, _load_state, _gh_api, run_gh) -> None:
        errors = dashboard_override_delivery.deliver_dashboard_command_replies(
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

        self.assertEqual(3, first.command_id)
        self.assertEqual(3, retry.command_id)
        self.assertEqual(0, acknowledged.command_id)

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

        self.assertEqual(5, facts.command_id)

    def test_rebuilds_unacknowledged_reply_across_refreshes(self) -> None:
        raw = {
            "issue_comments": [
                {"id": 5, "user": {"login": "author"}, "body": "/dashboard route:reviewers"},
            ]
        }

        for _ in range(2):
            override = dashboard_override.dashboard_override_facts(
                raw, "author", None, "current-head"
            )
            facts = dashboard_override.append_command_ack_reply(
                raw,
                result_facts(override, author="author"),
                DashboardRoute.APPROVER,
            )

            self.assertEqual(
                (
                    DashboardCommandReply(
                        5,
                        "routed",
                        "author",
                        head_sha="current-head",
                        route=DashboardRoute.APPROVER,
                    ),
                ),
                facts.dashboard_command_replies,
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

        self.assertEqual(0, facts.command_id)
        self.assertEqual((), facts.command_replies)

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

    def test_command_that_cleared_nothing_is_acknowledged_where_it_is_routed(self) -> None:
        raw = {
            "issue_comments": [
                {"id": 5, "user": {"login": "author"}, "body": "/dashboard route:reviewers"},
            ]
        }
        override = dashboard_override.dashboard_override_facts(
            raw, "author", None, "current-head"
        )

        facts = dashboard_override.append_command_ack_reply(
            raw,
            result_facts(override, author="author"),
            DashboardRoute.AUTHOR,
        )

        self.assertEqual(
            (
                DashboardCommandReply(
                    5,
                    "routed",
                    "author",
                    head_sha="current-head",
                    route=DashboardRoute.AUTHOR,
                ),
            ),
            facts.dashboard_command_replies,
        )

    def test_conflict_does_not_defer_override_acknowledgement(self) -> None:
        raw = {
            "issue_comments": [
                {"id": 5, "user": {"login": "author"}, "body": "/dashboard route:reviewers"},
            ]
        }
        override = dashboard_override.dashboard_override_facts(
            raw, "author", None, "current-head"
        )

        facts = dashboard_override.append_command_ack_reply(
            raw,
            result_facts(override, author="author", conflicts="yes"),
            DashboardRoute.APPROVER,
        )

        self.assertEqual(
            (
                DashboardCommandReply(
                    5,
                    "routed",
                    "author",
                    head_sha="current-head",
                    route=DashboardRoute.APPROVER,
                ),
            ),
            facts.dashboard_command_replies,
        )

    @patch.object(dashboard_override_delivery, "run_gh")
    @patch.object(dashboard_override_delivery, "gh_api", return_value=[])
    @patch.object(
        dashboard_override_delivery,
        "load_dashboard_state_cache",
        return_value=dashboard_state(stored_dashboard_result(
            7,
            facts=dashboard_facts(dashboard_command_replies=(
                DashboardCommandReply(
                    3,
                    "routed",
                    "author",
                    route=DashboardRoute.APPROVER,
                    held_gates="the Copilot review",
                ),
            )),
        )),
    )
    def test_delivers_command_acknowledgement(self, _load_state, _gh_api, run_gh) -> None:
        errors = dashboard_override_delivery.deliver_dashboard_command_replies(
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