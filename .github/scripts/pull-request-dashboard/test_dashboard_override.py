from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import call, patch

import dashboard_override
import dashboard_override_delivery
import state
from dashboard_contracts import DashboardCommandReply, DashboardRoute, RoutingHandoff
from dashboard_test_support import (
    actor,
    dashboard_facts,
    dashboard_state,
    issue_comment,
    stored_dashboard_result,
)
from pull_request_source import IssueComment
from routing_decision import reviewer_handoff_active


def override_input(
    *comments: IssueComment,
) -> dashboard_override.DashboardOverrideInput:
    return dashboard_override.DashboardOverrideInput(comments)


def result_facts(
    override: dashboard_override.DashboardOverrideFacts,
    **changes,
):
    values = {
        "dashboard_override_command_id": override.command_id,
        "dashboard_override_command_user": override.command_user,
        "handoff": override.handoff,
        "dashboard_top_level_feedback_cutoff": (
            override.top_level_feedback_cutoff
        ),
        "dashboard_command_replies": override.command_replies,
    }
    values.update(changes)
    return dashboard_facts(**values)


class DashboardOverrideTest(unittest.TestCase):
    def test_dashboard_command_body_remainder(self) -> None:
        self.assertIsNone(
            dashboard_override.dashboard_command_body_remainder(
                issue_comment(body="just a normal comment")
            )
        )
        self.assertEqual(
            "",
            dashboard_override.dashboard_command_body_remainder(
                issue_comment(body="/dashboard route:reviewers")
            ),
        )
        self.assertEqual(
            "I addressed everything by doing X.",
            dashboard_override.dashboard_command_body_remainder(
                issue_comment(
                    body=(
                        "/dashboard route:reviewers\n\n"
                        "I addressed everything by doing X."
                    )
                )
            ),
        )
        self.assertEqual(
            "I addressed the feedback.\nAdditional context follows.",
            dashboard_override.dashboard_command_body_remainder(
                issue_comment(
                    body=(
                        "/dashboard route:reviewers I addressed the feedback.\n"
                        "Additional context follows."
                    )
                )
            ),
        )

    def test_latest_authorized_command_accepts_author_and_approvers(self) -> None:
        source = override_input(
            issue_comment(
                database_id=1,
                actor=actor("outsider"),
                body="/dashboard route:reviewers",
            ),
            issue_comment(
                database_id=2,
                body="text /dashboard route:reviewers",
            ),
            issue_comment(
                database_id=3,
                body="/dashboard route:reviewers\nplease review",
            ),
            issue_comment(
                database_id=4,
                actor=actor("Approver"),
                body="/dashboard route:reviewers",
            ),
        )

        self.assertEqual(
            4,
            dashboard_override.dashboard_override_facts(
                source, "author", {"approver"}
            ).command_id,
        )

    def test_latest_authorized_command_uses_content_update_time(self) -> None:
        source = override_input(issue_comment(
            database_id=4,
            body="/dashboard route:reviewers",
            created_at="2026-08-16T07:00:00Z",
            content_updated_at="2026-08-16T09:00:00Z",
        ))

        facts = dashboard_override.dashboard_override_facts(source, "author")
        self.assertEqual("2026-08-16T09:00:00Z", facts.top_level_feedback_cutoff)

    def test_latest_authorized_command_cutoff_never_predates_creation(self) -> None:
        source = override_input(issue_comment(
            database_id=4,
            body="/dashboard route:reviewers",
            created_at="2026-08-16T09:00:00Z",
            content_updated_at="2026-08-16T07:00:00Z",
        ))

        facts = dashboard_override.dashboard_override_facts(source, "author")
        self.assertEqual("2026-08-16T09:00:00Z", facts.top_level_feedback_cutoff)

    def test_latest_authorized_command_ignores_app_acknowledged_command(self) -> None:
        source = override_input(
            issue_comment(
                database_id=3,
                body="/dashboard route:reviewers",
            ),
            issue_comment(
                database_id=4,
                actor=actor("opentelemetry-pr-dashboard[bot]"),
                body=dashboard_override.override_ack_marker(3),
            ),
        )

        facts = dashboard_override.dashboard_override_facts(source, "author")
        self.assertEqual(0, facts.command_id)

    def test_latest_authorized_command_ignores_previously_rejected_command(self) -> None:
        source = override_input(
            issue_comment(
                database_id=3,
                actor=actor("new-approver"),
                body="/dashboard route:reviewers",
            ),
            issue_comment(
                database_id=4,
                actor=actor("opentelemetry-pr-dashboard[bot]"),
                body=dashboard_override.command_reply_marker(3),
            ),
        )

        self.assertEqual(
            0,
            dashboard_override.dashboard_override_facts(
                source, "author", {"new-approver"}
            ).command_id,
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

    def test_author_command_rejects_outsiders_and_unattended_authors(self) -> None:
        source = override_input(
            issue_comment(
                database_id=1,
                actor=actor("outsider"),
                body="/dashboard route:author",
            ),
            issue_comment(
                database_id=2,
                actor=actor("approver"),
                body="/dashboard route:author",
            ),
        )
        facts = dashboard_override.dashboard_override_facts(
            source, "app[bot]", {"approver"}, "head", author_can_act=False
        )
        self.assertEqual(0, facts.handoff.command_id)
        outsider, approver = facts.command_replies
        self.assertIn(
            "can use `/dashboard route:author`.",
            dashboard_override.render_command_reply(outsider),
        )
        self.assertIn(
            "has no human author",
            dashboard_override.render_command_reply(approver),
        )

    def test_pending_replies_for_unauthorized_and_unknown_commands(self) -> None:
        source = override_input(
            issue_comment(
                database_id=1,
                body="/dashboard route:reviewers",
            ),
            issue_comment(
                database_id=2,
                actor=actor("outsider"),
                body="/dashboard route:reviewers",
            ),
            issue_comment(
                database_id=3,
                actor=actor("reviewer"),
                body="/dashboard frobnicate",
            ),
            issue_comment(database_id=4, body="/dashboard"),
            issue_comment(
                database_id=5,
                actor=actor("reviewer"),
                body="looks good to me",
            ),
            issue_comment(
                database_id=6,
                actor=actor("approver"),
                body="/dashboard route:reviewers",
            ),
        )

        facts = dashboard_override.dashboard_override_facts(
            source, "author", {"approver"}
        )

        self.assertEqual(6, facts.command_id)
        self.assertEqual(
            (
                DashboardCommandReply(2, "unauthorized", "outsider", "route:reviewers"),
                DashboardCommandReply(3, "unknown_command", "reviewer", "frobnicate"),
                DashboardCommandReply(4, "unknown_command", "author"),
            ),
            facts.command_replies,
        )

    def test_already_replied_commands_are_not_repeated(self) -> None:
        source = override_input(
            issue_comment(
                database_id=2,
                actor=actor("outsider"),
                body="/dashboard route:reviewers",
            ),
            issue_comment(
                database_id=9,
                actor=actor("opentelemetry-pr-dashboard[bot]"),
                body=dashboard_override.command_reply_marker(2) + "\n@outsider ...",
            ),
        )

        self.assertEqual(
            (),
            dashboard_override.dashboard_override_facts(source, "author").command_replies,
        )

    def test_forged_marker_from_non_app_user_does_not_suppress_reply(self) -> None:
        source = override_input(
            issue_comment(
                database_id=2,
                actor=actor("outsider"),
                body="/dashboard route:reviewers",
            ),
            issue_comment(
                database_id=9,
                actor=actor("outsider"),
                body=(
                    dashboard_override.command_reply_marker(2)
                    + "\nnothing to see here"
                ),
            ),
        )

        facts = dashboard_override.dashboard_override_facts(source, "author")

        self.assertEqual(
            (
                DashboardCommandReply(
                    2,
                    "unauthorized",
                    "outsider",
                    "route:reviewers",
                ),
            ),
            facts.command_replies,
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
                since="2026-08-16T08:00:00Z",
                top_level_feedback_cutoff="2026-08-16T08:00:00Z",
            )
        )
        conservative = dashboard_override.render_command_reply(
            DashboardCommandReply(
                8,
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
            "Only the pull request author or a member of an approving "
            "team can use `/dashboard route:reviewers`.",
            unauthorized,
        )
        self.assertIn(dashboard_override.command_reply_marker(3), unknown)
        self.assertIn(
            "`/dashboard frobnicate` is not a recognized dashboard command.",
            unknown,
        )
        for body in (unauthorized, unknown):
            self.assertNotIn("@", body)
        self.assertIn(dashboard_override.command_reply_marker(4), routed)
        self.assertIn(dashboard_override.override_ack_marker(4), routed)
        self.assertIn(
            "This pull request is now waiting on reviewers. Top-level feedback "
            "posted before this request no longer counts; unresolved review "
            "threads stay open. Comment `/dashboard route:author` to hand it back "
            "to the author.",
            routed,
        )
        self.assertNotIn("@author", routed)
        self.assertIn(
            "No top-level feedback was retired because the dashboard couldn't "
            "determine when this command was posted; unresolved review threads "
            "stay open.",
            conservative,
        )
        self.assertIn(dashboard_override.command_reply_marker(5), gate_held)
        self.assertIn(
            "The reviewer handoff is recorded and is waiting on the required "
            "status checks.",
            gate_held,
        )
        self.assertIn(dashboard_override.command_reply_marker(6), maintainer)
        self.assertIn(
            "This pull request has the approvals it needs and is now waiting on "
            "maintainers.",
            maintainer,
        )

    def test_routed_reply_omits_author_handoff_when_author_cannot_act(self) -> None:
        body = dashboard_override.render_command_reply(
            DashboardCommandReply(
                4,
                "routed",
                "reviewer",
                route=DashboardRoute.APPROVER,
                since="2026-08-16T08:00:00Z",
                top_level_feedback_cutoff="2026-08-16T08:00:00Z",
            ),
            author_can_act=False,
        )

        self.assertIn("This pull request is now waiting on reviewers.", body)
        self.assertNotIn("route:author", body)

    def test_renders_author_handoff_reply_without_mention(self) -> None:
        body = dashboard_override.render_command_reply(
            DashboardCommandReply(
                9,
                "routed",
                "reviewer",
                "route:author",
                head_sha="head",
                route=DashboardRoute.AUTHOR,
            )
        )

        self.assertEqual(
            "This pull request is now waiting on the author until they address "
            "review feedback or push a new commit. Comment "
            "`/dashboard route:reviewers` to hand it to reviewers instead.",
            body.splitlines()[-1],
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
                    "This request applied to an earlier head and is no longer "
                    "active. Comment `/dashboard route:reviewers` again to hand the "
                    "current head to reviewers.",
                    body,
                )
                self.assertNotIn("route:author", body)

    def test_ack_marker_records_the_bound_head(self) -> None:
        body = dashboard_override.render_command_reply(
            DashboardCommandReply(
                7,
                "routed",
                "author",
                head_sha="abcdef123456",
                route=DashboardRoute.APPROVER,
                since="2026-08-16T08:00:00Z",
                persistent_handoff=True,
            )
        )

        self.assertIn(
            "<!-- pull-request-dashboard-override-ack:"
            "7:abcdef123456:2026-08-16T08:00:00Z -->",
            body,
        )
        self.assertIn(
            "<!-- pull-request-dashboard-persistent-reviewer-handoff:"
            "7:abcdef123456 -->",
            body,
        )
        self.assertIn(
            "The handoff stays active across pushes until a reviewer leaves new "
            "actionable feedback.",
            body,
        )

    def test_pending_command_binds_to_the_head_this_pass_observed(self) -> None:
        source = override_input(
            issue_comment(
                database_id=5,
                body="/dashboard route:reviewers",
            )
        )

        facts = dashboard_override.dashboard_override_facts(
            source, "author", None, "current-head"
        )

        self.assertEqual(5, facts.command_id)
        self.assertEqual("current-head", facts.handoff.head_sha)
        self.assertTrue(facts.handoff.persistent)

    def test_persistent_marker_restores_handoff_after_push(self) -> None:
        source = override_input(issue_comment(
            database_id=9,
            actor=actor("opentelemetry-pr-dashboard[bot]"),
            body="\n".join([
                dashboard_override.override_ack_marker(5, "bound-head"),
                dashboard_override.persistent_handoff_marker(5, "bound-head"),
            ]),
        ))

        facts = dashboard_override.dashboard_override_facts(
            source,
            "author",
            None,
            "later-head",
        )

        self.assertEqual(5, facts.handoff.command_id)
        self.assertEqual("bound-head", facts.handoff.head_sha)
        self.assertTrue(facts.handoff.persistent)

    def test_legacy_acknowledgement_remains_head_bound(self) -> None:
        source = override_input(issue_comment(
            database_id=9,
            actor=actor("opentelemetry-pr-dashboard[bot]"),
            body=dashboard_override.override_ack_marker(5, "bound-head"),
        ))

        facts = dashboard_override.dashboard_override_facts(
            source,
            "author",
            None,
            "later-head",
        )

        self.assertFalse(facts.handoff.persistent)

    def test_pending_command_keeps_its_first_observed_head(self) -> None:
        source = override_input(
            issue_comment(
                database_id=5,
                body="/dashboard route:reviewers",
            )
        )
        first = dashboard_override.dashboard_override_facts(
            source, "author", None, "first-head"
        )

        retry = dashboard_override.dashboard_override_facts(
            source, "author", None, "later-head", result_facts(first)
        )

        self.assertEqual(5, retry.command_id)
        self.assertEqual("first-head", retry.handoff.head_sha)

    def test_pending_command_keeps_its_first_observed_cutoff(self) -> None:
        first_source = override_input(
            issue_comment(
                database_id=5,
                body="/dashboard route:reviewers",
                created_at="2026-08-16T08:00:00Z",
                content_updated_at="2026-08-16T08:00:00Z",
            )
        )
        first = dashboard_override.dashboard_override_facts(
            first_source, "author", None, "bound-head"
        )
        edited_source = override_input(
            issue_comment(
                database_id=5,
                body="/dashboard route:reviewers with more context",
                created_at="2026-08-16T08:00:00Z",
                content_updated_at="2026-08-16T10:00:00Z",
            )
        )

        retry = dashboard_override.dashboard_override_facts(
            edited_source,
            "author",
            None,
            "bound-head",
            result_facts(first),
        )

        self.assertEqual("2026-08-16T08:00:00Z", retry.handoff.since)
        self.assertEqual(
            "2026-08-16T08:00:00Z",
            retry.top_level_feedback_cutoff,
        )

    def test_new_pending_command_binds_to_the_newly_observed_head(self) -> None:
        previous_source = override_input(
            issue_comment(
                database_id=5,
                body="/dashboard route:reviewers",
            )
        )
        previous_facts = dashboard_override.dashboard_override_facts(
            previous_source, "author", None, "first-head"
        )
        source = override_input(
            *previous_source.issue_comments,
            issue_comment(
                database_id=6,
                body="/dashboard route:reviewers",
            ),
        )

        facts = dashboard_override.dashboard_override_facts(
            source, "author", None, "later-head", result_facts(previous_facts)
        )

        self.assertEqual(6, facts.command_id)
        self.assertEqual("later-head", facts.handoff.head_sha)

    def test_acknowledged_command_keeps_its_bound_head(self) -> None:
        source = override_input(
            issue_comment(
                database_id=5,
                body="/dashboard route:reviewers",
                created_at="2026-08-16T08:00:00Z",
            ),
            issue_comment(
                database_id=9,
                actor=actor("opentelemetry-pr-dashboard[bot]"),
                body=dashboard_override.override_ack_marker(5, "bound-head"),
            ),
        )

        facts = dashboard_override.dashboard_override_facts(
            source, "author", None, "current-head"
        )

        self.assertEqual(0, facts.command_id)
        self.assertEqual(5, facts.handoff.command_id)
        self.assertEqual("2026-08-16T08:00:00Z", facts.handoff.since)
        self.assertEqual("bound-head", facts.handoff.head_sha)

    def test_acknowledged_command_keeps_only_a_durable_cutoff(self) -> None:
        for previous_facts, acknowledged_since, expected_cutoff in (
            (
                dashboard_facts(
                    handoff=RoutingHandoff(
                        command_id=5,
                        head_sha='bound-head',
                        since='2026-08-16T09:00:00Z',
                    ),
                    dashboard_top_level_feedback_cutoff=(
                        "2026-08-16T09:00:00Z"
                    ),
                ),
                "",
                "2026-08-16T09:00:00Z",
            ),
            (None, "2026-08-16T09:00:00Z", ""),
        ):
            with self.subTest(previous_facts=previous_facts):
                source = override_input(
                    issue_comment(
                        database_id=5,
                        body="/dashboard route:reviewers",
                        created_at="2026-08-16T07:00:00Z",
                        content_updated_at="2026-08-16T10:00:00Z",
                    ),
                    issue_comment(
                        database_id=9,
                        actor=actor("opentelemetry-pr-dashboard[bot]"),
                        body=dashboard_override.override_ack_marker(
                            5,
                            "bound-head",
                            acknowledged_since,
                        ),
                    ),
                )

                facts = dashboard_override.dashboard_override_facts(
                    source,
                    "author",
                    None,
                    "bound-head",
                    previous_facts,
                )

                self.assertEqual("2026-08-16T09:00:00Z", facts.handoff.since)
                self.assertEqual(
                    expected_cutoff,
                    facts.top_level_feedback_cutoff,
                )

    def test_cutoff_marker_is_bound_to_the_acknowledged_command(self) -> None:
        source = override_input(
            issue_comment(
                database_id=9,
                actor=actor("opentelemetry-pr-dashboard[bot]"),
                body="\n".join((
                    dashboard_override.override_ack_marker(
                        5,
                        "bound-head",
                        "2026-08-16T08:00:00Z",
                    ),
                    dashboard_override.top_level_feedback_cutoff_marker(
                        5,
                        "2026-08-16T08:00:00Z",
                    ),
                )),
            ),
            issue_comment(
                database_id=10,
                actor=actor("opentelemetry-pr-dashboard[bot]"),
                body=dashboard_override.top_level_feedback_cutoff_marker(
                    6,
                    "2026-08-16T10:00:00Z",
                ),
            ),
        )

        facts = dashboard_override.dashboard_override_facts(
            source,
            "author",
            None,
            "current-head",
        )

        self.assertEqual(5, facts.handoff.command_id)
        self.assertEqual(
            "2026-08-16T08:00:00Z",
            facts.top_level_feedback_cutoff,
        )

    def test_newer_command_advances_permanent_top_level_cutoff(self) -> None:
        source = override_input(
            issue_comment(
                database_id=6,
                body="/dashboard route:reviewers",
                created_at="2026-08-16T10:00:00Z",
            ),
        )
        previous_facts = dashboard_facts(
            handoff=RoutingHandoff(command_id=5, head_sha='old-head'),
            dashboard_top_level_feedback_cutoff="2026-08-16T08:00:00Z",
        )

        facts = dashboard_override.dashboard_override_facts(
            source,
            "author",
            None,
            "new-head",
            previous_facts,
        )

        self.assertEqual(
            "2026-08-16T10:00:00Z",
            facts.top_level_feedback_cutoff,
        )

    def test_legacy_pending_binding_does_not_gain_persistence(self) -> None:
        source = override_input(issue_comment(
            database_id=5,
            body="/dashboard route:reviewers",
            created_at="2026-08-16T08:00:00Z",
            content_updated_at="2026-08-16T10:00:00Z",
        ))
        for version in (13, 16):
            with self.subTest(version=version):
                legacy_facts = dashboard_facts(
                    dashboard_override_command_id=5,
                    dashboard_override_command_user="author",
                    dashboard_top_level_feedback_cutoff=(
                        "2026-08-16T08:00:00Z"
                        if version == 16
                        else ""
                    ),
                )
                stored = state.encode_dashboard_state(dashboard_state(
                    stored_dashboard_result(7, facts=legacy_facts)
                ))
                stored["version"] = version
                raw_facts = stored["prs"]["7"]["facts"]
                raw_facts.update(
                    dashboard_override_head_sha="bound-head",
                    dashboard_override_since="2026-08-16T08:00:00Z",
                )
                if version == 16:
                    raw_facts["dashboard_override_bound_command_id"] = 5
                loaded = state.decode_dashboard_state(stored)
                previous_facts = loaded.results[0].facts

                migrated = dashboard_override.dashboard_override_facts(
                    source,
                    "author",
                    None,
                    "bound-head",
                    previous_facts,
                )

                self.assertFalse(migrated.handoff.persistent)
                self.assertEqual(
                    "2026-08-16T08:00:00Z",
                    migrated.handoff.since,
                )
                self.assertEqual(
                    (
                        "2026-08-16T08:00:00Z"
                        if version == 16
                        else ""
                    ),
                    migrated.top_level_feedback_cutoff,
                )
                acknowledgement = dashboard_override.append_command_ack_reply(
                    source,
                    result_facts(migrated, author="author"),
                    DashboardRoute.APPROVER,
                ).dashboard_command_replies[0]
                self.assertFalse(acknowledgement.persistent_handoff)
                acknowledgement_body = dashboard_override.render_command_reply(
                    acknowledgement
                )
                self.assertNotIn(
                    dashboard_override.PERSISTENT_HANDOFF_MARKER_PREFIX,
                    acknowledgement_body,
                )
                delivered_source = override_input(
                    *source.issue_comments,
                    issue_comment(
                        database_id=9,
                        actor=actor("opentelemetry-pr-dashboard[bot]"),
                        body=acknowledgement_body,
                    ),
                )

                after_push = dashboard_override.dashboard_override_facts(
                    delivered_source,
                    "author",
                    None,
                    "new-head",
                    result_facts(migrated),
                )
                self.assertFalse(after_push.handoff.persistent)
                self.assertFalse(
                    reviewer_handoff_active(
                        result_facts(after_push, head_sha="new-head")
                    )
                )

    def test_missing_new_command_time_preserves_permanent_cutoff(self) -> None:
        source = override_input(
            issue_comment(
                database_id=6,
                body="/dashboard route:reviewers",
                created_at="",
                updated_at="",
                content_updated_at="",
            ),
        )
        previous_facts = dashboard_facts(
            handoff=RoutingHandoff(command_id=5, head_sha='old-head'),
            dashboard_top_level_feedback_cutoff="2026-08-16T08:00:00Z",
        )

        facts = dashboard_override.dashboard_override_facts(
            source,
            "author",
            None,
            "new-head",
            previous_facts,
        )

        self.assertEqual(
            "2026-08-16T08:00:00Z",
            facts.top_level_feedback_cutoff,
        )

    def test_acknowledgement_preserves_cutoff_across_cache_loss(self) -> None:
        source = override_input(
            issue_comment(
                database_id=6,
                body="/dashboard route:reviewers",
                created_at="",
                updated_at="",
                content_updated_at="",
            ),
        )
        previous_facts = dashboard_facts(
            handoff=RoutingHandoff(command_id=5, head_sha='old-head'),
            dashboard_top_level_feedback_cutoff="2026-08-16T08:00:00Z",
        )
        override = dashboard_override.dashboard_override_facts(
            source,
            "author",
            None,
            "new-head",
            previous_facts,
        )
        facts = dashboard_override.append_command_ack_reply(
            source,
            result_facts(override, author="author"),
            DashboardRoute.APPROVER,
        )

        reply = facts.dashboard_command_replies[0]
        body = dashboard_override.render_command_reply(reply)
        self.assertIn(
            "<!-- pull-request-dashboard-top-level-feedback-cutoff:"
            "6:2026-08-16T08:00:00Z -->",
            body,
        )
        self.assertIn(
            "Top-level feedback retired by an earlier request still doesn't count, "
            "but no newer feedback was retired because the dashboard couldn't "
            "determine when this command was posted; unresolved review threads "
            "stay open.",
            body,
        )

        restored = dashboard_override.dashboard_override_facts(
            override_input(issue_comment(
                database_id=9,
                actor=actor("opentelemetry-pr-dashboard[bot]"),
                body=body,
                created_at="2026-08-16T10:00:00Z",
            )),
            "author",
            None,
            "new-head",
        )

        self.assertEqual(
            "2026-08-16T08:00:00Z",
            restored.top_level_feedback_cutoff,
        )

    def test_acknowledged_command_accepts_a_graphql_command_timestamp(self) -> None:
        source = override_input(
            issue_comment(
                database_id=5,
                body="/dashboard route:reviewers",
                created_at="2026-08-16T08:00:00Z",
            ),
            issue_comment(
                database_id=9,
                actor=actor("opentelemetry-pr-dashboard[bot]"),
                body=dashboard_override.override_ack_marker(5, "bound-head"),
            ),
        )

        facts = dashboard_override.dashboard_override_facts(
            source,
            "author",
            None,
            "current-head",
        )

        self.assertEqual("2026-08-16T08:00:00Z", facts.handoff.since)

    def test_deleted_command_keeps_previous_handoff_cutoff(self) -> None:
        source = override_input(
            issue_comment(
                database_id=9,
                actor=actor("opentelemetry-pr-dashboard[bot]"),
                body=dashboard_override.override_ack_marker(5, "bound-head"),
            )
        )
        previous_facts = dashboard_facts(
            handoff=RoutingHandoff(
                command_id=5,
                head_sha='bound-head',
                since='2026-08-16T08:00:00Z',
            ),
        )

        facts = dashboard_override.dashboard_override_facts(
            source,
            "author",
            None,
            "bound-head",
            previous_facts,
        )

        self.assertEqual("2026-08-16T08:00:00Z", facts.handoff.since)

    def test_disappearing_command_does_not_restore_older_reviewer_handoff(self) -> None:
        for target in ("author", "reviewers"):
            for acknowledged in (False, True):
                with self.subTest(target=target, acknowledged=acknowledged):
                    older_command = issue_comment(
                        database_id=12, body="/dashboard route:reviewers",
                    )
                    older_comments = (older_command,)
                    if acknowledged:
                        older_comments += (issue_comment(
                            database_id=13,
                            actor=actor("opentelemetry-pr-dashboard[bot]"),
                            body="\n".join((
                                dashboard_override.override_ack_marker(12, "head"),
                                dashboard_override.persistent_handoff_marker(12, "head"),
                            )),
                        ),)
                    accepted = dashboard_override.dashboard_override_facts(
                        override_input(*older_comments, issue_comment(
                            database_id=14, body=f"/dashboard route:{target}",
                        )),
                        "author",
                        head_sha="head",
                    )
                    self.assertEqual(14, accepted.command_id)
                    self.assertTrue(accepted.handoff.active_on("head"))

                    previous = accepted
                    for _ in range(2):
                        previous = dashboard_override.dashboard_override_facts(
                            override_input(*older_comments),
                            "author",
                            head_sha="head",
                            previous_facts=result_facts(previous),
                        )
                        self.assertEqual(0, previous.command_id)
                        self.assertEqual(accepted.handoff.release(), previous.handoff)
                        self.assertEqual(
                            accepted.top_level_feedback_cutoff, previous.top_level_feedback_cutoff,
                        )

                    published = issue_comment(
                        database_id=15,
                        actor=actor("opentelemetry-pr-dashboard[bot]"),
                        body="\n".join(dashboard_override.released_handoff_markers(
                            previous.handoff, previous.top_level_feedback_cutoff,
                        )),
                    )
                    recovered = dashboard_override.dashboard_override_facts(
                        override_input(*older_comments, published), "author", head_sha="head",
                    )
                    self.assertEqual(14, recovered.handoff.command_id)
                    self.assertTrue(recovered.handoff.released)

    def test_legacy_acknowledgement_does_not_recover_permanent_cutoff(self) -> None:
        source = override_input(
            issue_comment(
                database_id=9,
                actor=actor("opentelemetry-pr-dashboard[bot]"),
                body=dashboard_override.override_ack_marker(
                    5,
                    "bound-head",
                    "2026-08-16T08:00:00Z",
                ),
            )
        )

        facts = dashboard_override.dashboard_override_facts(
            source,
            "author",
            None,
            "bound-head",
        )

        self.assertEqual(5, facts.handoff.command_id)
        self.assertEqual("bound-head", facts.handoff.head_sha)
        self.assertEqual("2026-08-16T08:00:00Z", facts.handoff.since)
        self.assertEqual("", facts.top_level_feedback_cutoff)

    def test_acknowledgement_timestamp_restores_only_handoff_since(self) -> None:
        source = override_input(
            issue_comment(
                database_id=9,
                actor=actor("opentelemetry-pr-dashboard[bot]"),
                body=dashboard_override.override_ack_marker(5, "bound-head"),
                created_at="2026-08-16T08:00:00Z",
            )
        )

        facts = dashboard_override.dashboard_override_facts(
            source,
            "author",
            None,
            "bound-head",
        )

        self.assertEqual(5, facts.handoff.command_id)
        self.assertEqual("bound-head", facts.handoff.head_sha)
        self.assertEqual("2026-08-16T08:00:00Z", facts.handoff.since)
        self.assertEqual("", facts.top_level_feedback_cutoff)

    def test_status_marker_clears_only_its_bound_handoff(self) -> None:
        source = override_input(
            issue_comment(
                database_id=5,
                body="/dashboard route:reviewers",
                created_at="2026-08-16T08:00:00Z",
            ),
            issue_comment(
                database_id=9,
                actor=actor("opentelemetry-pr-dashboard[bot]"),
                body=dashboard_override.override_ack_marker(5, "bound-head"),
            ),
            issue_comment(
                database_id=10,
                actor=actor("opentelemetry-pr-dashboard[bot]"),
                body=(
                    "<!-- pull-request-dashboard-status -->\n"
                    "<!-- pull-request-dashboard-reviewer-handoff-cleared:"
                    "5:bound-head -->"
                ),
            ),
        )

        cleared = dashboard_override.dashboard_override_facts(
            source, "author", None, "bound-head"
        )
        reapplied_source = override_input(
            *source.issue_comments,
            issue_comment(
                database_id=11,
                body="/dashboard route:reviewers",
                created_at="2026-08-16T09:00:00Z",
            ),
        )
        reapplied = dashboard_override.dashboard_override_facts(
            reapplied_source,
            "author",
            None,
            "bound-head",
            result_facts(cleared),
        )

        self.assertTrue(cleared.handoff.released)
        self.assertEqual(11, reapplied.handoff.command_id)
        self.assertFalse(reapplied.handoff.released)

    def test_marker_without_a_head_still_retires_its_command(self) -> None:
        # Acknowledgements written before the dashboard recorded a head have to
        # keep retiring their command, or an old command would run again.
        source = override_input(
            issue_comment(
                database_id=5,
                body="/dashboard route:reviewers",
            ),
            issue_comment(
                database_id=9,
                actor=actor("opentelemetry-pr-dashboard[bot]"),
                body=dashboard_override.override_ack_marker(5),
            ),
        )

        facts = dashboard_override.dashboard_override_facts(
            source, "author", None, "current-head"
        )

        self.assertEqual(0, facts.command_id)
        self.assertEqual("", facts.handoff.head_sha)

    def test_forged_acknowledgement_does_not_bind_a_head(self) -> None:
        source = override_input(
            issue_comment(
                database_id=5,
                body="/dashboard route:reviewers",
            ),
            issue_comment(
                database_id=9,
                actor=actor("outsider"),
                body=dashboard_override.override_ack_marker(5, "forged-head"),
            ),
        )

        facts = dashboard_override.dashboard_override_facts(
            source, "author", None, "current-head"
        )

        self.assertEqual(5, facts.command_id)
        self.assertEqual("current-head", facts.handoff.head_sha)

    def test_forged_marker_does_not_persist_handoff(self) -> None:
        source = override_input(
            issue_comment(
                database_id=9,
                actor=actor("opentelemetry-pr-dashboard[bot]"),
                body=dashboard_override.override_ack_marker(5, "bound-head"),
            ),
            issue_comment(
                database_id=10,
                actor=actor("outsider"),
                body=dashboard_override.persistent_handoff_marker(
                    5,
                    "bound-head",
                ),
            ),
        )

        facts = dashboard_override.dashboard_override_facts(
            source,
            "author",
            None,
            "later-head",
        )

        self.assertFalse(facts.handoff.persistent)

    def test_appends_routed_reply_for_break_glass_command_that_cleared_nothing(self) -> None:
        facts = dashboard_facts(
            author="author",
            dashboard_override_command_id=12,
            handoff=RoutingHandoff(head_sha='bound-head', since='2026-08-16T08:00:00Z'),
        )

        facts = dashboard_override.append_command_ack_reply(
            override_input(),
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
                    since="2026-08-16T08:00:00Z",
                ),
            ),
            facts.dashboard_command_replies,
        )

    def test_does_not_reply_when_feedback_supersedes_a_pending_command(self) -> None:
        source = override_input(
            issue_comment(
                database_id=12,
                created_at="2026-08-16T08:00:00Z",
            )
        )
        facts = dashboard_facts(
            author="author",
            dashboard_override_command_id=12,
            handoff=RoutingHandoff(
                command_id=12,
                head_sha="bound-head",
                since="2026-08-16T08:00:00Z",
                released=True,
            ),
        )

        facts = dashboard_override.append_command_ack_reply(
            source, facts, DashboardRoute.AUTHOR
        )

        self.assertEqual(
            "2026-08-16T08:00:00Z",
            facts.handoff.since,
        )
        self.assertEqual((), facts.dashboard_command_replies)

    def test_does_not_reply_when_feedback_supersedes_an_acknowledged_command(
        self,
    ) -> None:
        routed_reply = issue_comment(
            database_id=9,
            actor=actor("opentelemetry-pr-dashboard[bot]"),
            body=(
                dashboard_override.command_reply_marker(12)
                + "\n"
                + dashboard_override.override_ack_marker(
                    12,
                    "bound-head",
                    "2026-08-16T08:00:00Z",
                )
            ),
        )
        facts = dashboard_facts(
            author="author",
            handoff=RoutingHandoff(
                command_id=12,
                head_sha='bound-head',
                since='2026-08-16T08:00:00Z',
                released=True,
            ),
        )

        facts = dashboard_override.append_command_ack_reply(
            override_input(routed_reply),
            facts,
            DashboardRoute.AUTHOR,
        )

        self.assertEqual((), facts.dashboard_command_replies)

    def test_status_preserves_legacy_cutoff_when_a_newer_handoff_releases(self) -> None:
        app = actor("opentelemetry-pr-dashboard[bot]")
        cutoff = "2026-08-16T08:00:00Z"
        previous_comment = issue_comment(
            database_id=13,
            actor=app,
            body="\n".join((
                "<!-- pull-request-dashboard-reviewer-handoff-cleared:12:old-head -->",
                dashboard_override.top_level_feedback_cutoff_marker(12, cutoff),
            )),
        )
        rendered = "\n".join((
            "<!-- pull-request-dashboard-status -->",
            "<!-- pull-request-dashboard-status-revision:4 -->",
            *dashboard_override.released_handoff_markers(
                RoutingHandoff(command_id=14, head_sha="new-head", released=True), ""
            ),
        ))

        merged = dashboard_override.preserve_released_handoff_markers(
            rendered, (previous_comment,)
        )
        recovered = dashboard_override.dashboard_override_facts(
            override_input(issue_comment(database_id=13, actor=app, body=merged)),
            "author",
        )

        self.assertEqual(14, recovered.handoff.command_id)
        self.assertTrue(recovered.handoff.released)
        self.assertEqual(cutoff, recovered.top_level_feedback_cutoff)

    def test_status_markers_recover_clearance_after_cache_loss(self) -> None:
        source = override_input(
            issue_comment(
                database_id=9,
                actor=actor("opentelemetry-pr-dashboard[bot]"),
                body="\n".join(
                    [
                        "<!-- pull-request-dashboard-status -->",
                        dashboard_override.override_ack_marker(
                            5,
                            "bound-head",
                            "2026-08-16T08:00:00Z",
                        ),
                        (
                            "<!-- pull-request-dashboard-reviewer-handoff-cleared:"
                            "5:bound-head -->"
                        ),
                    ]
                ),
            )
        )

        facts = dashboard_override.dashboard_override_facts(
            source, "author", None, "bound-head"
        )

        self.assertEqual(5, facts.handoff.command_id)
        self.assertEqual("bound-head", facts.handoff.head_sha)
        self.assertTrue(facts.handoff.released)

    def test_no_ack_reply_without_a_pending_command(self) -> None:
        facts = dashboard_facts(author="author")

        updated = dashboard_override.append_command_ack_reply(
            override_input(),
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
            override_input(),
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
            handoff=RoutingHandoff(head_sha='bound-head'),
        )
        source = override_input(
            issue_comment(
                actor=actor("opentelemetry-pr-dashboard[bot]"),
                body=dashboard_override.command_reply_marker(12) + "\n@author ...",
            )
        )

        facts = dashboard_override.append_command_ack_reply(
            source,
            facts,
            DashboardRoute.APPROVER,
        )

        self.assertEqual((), facts.dashboard_command_replies)

    def test_forged_marker_does_not_dedupe_ack_reply(self) -> None:
        facts = dashboard_facts(
            author="author",
            dashboard_override_command_id=12,
            handoff=RoutingHandoff(head_sha='bound-head'),
        )
        source = override_input(
            issue_comment(
                actor=actor("outsider"),
                body=dashboard_override.command_reply_marker(12) + "\n@author ...",
            )
        )

        facts = dashboard_override.append_command_ack_reply(
            source,
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
    def test_targeted_delivery_skips_other_prs(
        self, _load_state, gh_api, run_gh
    ) -> None:
        errors = dashboard_override_delivery.deliver_dashboard_command_replies(
            "open-telemetry/example",
            only_pr_number=7,
        )

        self.assertEqual([], errors)
        gh_api.assert_not_called()
        run_gh.assert_not_called()

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

    @patch.object(dashboard_override_delivery, "run_gh")
    @patch.object(
        dashboard_override_delivery,
        "gh_api",
        side_effect=RuntimeError("comments unavailable"),
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
    def test_delivery_reports_prs_with_failed_command_replies(
        self,
        _load_state,
        _gh_api,
        _run_gh,
    ) -> None:
        failed_pr_numbers: set[int] = set()

        errors = dashboard_override_delivery.deliver_dashboard_command_replies(
            "open-telemetry/example",
            failed_pr_numbers,
        )

        self.assertEqual(["PR #5: comments unavailable"], errors)
        self.assertEqual({5}, failed_pr_numbers)

    def test_command_stays_pending_until_app_acknowledges_it(self) -> None:
        source = override_input(
            issue_comment(
                database_id=3,
                body="/dashboard route:reviewers",
            )
        )

        first = dashboard_override.dashboard_override_facts(source, "author")
        retry = dashboard_override.dashboard_override_facts(source, "author")
        acknowledged_source = override_input(
            *source.issue_comments,
            issue_comment(
                database_id=4,
                actor=actor("opentelemetry-pr-dashboard[bot]"),
                body=dashboard_override.override_ack_marker(3),
            ),
        )
        acknowledged = dashboard_override.dashboard_override_facts(
            acknowledged_source,
            "author",
        )

        self.assertEqual(3, first.command_id)
        self.assertEqual(3, retry.command_id)
        self.assertEqual(0, acknowledged.command_id)

    def test_newer_command_reapplies_removed_override(self) -> None:
        source = override_input(
            issue_comment(
                database_id=3,
                body="/dashboard route:reviewers",
            ),
            issue_comment(
                database_id=4,
                actor=actor("opentelemetry-pr-dashboard[bot]"),
                body=dashboard_override.override_ack_marker(3),
            ),
            issue_comment(
                database_id=5,
                body="/dashboard route:reviewers",
            ),
        )

        facts = dashboard_override.dashboard_override_facts(source, "author")

        self.assertEqual(5, facts.command_id)

    def test_rebuilds_unacknowledged_reply_across_refreshes(self) -> None:
        source = override_input(
            issue_comment(
                database_id=5,
                body="/dashboard route:reviewers",
            )
        )

        for _ in range(2):
            override = dashboard_override.dashboard_override_facts(
                source, "author", None, "current-head"
            )
            facts = dashboard_override.append_command_ack_reply(
                source,
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
                        since="2026-08-16T07:00:00Z",
                        top_level_feedback_cutoff="2026-08-16T07:00:00Z",
                        persistent_handoff=True,
                    ),
                ),
                facts.dashboard_command_replies,
            )

    def test_acknowledged_command_does_not_replay_after_cache_eviction(self) -> None:
        source = override_input(
            issue_comment(
                database_id=5,
                body="/dashboard route:reviewers",
            ),
            issue_comment(
                database_id=9,
                actor=actor("opentelemetry-pr-dashboard[bot]"),
                body=dashboard_override.override_ack_marker(5) + "\n@author ...",
            ),
        )

        facts = dashboard_override.dashboard_override_facts(source, "author")

        self.assertEqual(0, facts.command_id)
        self.assertEqual((), facts.command_replies)

    def test_acknowledgement_retires_older_commands_but_not_rejections(self) -> None:
        source = override_input(
            issue_comment(
                database_id=3,
                body="/dashboard route:reviewers",
            ),
            issue_comment(
                database_id=4,
                actor=actor("outsider"),
                body="/dashboard route:author",
            ),
            issue_comment(
                database_id=5,
                body="/dashboard route:reviewers",
            ),
            issue_comment(
                database_id=9,
                actor=actor("opentelemetry-pr-dashboard[bot]"),
                body=dashboard_override.override_ack_marker(5),
            ),
        )

        facts = dashboard_override.dashboard_override_facts(source, "author")
        self.assertEqual(0, facts.command_id)
        self.assertEqual(5, facts.handoff.command_id)
        self.assertEqual(
            (DashboardCommandReply(4, "unauthorized", "outsider", "route:author"),),
            facts.command_replies,
        )

    def test_command_that_cleared_nothing_is_acknowledged_where_it_is_routed(self) -> None:
        source = override_input(
            issue_comment(
                database_id=5,
                body="/dashboard route:reviewers",
            )
        )
        override = dashboard_override.dashboard_override_facts(
            source, "author", None, "current-head"
        )

        facts = dashboard_override.append_command_ack_reply(
            source,
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
                    since="2026-08-16T07:00:00Z",
                    top_level_feedback_cutoff="2026-08-16T07:00:00Z",
                    persistent_handoff=True,
                ),
            ),
            facts.dashboard_command_replies,
        )

    def test_conflict_does_not_defer_override_acknowledgement(self) -> None:
        source = override_input(
            issue_comment(
                database_id=5,
                body="/dashboard route:reviewers",
            )
        )
        override = dashboard_override.dashboard_override_facts(
            source, "author", None, "current-head"
        )

        facts = dashboard_override.append_command_ack_reply(
            source,
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
                    since="2026-08-16T07:00:00Z",
                    top_level_feedback_cutoff="2026-08-16T07:00:00Z",
                    persistent_handoff=True,
                ),
            ),
            facts.dashboard_command_replies,
        )

    def test_ack_names_the_open_copilot_findings_holding_the_handoff(self) -> None:
        source = override_input(
            issue_comment(
                database_id=5,
                body="/dashboard route:reviewers",
            )
        )
        override = dashboard_override.dashboard_override_facts(
            source, "author", None, "current-head"
        )

        facts = dashboard_override.append_command_ack_reply(
            source,
            result_facts(
                override,
                author="author",
                route_held_for_gates=True,
                required_checks_settled=True,
                copilot_review_outstanding=True,
                copilot_review_unreported=False,
            ),
            DashboardRoute.APPROVER,
        )

        reply = facts.dashboard_command_replies[0]
        self.assertEqual("the open Copilot review findings", reply.held_gates)
        self.assertIn(
            "The reviewer handoff is recorded and is waiting on the open Copilot "
            "review findings.",
            dashboard_override.render_command_reply(reply),
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
                    "-f", "body=<!-- pull-request-dashboard-command-reply:3 -->\n<!-- pull-request-dashboard-override-ack:3 -->\nThe reviewer handoff is recorded and is waiting on the Copilot review. No top-level feedback was retired because the dashboard couldn't determine when this command was posted; unresolved review threads stay open. Comment `/dashboard route:author` to hand it back to the author.\n",
                ]),
            ],
            run_gh.call_args_list,
        )

    @patch.object(dashboard_override_delivery, "run_gh")
    @patch.object(dashboard_override_delivery, "gh_api")
    @patch.object(
        dashboard_override_delivery,
        "load_dashboard_state_cache",
        return_value=dashboard_state(stored_dashboard_result(
            7,
            facts=dashboard_facts(dashboard_command_replies=(
                DashboardCommandReply(
                    3,
                    "cleared_by_feedback",
                    "author",
                    head_sha="current-head",
                ),
            )),
        )),
    )
    def test_does_not_deliver_legacy_cleared_reply(
        self,
        _load_state,
        gh_api,
        run_gh,
    ) -> None:
        errors = dashboard_override_delivery.deliver_dashboard_command_replies(
            "open-telemetry/example"
        )

        self.assertEqual([], errors)
        gh_api.assert_not_called()
        run_gh.assert_not_called()


if __name__ == "__main__":
    unittest.main()