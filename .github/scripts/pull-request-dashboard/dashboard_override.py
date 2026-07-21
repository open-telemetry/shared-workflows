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
# Automatic routes from which "route to reviewers" is a meaningful override: the
# pull request is not yet in review. `approver` is already at reviewers and
# `maintainer` is past them, so the override must not force those; `copilot`
# routing reflects a required review gate that the override should not bypass.
OVERRIDABLE_ROUTES = ("author", "external")
REVIEWERS_OR_LATER_ROUTES = ("approver", "maintainer")


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
    """Return the comment body after a leading `/dashboard` command line.

    Returns None when the comment is not a `/dashboard` command, and the
    (possibly empty) text after the command line otherwise. This lets callers
    keep an author's explanation that follows the command while treating the
    command line itself as control metadata.
    """
    if parse_dashboard_command(comment) is None:
        return None
    lines = (comment.get("body") or "").strip().splitlines()
    return "\n".join(lines[1:]).strip()


def is_authorized_commander(login: str, author: str, reviewers: set[str] | None) -> bool:
    low = (login or "").lower()
    return bool(low) and (low == author.lower() or low in (reviewers or set()))


def latest_authorized_command(
    raw: dict[str, Any],
    author: str,
    reviewers: set[str] | None,
) -> tuple[int, str]:
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
        if comment_id > best_id:
            best_id, best_user = comment_id, commenter
    return best_id, best_user


def dashboard_override_facts(
    raw: dict[str, Any],
    author: str,
    labels: set[str],
    previous_facts: dict[str, Any] | None,
    reviewers: set[str] | None = None,
) -> dict[str, Any]:
    label_applied = DASHBOARD_OVERRIDE_LABEL in labels
    previous_facts = previous_facts or {}
    previous_command_id = int(
        previous_facts.get("dashboard_override_command_id") or 0
    )
    latest_id, latest_user = latest_authorized_command(raw, author, reviewers)
    command_id = max(previous_command_id, latest_id)
    if latest_id > previous_command_id:
        command_user = latest_user
    else:
        command_user = str(previous_facts.get("dashboard_override_command_user") or "")
    label_requested = (
        not label_applied
        and (
            command_id > previous_command_id
            or bool(previous_facts.get("dashboard_override_requested"))
        )
    )
    return {
        "dashboard_override": label_applied,
        "dashboard_override_label_applied": label_applied,
        "dashboard_override_command_id": command_id,
        "dashboard_override_command_user": command_user,
        "dashboard_override_requested": label_requested,
        "dashboard_override_release_requested": False,
        "dashboard_command_replies": pending_command_replies(raw, author, reviewers),
    }


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
    replied_ids: set[int] = set()
    for comment in comments:
        for match in _COMMAND_REPLY_MARKER_RE.findall(comment.get("body") or ""):
            replied_ids.add(int(match))

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


ROUTE_ALREADY_ROUTED_PHRASE = {
    "approver": "already waiting on reviewers",
    "maintainer": "already past review and waiting on maintainers",
    "external": "waiting on an external dependency, not on you",
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
    elif kind == "already_routed":
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
    return "\n".join([
        command_reply_marker(int(reply["comment_id"])),
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


def apply_dashboard_override(facts: dict[str, Any], route: str) -> str:
    label_applied = bool(facts.get("dashboard_override_label_applied"))
    requested = bool(facts.get("dashboard_override_requested"))
    command_applies = requested and route in OVERRIDABLE_ROUTES
    # The override only takes effect from a pre-review route (waiting on the
    # author or an external dependency). On a route that is already at or past
    # reviewers the natural routing stands, so a label left behind cannot pin
    # the pull request at reviewers or drag it back from maintainers.
    override_applies = route in OVERRIDABLE_ROUTES and (label_applied or requested)
    # A pending command that cannot apply because the pull request is not on an
    # overridable route is a no-op; the author is told where it is routed.
    facts["dashboard_override_noop"] = requested and not command_applies
    if requested and not command_applies:
        facts["dashboard_override_requested"] = False
    facts["dashboard_override"] = override_applies
    # Release the label once automatic routing reaches or passes reviewers, so a
    # forgotten override cannot hold the pull request at reviewers forever.
    facts["dashboard_override_release_requested"] = (
        label_applied and route in REVIEWERS_OR_LATER_ROUTES
    )
    return "approver" if override_applies else route


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
    replied_ids: set[int] = set()
    for comment in raw.get("issue_comments") or []:
        for match in _COMMAND_REPLY_MARKER_RE.findall(comment.get("body") or ""):
            replied_ids.add(int(match))
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

    dashboard_state = load_dashboard_state_cache()
    if dashboard_state is None:
        return []
    errors: list[str] = []
    for key, result in sorted(
        (dashboard_state.get("prs") or {}).items(),
        key=lambda item: int(item[0]),
    ):
        facts = (result or {}).get("facts") or {}
        pr_number = int(key)
        if facts.get("dashboard_override_requested"):
            try:
                run_gh([
                    "gh", "api", "--method", "POST",
                    f"repos/{repo}/issues/{pr_number}/labels",
                    "-f", f"labels[]={DASHBOARD_OVERRIDE_LABEL}",
                ])
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