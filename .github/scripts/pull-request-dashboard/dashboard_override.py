"""Recognize and deliver explicit reviewer-routing overrides."""

from __future__ import annotations

import re
from typing import Any

from github_cli import gh_api, run_gh
from route_presentation import outstanding_gate_phrase
from state import load_dashboard_state_cache
from utils import actor_login, parse_ts


DASHBOARD_COMMAND_PREFIX = "/dashboard"
DASHBOARD_OVERRIDE_COMMAND = "/dashboard route:reviewers"
DASHBOARD_OVERRIDE_SUBCOMMAND = "route:reviewers"
# Mirrors pr_status_comment.DASHBOARD_APP_SLUG; duplicated here to avoid a
# circular import between the two modules.
DASHBOARD_APP_SLUG = "opentelemetry-pr-dashboard"
COMMAND_REPLY_MARKER_PREFIX = "<!-- pull-request-dashboard-command-reply:"
_COMMAND_REPLY_MARKER_RE = re.compile(
    r"<!-- pull-request-dashboard-command-reply:(\d+) -->"
)
OVERRIDE_ACK_MARKER_PREFIX = "<!-- pull-request-dashboard-override-ack:"
_OVERRIDE_ACK_MARKER_RE = re.compile(
    r"<!-- pull-request-dashboard-override-ack:(\d+) -->"
)
PRE_REVIEW_ROUTES = ("author",)


def author_override_guidance(staleness_note: str = "") -> str:
    guidance = (
        "If you need reviewer or maintainer help, comment "
        "`/dashboard route:reviewers` to route this pull request immediately "
        "from waiting on the author to waiting on reviewers."
    )
    if staleness_note:
        guidance = f"{guidance} {staleness_note}"
    return guidance


def parse_dashboard_command(comment: dict[str, Any]) -> str | None:
    """Return the subcommand of a `/dashboard` command, or None.

    Returns the (possibly empty) subcommand token when the comment's first
    line is a `/dashboard` command, and None when it is not a command at all.
    """
    if comment.get("minimized"):
        return None
    lines = (comment.get("body") or "").strip().splitlines()
    if not lines:
        return None
    tokens = lines[0].strip().split()
    if not tokens or tokens[0] != DASHBOARD_COMMAND_PREFIX:
        return None
    return tokens[1] if len(tokens) > 1 else ""


def dashboard_command_body_remainder(comment: dict[str, Any]) -> str | None:
    """Return the comment body after a leading `/dashboard` command.

    Returns None when the comment is not a `/dashboard` command, and the
    (possibly empty) text after the subcommand otherwise. This lets callers
    keep an author's explanation on the same or later lines while treating the
    command tokens themselves as control metadata.
    """
    if parse_dashboard_command(comment) is None:
        return None
    lines = (comment.get("body") or "").strip().splitlines()
    first_line = lines[0].strip().split(maxsplit=2)
    return "\n".join([first_line[2] if len(first_line) > 2 else "", *lines[1:]]).strip()


def is_authorized_commander(login: str, author: str, reviewers: set[str] | None) -> bool:
    low = (login or "").lower()
    return bool(low) and (low == author.lower() or low in (reviewers or set()))


def latest_authorized_command(
    raw: dict[str, Any],
    author: str,
    reviewers: set[str] | None,
) -> tuple[int, str]:
    acknowledged_id = _acknowledged_override_command_id(raw.get("issue_comments"))
    best_id = 0
    best_user = ""
    for comment in raw.get("issue_comments") or []:
        if parse_dashboard_command(comment) != DASHBOARD_OVERRIDE_SUBCOMMAND:
            continue
        commenter = actor_login(comment.get("user") or {})
        if not is_authorized_commander(commenter, author, reviewers):
            continue
        try:
            comment_id = int(comment.get("id"))
        except (TypeError, ValueError):
            continue
        if comment_id <= acknowledged_id:
            continue
        if comment_id > best_id:
            best_id, best_user = comment_id, commenter
    return best_id, best_user


def latest_authorized_command_at(
    raw: dict[str, Any],
    author: str,
    reviewers: set[str] | None,
) -> str:
    """Timestamp of the newest authorized override command, acknowledged or not.

    Unlike `latest_authorized_command`, acknowledged commands still count: the
    watermark has to outlive the acknowledgement, or the discussions a command
    cleared would come back on the next refresh. An acknowledgement is also
    treated as durable proof of authorization, since approver-team membership is
    resolved live and an approver can leave the team after commanding.
    """
    acknowledged_ids = _acknowledged_override_command_ids(raw.get("issue_comments"))
    latest = ""
    for comment in raw.get("issue_comments") or []:
        if parse_dashboard_command(comment) != DASHBOARD_OVERRIDE_SUBCOMMAND:
            continue
        try:
            comment_id = int(comment.get("id"))
        except (TypeError, ValueError):
            comment_id = 0
        if comment_id not in acknowledged_ids and not is_authorized_commander(
            actor_login(comment.get("user") or {}), author, reviewers
        ):
            continue
        created_at = comment.get("created_at") or ""
        if created_at > latest:
            latest = created_at
    return latest


def dashboard_override_facts(
    raw: dict[str, Any],
    author: str,
    reviewers: set[str] | None = None,
) -> dict[str, Any]:
    command_id, command_user = latest_authorized_command(raw, author, reviewers)
    return {
        "dashboard_override_command_id": command_id,
        "dashboard_override_command_user": command_user,
        "dashboard_override_since": latest_authorized_command_at(
            raw, author, reviewers
        ),
        "dashboard_command_replies": pending_command_replies(raw, author, reviewers),
    }


def _is_dashboard_app_comment(comment: dict[str, Any]) -> bool:
    """Whether a comment was authored by the dashboard GitHub App.

    Handles both the REST shape (``performed_via_github_app.slug``) and the
    normalized GraphQL shape from ``fetch_pr_issue_comments`` (a bot
    ``user.login`` of ``<slug>[bot]``).
    """
    if (comment.get("performed_via_github_app") or {}).get("slug") == DASHBOARD_APP_SLUG:
        return True
    return (comment.get("user") or {}).get("login") == f"{DASHBOARD_APP_SLUG}[bot]"


def _replied_command_ids(comments: list[dict[str, Any]] | None) -> set[int]:
    """Command ids already answered by a dashboard-app reply comment.

    Reply markers are matched only in comments authored by the dashboard app, so
    a user cannot forge a `command-reply` marker to suppress an owed reply.
    """
    replied_ids: set[int] = set()
    for comment in comments or []:
        if not _is_dashboard_app_comment(comment):
            continue
        for match in _COMMAND_REPLY_MARKER_RE.findall(comment.get("body") or ""):
            replied_ids.add(int(match))
    return replied_ids


def _acknowledged_override_command_ids(
    comments: list[dict[str, Any]] | None,
) -> set[int]:
    """Command ids the dashboard app has already acknowledged.

    Ack markers are matched only in comments authored by the dashboard app, so a
    user cannot forge one to authorize their own command.
    """
    acknowledged_ids: set[int] = set()
    for comment in comments or []:
        if not _is_dashboard_app_comment(comment):
            continue
        for match in _OVERRIDE_ACK_MARKER_RE.findall(comment.get("body") or ""):
            acknowledged_ids.add(int(match))
    return acknowledged_ids


def _acknowledged_override_command_id(
    comments: list[dict[str, Any]] | None,
) -> int:
    return max(_acknowledged_override_command_ids(comments), default=0)


def pending_command_replies(
    raw: dict[str, Any],
    author: str,
    reviewers: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Return replies owed to unsupported or unauthorized `/dashboard` commands.

    `/dashboard route:reviewers` from the author or an approver is handled by the
    override flow and never gets a reply here. The same command from anyone else
    gets an unauthorized reply, and any unrecognized `/dashboard` subcommand gets
    an unknown-command reply. Commands that already have a reply comment are
    skipped so replies are posted at most once.
    """
    comments = raw.get("issue_comments") or []
    replied_ids = _replied_command_ids(comments)

    replies: list[dict[str, Any]] = []
    for comment in comments:
        subcommand = parse_dashboard_command(comment)
        if subcommand is None:
            continue
        try:
            comment_id = int(comment.get("id"))
        except (TypeError, ValueError):
            continue
        if comment_id in replied_ids:
            continue
        commenter = actor_login(comment.get("user") or {})
        if subcommand == DASHBOARD_OVERRIDE_SUBCOMMAND:
            if is_authorized_commander(commenter, author, reviewers):
                continue
            kind = "unauthorized"
        else:
            kind = "unknown_command"
        replies.append({
            "comment_id": comment_id,
            "kind": kind,
            "user": commenter,
            "subcommand": subcommand,
        })
    return replies


def command_reply_marker(comment_id: int) -> str:
    return f"{COMMAND_REPLY_MARKER_PREFIX}{comment_id} -->"


def override_ack_marker(comment_id: int) -> str:
    return f"{OVERRIDE_ACK_MARKER_PREFIX}{comment_id} -->"


ROUTE_ALREADY_ROUTED_PHRASE = {
    "approver": "already waiting on reviewers",
    "maintainer": "already past review and waiting on maintainers",
}


def render_command_reply(reply: dict[str, Any]) -> str:
    user = reply.get("user") or ""
    mention = f"@{user}, " if user else ""
    kind = reply.get("kind")
    if kind == "unauthorized":
        message = (
            "only the pull request author or a member of an approving team can "
            "use `/dashboard route:reviewers`."
        )
    elif kind in ("routed", "already_routed"):
        route = reply.get("route") or ""
        held_gates = reply.get("held_gates") or ""
        if held_gates:
            message = (
                "your reviewer-routing request was recorded; the reviewer handoff "
                f"is waiting on {held_gates}."
            )
        elif route in PRE_REVIEW_ROUTES:
            message = (
                "everything still open on this pull request arrived after your "
                "`/dashboard route:reviewers` command, so it is still waiting "
                "on you."
            )
        elif kind == "already_routed":
            where = ROUTE_ALREADY_ROUTED_PHRASE.get(
                route, "not currently waiting on you"
            )
            message = (
                f"this pull request is {where}, so `/dashboard route:reviewers` had "
                "no effect."
            )
        elif route == "maintainer":
            message = (
                "your reviewer-routing request was recorded; this pull request has "
                "the approvals it needs and is now waiting on maintainers."
            )
        else:
            message = "this pull request was routed to reviewers."
    else:
        subcommand = reply.get("subcommand") or ""
        attempted = DASHBOARD_COMMAND_PREFIX + (f" {subcommand}" if subcommand else "")
        message = (
            f"`{attempted}` is not a recognized dashboard command. The only "
            "supported command is `/dashboard route:reviewers`, which the pull "
            "request author can use to move a pull request from waiting on the "
            "author to waiting on reviewers."
        )
    comment_id = int(reply["comment_id"])
    markers = [command_reply_marker(comment_id)]
    if kind in ("routed", "already_routed"):
        markers.append(override_ack_marker(comment_id))
    return "\n".join([
        *markers,
        f"{mention}{message}",
        "",
    ])


def command_reply_exists(
    comments: list[dict[str, Any]] | None,
    comment_id: int,
) -> bool:
    marker = command_reply_marker(comment_id)
    return any(
        (comment.get("performed_via_github_app") or {}).get("slug") == DASHBOARD_APP_SLUG
        and marker in (comment.get("body") or "")
        for comment in comments or []
    )


def deliver_dashboard_command_replies(repo: str) -> list[str]:
    dashboard_state = load_dashboard_state_cache()
    if dashboard_state is None:
        return []
    errors: list[str] = []
    for key, result in sorted(
        (dashboard_state.get("prs") or {}).items(),
        key=lambda item: int(item[0]),
    ):
        replies = ((result or {}).get("facts") or {}).get("dashboard_command_replies") or []
        if not replies:
            continue
        pr_number = int(key)
        try:
            comments = gh_api(
                f"/repos/{repo}/issues/{pr_number}/comments?per_page=100",
                paginate=True,
            )
        except Exception as e:
            errors.append(f"PR #{pr_number}: {e}")
            continue
        for reply in replies:
            try:
                if command_reply_exists(comments, int(reply["comment_id"])):
                    continue
                run_gh([
                    "gh", "api", "--method", "POST",
                    f"repos/{repo}/issues/{pr_number}/comments",
                    "-f", f"body={render_command_reply(reply)}",
                ])
            except Exception as e:
                errors.append(f"PR #{pr_number}: {e}")
    return errors


def clear_overridden_actions(
    facts: dict[str, Any],
    pending_actions: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Drop the author actions an override command already answered.

    `/dashboard route:reviewers` is the author saying everything open at that
    moment is handled. Clearing those actions keeps them from routing the pull
    request back to the author on every later refresh, which would make the
    author repeat the command each time review comes back around. Anything that
    happens after the command keeps its author action, so new feedback still
    reaches the author, including a reviewer reopening something the command
    cleared.
    """
    # Timestamps reach here in both GitHub's `...Z` form and `format_ts`'s
    # `...+00:00` form, so they have to be parsed rather than compared as text.
    override_since = parse_ts(facts.get("dashboard_override_since"))
    facts["dashboard_override_cleared_count"] = 0
    facts["dashboard_override_cleared_ci"] = False
    if override_since is None:
        return pending_actions
    remaining: dict[str, dict[str, Any]] = {}
    cleared = 0
    for discussion_id, entry in pending_actions.items():
        since = parse_ts(entry.get("since"))
        # GitHub timestamps are second-granularity, so an item sharing the
        # command's second is left open rather than risking masking it.
        if entry.get("action") == "author" and since and since < override_since:
            cleared += 1
            continue
        remaining[discussion_id] = entry
    ci_failing_count = facts.get("ci_failing_count") or 0
    facts["dashboard_override_cleared_count"] = cleared
    facts["dashboard_override_cleared_ci"] = uncleared_ci_failing_count(facts) < ci_failing_count
    return remaining


def uncleared_ci_failing_count(facts: dict[str, Any]) -> int:
    """Failing required checks that an override command has not cleared."""
    return facts.get("ci_uncleared_failing_count") or 0


def append_command_ack_reply(
    raw: dict[str, Any],
    facts: dict[str, Any],
    route: str,
) -> None:
    """Queue the reply that acknowledges an override command.

    The reply carries the acknowledgement marker that stops the command from
    being processed again. Every authorized command gets a reply because the
    command forces the reviewer route even when no discussion or failing check
    was cleared.
    """
    command_id = int(facts.get("dashboard_override_command_id") or 0)
    if not command_id:
        return
    if command_id in _replied_command_ids(raw.get("issue_comments") or []):
        return
    replies = facts.setdefault("dashboard_command_replies", [])
    if any(reply.get("comment_id") == command_id for reply in replies):
        return
    handoff = bool(facts.get("copilot_review_bypassed_by_override"))
    replies.append({
        "comment_id": command_id,
        "kind": "routed" if handoff else "already_routed",
        "user": facts.get("dashboard_override_command_user") or facts.get("author") or "",
        "route": route,
        "held_gates": (
            outstanding_gate_phrase(facts)
            if facts.get("route_held_for_gates")
            else ""
        ),
    })
