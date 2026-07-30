"""Recognize and deliver explicit reviewer-routing overrides."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from github_cli import gh_api, run_gh
from state import load_dashboard_state_cache
from utils import actor_login


DASHBOARD_OVERRIDE_LABEL = "dashboard:route-overridden"
DASHBOARD_COMMAND_PREFIX = "/dashboard"
DASHBOARD_OVERRIDE_COMMAND = "/dashboard route:reviewers"
DASHBOARD_OVERRIDE_SUBCOMMAND = "route:reviewers"
DASHBOARD_OVERRIDE_LABEL_COLOR = "1D76DB"
DASHBOARD_OVERRIDE_LABEL_DESCRIPTION = "Routing manually overridden to reviewers"
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
        "If you believe this pull request is incorrectly routed as waiting on "
        "the author, comment `/dashboard route:reviewers` to route it from "
        "waiting on the author to waiting on reviewers."
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
    cleared would come back on the next refresh.
    """
    latest = ""
    for comment in raw.get("issue_comments") or []:
        if parse_dashboard_command(comment) != DASHBOARD_OVERRIDE_SUBCOMMAND:
            continue
        if not is_authorized_commander(
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
    labels: set[str],
    reviewers: set[str] | None = None,
) -> dict[str, Any]:
    label_applied = DASHBOARD_OVERRIDE_LABEL in labels
    command_id, command_user = latest_authorized_command(raw, author, reviewers)
    command_pending = bool(command_id)
    return {
        "dashboard_override": label_applied,
        "dashboard_override_label_applied": label_applied,
        "dashboard_override_command_id": command_id,
        "dashboard_override_command_user": command_user,
        "dashboard_override_since": latest_authorized_command_at(
            raw, author, reviewers
        ),
        "dashboard_override_requested": command_pending and not label_applied,
        "dashboard_override_release_requested": False,
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


def _acknowledged_override_command_id(
    comments: list[dict[str, Any]] | None,
) -> int:
    acknowledged_id = 0
    for comment in comments or []:
        if not _is_dashboard_app_comment(comment):
            continue
        for match in _OVERRIDE_ACK_MARKER_RE.findall(comment.get("body") or ""):
            acknowledged_id = max(acknowledged_id, int(match))
    return acknowledged_id


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
    "copilot": "waiting on an automated Copilot review",
}


def render_command_reply(reply: dict[str, Any]) -> str:
    user = reply.get("user") or ""
    mention = f"@{user} " if user else ""
    kind = reply.get("kind")
    if kind == "unauthorized":
        message = (
            "only the pull request author or a member of an approving team can "
            "use `/dashboard route:reviewers`."
        )
    elif kind == "routed":
        if reply.get("route") == "copilot":
            message = (
                "accepted the reviewer-routing override; the reviewer handoff "
                "is waiting on Copilot."
            )
        else:
            message = "routed this pull request to reviewers."
    elif kind == "already_routed":
        if reply.get("route") in PRE_REVIEW_ROUTES:
            message = (
                "everything still open on this pull request arrived after your "
                "`/dashboard route:reviewers` command, so it is still waiting "
                "on you."
            )
        else:
            where = ROUTE_ALREADY_ROUTED_PHRASE.get(
                reply.get("route") or "", "not currently waiting on you"
            )
            message = (
                f"this pull request is {where}, so `/dashboard route:reviewers` had "
                "no effect. The command only applies while the pull request is "
                "waiting on you."
            )
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


def ensure_command_reply(
    repo: str,
    pr_number: int,
    reply: dict[str, Any],
) -> None:
    comments = gh_api(
        f"/repos/{repo}/issues/{pr_number}/comments?per_page=100",
        paginate=True,
    )
    if command_reply_exists(comments, int(reply["comment_id"])):
        return
    run_gh([
        "gh", "api", "--method", "POST",
        f"repos/{repo}/issues/{pr_number}/comments",
        "-f", f"body={render_command_reply(reply)}",
    ])


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
    moment is handled. Clearing those actions, instead of only masking the route
    once, keeps them from routing the pull request back to the author on every
    later refresh, which would make the author repeat the command each time
    review comes back around. Anything that happens after the command keeps its
    author action, so new feedback still reaches the author.
    """
    override_since = facts.get("dashboard_override_since") or ""
    active = bool(facts.get("dashboard_override_label_applied")) or bool(
        facts.get("dashboard_override_requested")
    )
    facts["dashboard_override_cleared_count"] = 0
    facts["dashboard_override_cleared_ci"] = False
    if not override_since or not active:
        return pending_actions
    remaining: dict[str, dict[str, Any]] = {}
    cleared = 0
    for discussion_id, entry in pending_actions.items():
        since = entry.get("since") or ""
        if entry.get("action") == "author" and since and since <= override_since:
            cleared += 1
            continue
        remaining[discussion_id] = entry
    ci_failing_since = facts.get("ci_failing_since") or ""
    facts["dashboard_override_cleared_count"] = cleared
    facts["dashboard_override_cleared_ci"] = bool(
        facts.get("ci_failing_count")
        and ci_failing_since
        and ci_failing_since <= override_since
    )
    return remaining


def apply_dashboard_override(facts: dict[str, Any], route: str) -> str:
    label_applied = bool(facts.get("dashboard_override_label_applied"))
    requested = bool(facts.get("dashboard_override_requested"))
    command_pending = bool(facts.get("dashboard_override_command_id"))
    cleared = bool(facts.get("dashboard_override_cleared_count")) or bool(
        facts.get("dashboard_override_cleared_ci")
    )
    # `route` already reflects what `clear_overridden_actions` cleared, so a
    # command needs no further route masking. Masking is left for a label applied
    # by hand, which carries no command and so clears nothing; it only takes
    # effect before review, and on every later route natural routing stands.
    masks_route = (
        route in PRE_REVIEW_ROUTES
        and (label_applied or requested)
        and not facts.get("dashboard_override_since")
    )
    override_applies = cleared or masks_route
    # A command that does not newly move the pull request to reviewers is a
    # no-op; the author is told where it is routed. This covers both a
    # non-overridable route and an existing override that already provides the
    # reviewer handoff.
    facts["dashboard_override_noop"] = command_pending and not cleared and (
        label_applied or not override_applies
    )
    if requested and not override_applies:
        facts["dashboard_override_requested"] = False
    elif command_pending and cleared and not requested:
        # A command that cleared something while the label was already applied
        # still needs its label refresh and acknowledgement reply.
        facts["dashboard_override_requested"] = True
    facts["dashboard_override"] = override_applies
    # Release the label once the override stops clearing anything, so a
    # forgotten override cannot keep new feedback away from the author.
    facts["dashboard_override_release_requested"] = (
        label_applied and not cleared and not masks_route
    )
    return "approver" if masks_route else route


def append_route_noop_reply(
    raw: dict[str, Any],
    facts: dict[str, Any],
    route: str,
) -> None:
    if not facts.get("dashboard_override_noop"):
        return
    command_id = int(facts.get("dashboard_override_command_id") or 0)
    if not command_id:
        return
    replied_ids = _replied_command_ids(raw.get("issue_comments") or [])
    if command_id in replied_ids:
        return
    replies = facts.setdefault("dashboard_command_replies", [])
    if any(reply.get("comment_id") == command_id for reply in replies):
        return
    replies.append({
        "comment_id": command_id,
        "kind": "already_routed",
        "user": facts.get("dashboard_override_command_user") or facts.get("author") or "",
        "route": route,
    })


def deliver_dashboard_override_requests(repo: str) -> list[str]:
    dashboard_state = load_dashboard_state_cache()
    if dashboard_state is None:
        return []
    pr_results = sorted(
        (dashboard_state.get("prs") or {}).items(),
        key=lambda item: int(item[0]),
    )
    if any(
        ((result or {}).get("facts") or {}).get("dashboard_override_requested")
        for _key, result in pr_results
    ):
        try:
            run_gh([
                "gh", "label", "create", DASHBOARD_OVERRIDE_LABEL,
                "--repo", repo,
                "--color", DASHBOARD_OVERRIDE_LABEL_COLOR,
                "--description", DASHBOARD_OVERRIDE_LABEL_DESCRIPTION,
                "--force",
            ])
        except Exception as e:
            return [f"label: {e}"]

    errors: list[str] = []
    for key, result in pr_results:
        facts = (result or {}).get("facts") or {}
        pr_number = int(key)
        if facts.get("dashboard_override_requested"):
            try:
                run_gh([
                    "gh", "api", "--method", "POST",
                    f"repos/{repo}/issues/{pr_number}/labels",
                    "-f", f"labels[]={DASHBOARD_OVERRIDE_LABEL}",
                ])
                ensure_command_reply(
                    repo,
                    pr_number,
                    {
                        "comment_id": facts["dashboard_override_command_id"],
                        "kind": "routed",
                        "route": (result or {}).get("route") or "",
                        "user": facts.get("dashboard_override_command_user") or "",
                    },
                )
            except Exception as e:
                errors.append(f"PR #{pr_number}: {e}")
        elif facts.get("dashboard_override_release_requested"):
            try:
                run_gh([
                    "gh", "api", "--method", "DELETE",
                    f"repos/{repo}/issues/{pr_number}/labels/{quote(DASHBOARD_OVERRIDE_LABEL)}",
                ])
            except Exception as e:
                if "404" not in str(e):
                    errors.append(f"PR #{pr_number}: {e}")
    return errors