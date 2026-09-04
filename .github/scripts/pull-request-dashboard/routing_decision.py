"""Resolve pull request routing and its durable clocks."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from classification_policy import normalize_discussion_action
from copilot_review import (
    copilot_review_outstanding,
    copilot_review_unreported,
    set_copilot_first_review_missing_since,
    set_copilot_review_request_needed,
)
from dashboard_contracts import DashboardFacts, DashboardRoute
from utils import (
    format_ts,
    parse_ts,
    required_checks_settled,
    required_checks_unreported,
    utc_now,
)


@dataclass(frozen=True)
class RoutingInput:
    facts: DashboardFacts
    pending_actions: Mapping[str, Any]
    previous_route: DashboardRoute | None
    previous_facts: DashboardFacts
    required_approvals: int
    require_clean_copilot_review: bool
    manual_reviewer_handoff: bool
    pending_human_reviewer_logins: frozenset[str]
    now: datetime | None = None


@dataclass(frozen=True)
class RoutingOutcome:
    route: DashboardRoute
    facts: DashboardFacts


_ROUTE_DISCUSSION_ACTIONS = {
    "author": {"author"},
    "approver": {"reviewer"},
    "maintainer": {"reviewer"},
}
_REVIEWER_ROUTES = ("approver", "maintainer")
_ROUTE_PROGRESSION = ("author", "approver", "maintainer")
_GATE_HOLD_LIMIT = timedelta(hours=4)


def reviewer_handoff_active(facts: DashboardFacts) -> bool:
    """Return whether the reviewer handoff remains active."""
    return (
        bool(facts.dashboard_override_head_sha)
        and bool(facts.head_sha)
        and (
            facts.dashboard_override_persistent
            or facts.dashboard_override_head_sha == facts.head_sha
        )
        and not facts.dashboard_override_cleared_by_feedback
    )


def routing_failure_facts(
    facts: DashboardFacts,
    previous_facts: DashboardFacts,
) -> DashboardFacts:
    """Return failure-path facts without restarting the first-review clock."""
    return facts.with_changes(
        copilot_first_review_missing_since=(
            previous_facts.copilot_first_review_missing_since
            or facts.copilot_first_review_missing_since
        )
    )


def _action_counts(
    pending_actions: Mapping[str, Any],
) -> dict[str, int]:
    counts = {"author": 0, "reviewer": 0, "none": 0, "unclear": 0}
    for entry in pending_actions.values():
        counts[normalize_discussion_action(entry.get("action") or "").value] += 1
    return counts


def _base_route(
    facts: DashboardFacts,
    pending_actions: Mapping[str, Any],
    required_approvals: int,
) -> DashboardRoute:
    counts = _action_counts(pending_actions)
    is_maintenance_bot = facts.is_maintenance_bot
    approval_threshold = 1 if is_maintenance_bot else required_approvals
    if (facts.ci_failing_count or 0) > 0 and facts.author_can_act:
        return DashboardRoute.AUTHOR
    if counts["author"] and facts.author_can_act:
        return DashboardRoute.AUTHOR
    if facts.approval_count >= approval_threshold:
        return DashboardRoute.MAINTAINER
    return DashboardRoute.APPROVER


def _route_progress(route: DashboardRoute) -> int:
    return (
        _ROUTE_PROGRESSION.index(route.value)
        if route.value in _ROUTE_PROGRESSION
        else 0
    )


def _gate_hold_expired(facts: DashboardFacts, now: datetime) -> bool:
    held_since = parse_ts(facts.route_held_since)
    if held_since is None:
        return False
    return now - held_since >= _GATE_HOLD_LIMIT


def _set_gate_hold_clock(
    facts: DashboardFacts,
    previous_facts: DashboardFacts,
    route: DashboardRoute,
    *,
    unreported_gates: bool,
    would_hold: bool,
    now: datetime,
) -> DashboardFacts:
    head_sha = facts.head_sha
    carried = (
        previous_facts.route_held_since or ""
        if head_sha and head_sha == previous_facts.head_sha
        else ""
    )
    if not (
        unreported_gates
        and (carried or (route.value in _REVIEWER_ROUTES and would_hold))
    ):
        return facts.with_changes(route_held_since=None)
    return facts.with_changes(route_held_since=carried or format_ts(now))


def _hold_route_until_gates_settle(
    facts: DashboardFacts,
    route: DashboardRoute,
    previous_route: DashboardRoute | None,
    previous_facts: DashboardFacts,
    *,
    require_clean_copilot_review: bool,
    now: datetime,
    bypass_gates: bool,
) -> tuple[DashboardRoute, DashboardFacts]:
    effective_previous_route = previous_route or DashboardRoute.AUTHOR
    if effective_previous_route.value not in _ROUTE_PROGRESSION or (
        not facts.author_can_act
        and effective_previous_route is DashboardRoute.AUTHOR
    ):
        effective_previous_route = (
            DashboardRoute.APPROVER
            if not facts.author_can_act
            else DashboardRoute.AUTHOR
        )
    gates_enabled = not bypass_gates
    copilot_review_gate_enabled = require_clean_copilot_review and gates_enabled
    facts = facts.with_changes(
        copilot_review_outstanding=copilot_review_outstanding(
            facts,
            enabled=copilot_review_gate_enabled,
        ),
        copilot_review_unreported=copilot_review_unreported(
            facts,
            enabled=copilot_review_gate_enabled,
        ),
        required_checks_settled=required_checks_settled(facts),
    )
    checks_unreported = required_checks_unreported(facts)
    gates_outstanding = gates_enabled and (
        checks_unreported or facts.copilot_review_outstanding
    )
    unreported_gates = gates_enabled and (
        checks_unreported or facts.copilot_review_unreported
    )
    would_hold = _route_progress(route) > _route_progress(effective_previous_route)
    facts = _set_gate_hold_clock(
        facts,
        previous_facts,
        route,
        unreported_gates=unreported_gates and facts.conflicts != "yes",
        would_hold=would_hold,
        now=now,
    )
    expired = _gate_hold_expired(facts, now)
    held = would_hold and gates_outstanding and not expired
    facts = facts.with_changes(
        route_hold_expired=expired,
        route_held_for_gates=held,
    )
    return (effective_previous_route if held else route), facts


def _oldest_pending_action_ts(
    pending_actions: Mapping[str, Any],
    actions: set[str],
) -> datetime | None:
    timestamps = [
        parse_ts(entry.get("since") or "")
        for entry in pending_actions.values()
        if normalize_discussion_action(entry.get("action") or "").value in actions
    ]
    timestamps = [timestamp for timestamp in timestamps if timestamp is not None]
    return min(timestamps) if timestamps else None


def _fallback_wait_ts(
    route: DashboardRoute,
    facts: DashboardFacts,
) -> tuple[datetime | None, str]:
    if route.value in _REVIEWER_ROUTES:
        return (
            parse_ts(facts.last_author_activity_at),
            "last_author_activity",
        )
    if route is DashboardRoute.AUTHOR:
        if facts.conflicts == "yes":
            return (
                parse_ts(facts.last_author_activity_at),
                "last_author_activity",
            )
        if (facts.ci_failing_count or 0) > 0:
            ci_failing_since = parse_ts(facts.ci_failing_since)
            if ci_failing_since is not None:
                return ci_failing_since, "ci_failure"
            return (
                parse_ts(facts.last_author_activity_at),
                "last_author_activity",
            )
        return (
            parse_ts(facts.last_approver_activity_at),
            "last_approver_activity",
        )
    return parse_ts(facts.last_activity_at), "last_activity"


def _add_wait_age_facts(
    facts: DashboardFacts,
    route: DashboardRoute,
    pending_actions: Mapping[str, Any],
    previous_route: DashboardRoute | None,
    previous_facts: DashboardFacts,
    now: datetime,
    pending_human_reviewer_logins: frozenset[str],
) -> DashboardFacts:
    previous_pending_reviewers = {
        reviewer.login
        for reviewer in previous_facts.reviewers
        if reviewer.pending_review
    }
    if facts.route_held_for_gates and previous_facts.waiting_since:
        return facts.with_changes(
            waiting_since=previous_facts.waiting_since,
            waiting_age_basis="gate_hold",
        )
    if (
        route.value in _REVIEWER_ROUTES
        and previous_facts.route_held_for_gates
        and previous_route is DashboardRoute.AUTHOR
    ):
        return facts.with_changes(
            waiting_since=format_ts(now),
            waiting_age_basis="gate_release",
        )
    if (
        route is DashboardRoute.APPROVER
        and previous_route is DashboardRoute.MAINTAINER
        and pending_human_reviewer_logins - previous_pending_reviewers
    ):
        return facts.with_changes(
            waiting_since=format_ts(now),
            waiting_age_basis="review_rerequest",
        )
    if (
        route.value in _REVIEWER_ROUTES
        and previous_route is not None
        and previous_route.value in _REVIEWER_ROUTES
        and previous_facts.waiting_age_basis == "gate_release"
        and previous_facts.waiting_since
        and facts.head_sha
        and facts.head_sha == previous_facts.head_sha
    ):
        return facts.with_changes(
            waiting_since=previous_facts.waiting_since,
            waiting_age_basis="gate_release",
        )
    if (
        route.value in _REVIEWER_ROUTES
        and previous_route is not None
        and previous_route.value in _REVIEWER_ROUTES
        and previous_facts.waiting_age_basis == "review_rerequest"
        and previous_facts.waiting_since
        and pending_human_reviewer_logins & previous_pending_reviewers
    ):
        return facts.with_changes(
            waiting_since=previous_facts.waiting_since,
            waiting_age_basis="review_rerequest",
        )
    actions = _ROUTE_DISCUSSION_ACTIONS.get(route.value)
    wait_ts = (
        _oldest_pending_action_ts(pending_actions, actions) if actions else None
    )
    basis = "oldest_pending_thread" if wait_ts else ""
    fallback_ts, fallback_basis = _fallback_wait_ts(route, facts)
    if wait_ts is None or (
        fallback_basis == "ci_failure"
        and fallback_ts is not None
        and fallback_ts < wait_ts
    ):
        wait_ts, basis = fallback_ts, fallback_basis
    if wait_ts is None:
        wait_ts = parse_ts(facts.created_at)
        basis = "created"
    previous_wait_ts = parse_ts(previous_facts.waiting_since)
    if (
        route.value in _REVIEWER_ROUTES
        and previous_route is not None
        and previous_route.value in _REVIEWER_ROUTES
        and previous_wait_ts is not None
        and wait_ts is not None
        and previous_wait_ts < wait_ts
    ):
        wait_ts = previous_wait_ts
        basis = previous_facts.waiting_age_basis
    return facts.with_changes(
        waiting_since=format_ts(wait_ts),
        waiting_age_basis=basis,
    )


def resolve_routing(routing_input: RoutingInput) -> RoutingOutcome:
    """Resolve the final route and return an enriched copy of the input facts."""
    facts = routing_input.facts
    now = routing_input.now or utc_now()
    route = _base_route(
        facts,
        routing_input.pending_actions,
        routing_input.required_approvals,
    )
    if routing_input.manual_reviewer_handoff:
        route = DashboardRoute.APPROVER
    copilot_review_gate_enabled = (
        routing_input.require_clean_copilot_review
        and not routing_input.manual_reviewer_handoff
    )
    copilot_review_request_enabled = (
        copilot_review_gate_enabled and facts.conflicts != "yes"
    )
    facts = set_copilot_first_review_missing_since(
        facts,
        routing_input.previous_facts,
        enabled=copilot_review_request_enabled,
        now=now,
    )
    facts = set_copilot_review_request_needed(
        facts,
        route.value,
        enabled=copilot_review_request_enabled,
        now=now,
    )
    route, facts = _hold_route_until_gates_settle(
        facts,
        route,
        routing_input.previous_route,
        routing_input.previous_facts,
        require_clean_copilot_review=copilot_review_gate_enabled,
        bypass_gates=routing_input.manual_reviewer_handoff,
        now=now,
    )
    facts = _add_wait_age_facts(
        facts,
        route,
        routing_input.pending_actions,
        routing_input.previous_route,
        routing_input.previous_facts,
        now,
        routing_input.pending_human_reviewer_logins,
    )
    return RoutingOutcome(route=route, facts=facts)
