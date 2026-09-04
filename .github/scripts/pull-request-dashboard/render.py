from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from datetime import datetime
from fnmatch import fnmatchcase
from typing import Any

from dashboard_contracts import (
    DashboardFacts,
    DashboardRoute,
    ReviewerSummary,
    StoredDashboardResult,
)
from route_presentation import ROUTE_ORDER, route_label
from utils import (
    COPILOT_REVIEWER_LOGINS,
    actor_login,
    activity_age,
    is_copilot_reviewer_login,
    markdown_escape,
    parse_ts,
    seconds_since,
)


def _limit_rows(rows: list[Any], max_rows: int | None) -> tuple[list[Any], int]:
    if max_rows is None or max_rows <= 0 or len(rows) <= max_rows:
        return rows, 0
    return rows[:max_rows], len(rows) - max_rows


def _truncation_note(count: int) -> str:
    plural = "PR" if count == 1 else "PRs"
    return f"_More {count} {plural} not shown_"


def _pr_cell_text(
    pr: dict[str, Any],
    labels_to_display: list[str] | None = None,
) -> str:
    number = pr["number"]
    title = markdown_escape(pr.get("title", ""))
    pr_cell = f"#{number} {title}"
    patterns = labels_to_display or []

    matched_labels: list[str] = []
    seen: set[str] = set()
    for label in pr.get("labels") or []:
        if not isinstance(label, str) or not label or label in seen:
            continue
        if any(fnmatchcase(label, pattern) for pattern in patterns):
            matched_labels.append(label)
            seen.add(label)
    if not matched_labels:
        return pr_cell
    rendered_labels = " · ".join(
        f"<code>{markdown_escape(label)}</code>" for label in matched_labels
    )
    return f"{pr_cell} · {rendered_labels}"


def render_draft_pr_section(
    prs: list[dict[str, Any]],
    max_rows_per_section: int | None = None,
    labels_to_display: list[str] | None = None,
) -> list[str]:
    drafts = [p for p in prs if p.get("isDraft")]
    if not drafts:
        return []
    drafts.sort(key=lambda p: p.get("draftSince") or p.get("createdAt") or "")
    drafts, truncated = _limit_rows(drafts, max_rows_per_section)
    lines = ["## Draft pull requests", ""]
    lines.append("| PR | Author | Draft age |")
    lines.append("|---|---|:---:|")
    for pr in drafts:
        author = actor_login(pr.get("author") or {})
        draft_age = activity_age(
            parse_ts(pr.get("draftSince") or pr.get("createdAt") or "")
        )
        # GitHub autolinks same-repo PR numbers; avoid full URLs so large
        # dashboards can show more PRs before hitting the issue body limit.
        lines.append(
            f"| {_pr_cell_text(pr, labels_to_display)} | {author} | {draft_age} |"
        )
    lines.append("")
    if truncated:
        lines.append(_truncation_note(truncated))
        lines.append("")
    return lines


def ci_cell(facts: DashboardFacts) -> str:
    if (
        facts.ci_failing_count is None
        and facts.ci_maintainer_action_required_count is None
        and facts.ci_pending_count is None
    ):
        return "?"
    failing = (facts.ci_failing_count or 0) > 0
    write_access_required = (
        facts.ci_maintainer_action_required_count or 0
    ) > 0
    if failing and write_access_required:
        return "❌ 🔐"
    if failing:
        return "❌"
    if (facts.ci_pending_count or 0) > 0 and write_access_required:
        return "⏳ 🔐"
    if write_access_required:
        return "🔐"
    if (facts.ci_pending_count or 0) > 0:
        return "⏳"
    return "✅"


def conflicts_cell(facts: DashboardFacts) -> str:
    conflicts = facts.conflicts
    if conflicts == "yes":
        return "❌"
    if conflicts == "no":
        return "✅"
    return "?"


def _age_ts(facts: DashboardFacts) -> datetime | None:
    return parse_ts(facts.waiting_since or facts.last_activity_at)


def age_seconds(facts: DashboardFacts) -> int | None:
    return seconds_since(_age_ts(facts))


def age_cell(facts: DashboardFacts) -> str:
    return activity_age(_age_ts(facts))


WORD_JOINER = "\u2060"


def reviewer_icon(reviewer: ReviewerSummary) -> str:
    discussion_icons = []
    pending_review = reviewer.pending_review
    if pending_review:
        discussion_icons.append("⏳")
    if reviewer.open_thread:
        discussion_icons.append("💬")
    if reviewer.top_level_feedback:
        discussion_icons.append("📌")
    if reviewer.changes_requested:
        discussion_icons.append("🔴")
        return WORD_JOINER.join(discussion_icons)
    if not pending_review and reviewer.approved:
        discussion_icons.append("✅")
    elif not pending_review and reviewer.approved_non_team:
        # A black/gray check distinguishes a non-code-owner approval from a
        # code-owner approval; only code-owner approvals count toward merge.
        discussion_icons.append("✔️")
    return WORD_JOINER.join(discussion_icons)


# Friendlier display names for bot reviewers whose login is verbose, keyed by
# the lowercased login so they match the same way the reviewer itself does.
REVIEWER_DISPLAY_NAMES = {
    login: "Copilot" for login in COPILOT_REVIEWER_LOGINS
}


def reviewer_display_name(login: str) -> str:
    return REVIEWER_DISPLAY_NAMES.get((login or "").strip().lower(), login)


COPILOT_REVIEWER_LOGIN = "copilot-pull-request-reviewer"


def copilot_review_pending(facts: DashboardFacts) -> bool:
    # "Pending" has to mean a review is genuinely in flight, or the icon shows
    # on nearly every row and stops carrying information. It also has to mean
    # the wait is someone's turn, which is what the gate decides. First-time
    # human review requests are left off the row; human re-reviews are already
    # marked pending in the stored reviewer facts. Copilot earns a place only
    # where its review holds the pull request. That scope comes first, and
    # within it a requested review qualifies, as does a pull request Copilot
    # has never reviewed, because the automatic first review is not requested
    # through the dashboard and the hold it causes would otherwise have nothing
    # on the row to explain it. A hold is not enough on its own: unsettled
    # checks hold a route too.
    if not facts.copilot_review_outstanding:
        return False
    if facts.copilot_review_requested:
        return True
    return facts.route_held_for_gates and not facts.copilot_review_exists


def display_reviewers(facts: DashboardFacts) -> list[ReviewerSummary]:
    # Copilot only joins the reviewer list once it has reviewed, so a review
    # that is still in flight has to be added for the wait to be visible at all.
    reviewers = list(facts.reviewers)
    if not copilot_review_pending(facts):
        return reviewers
    for reviewer in reviewers:
        if is_copilot_reviewer_login(reviewer.login):
            reviewers[reviewers.index(reviewer)] = replace(
                reviewer,
                pending_review=True,
            )
            return reviewers
    reviewers.append(ReviewerSummary(
        login=COPILOT_REVIEWER_LOGIN,
        pending_review=True,
    ))
    reviewers.sort(key=lambda reviewer: reviewer.login.lower())
    return reviewers


def reviewers_cell_text(facts: DashboardFacts) -> str:
    parts = []
    for reviewer in display_reviewers(facts):
        login = markdown_escape(reviewer_display_name(reviewer.login))
        if not login:
            continue
        icon = reviewer_icon(reviewer)
        # Join name and icon with a non-breaking space so they never wrap apart.
        parts.append(f"{login}&nbsp;{icon}" if icon else login)
    return "<br>".join(parts)


def render_pr_tables(
    prs: list[dict[str, Any]],
    results: Iterable[StoredDashboardResult],
    max_rows_per_section: int | None = None,
    skip_drafts: bool = False,
    labels_to_display: list[str] | None = None,
) -> str:
    results_by_number = {
        result.pr_number: result
        for result in results
    }
    source_url = "https://github.com/open-telemetry/shared-workflows/blob/main/.github/scripts/pull-request-dashboard/dashboard.py"
    draft_note = (
        "Draft PRs are omitted to keep this dashboard concise."
        if skip_drafts
        else "Draft PRs are listed separately."
    )
    grouping_note = (
        f"Open non-draft PRs grouped by who is expected to act next. {draft_note} The grouping is "
        f"partly performed by an LLM ([source]({source_url})) and could contain mistakes."
    )
    reviewers_note = (
        "Reviewers column: ✅ active approval · ✔️ active approval (non-code-owner) · "
        "⏳ review pending · 💬 open review thread · 📌 top-level feedback needs author action · "
        "🔴 changes requested."
    )
    ci_note = (
        "CI column: ✅ passing · ⏳ running · ❌ failing · "
        "🔐 workflow action required."
    )
    out: list[str] = [
        "> [!NOTE]",
        f"> {grouping_note}",
        ">",
        f"> {reviewers_note}",
        ">",
        f"> {ci_note}",
        "",
    ]

    by_route: dict[str, list[dict[str, Any]]] = {}
    for pr in prs:
        if pr.get("isDraft"):
            continue
        res = results_by_number.get(pr["number"])
        route = (
            res.route.value
            if res is not None
            else DashboardRoute.UNKNOWN.value
        )
        if route not in ROUTE_ORDER:
            route = "unknown"
        by_route.setdefault(route, []).append(pr)

    def row_sort_key(pr: dict[str, Any]) -> tuple[int, int]:
        res = results_by_number.get(pr["number"])
        facts = res.facts if res is not None else DashboardFacts()
        activity = age_seconds(facts)
        return (activity if activity is not None else -1, pr["number"])

    for route in ROUTE_ORDER:
        rows = by_route.get(route) or []
        if not rows:
            continue
        rows.sort(key=row_sort_key, reverse=True)
        rows, truncated = _limit_rows(rows, max_rows_per_section)
        out.append(f"## {route_label(route)}")
        out.append("")
        out.append("| PR | Author | Reviewers | CI | Conflicts | Age |")
        out.append("|---|---|---|:---:|:---:|:---:|")
        for pr in rows:
            number = pr["number"]
            res = results_by_number.get(number)
            facts = res.facts if res is not None else DashboardFacts()
            author = facts.author or actor_login(pr.get("author") or {})
            reviewers_cell = reviewers_cell_text(facts)
            activity_cell = age_cell(facts)
            # GitHub autolinks same-repo PR numbers; avoid full URLs so large
            # dashboards can show more PRs before hitting the issue body limit.
            pr_cell = _pr_cell_text(pr, labels_to_display)
            out.append(
                f"| {pr_cell} | {author} | {reviewers_cell} | {ci_cell(facts)} | "
                f"{conflicts_cell(facts)} | {activity_cell} |"
            )
        out.append("")
        if truncated:
            out.append(_truncation_note(truncated))
            out.append("")

    if not skip_drafts:
        out.extend(render_draft_pr_section(
            prs,
            max_rows_per_section,
            labels_to_display,
        ))
    return "\n".join(out) + "\n"
