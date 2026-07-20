from __future__ import annotations


ROUTE_PRESENTATION = {
    "maintainer": {
        "dashboard_label": "Waiting on maintainers",
        "status_waiting_on": "Maintainers",
        "status_next_step": "Merge when ready.",
    },
    "approver": {
        "dashboard_label": "Waiting on reviewers",
        "status_waiting_on": "Reviewers",
        "status_next_step": "Review the latest changes.",
    },
    "external": {
        "dashboard_label": "Waiting on external",
        "status_waiting_on": "An external dependency or decision",
        "status_next_step": "Resolve it before work can continue.",
    },
    "author": {
        "dashboard_label": "Waiting on authors",
        "status_waiting_on": "Author",
        "status_next_step": "Address or respond to review feedback.",
    },
    "transient-failure": {
        "dashboard_label": "Transient GitHub failure retrieving PR data",
        "status_waiting_on": "Pull request dashboard maintainers",
        "status_next_step": "Determine the next action.",
    },
    "unknown": {
        "dashboard_label": "Unknown",
        "status_waiting_on": "Pull request dashboard maintainers",
        "status_next_step": "Determine the next action.",
    },
}
ROUTE_ORDER = list(ROUTE_PRESENTATION)


def route_label(route: str) -> str:
    return ROUTE_PRESENTATION.get(route, ROUTE_PRESENTATION["unknown"])["dashboard_label"]


def route_status_summary(route: str) -> tuple[str, str]:
    presentation = ROUTE_PRESENTATION.get(route, ROUTE_PRESENTATION["unknown"])
    return (
        presentation["status_waiting_on"],
        presentation["status_next_step"],
    )
