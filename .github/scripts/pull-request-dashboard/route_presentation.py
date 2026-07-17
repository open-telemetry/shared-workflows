from __future__ import annotations


ROUTE_PRESENTATION = {
    "maintainer": {
        "dashboard_label": "Waiting on maintainers",
        "status": "Waiting on maintainers to merge the pull request.",
    },
    "approver": {
        "dashboard_label": "Waiting on reviewers",
        "status": "Waiting on reviewers to review the latest changes.",
    },
    "author": {
        "dashboard_label": "Waiting on authors",
        "status": "Waiting on {author} to address or respond to unresolved review discussions.",
    },
    "external": {
        "dashboard_label": "Waiting on external",
        "status": "Waiting on an external dependency or decision.",
    },
    "transient-failure": {
        "dashboard_label": "Transient GitHub failure retrieving PR data",
        "status": "Waiting on dashboard maintainers to determine the next action.",
    },
    "unknown": {
        "dashboard_label": "Unknown",
        "status": "Waiting on dashboard maintainers to determine the next action.",
    },
}
ROUTE_ORDER = list(ROUTE_PRESENTATION)


def route_label(route: str) -> str:
    return ROUTE_PRESENTATION.get(route, ROUTE_PRESENTATION["unknown"])["dashboard_label"]


def route_status(route: str, author: str = "the author") -> str:
    template = ROUTE_PRESENTATION.get(route, ROUTE_PRESENTATION["unknown"])["status"]
    return template.format(author=author)