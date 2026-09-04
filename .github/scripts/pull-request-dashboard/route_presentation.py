from __future__ import annotations

from dashboard_contracts import DashboardFacts, DashboardRoute
from utils import required_checks_unreported


ROUTE_PRESENTATION = {
    "maintainer": {
        "dashboard_label": "Waiting on maintainers",
        "status_headline": "Waiting on maintainers",
        "status_waiting_on": "Maintainers",
        "status_next_step": "Merge when ready.",
    },
    "approver": {
        "dashboard_label": "Waiting on reviewers",
        "status_headline": "Waiting on reviewers",
        "status_waiting_on": "Reviewers",
        "status_next_step": "Review the latest changes.",
    },
    "author": {
        "dashboard_label": "Waiting on authors",
        "status_headline": "Waiting on the author",
        "status_waiting_on": "Author",
        "status_next_step": "Address or respond to review feedback.",
    },
    "transient-failure": {
        "dashboard_label": "Transient GitHub failure retrieving PR data",
        "status_headline": "Waiting on the pull request dashboard maintainers",
        "status_waiting_on": "Pull request dashboard maintainers",
        "status_next_step": "Determine the next action.",
    },
    "unknown": {
        "dashboard_label": "Unknown",
        "status_headline": "Waiting on the pull request dashboard maintainers",
        "status_waiting_on": "Pull request dashboard maintainers",
        "status_next_step": "Determine the next action.",
    },
}
ROUTE_ORDER = list(ROUTE_PRESENTATION)


def _route_value(route: DashboardRoute | str) -> str:
    return route.value if isinstance(route, DashboardRoute) else route


def route_label(route: DashboardRoute | str) -> str:
    return ROUTE_PRESENTATION.get(
        _route_value(route),
        ROUTE_PRESENTATION["unknown"],
    )["dashboard_label"]


def route_status_summary(route: DashboardRoute | str) -> tuple[str, str]:
    presentation = ROUTE_PRESENTATION.get(
        _route_value(route),
        ROUTE_PRESENTATION["unknown"],
    )
    return (
        presentation["status_waiting_on"],
        presentation["status_next_step"],
    )


def status_headline(route: DashboardRoute | str) -> str:
    return ROUTE_PRESENTATION.get(
        _route_value(route),
        ROUTE_PRESENTATION["unknown"],
    )["status_headline"]


def unreported_gate_phrase(facts: DashboardFacts) -> str:
    # Which gate has said nothing at all about the current head. This is not
    # the same as the gate that is holding the PR: a Copilot review that left
    # findings holds it but has reported, so naming it would send the reader
    # after a gate that arrived.
    gates = []
    if required_checks_unreported(facts):
        gates.append("the required status checks")
    if facts.copilot_review_unreported:
        gates.append("the Copilot review")
    return " and ".join(gates)


def held_gate_phrase(facts: DashboardFacts) -> str:
    # Which gate is keeping the pull request off the route it computed. A
    # Copilot review that reported findings holds it just as a silent gate
    # does, so name the findings the author still has to clear rather than the
    # review, which has already arrived. A held route always has one of these,
    # so the phrase is never empty while the pull request is held.
    gates = []
    if required_checks_unreported(facts):
        gates.append("the required status checks")
    if facts.copilot_review_unreported:
        gates.append("the Copilot review")
    elif facts.copilot_review_outstanding:
        gates.append("the open Copilot review findings")
    return " and ".join(gates)


def abandoned_gate_note(facts: DashboardFacts) -> str:
    # Said once the dashboard has stopped waiting, so the reader knows the
    # missing gate is not something they are supposed to sit and wait for.
    gates = unreported_gate_phrase(facts)
    if not gates:
        return ""
    return (
        f"The dashboard stopped waiting for {gates} to report, "
        "and routed this pull request anyway."
    )
