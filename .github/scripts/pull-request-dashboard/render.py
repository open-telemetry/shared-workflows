from __future__ import annotations

from datetime import datetime
from typing import Any

from utils import actor_login, activity_age, parse_ts, seconds_since


ROUTE_LABELS = {
    "maintainer": "Waiting on maintainers",
    "approver": "Waiting on reviewers",
    "author": "Waiting on authors",
    "external": "Waiting on external",
    "transient-failure": "Transient GitHub failure retrieving PR data",
    "unknown": "Unknown",
}
ROUTE_ORDER = ["maintainer", "approver", "author", "external", "transient-failure", "unknown"]


def _md_escape(s: str) -> str:
    return (
        (s or "")
        .replace("\\", "\\\\")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("|", "\\|")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace("@", "&#64;")
        .replace("\n", " ")
        .strip()
    )


def _limit_rows(rows: list[Any], max_rows: int | None) -> tuple[list[Any], int]:
    if max_rows is None or max_rows <= 0 or len(rows) <= max_rows:
        return rows, 0
    return rows[:max_rows], len(rows) - max_rows


def _truncation_note(count: int) -> str:
    plural = "PR" if count == 1 else "PRs"
    return f"_More {count} {plural} not shown_"


def render_draft_pr_section(
    prs: list[dict[str, Any]],
    max_rows_per_section: int | None = None,
) -> list[str]:
    drafts = [p for p in prs if p.get("isDraft")]
    if not drafts:
        return []
    drafts.sort(key=lambda p: p.get("updatedAt") or "")
    drafts, truncated = _limit_rows(drafts, max_rows_per_section)
    lines = ["## Draft pull requests", ""]
    lines.append("| PR | Author | Updated |")
    lines.append("|---|---|:---:|")
    for pr in drafts:
        number = pr["number"]
        title = _md_escape(pr.get("title", ""))
        author = actor_login(pr.get("author") or {})
        updated = activity_age(parse_ts(pr.get("updatedAt") or ""))
        # GitHub autolinks same-repo PR numbers; avoid full URLs so large
        # dashboards can show more PRs before hitting the issue body limit.
        lines.append(f"| #{number} {title} | {author} | {updated} |")
    lines.append("")
    if truncated:
        lines.append(_truncation_note(truncated))
        lines.append("")
    return lines


def ci_cell(facts: dict[str, Any]) -> str:
    if "ci_failing_count" not in facts and "ci_pending_count" not in facts:
        return "?"
    if facts.get("ci_failing_count", 0) > 0:
        return "❌"
    if facts.get("ci_pending_count", 0) > 0:
        return "⏳"
    return "✅"


def conflicts_cell(facts: dict[str, Any]) -> str:
    conflicts = facts.get("conflicts")
    if conflicts == "yes":
        return "❌"
    if conflicts == "no":
        return "✅"
    return "?"


def _age_ts(facts: dict[str, Any]) -> datetime | None:
    return parse_ts(facts.get("waiting_since") or facts.get("last_activity_at") or "")


def age_seconds(facts: dict[str, Any]) -> int | None:
    return seconds_since(_age_ts(facts))


def age_cell(facts: dict[str, Any]) -> str:
    return activity_age(_age_ts(facts))


WORD_JOINER = "\u2060"


def reviewer_icon(reviewer: dict[str, Any]) -> str:
    discussion_icons = []
    if reviewer.get("open_thread"):
        discussion_icons.append("💬")
    if reviewer.get("mainline_feedback"):
        discussion_icons.append("📌")
    if reviewer.get("changes_requested"):
        discussion_icons.append("🔴")
        return WORD_JOINER.join(discussion_icons)
    if reviewer.get("approved"):
        discussion_icons.append("✅")
    elif reviewer.get("approved_non_team"):
        # A black/gray check distinguishes a non-code-owner approval from a
        # code-owner approval; only code-owner approvals count toward merge.
        discussion_icons.append("✔️")
    return WORD_JOINER.join(discussion_icons)


# Friendlier display names for bot reviewers whose login is verbose.
REVIEWER_DISPLAY_NAMES = {
    "copilot-pull-request-reviewer": "Copilot",
    "copilot-pull-request-reviewer[bot]": "Copilot",
}


def reviewer_display_name(login: str) -> str:
    return REVIEWER_DISPLAY_NAMES.get(login, login)


def reviewers_cell_text(facts: dict[str, Any]) -> str:
    reviewers = facts.get("reviewers") or []
    parts = []
    for reviewer in reviewers:
        login = _md_escape(reviewer_display_name(reviewer.get("login") or ""))
        if not login:
            continue
        icon = reviewer_icon(reviewer)
        # Join name and icon with a non-breaking space so they never wrap apart.
        parts.append(f"{login}&nbsp;{icon}" if icon else login)
    return "<br>".join(parts)


def _neutralize_code_fence(s: str) -> str:
    return (s or "").replace("```", "`\u200d`\u200d`")


def render_diagnostics_section(
    results: dict[int, dict[str, Any]],
    max_rows_per_section: int | None = None,
) -> list[str]:
    prs_with_content = [
        number for number in sorted(results, reverse=True)
        if (
            results[number].get("review_thread_classifications")
            or results[number].get("mainline_action_classifications")
            or results[number].get("error")
        )
    ]
    if not prs_with_content:
        return []
    prs_with_content, truncated = _limit_rows(prs_with_content, max_rows_per_section)
    data_lines: list[str] = []
    for number in prs_with_content:
        result = results[number]
        pending_actions = result.get("pending_actions") or {}
        data_lines.append(f"PR #{number}")
        classifications = (
            (result.get("review_thread_classifications") or [])
            + (result.get("mainline_action_classifications") or [])
        )
        for c in classifications:
            decision = c.get("decision") or {}
            reason = (decision.get("reason") or "").replace("\n", " ")
            pending_action = pending_actions.get(c.get("discussion_id"))
            if pending_action:
                lifecycle_suffix = f", pending:{pending_action.get('action')}"
            elif c.get("discussion_kind") == "pr-conversation-item":
                lifecycle_suffix = ", addressed"
            else:
                lifecycle_suffix = ", closed"
            data_lines.append(
                f"llm: {c.get('discussion_id')} -> {decision.get('discussion_action')}{lifecycle_suffix} ({reason})"
            )
        error = result.get("error")
        if error:
            data_lines.append(f"error: {error}")
        data_lines.append("")
    section = [
        "<details>",
        "<summary>Diagnostics</summary>",
        "",
        "```text",
        *(_neutralize_code_fence(line) for line in data_lines),
        "```",
        "",
        "</details>",
        "",
    ]
    if truncated:
        section.append(_truncation_note(truncated))
        section.append("")
    return section


def render_pr_tables(
    prs: list[dict[str, Any]],
    results: dict[int, dict[str, Any]],
    max_rows_per_section: int | None = None,
    skip_drafts: bool = False,
) -> str:
    source_url = "https://github.com/open-telemetry/shared-workflows/blob/main/.github/scripts/pull-request-dashboard/dashboard.py"
    refresh_url = "https://github.com/open-telemetry/shared-workflows/actions/workflows/pull-request-dashboard.yml"
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
        "Reviewers column: ✅ approved · ✔️ approved (non-code-owner) · "
        "💬 open discussion · 📌 author action pending · 🔴 changes requested."
    )
    out: list[str] = [
        "> [!NOTE]",
        f"> {grouping_note}",
        ">",
        f"> {reviewers_note}",
        "",
    ]

    by_route: dict[str, list[dict[str, Any]]] = {}
    for pr in prs:
        if pr.get("isDraft"):
            continue
        res = results.get(pr["number"]) or {"route": "unknown"}
        route = res.get("route") or "unknown"
        if route not in ROUTE_ORDER:
            route = "unknown"
        by_route.setdefault(route, []).append(pr)

    def row_sort_key(pr: dict[str, Any]) -> tuple[int, int]:
        res = results.get(pr["number"]) or {}
        facts = res.get("facts") or {}
        activity = age_seconds(facts)
        return (activity if activity is not None else -1, pr["number"])

    for route in ROUTE_ORDER:
        rows = by_route.get(route) or []
        if not rows:
            continue
        rows.sort(key=row_sort_key, reverse=True)
        rows, truncated = _limit_rows(rows, max_rows_per_section)
        out.append(f"## {ROUTE_LABELS.get(route, route)}")
        out.append("")
        out.append("| PR | Author | Reviewers | CI | Conflicts | Age |")
        out.append("|---|---|---|:---:|:---:|:---:|")
        for pr in rows:
            number = pr["number"]
            title = _md_escape(pr.get("title", ""))
            res = results.get(number) or {}
            facts = res.get("facts") or {}
            author = facts.get("author") or actor_login(pr.get("author") or {})
            reviewers_cell = reviewers_cell_text(facts)
            activity_cell = age_cell(facts)
            # GitHub autolinks same-repo PR numbers; avoid full URLs so large
            # dashboards can show more PRs before hitting the issue body limit.
            pr_cell = f"#{number} {title}"
            out.append(
                f"| {pr_cell} | {author} | {reviewers_cell} | {ci_cell(facts)} | "
                f"{conflicts_cell(facts)} | {activity_cell} |"
            )
        out.append("")
        if truncated:
            out.append(_truncation_note(truncated))
            out.append("")

    if not skip_drafts:
        out.extend(render_draft_pr_section(prs, max_rows_per_section))
    out.extend(render_diagnostics_section(results, max_rows_per_section))
    out.append(f"_Approvers may [force a refresh]({refresh_url})._")
    out.append("")
    return "\n".join(out) + "\n"
