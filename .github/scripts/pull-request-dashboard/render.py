from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
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
    is_unattended_author_login,
    markdown_escape,
    normalize_author_identity,
    parse_ts,
    seconds_since,
)


ISSUE_BODY_MAX_CHARS = 65536


def _truncation_note(count: int) -> str:
    plural = "PR" if count == 1 else "PRs"
    return f"_More {count} {plural} not shown_"


@dataclass
class _PrSection:
    heading: str
    table_header: tuple[str, str]
    rows: list[str]
    omitted: int = 0

    def lines(self) -> list[str]:
        lines = [self.heading, ""]
        if self.rows:
            lines.extend(self.table_header)
            lines.extend(self.rows)
            lines.append("")
        if self.omitted:
            lines.extend([_truncation_note(self.omitted), ""])
        return lines

    def trim_last_row(self) -> int:
        previous_footer_chars = (
            len(_truncation_note(self.omitted)) + 2 if self.omitted else 0
        )
        removed_chars = len(self.rows.pop()) + 1
        self.omitted += 1
        if not self.rows:
            removed_chars += sum(len(line) + 1 for line in self.table_header) + 1
        return (
            removed_chars + previous_footer_chars
            - len(_truncation_note(self.omitted)) - 2
        )


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


FIRST_TIME_AUTHOR_ASSOCIATIONS = ("FIRST_TIME_CONTRIBUTOR", "FIRST_TIMER")


def author_badge(
    pr: dict[str, Any],
    author: str,
    approver_logins: frozenset[str] = frozenset(),
) -> str:
    pr_author = pr.get("author") or {}
    # GitHub's author association and account type describe the PR author, so
    # they do not apply when a Copilot PR is shown under its human author.
    is_pr_author = (
        normalize_author_identity(author)
        == normalize_author_identity(actor_login(pr_author))
    )
    if is_unattended_author_login(author) or (
        is_pr_author and str(pr_author.get("type") or "").lower() == "bot"
    ):
        return "🤖"
    # Approver team listings include their child maintainer teams. Approvers are
    # also organization members; show only the more specific badge.
    if author.lower() in approver_logins:
        return "🛠️"
    if not is_pr_author:
        return ""
    association = pr.get("authorAssociation")
    if association == "MEMBER":
        return "🔭"
    if association in FIRST_TIME_AUTHOR_ASSOCIATIONS:
        return "🆕"
    return ""


def author_cell_text(
    pr: dict[str, Any],
    author: str,
    approver_logins: frozenset[str] = frozenset(),
) -> str:
    badge = author_badge(pr, author, approver_logins) if author else ""
    # Join login and badge with a non-breaking space so they never wrap apart.
    return f"{author}&nbsp;{badge}" if badge else author


def _draft_pr_section(
    prs: list[dict[str, Any]],
    labels_to_display: list[str] | None = None,
    approver_logins: frozenset[str] = frozenset(),
    skip_drafts: bool = False,
) -> _PrSection | None:
    drafts = [p for p in prs if p.get("isDraft")]
    if not drafts:
        return None
    section = _PrSection(
        "## Draft pull requests",
        ("| PR | Author | Draft age |", "|---|---|:---:|"),
        [],
    )
    if skip_drafts:
        section.omitted = len(drafts)
        return section
    drafts.sort(key=lambda p: p.get("draftSince") or p.get("createdAt") or "")
    for pr in drafts:
        author = author_cell_text(
            pr,
            pr.get("effectiveAuthor") or actor_login(pr.get("author") or {}),
            approver_logins,
        )
        draft_age = activity_age(
            parse_ts(pr.get("draftSince") or pr.get("createdAt") or "")
        )
        # GitHub autolinks same-repo PR numbers; avoid full URLs so large
        # dashboards can show more PRs before hitting the issue body limit.
        section.rows.append(
            f"| {_pr_cell_text(pr, labels_to_display)} | {author} | {draft_age} |"
        )
    return section


def render_draft_pr_section(
    prs: list[dict[str, Any]],
    labels_to_display: list[str] | None = None,
    approver_logins: frozenset[str] = frozenset(),
) -> list[str]:
    section = _draft_pr_section(prs, labels_to_display, approver_logins)
    return section.lines() if section is not None else []


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
    if reviewer.unresolved_thread:
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
    # Show Copilot's wait only where its review gates the handoff. A missing
    # first review also qualifies while its request awaits delivery, so the
    # held row explains the wait before GitHub records the pending reviewer.
    if not facts.copilot_review_outstanding:
        return False
    if facts.copilot_review_requested:
        return True
    return facts.route_held_for_gates and not facts.copilot_review_exists


def display_reviewers(facts: DashboardFacts) -> list[ReviewerSummary]:
    copilot_pending = copilot_review_pending(facts)
    reviewers = [
        reviewer
        for reviewer in facts.reviewers
        if not is_copilot_reviewer_login(reviewer.login)
        or reviewer.unresolved_thread
        or copilot_pending
    ]
    if not copilot_pending:
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
    max_chars: int | None = ISSUE_BODY_MAX_CHARS,
    skip_drafts: bool = False,
    labels_to_display: list[str] | None = None,
    approver_logins: frozenset[str] = frozenset(),
) -> str:
    if max_chars is not None and max_chars <= 0:
        raise ValueError("max_chars must be positive")
    results_by_number = {
        result.pr_number: result
        for result in results
    }
    source_url = "https://github.com/open-telemetry/shared-workflows/blob/main/.github/scripts/pull-request-dashboard/dashboard.py"
    author_note = (
        "Author column: 🆕 first-time contributor · 🔭 open-telemetry member · "
        "🛠️ approver or maintainer · 🤖 bot."
    )
    reviewers_note = (
        "Reviewers column: ✅ active approval · ✔️ active approval (non-code-owner) · "
        "⏳ review pending · 💬 unresolved feedback from reviewer · "
        "📌 top-level feedback needs author action · "
        "🔴 changes requested."
    )
    ci_note = (
        "CI column: ✅ passing · ⏳ running · ❌ failing · "
        "🔐 workflow action required."
    )
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

    sections: list[_PrSection] = []
    for route in ROUTE_ORDER:
        rows = by_route.get(route) or []
        if not rows:
            continue
        rows.sort(key=row_sort_key, reverse=True)
        section = _PrSection(
            f"## {route_label(route)}",
            (
                "| PR | Author | Reviewers | CI | Conflicts | Age |",
                "|---|---|---|:---:|:---:|:---:|",
            ),
            [],
        )
        for pr in rows:
            number = pr["number"]
            res = results_by_number.get(number)
            facts = res.facts if res is not None else DashboardFacts()
            author = author_cell_text(
                pr,
                facts.author or actor_login(pr.get("author") or {}),
                approver_logins,
            )
            reviewers_cell = reviewers_cell_text(facts)
            activity_cell = age_cell(facts)
            # GitHub autolinks same-repo PR numbers; avoid full URLs so large
            # dashboards can show more PRs before hitting the issue body limit.
            pr_cell = _pr_cell_text(pr, labels_to_display)
            section.rows.append(
                f"| {pr_cell} | {author} | {reviewers_cell} | {ci_cell(facts)} | "
                f"{conflicts_cell(facts)} | {activity_cell} |"
            )
        sections.append(section)

    draft_section = _draft_pr_section(
        prs, labels_to_display, approver_logins, skip_drafts,
    )
    if draft_section is not None:
        sections.append(draft_section)
    truncated = any(section.omitted for section in sections)

    def intro_lines() -> list[str]:
        draft_note = (
            "All drafts are omitted to fit GitHub's issue-body limit."
            if draft_section is not None and not draft_section.rows
            else "Draft PRs are listed separately."
        )
        grouping_note = (
            f"Open non-draft PRs grouped by who is expected to act next. {draft_note} The grouping is "
            f"partly performed by an LLM ([source]({source_url})) and could contain mistakes."
        )
        lines = [
            "> [!NOTE]",
            f"> {grouping_note}",
            ">",
            f"> {author_note}",
            ">",
            f"> {reviewers_note}",
            ">",
            f"> {ci_note}",
            "",
        ]
        if truncated:
            lines.extend([
                "> [!WARNING]",
                "> This dashboard is truncated to fit GitHub's issue-body limit. "
                "Sections are trimmed from bottom to top. "
                "The longest-waiting PRs in each section are kept.",
                "",
            ])
        return lines

    def markdown() -> str:
        lines = intro_lines()
        for section in sections:
            lines.extend(section.lines())
        return "\n".join(lines) + "\n"

    body = markdown()
    if max_chars is None or len(body) <= max_chars:
        return body

    intro_chars = sum(len(line) + 1 for line in intro_lines())
    truncated = True
    new_intro_chars = sum(len(line) + 1 for line in intro_lines())
    body_chars = len(body) + new_intro_chars - intro_chars
    intro_chars = new_intro_chars
    for section in reversed(sections):
        while section.rows and body_chars > max_chars:
            body_chars -= section.trim_last_row()
            new_intro_chars = sum(len(line) + 1 for line in intro_lines())
            body_chars += new_intro_chars - intro_chars
            intro_chars = new_intro_chars
        if body_chars <= max_chars:
            break

    body = markdown()
    if len(body) > max_chars:
        raise RuntimeError(
            f"dashboard headings and omission notices exceed the {max_chars}-character limit"
        )
    return body
