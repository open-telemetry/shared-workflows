"""Resolve pull request routing and its durable clocks."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from classification import normalize_discussion_action
from copilot_review import (
    copilot_review_outstanding,
    copilot_review_unreported,
    set_copilot_first_review_missing_since,
    set_copilot_review_request_needed,
)
from utils import format_ts, parse_ts, required_checks_settled, utc_now


@dataclass(frozen=True)
class RoutingInput:
    facts: dict[str, Any]
    pending_actions: dict[str, dict[str, Any]]
    previous_route: str | None
    previous_facts: dict[str, Any]
    required_approvals: int
    require_clean_copilot_review: bool
    manual_reviewer_handoff: bool
    pending_human_reviewer_logins: frozenset[str]
    now: datetime | None = None


@dataclass(frozen=True)
class RoutingOutcome:
    route: str
    facts: dict[str, Any]


_ROUTE_DISCUSSION_ACTIONS = {
    "author": {"author"},
    "approver": {"reviewer"},
    "maintainer": {"reviewer"},
}
_REVIEWER_ROUTES = ("approver", "maintainer")
_ROUTE_PROGRESSION = ("author", "approver", "maintainer")
_GATE_HOLD_LIMIT = timedelta(hours=4)


def reviewer_handoff_active(facts: dict[str, Any]) -> bool:
    """Return whether the acknowledged reviewer handoff matches the current head."""
    bound_head = facts.get("dashboard_override_head_sha") or ""
    return bool(bound_head) and bound_head == (facts.get("head_sha") or "")


def routing_failure_facts(
    facts: dict[str, Any],
    previous_facts: dict[str, Any],
) -> dict[str, Any]:
    """Return failure-path facts without restarting the first-review clock."""
    failed_facts = deepcopy(facts)
    if previous_facts.get("copilot_first_review_missing_since"):
        failed_facts["copilot_first_review_missing_since"] = previous_facts[
            "copilot_first_review_missing_since"
        ]
    return failed_facts


def _action_counts(
    pending_actions: dict[str, dict[str, Any]],
) -> dict[str, int]:
    counts = {"author": 0, "reviewer": 0, "none": 0, "unclear": 0}
    for entry in pending_actions.values():
        counts[normalize_discussion_action(entry.get("action") or "")] += 1
    return counts


def _base_route(
    facts: dict[str, Any],
    pending_actions: dict[str, dict[str, Any]],
    required_approvals: int,
) -> str:
    counts = _action_counts(pending_actions)
    is_maintenance_bot = facts.get("is_maintenance_bot")
    approval_threshold = 1 if is_maintenance_bot else required_approvals
    if (facts.get("ci_failing_count") or 0) > 0 and not is_maintenance_bot:
        return "author"
    if counts["author"] and not is_maintenance_bot:
        return "author"
    if facts.get("approval_count", 0) >= approval_threshold:
        return "maintainer"
    return "approver"


def _route_progress(route: str) -> int:
    return _ROUTE_PROGRESSION.index(route) if route in _ROUTE_PROGRESSION else 0


def _gate_hold_expired(facts: dict[str, Any], now: datetime) -> bool:
    held_since = parse_ts(facts.get("route_held_since"))
    if held_since is None:
        return False
    return now - held_since >= _GATE_HOLD_LIMIT


def _set_gate_hold_clock(
    facts: dict[str, Any],
    previous_facts: dict[str, Any],
    route: str,
    *,
    unreported_gates: bool,
    would_hold: bool,
    now: datetime,
) -> None:
    head_sha = str(facts.get("head_sha") or "")
    carried = (
        str(previous_facts.get("route_held_since") or "")
        if head_sha and head_sha == previous_facts.get("head_sha")
        else ""
    )
    if not (
        unreported_gates
        and (carried or (route in _REVIEWER_ROUTES and would_hold))
    ):
        facts.pop("route_held_since", None)
        return
    facts["route_held_since"] = carried or format_ts(now)


def _hold_route_until_gates_settle(
    facts: dict[str, Any],
    route: str,
    previous_route: str | None,
    previous_facts: dict[str, Any],
    *,
    require_clean_copilot_review: bool,
    now: datetime,
    bypass_gates: bool,
) -> str:
    effective_previous_route = previous_route or ""
    if effective_previous_route not in _ROUTE_PROGRESSION or (
        facts.get("is_maintenance_bot") and effective_previous_route == "author"
    ):
        effective_previous_route = (
            "approver" if facts.get("is_maintenance_bot") else "author"
        )
    gates_enabled = not bypass_gates
    copilot_review_gate_enabled = require_clean_copilot_review and gates_enabled
    facts["copilot_review_outstanding"] = copilot_review_outstanding(
        facts, enabled=copilot_review_gate_enabled
    )
    facts["copilot_review_unreported"] = copilot_review_unreported(
        facts, enabled=copilot_review_gate_enabled
    )
    facts["required_checks_settled"] = required_checks_settled(facts)
    gates_outstanding = gates_enabled and (
        not facts["required_checks_settled"] or facts["copilot_review_outstanding"]
    )
    unreported_gates = gates_enabled and (
        not facts["required_checks_settled"] or facts["copilot_review_unreported"]
    )
    would_hold = _route_progress(route) > _route_progress(effective_previous_route)
    _set_gate_hold_clock(
        facts,
        previous_facts,
        route,
        unreported_gates=unreported_gates and facts.get("conflicts") != "yes",
        would_hold=would_hold,
        now=now,
    )
    expired = _gate_hold_expired(facts, now)
    facts["route_hold_expired"] = expired
    held = would_hold and gates_outstanding and not expired
    facts["route_held_for_gates"] = held
    return effective_previous_route if held else route


def _oldest_pending_action_ts(
    pending_actions: dict[str, dict[str, Any]],
    actions: set[str],
) -> datetime | None:
    timestamps = [
        parse_ts(entry.get("since") or "")
        for entry in pending_actions.values()
        if normalize_discussion_action(entry.get("action") or "") in actions
    ]
    timestamps = [timestamp for timestamp in timestamps if timestamp is not None]
    return min(timestamps) if timestamps else None


def _fallback_wait_ts(
    route: str,
    facts: dict[str, Any],
) -> tuple[datetime | None, str]:
    if route in _REVIEWER_ROUTES:
        return (
            parse_ts(facts.get("last_author_activity_at") or ""),
            "last_author_activity",
        )
    if route == "author":
        if facts.get("conflicts") == "yes":
            return (
                parse_ts(facts.get("last_author_activity_at") or ""),
                "last_author_activity",
            )
        if (facts.get("ci_failing_count") or 0) > 0:
            ci_failing_since = parse_ts(facts.get("ci_failing_since") or "")
            if ci_failing_since is not None:
                return ci_failing_since, "ci_failure"
            return (
                parse_ts(facts.get("last_author_activity_at") or ""),
                "last_author_activity",
            )
        return (
            parse_ts(facts.get("last_approver_activity_at") or ""),
            "last_approver_activity",
        )
    return parse_ts(facts.get("last_activity_at") or ""), "last_activity"


def _add_wait_age_facts(
    facts: dict[str, Any],
    route: str,
    pending_actions: dict[str, dict[str, Any]],
    previous_route: str | None,
    previous_facts: dict[str, Any],
    now: datetime,
    pending_human_reviewer_logins: frozenset[str],
) -> None:
    previous_pending_reviewers = {
        reviewer.get("login") or ""
        for reviewer in previous_facts.get("reviewers") or []
        if reviewer.get("pending_review")
    }
    if facts.get("route_held_for_gates") and previous_facts.get("waiting_since"):
        facts["waiting_since"] = previous_facts["waiting_since"]
        facts["waiting_age_basis"] = "gate_hold"
        return
    if (
        route in _REVIEWER_ROUTES
        and previous_facts.get("route_held_for_gates")
        and previous_route == "author"
    ):
        facts["waiting_since"] = format_ts(now)
        facts["waiting_age_basis"] = "gate_release"
        return
    if (
        route == "approver"
        and previous_route == "maintainer"
        and pending_human_reviewer_logins - previous_pending_reviewers
    ):
        facts["waiting_since"] = format_ts(now)
        facts["waiting_age_basis"] = "review_rerequest"
        return
    if (
        route in _REVIEWER_ROUTES
        and previous_route in _REVIEWER_ROUTES
        and previous_facts.get("waiting_age_basis") == "gate_release"
        and previous_facts.get("waiting_since")
        and facts.get("head_sha")
        and facts.get("head_sha") == previous_facts.get("head_sha")
    ):
        facts["waiting_since"] = previous_facts["waiting_since"]
        facts["waiting_age_basis"] = "gate_release"
        return
    if (
        route in _REVIEWER_ROUTES
        and previous_route in _REVIEWER_ROUTES
        and previous_facts.get("waiting_age_basis") == "review_rerequest"
        and previous_facts.get("waiting_since")
        and pending_human_reviewer_logins & previous_pending_reviewers
    ):
        facts["waiting_since"] = previous_facts["waiting_since"]
        facts["waiting_age_basis"] = "review_rerequest"
        return
    actions = _ROUTE_DISCUSSION_ACTIONS.get(route)
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
        wait_ts = parse_ts(facts.get("created_at") or "")
        basis = "created"
    previous_wait_ts = parse_ts(previous_facts.get("waiting_since") or "")
    if (
        route in _REVIEWER_ROUTES
        and previous_route in _REVIEWER_ROUTES
        and previous_wait_ts is not None
        and wait_ts is not None
        and previous_wait_ts < wait_ts
    ):
        wait_ts = previous_wait_ts
        basis = previous_facts.get("waiting_age_basis") or ""
    facts["waiting_since"] = format_ts(wait_ts)
    facts["waiting_age_basis"] = basis


def resolve_routing(routing_input: RoutingInput) -> RoutingOutcome:
    """Resolve the final route and return an enriched copy of the input facts."""
    facts = deepcopy(routing_input.facts)
    now = routing_input.now or utc_now()
    route = _base_route(
        facts,
        routing_input.pending_actions,
        routing_input.required_approvals,
    )
    if routing_input.manual_reviewer_handoff:
        route = "approver"
    copilot_review_gate_enabled = (
        routing_input.require_clean_copilot_review
        and not routing_input.manual_reviewer_handoff
    )
    copilot_review_request_enabled = (
        copilot_review_gate_enabled and facts.get("conflicts") != "yes"
    )
    set_copilot_first_review_missing_since(
        facts,
        {"facts": routing_input.previous_facts},
        enabled=copilot_review_request_enabled,
        now=now,
    )
    set_copilot_review_request_needed(
        facts,
        route,
        enabled=copilot_review_request_enabled,
        now=now,
    )
    route = _hold_route_until_gates_settle(
        facts,
        route,
        routing_input.previous_route,
        routing_input.previous_facts,
        require_clean_copilot_review=copilot_review_gate_enabled,
        bypass_gates=routing_input.manual_reviewer_handoff,
        now=now,
    )
    _add_wait_age_facts(
        facts,
        route,
        routing_input.pending_actions,
        routing_input.previous_route,
        routing_input.previous_facts,
        now,
        routing_input.pending_human_reviewer_logins,
    )
    return RoutingOutcome(route=route, facts=facts)
