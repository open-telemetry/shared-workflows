from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import unittest

from dashboard_contracts import DashboardFacts, DashboardRoute
from dashboard_test_support import dashboard_facts
from routing_decision import (
    RoutingInput,
    RoutingOutcome,
    resolve_routing,
    reviewer_handoff_active,
    routing_failure_facts,
)


NOW = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)


class RoutingTestMixin:
    def resolve(
        self,
        facts: DashboardFacts | dict[str, object],
        pending_actions: dict[str, dict[str, object]] | None = None,
        *,
        previous_route: DashboardRoute | str | None = None,
        previous_facts: DashboardFacts | dict[str, object] | None = None,
        required_approvals: int = 1,
        require_clean_copilot_review: bool = False,
        manual_reviewer_handoff: bool = False,
        pending_human_reviewer_logins: frozenset[str] = frozenset(),
        now: datetime = NOW,
    ) -> RoutingOutcome:
        typed_facts = (
            facts
            if isinstance(facts, DashboardFacts)
            else dashboard_facts(**facts)
        )
        typed_previous_facts = (
            previous_facts
            if isinstance(previous_facts, DashboardFacts)
            else dashboard_facts(**(previous_facts or {}))
        )
        outcome = resolve_routing(
            RoutingInput(
                facts=typed_facts,
                pending_actions=pending_actions or {},
                previous_route=(
                    DashboardRoute(previous_route)
                    if previous_route is not None
                    else None
                ),
                previous_facts=typed_previous_facts,
                required_approvals=required_approvals,
                require_clean_copilot_review=require_clean_copilot_review,
                manual_reviewer_handoff=manual_reviewer_handoff,
                pending_human_reviewer_logins=pending_human_reviewer_logins,
                now=now,
            )
        )
        return outcome


class RoutingDecisionTest(RoutingTestMixin, unittest.TestCase):
    def test_normal_route_has_the_complete_expected_outcome(self) -> None:
        facts = {
            "approval_count": 0,
            "is_maintenance_bot": False,
            "ci_failing_count": 0,
            "ci_pending_count": 0,
            "head_sha": "abc",
            "last_author_activity_at": "2026-08-16T08:00:00+00:00",
        }

        outcome = self.resolve(facts)

        self.assertEqual(
            RoutingOutcome(
                route=DashboardRoute.APPROVER,
                facts=dashboard_facts(**{
                    **facts,
                    "copilot_review_request_needed": False,
                    "copilot_review_outstanding": False,
                    "copilot_review_unreported": False,
                    "required_checks_settled": True,
                    "route_hold_expired": False,
                    "route_held_for_gates": False,
                    "waiting_since": "2026-08-16T08:00:00+00:00",
                    "waiting_age_basis": "last_author_activity",
                }),
            ),
            outcome,
        )

    def test_held_route_has_the_complete_expected_outcome(self) -> None:
        facts = {
            "approval_count": 0,
            "is_maintenance_bot": False,
            "ci_failing_count": 0,
            "ci_pending_count": 1,
            "head_sha": "abc",
            "last_author_activity_at": "2026-08-16T08:00:00+00:00",
        }

        outcome = self.resolve(
            facts,
            previous_route="author",
            previous_facts={
                "head_sha": "abc",
                "waiting_since": "2026-08-16T08:00:00+00:00",
                "waiting_age_basis": "last_approver_activity",
            },
        )

        self.assertEqual(
            RoutingOutcome(
                route=DashboardRoute.AUTHOR,
                facts=dashboard_facts(**{
                    **facts,
                    "copilot_review_request_needed": False,
                    "copilot_review_outstanding": False,
                    "copilot_review_unreported": False,
                    "required_checks_settled": False,
                    "route_held_since": "2026-08-16T12:00:00+00:00",
                    "route_hold_expired": False,
                    "route_held_for_gates": True,
                    "waiting_since": "2026-08-16T08:00:00+00:00",
                    "waiting_age_basis": "gate_hold",
                }),
            ),
            outcome,
        )

    def test_expired_route_has_the_complete_expected_outcome(self) -> None:
        facts = {
            "approval_count": 0,
            "is_maintenance_bot": False,
            "ci_failing_count": 0,
            "ci_pending_count": 1,
            "head_sha": "abc",
            "last_author_activity_at": "2026-08-16T08:00:00+00:00",
        }

        outcome = self.resolve(
            facts,
            previous_route="author",
            previous_facts={
                "head_sha": "abc",
                "route_held_since": "2026-08-16T08:00:00+00:00",
                "route_held_for_gates": True,
                "waiting_since": "2026-08-16T08:00:00+00:00",
            },
        )

        self.assertEqual(
            RoutingOutcome(
                route=DashboardRoute.APPROVER,
                facts=dashboard_facts(**{
                    **facts,
                    "copilot_review_request_needed": False,
                    "copilot_review_outstanding": False,
                    "copilot_review_unreported": False,
                    "required_checks_settled": False,
                    "route_held_since": "2026-08-16T08:00:00+00:00",
                    "route_hold_expired": True,
                    "route_held_for_gates": False,
                    "waiting_since": "2026-08-16T12:00:00+00:00",
                    "waiting_age_basis": "gate_release",
                }),
            ),
            outcome,
        )

    def test_manual_handoff_has_the_complete_expected_outcome(self) -> None:
        facts = {
            "approval_count": 0,
            "is_maintenance_bot": False,
            "ci_failing_count": 1,
            "ci_pending_count": 1,
            "head_sha": "abc",
            "dashboard_override_head_sha": "abc",
            "last_author_activity_at": "2026-08-16T08:00:00+00:00",
            "copilot_review_exists": False,
            "copilot_review_requested": False,
        }

        outcome = self.resolve(
            facts,
            require_clean_copilot_review=True,
            manual_reviewer_handoff=True,
        )

        self.assertEqual(
            RoutingOutcome(
                route=DashboardRoute.APPROVER,
                facts=dashboard_facts(**{
                    **facts,
                    "copilot_review_request_needed": False,
                    "copilot_review_outstanding": False,
                    "copilot_review_unreported": False,
                    "required_checks_settled": False,
                    "route_hold_expired": False,
                    "route_held_for_gates": False,
                    "waiting_since": "2026-08-16T08:00:00+00:00",
                    "waiting_age_basis": "last_author_activity",
                }),
            ),
            outcome,
        )

    def test_route_selection_preserves_discussion_and_approval_rules(self) -> None:
        cases = (
            (
                "completed author reply with approval",
                {
                    "approval_count": 1,
                    "ci_failing_count": 0,
                    "ci_pending_count": 0,
                    "is_maintenance_bot": False,
                },
                {"thread": {"action": "reviewer", "since": "2026-08-11T13:44:18Z"}},
                1,
                "maintainer",
            ),
            (
                "unfinished author work",
                {
                    "approval_count": 1,
                    "ci_failing_count": 0,
                    "ci_pending_count": 0,
                    "is_maintenance_bot": False,
                },
                {"thread": {"action": "author", "since": "2026-08-11T13:44:18Z"}},
                1,
                "author",
            ),
            (
                "completed reply without approval",
                {
                    "approval_count": 0,
                    "ci_failing_count": 0,
                    "ci_pending_count": 0,
                    "is_maintenance_bot": False,
                },
                {"thread": {"action": "reviewer", "since": "2026-08-11T13:44:18Z"}},
                1,
                "approver",
            ),
            (
                "Dependabot approval threshold",
                {
                    "author": "app/dependabot",
                    "approval_count": 1,
                    "ci_failing_count": 1,
                    "ci_pending_count": 0,
                    "is_maintenance_bot": True,
                    "author_can_act": False,
                },
                {},
                2,
                "maintainer",
            ),
        )
        for name, facts, actions, required_approvals, expected in cases:
            with self.subTest(name=name):
                self.assertEqual(
                    expected,
                    self.resolve(
                        facts,
                        actions,
                        required_approvals=required_approvals,
                    ).route,
                )

    def test_required_check_failure_routes_human_authored_pr_to_author(self) -> None:
        outcome = self.resolve(
            {
                "approval_count": 1,
                "ci_failing_count": 1,
                "ci_pending_count": 0,
                "ci_failing_since": "2026-07-17T01:00:00+00:00",
                "is_maintenance_bot": False,
            }
        )

        self.assertEqual("author", outcome.route)
        self.assertEqual("2026-07-17T01:00:00+00:00", outcome.facts.waiting_since)
        self.assertEqual("ci_failure", outcome.facts.waiting_age_basis)

    def test_only_maintainer_owned_check_actions_route_by_approval_count(
        self,
    ) -> None:
        for approval_count, expected in (
            (0, DashboardRoute.APPROVER),
            (1, DashboardRoute.MAINTAINER),
        ):
            with self.subTest(approval_count=approval_count):
                outcome = self.resolve({
                    "approval_count": approval_count,
                    "ci_failing_count": 0,
                    "ci_maintainer_action_required_count": 1,
                    "ci_pending_count": 0,
                    "is_maintenance_bot": False,
                })

                self.assertEqual(expected, outcome.route)
                self.assertFalse(outcome.facts.required_checks_settled)
                self.assertFalse(outcome.facts.route_held_for_gates)

    def test_genuine_failure_wins_over_maintainer_owned_check_action(
        self,
    ) -> None:
        outcome = self.resolve({
            "approval_count": 1,
            "ci_failing_count": 1,
            "ci_maintainer_action_required_count": 1,
            "ci_pending_count": 0,
            "is_maintenance_bot": False,
        })

        self.assertEqual(DashboardRoute.AUTHOR, outcome.route)

    def test_pending_checks_still_hold_maintainer_owned_action_route(
        self,
    ) -> None:
        outcome = self.resolve({
            "approval_count": 0,
            "ci_failing_count": 0,
            "ci_maintainer_action_required_count": 1,
            "ci_pending_count": 1,
            "is_maintenance_bot": False,
        })

        self.assertEqual(DashboardRoute.AUTHOR, outcome.route)
        self.assertTrue(outcome.facts.route_held_for_gates)

    def test_reviewer_handoff_is_bound_to_the_current_head(self) -> None:
        self.assertTrue(
            reviewer_handoff_active(
                dashboard_facts(
                    dashboard_override_head_sha="current-head",
                    head_sha="current-head",
                )
            )
        )
        self.assertTrue(
            reviewer_handoff_active(
                dashboard_facts(
                    dashboard_override_head_sha="current-head",
                    head_sha="current-head",
                    dashboard_override_since="not-a-timestamp",
                )
            )
        )
        for facts in (
            {"dashboard_override_head_sha": "old-head", "head_sha": "new-head"},
            {"dashboard_override_head_sha": "", "head_sha": "current-head"},
            {"head_sha": "current-head"},
            {"dashboard_override_head_sha": "current-head", "head_sha": ""},
            {
                "dashboard_override_head_sha": "current-head",
                "head_sha": "current-head",
                "dashboard_override_cleared_by_feedback": True,
            },
        ):
            with self.subTest(facts=facts):
                self.assertFalse(
                    reviewer_handoff_active(dashboard_facts(**facts))
                )

    def test_normal_handoff_remains_gated_by_a_required_copilot_review(self) -> None:
        outcome = self.resolve(
            {
                "approval_count": 0,
                "ci_failing_count": 0,
                "ci_pending_count": 0,
                "copilot_review_exists": True,
                "copilot_review_needed": True,
                "copilot_review_stale": True,
                "copilot_review_requested": False,
            },
            require_clean_copilot_review=True,
        )

        self.assertEqual("author", outcome.route)
        self.assertTrue(outcome.facts.copilot_review_request_needed)
        self.assertTrue(outcome.facts.copilot_review_outstanding)
        self.assertTrue(outcome.facts.route_held_for_gates)

    def test_required_checks_hold_route_progression_but_not_regression(self) -> None:
        cases = (
            ("author", 0, "approver", "author"),
            ("approver", 1, "maintainer", "approver"),
            ("maintainer", 0, "approver", "approver"),
            ("maintainer", 0, "author", "author"),
        )
        for previous_route, approvals, base_route, expected in cases:
            with self.subTest(previous_route=previous_route, base_route=base_route):
                facts = {
                    "approval_count": approvals,
                    "ci_failing_count": 1 if base_route == "author" else 0,
                    "ci_pending_count": 1,
                    "is_maintenance_bot": False,
                }
                self.assertEqual(
                    expected,
                    self.resolve(facts, previous_route=previous_route).route,
                )

    def test_missing_check_results_hold_a_new_pr(self) -> None:
        outcome = self.resolve(
            {"approval_count": 0, "is_maintenance_bot": False}
        )

        self.assertEqual("author", outcome.route)
        self.assertTrue(outcome.facts.route_held_for_gates)

    def test_opentelemetrybot_pr_never_routes_to_its_author(self) -> None:
        facts = {
            "author": "opentelemetrybot",
            "ci_failing_count": 1,
            "ci_pending_count": 0,
            "is_maintenance_bot": True,
            "author_can_act": False,
        }
        pending_actions = {
            "thread": {
                "action": "author",
                "since": "2026-08-11T13:44:18Z",
            }
        }

        awaiting_approval = self.resolve(
            {**facts, "approval_count": 0},
            pending_actions,
            required_approvals=2,
        )
        approved = self.resolve(
            {**facts, "approval_count": 1},
            pending_actions,
            required_approvals=2,
        )

        self.assertEqual("approver", awaiting_approval.route)
        self.assertEqual("maintainer", approved.route)

    def test_other_automation_uses_the_configured_approval_threshold(self) -> None:
        facts = {
            "author": "app/custom-automation",
            "author_can_act": False,
            "ci_failing_count": 1,
            "ci_pending_count": 0,
            "is_maintenance_bot": False,
        }
        pending_actions = {
            "thread": {
                "action": "author",
                "since": "2026-08-11T13:44:18Z",
            }
        }

        one_approval = self.resolve(
            {**facts, "approval_count": 1},
            pending_actions,
            required_approvals=2,
        )
        two_approvals = self.resolve(
            {**facts, "approval_count": 2},
            pending_actions,
            required_approvals=2,
        )

        self.assertEqual("approver", one_approval.route)
        self.assertEqual("maintainer", two_approvals.route)

    def test_a_newly_classified_maintenance_bot_falls_back_to_approvers(self) -> None:
        # A cached result can still say "author" when the pull request author
        # was only classified as a maintenance bot after it was stored, and a
        # maintenance bot has no author route to fall back to.
        outcome = self.resolve(
            {
                "approval_count": 1,
                "ci_pending_count": 1,
                "head_sha": "abc",
                "is_maintenance_bot": True,
                "author_can_act": False,
            },
            previous_route="author",
        )

        self.assertEqual("approver", outcome.route)

    def test_hold_clock_carries_on_the_same_head_and_resets_after_push(self) -> None:
        previous_facts = {
            "head_sha": "abc",
            "route_held_since": "2026-08-16T09:00:00+00:00",
        }
        facts = {
            "approval_count": 0,
            "ci_failing_count": 0,
            "ci_pending_count": 1,
            "head_sha": "abc",
            "is_maintenance_bot": False,
        }

        carried = self.resolve(
            facts,
            previous_route="author",
            previous_facts=previous_facts,
        )
        reset = self.resolve(
            {**facts, "head_sha": "def"},
            previous_route="author",
            previous_facts=previous_facts,
        )

        self.assertEqual(
            "2026-08-16T09:00:00+00:00", carried.facts.route_held_since
        )
        self.assertEqual(
            "2026-08-16T12:00:00+00:00", reset.facts.route_held_since
        )

    def test_a_route_that_does_not_advance_never_starts_the_hold_clock(self) -> None:
        # A pull request already with its reviewers is not a stalled handoff,
        # however long its checks run.
        outcome = self.resolve(
            {
                "approval_count": 1,
                "ci_failing_count": 0,
                "ci_pending_count": 1,
                "head_sha": "abc",
                "is_maintenance_bot": False,
            },
            previous_route="maintainer",
            previous_facts={"head_sha": "abc"},
        )

        self.assertEqual("maintainer", outcome.route)
        self.assertFalse(outcome.facts.route_held_for_gates)
        self.assertIsNone(outcome.facts.route_held_since)
        self.assertFalse(outcome.facts.route_hold_expired)

    def test_settled_gates_clear_the_hold_clock(self) -> None:
        outcome = self.resolve(
            {
                "approval_count": 0,
                "ci_failing_count": 0,
                "ci_pending_count": 0,
                "head_sha": "abc",
                "is_maintenance_bot": False,
            },
            previous_route="author",
            previous_facts={
                "head_sha": "abc",
                "route_held_since": "2026-08-16T09:00:00+00:00",
            },
        )

        self.assertIsNone(outcome.facts.route_held_since)
        self.assertFalse(outcome.facts.route_hold_expired)

    def test_author_round_trip_does_not_restart_an_expired_hold(self) -> None:
        held_since = NOW - timedelta(hours=5)
        previous_facts = {
            "head_sha": "abc",
            "route_held_since": held_since.isoformat(),
        }

        author = self.resolve(
            {
                "approval_count": 0,
                "ci_failing_count": 1,
                "ci_pending_count": 1,
                "head_sha": "abc",
                "is_maintenance_bot": False,
            },
            previous_route="approver",
            previous_facts=previous_facts,
        )
        reviewers = self.resolve(
            {
                "approval_count": 0,
                "ci_failing_count": 0,
                "ci_pending_count": 1,
                "head_sha": "abc",
                "is_maintenance_bot": False,
            },
            previous_route="author",
            previous_facts=previous_facts,
            now=NOW + timedelta(minutes=20),
        )

        self.assertEqual("author", author.route)
        self.assertEqual(held_since.isoformat(), author.facts.route_held_since)
        self.assertTrue(author.facts.route_hold_expired)
        self.assertEqual("approver", reviewers.route)
        self.assertEqual(held_since.isoformat(), reviewers.facts.route_held_since)
        self.assertTrue(reviewers.facts.route_hold_expired)

    def test_conflict_clears_the_hold_clock_and_copilot_request(self) -> None:
        outcome = self.resolve(
            {
                "approval_count": 1,
                "conflicts": "yes",
                "is_maintenance_bot": False,
                "head_sha": "abc",
                "ci_pending_count": 1,
                "copilot_review_exists": False,
                "copilot_review_requested": False,
                "copilot_first_review_missing_since": "2026-08-16T08:00:00+00:00",
            },
            previous_route="approver",
            previous_facts={
                "head_sha": "abc",
                "route_held_since": "2026-08-16T08:00:00+00:00",
            },
            require_clean_copilot_review=True,
            now=datetime(2026, 8, 16, 13, 0, tzinfo=timezone.utc),
        )

        self.assertEqual("approver", outcome.route)
        self.assertFalse(outcome.facts.copilot_review_request_needed)
        self.assertTrue(outcome.facts.copilot_review_outstanding)
        self.assertTrue(outcome.facts.copilot_review_unreported)
        self.assertTrue(outcome.facts.route_held_for_gates)
        self.assertFalse(outcome.facts.route_hold_expired)
        self.assertIsNone(outcome.facts.copilot_first_review_missing_since)
        self.assertIsNone(outcome.facts.route_held_since)

    def test_reported_copilot_findings_do_not_keep_the_hold_clock(self) -> None:
        outcome = self.resolve(
            {
                "approval_count": 0,
                "ci_failing_count": 0,
                "ci_pending_count": 0,
                "head_sha": "abc",
                "is_maintenance_bot": False,
                "copilot_review_exists": True,
                "copilot_review_stale": False,
                "copilot_review_needed": True,
            },
            {"thread": {"action": "author", "since": "2026-08-16T09:00:00Z"}},
            previous_route="approver",
            previous_facts={
                "head_sha": "abc",
                "route_held_since": "2026-08-16T07:00:00+00:00",
            },
            require_clean_copilot_review=True,
        )

        self.assertEqual("author", outcome.route)
        self.assertTrue(outcome.facts.copilot_review_outstanding)
        self.assertFalse(outcome.facts.copilot_review_unreported)
        self.assertIsNone(outcome.facts.route_held_since)
        self.assertFalse(outcome.facts.route_hold_expired)

    def test_stale_copilot_review_can_expire_without_looking_settled(self) -> None:
        outcome = self.resolve(
            {
                "approval_count": 0,
                "ci_failing_count": 0,
                "ci_pending_count": 0,
                "head_sha": "abc",
                "is_maintenance_bot": False,
                "copilot_review_exists": True,
                "copilot_review_stale": True,
                "copilot_review_needed": True,
            },
            previous_route="author",
            previous_facts={
                "head_sha": "abc",
                "route_held_since": "2026-08-16T08:00:00+00:00",
            },
            require_clean_copilot_review=True,
        )

        self.assertEqual("approver", outcome.route)
        self.assertTrue(outcome.facts.copilot_review_unreported)
        self.assertTrue(outcome.facts.route_hold_expired)


class RoutingWaitAgeTest(RoutingTestMixin, unittest.TestCase):
    def test_held_route_carries_the_previous_wait_forward(self) -> None:
        outcome = self.resolve(
            {
                "approval_count": 0,
                "ci_failing_count": 0,
                "ci_pending_count": 1,
                "head_sha": "abc",
                "is_maintenance_bot": False,
                "last_approver_activity_at": "2026-07-10T01:00:00+00:00",
            },
            previous_route="author",
            previous_facts={
                "head_sha": "abc",
                "waiting_since": "2026-07-20T01:00:00+00:00",
                "waiting_age_basis": "ci_failure",
            },
        )

        self.assertEqual("2026-07-20T01:00:00+00:00", outcome.facts.waiting_since)
        self.assertEqual("gate_hold", outcome.facts.waiting_age_basis)

    def test_gate_release_starts_and_then_carries_the_reviewer_wait(self) -> None:
        released = self.resolve(
            {
                "approval_count": 0,
                "ci_failing_count": 0,
                "ci_pending_count": 0,
                "head_sha": "abc",
                "is_maintenance_bot": False,
                "last_author_activity_at": "2026-08-16T08:00:00+00:00",
            },
            previous_route="author",
            previous_facts={
                "head_sha": "abc",
                "route_held_for_gates": True,
                "waiting_since": "2026-08-16T08:00:00+00:00",
            },
        )
        carried = self.resolve(
            {
                "approval_count": 0,
                "ci_failing_count": 0,
                "ci_pending_count": 0,
                "head_sha": "abc",
                "is_maintenance_bot": False,
                "last_author_activity_at": "2026-08-16T08:00:00+00:00",
            },
            previous_route="approver",
            previous_facts={
                "head_sha": "abc",
                "waiting_since": "2026-08-16T12:00:00+00:00",
                "waiting_age_basis": "gate_release",
            },
            now=NOW + timedelta(hours=1),
        )

        self.assertEqual("2026-08-16T12:00:00+00:00", released.facts.waiting_since)
        self.assertEqual("gate_release", released.facts.waiting_age_basis)
        self.assertEqual("2026-08-16T12:00:00+00:00", carried.facts.waiting_since)
        self.assertEqual("gate_release", carried.facts.waiting_age_basis)

    def test_a_release_between_reviewer_routes_keeps_the_wait(self) -> None:
        # This pull request never left the people who owe it a response, so the
        # merge request is as old as the review that produced it.
        outcome = self.resolve(
            {
                "approval_count": 1,
                "ci_failing_count": 0,
                "ci_pending_count": 0,
                "head_sha": "abc",
                "is_maintenance_bot": False,
                "last_author_activity_at": "2026-08-16T08:00:00+00:00",
            },
            previous_route="approver",
            previous_facts={
                "head_sha": "abc",
                "route_held_for_gates": True,
                "waiting_since": "2026-08-10T01:00:00+00:00",
                "waiting_age_basis": "last_author_activity",
            },
        )

        self.assertEqual("maintainer", outcome.route)
        self.assertEqual("2026-08-10T01:00:00+00:00", outcome.facts.waiting_since)
        self.assertEqual(
            "last_author_activity", outcome.facts.waiting_age_basis
        )

    def test_unheld_handoff_dates_from_the_latest_author_activity(self) -> None:
        outcome = self.resolve(
            {
                "approval_count": 0,
                "ci_failing_count": 0,
                "ci_pending_count": 0,
                "is_maintenance_bot": False,
                "last_author_activity_at": "2026-08-16T08:00:00+00:00",
            },
            previous_route="author",
            previous_facts={"waiting_since": "2026-08-10T01:00:00+00:00"},
        )

        self.assertEqual("2026-08-16T08:00:00+00:00", outcome.facts.waiting_since)
        self.assertEqual("last_author_activity", outcome.facts.waiting_age_basis)

    def test_reviewer_rerequest_restarts_and_carries_the_wait(self) -> None:
        facts = {
            "approval_count": 0,
            "ci_failing_count": 0,
            "ci_pending_count": 0,
            "head_sha": "abc",
            "is_maintenance_bot": False,
            "last_author_activity_at": "2026-07-30T01:00:00+00:00",
        }
        started = self.resolve(
            facts,
            previous_route="maintainer",
            previous_facts={
                "head_sha": "abc",
                "waiting_since": "2026-07-30T01:00:00+00:00",
                "reviewers": [{"login": "reviewer", "pending_review": False}],
            },
            pending_human_reviewer_logins=frozenset({"reviewer"}),
            now=datetime(2026, 8, 17, 20, 0, tzinfo=timezone.utc),
        )
        carried = self.resolve(
            facts,
            previous_route="approver",
            previous_facts={
                "head_sha": "abc",
                "waiting_since": "2026-08-17T20:00:00+00:00",
                "waiting_age_basis": "review_rerequest",
                "reviewers": [{"login": "reviewer", "pending_review": True}],
            },
            pending_human_reviewer_logins=frozenset({"reviewer"}),
            now=datetime(2026, 8, 17, 21, 0, tzinfo=timezone.utc),
        )

        self.assertEqual("2026-08-17T20:00:00+00:00", started.facts.waiting_since)
        self.assertEqual("review_rerequest", started.facts.waiting_age_basis)
        self.assertEqual("2026-08-17T20:00:00+00:00", carried.facts.waiting_since)
        self.assertEqual("review_rerequest", carried.facts.waiting_age_basis)

    def test_reviewer_wait_keeps_older_evidence_on_the_same_route(self) -> None:
        outcome = self.resolve(
            {
                "approval_count": 0,
                "ci_failing_count": 0,
                "ci_pending_count": 0,
                "is_maintenance_bot": False,
                "last_author_activity_at": "2026-07-30T01:00:00+00:00",
            },
            previous_route="approver",
            previous_facts={
                "waiting_since": "2026-07-23T01:00:00+00:00",
                "waiting_age_basis": "last_author_activity",
            },
        )

        self.assertEqual("2026-07-23T01:00:00+00:00", outcome.facts.waiting_since)
        self.assertEqual("last_author_activity", outcome.facts.waiting_age_basis)

    def test_reviewer_wait_moves_back_to_newly_found_older_evidence(self) -> None:
        # The wait only moves back while the pull request stays with its
        # reviewers, so evidence older than the carried wait replaces it.
        outcome = self.resolve(
            {
                "approval_count": 0,
                "ci_failing_count": 0,
                "ci_pending_count": 0,
                "is_maintenance_bot": False,
                "last_author_activity_at": "2026-07-10T01:00:00+00:00",
            },
            previous_route="approver",
            previous_facts={
                "waiting_since": "2026-07-23T01:00:00+00:00",
                "waiting_age_basis": "last_author_activity",
            },
        )

        self.assertEqual("2026-07-10T01:00:00+00:00", outcome.facts.waiting_since)
        self.assertEqual("last_author_activity", outcome.facts.waiting_age_basis)

    def test_author_wait_without_a_failure_dates_from_the_last_approver(self) -> None:
        # A pending required check holds the computed approver route at the
        # previous author route. With no failure, conflict, or pending author
        # thread, the wait dates from the last substantive approver activity.
        outcome = self.resolve(
            {
                "approval_count": 0,
                "ci_failing_count": 0,
                "ci_pending_count": 1,
                "conflicts": "no",
                "head_sha": "abc",
                "is_maintenance_bot": False,
                "last_activity_at": "2026-07-20T01:00:00+00:00",
                "last_approver_activity_at": "2026-07-10T01:00:00+00:00",
            },
            previous_route="author",
            previous_facts={"head_sha": "abc"},
        )

        self.assertEqual("author", outcome.route)
        self.assertEqual("2026-07-10T01:00:00+00:00", outcome.facts.waiting_since)
        self.assertEqual(
            "last_approver_activity", outcome.facts.waiting_age_basis
        )

    def test_conflict_wait_dates_from_the_last_author_activity(self) -> None:
        # A conflicted pull request waits on its author from their own last
        # activity, not from the failing check or the last approver activity.
        outcome = self.resolve(
            {
                "approval_count": 0,
                "ci_failing_count": 1,
                "ci_failing_since": "2026-08-10T08:00:00+00:00",
                "ci_pending_count": 0,
                "conflicts": "yes",
                "is_maintenance_bot": False,
                "last_author_activity_at": "2026-08-16T08:00:00+00:00",
                "last_approver_activity_at": "2026-08-09T08:00:00+00:00",
            }
        )

        self.assertEqual("author", outcome.route)
        self.assertEqual("2026-08-16T08:00:00+00:00", outcome.facts.waiting_since)
        self.assertEqual("last_author_activity", outcome.facts.waiting_age_basis)

    def test_conflict_wait_uses_oldest_relevant_author_evidence(self) -> None:
        outcome = self.resolve(
            {
                "approval_count": 0,
                "ci_failing_count": 1,
                "ci_pending_count": 0,
                "conflicts": "yes",
                "is_maintenance_bot": False,
                "last_author_activity_at": "2026-08-16T08:00:00+00:00",
            },
            {
                "thread": {
                    "action": "author",
                    "since": "2026-08-10T08:00:00+00:00",
                }
            },
        )

        self.assertEqual("2026-08-10T08:00:00+00:00", outcome.facts.waiting_since)
        self.assertEqual("oldest_pending_thread", outcome.facts.waiting_age_basis)

    def test_required_check_failure_outranks_only_newer_author_threads(self) -> None:
        cases = (
            (
                "2026-07-17T03:00:00Z",
                "2026-07-17T01:00:00+00:00",
                "ci_failure",
            ),
            (
                "2026-07-16T23:00:00Z",
                "2026-07-16T23:00:00+00:00",
                "oldest_pending_thread",
            ),
        )
        for thread_since, waiting_since, basis in cases:
            with self.subTest(thread_since=thread_since):
                outcome = self.resolve(
                    {
                        "approval_count": 0,
                        "ci_failing_count": 1,
                        "ci_failing_since": "2026-07-17T01:00:00+00:00",
                        "ci_pending_count": 0,
                        "conflicts": "no",
                        "is_maintenance_bot": False,
                        "last_author_activity_at": "2026-07-14T02:00:00+00:00",
                    },
                    {"thread": {"action": "author", "since": thread_since}},
                )

                self.assertEqual("author", outcome.route)
                self.assertEqual(waiting_since, outcome.facts.waiting_since)
                self.assertEqual(basis, outcome.facts.waiting_age_basis)

    def test_unclear_classification_uses_reviewer_thread_wait_age(self) -> None:
        outcome = self.resolve(
            {
                "approval_count": 0,
                "ci_failing_count": 0,
                "ci_pending_count": 0,
                "is_maintenance_bot": False,
                "last_author_activity_at": "2026-07-14T04:00:00Z",
                "created_at": "2026-07-13T01:00:00Z",
            },
            {
                "unclear": {
                    "action": "reviewer",
                    "since": "2026-07-14T01:00:00Z",
                }
            },
        )

        self.assertEqual("2026-07-14T01:00:00+00:00", outcome.facts.waiting_since)
        self.assertEqual("oldest_pending_thread", outcome.facts.waiting_age_basis)


class RoutingFailureTest(unittest.TestCase):
    def test_failure_facts_preserve_the_first_review_clock_exactly(self) -> None:
        facts = dashboard_facts(
            head_sha="current-head",
            dashboard_override_head_sha="current-head",
            copilot_first_review_missing_since="2026-08-16T12:00:00+00:00",
        )
        previous_facts = dashboard_facts(
            head_sha="old-head",
            copilot_first_review_missing_since="2026-08-11T12:00:00Z",
        )
        original_facts = deepcopy(facts)
        original_previous_facts = deepcopy(previous_facts)

        failed_facts = routing_failure_facts(facts, previous_facts)

        self.assertEqual(
            dashboard_facts(
                head_sha="current-head",
                dashboard_override_head_sha="current-head",
                copilot_first_review_missing_since="2026-08-11T12:00:00Z",
            ),
            failed_facts,
        )
        self.assertEqual(original_facts, facts)
        self.assertEqual(original_previous_facts, previous_facts)
        self.assertTrue(reviewer_handoff_active(failed_facts))

    def test_failure_does_not_restore_handoff_for_an_old_head(self) -> None:
        failed_facts = routing_failure_facts(
            dashboard_facts(
                dashboard_override_head_sha="old-head",
                head_sha="new-head",
            ),
            dashboard_facts(
                dashboard_override_head_sha="old-head",
                head_sha="old-head",
            ),
        )

        self.assertFalse(reviewer_handoff_active(failed_facts))


if __name__ == "__main__":
    unittest.main()
