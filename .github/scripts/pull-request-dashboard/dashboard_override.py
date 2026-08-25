"""Recognize explicit reviewer-routing overrides and prepare dashboard command replies."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from dashboard_contracts import (
    DashboardCommandReply,
    DashboardFacts,
    DashboardRoute,
)
from dashboard_status import DASHBOARD_APP_SLUG
from pull_request_source import IssueComment
from route_presentation import outstanding_gate_phrase


DASHBOARD_COMMAND_PREFIX = "/dashboard"
DASHBOARD_OVERRIDE_COMMAND = "/dashboard route:reviewers"
DASHBOARD_OVERRIDE_SUBCOMMAND = "route:reviewers"
COMMAND_REPLY_MARKER_PREFIX = "<!-- pull-request-dashboard-command-reply:"
_COMMAND_REPLY_MARKER_RE = re.compile(
    r"<!-- pull-request-dashboard-command-reply:(\d+) -->"
)
OVERRIDE_ACK_MARKER_PREFIX = "<!-- pull-request-dashboard-override-ack:"
# The acknowledgement records which head the command bound to, so the handoff is
# a comparison against the current head rather than an inference from timestamps.
# The head is optional because acknowledgements written before the dashboard
# recorded it still have to retire their command.
_OVERRIDE_ACK_MARKER_RE = re.compile(
    r"<!-- pull-request-dashboard-override-ack:(\d+)(?::([^\s>]+))? -->"
)
PRE_REVIEW_ROUTES = ("author",)


@dataclass(frozen=True)
class DashboardOverrideFacts:
    command_id: int
    command_user: str
    head_sha: str
    command_replies: tuple[DashboardCommandReply, ...]


@dataclass(frozen=True)
class DashboardOverrideInput:
    issue_comments: tuple[IssueComment, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "issue_comments", tuple(self.issue_comments))


def author_override_guidance(staleness_note: str = "") -> str:
    guidance = (
        "If you need reviewer or maintainer help, comment "
        "`/dashboard route:reviewers` to request routing from waiting on the "
        "author to waiting on reviewers. The dashboard binds the request to "
        "the head it sees when it reads the command, and a later push restores "
        "normal routing."
    )
    if staleness_note:
        guidance = f"{guidance} {staleness_note}"
    return guidance


def parse_dashboard_command(comment: IssueComment) -> str | None:
    """Return the subcommand of a `/dashboard` command, or None.

    Returns the (possibly empty) subcommand token when the comment's first
    line is a `/dashboard` command, and None when it is not a command at all.
    """
    if comment.minimized:
        return None
    lines = comment.body.strip().splitlines()
    if not lines:
        return None
    tokens = lines[0].strip().split()
    if not tokens or tokens[0] != DASHBOARD_COMMAND_PREFIX:
        return None
    return tokens[1] if len(tokens) > 1 else ""


def dashboard_command_body_remainder(comment: IssueComment) -> str | None:
    """Return the comment body after a leading `/dashboard` command.

    Returns None when the comment is not a `/dashboard` command, and the
    (possibly empty) text after the subcommand otherwise. This lets callers
    keep an author's explanation on the same or later lines while treating the
    command tokens themselves as control metadata.
    """
    if parse_dashboard_command(comment) is None:
        return None
    lines = comment.body.strip().splitlines()
    first_line = lines[0].strip().split(maxsplit=2)
    return "\n".join([first_line[2] if len(first_line) > 2 else "", *lines[1:]]).strip()


def is_authorized_commander(login: str, author: str, reviewers: set[str] | None) -> bool:
    low = (login or "").lower()
    return bool(low) and (low == author.lower() or low in (reviewers or set()))


def latest_authorized_command(
    source: DashboardOverrideInput,
    author: str,
    reviewers: set[str] | None,
) -> tuple[int, str]:
    comments = source.issue_comments
    acknowledged_id = _acknowledged_override_command_id(comments)
    replied_ids = _replied_command_ids(comments)
    best_id = 0
    best_user = ""
    for comment in comments:
        if parse_dashboard_command(comment) != DASHBOARD_OVERRIDE_SUBCOMMAND:
            continue
        commenter = comment.actor.login
        if not is_authorized_commander(commenter, author, reviewers):
            continue
        comment_id = comment.database_id
        if comment_id <= acknowledged_id or comment_id in replied_ids:
            continue
        if comment_id > best_id:
            best_id, best_user = comment_id, commenter
    return best_id, best_user


def dashboard_override_facts(
    source: DashboardOverrideInput,
    author: str,
    reviewers: set[str] | None = None,
    head_sha: str = "",
    previous_facts: DashboardFacts | None = None,
) -> DashboardOverrideFacts:
    command_id, command_user = latest_authorized_command(
        source,
        author,
        reviewers,
    )
    previous_command_id = (
        previous_facts.dashboard_override_command_id
        if previous_facts is not None
        else 0
    )
    previous_head_sha = (
        previous_facts.dashboard_override_head_sha
        if previous_facts is not None
        else ""
    )
    if command_id:
        if command_id == previous_command_id:
            bound_head = previous_head_sha
        else:
            bound_head = head_sha
    else:
        bound_head = acknowledged_override_head(source.issue_comments)
    return DashboardOverrideFacts(
        command_id=command_id,
        command_user=command_user,
        # A pending command keeps its first observed binding until delivery
        # records it in the acknowledgement. An acknowledged command keeps the
        # binding from that acknowledgement.
        head_sha=bound_head,
        command_replies=pending_command_replies(source, author, reviewers),
    )


def _is_dashboard_app_comment(comment: IssueComment) -> bool:
    return comment.is_from_app(DASHBOARD_APP_SLUG)


def _replied_command_ids(comments: Sequence[IssueComment]) -> set[int]:
    """Command ids already answered by a dashboard-app reply comment.

    Reply markers are matched only in comments authored by the dashboard app, so
    a user cannot forge a `command-reply` marker to suppress an owed reply.
    """
    replied_ids: set[int] = set()
    for comment in comments or []:
        if not _is_dashboard_app_comment(comment):
            continue
        for match in _COMMAND_REPLY_MARKER_RE.findall(comment.body):
            replied_ids.add(int(match))
    return replied_ids


def _acknowledged_override_command_ids(
    comments: Sequence[IssueComment],
) -> set[int]:
    """Command ids the dashboard app has already acknowledged.

    Ack markers are matched only in comments authored by the dashboard app, so a
    user cannot forge one to authorize their own command.
    """
    acknowledged_ids: set[int] = set()
    for comment in comments or []:
        if not _is_dashboard_app_comment(comment):
            continue
        for match in _OVERRIDE_ACK_MARKER_RE.finditer(comment.body):
            acknowledged_ids.add(int(match.group(1)))
    return acknowledged_ids


def acknowledged_override_head(comments: Sequence[IssueComment]) -> str:
    """Head SHA the newest acknowledged override command bound to.

    Empty when no command has been acknowledged, and also when the newest
    acknowledgement predates the dashboard recording a head. An unknown head ends
    the handoff instead of guessing at one, so the author runs the command again.
    """
    best_id = 0
    best_head = ""
    for comment in comments or []:
        if not _is_dashboard_app_comment(comment):
            continue
        for match in _OVERRIDE_ACK_MARKER_RE.finditer(comment.body):
            comment_id = int(match.group(1))
            if comment_id > best_id or (comment_id == best_id and not best_head):
                best_id, best_head = comment_id, match.group(2) or ""
    return best_head


def _acknowledged_override_command_id(
    comments: Sequence[IssueComment],
) -> int:
    return max(_acknowledged_override_command_ids(comments), default=0)


def pending_command_replies(
    source: DashboardOverrideInput,
    author: str,
    reviewers: set[str] | None = None,
) -> tuple[DashboardCommandReply, ...]:
    """Return replies owed to unsupported or unauthorized `/dashboard` commands.

    `/dashboard route:reviewers` from the author or an approver is handled by the
    override flow and never gets a reply here. The same command from anyone else
    gets an unauthorized reply, and any unrecognized `/dashboard` subcommand gets
    an unknown-command reply. Commands that already have a reply comment are
    skipped so replies are posted at most once.
    """
    comments = source.issue_comments
    replied_ids = _replied_command_ids(comments)

    replies: list[DashboardCommandReply] = []
    for comment in comments:
        subcommand = parse_dashboard_command(comment)
        if subcommand is None:
            continue
        comment_id = comment.database_id
        if comment_id in replied_ids:
            continue
        commenter = comment.actor.login
        if subcommand == DASHBOARD_OVERRIDE_SUBCOMMAND:
            if is_authorized_commander(commenter, author, reviewers):
                continue
            kind = "unauthorized"
        else:
            kind = "unknown_command"
        replies.append(DashboardCommandReply(
            comment_id=comment_id,
            kind=kind,
            user=commenter,
            subcommand=subcommand,
        ))
    return tuple(replies)


def command_reply_marker(comment_id: int) -> str:
    return f"{COMMAND_REPLY_MARKER_PREFIX}{comment_id} -->"


def override_ack_marker(comment_id: int, head_sha: str = "") -> str:
    head = f":{head_sha}" if head_sha else ""
    return f"{OVERRIDE_ACK_MARKER_PREFIX}{comment_id}{head} -->"


def render_command_reply(reply: DashboardCommandReply) -> str:
    user = reply.user
    mention = f"@{user}, " if user else ""
    kind = reply.kind
    if kind == "unauthorized":
        message = (
            "only the pull request author or a member of an approving team can "
            "use `/dashboard route:reviewers`."
        )
    elif kind == "routed":
        # DashboardCommandReply refuses a routed reply without a route.
        route = reply.route.value
        held_gates = reply.held_gates
        if route in PRE_REVIEW_ROUTES:
            # An active handoff always routes to approvers, so a pre-review
            # route means the command is bound to a head that has been pushed
            # over.
            message = (
                "your reviewer-routing request is not active for the current "
                "pull request head; comment `/dashboard route:reviewers` again "
                "to hand the current head to reviewers."
            )
        elif held_gates:
            message = (
                "your reviewer-routing request was recorded; the reviewer handoff "
                f"is waiting on {held_gates}."
            )
        elif route == "maintainer":
            message = (
                "your reviewer-routing request was recorded; this pull request has "
                "the approvals it needs and is now waiting on maintainers."
            )
        else:
            message = "this pull request was routed to reviewers."
    else:
        subcommand = reply.subcommand
        attempted = DASHBOARD_COMMAND_PREFIX + (f" {subcommand}" if subcommand else "")
        message = (
            f"`{attempted}` is not a recognized dashboard command. The only "
            "supported command is `/dashboard route:reviewers`, which the pull "
            "request author can use to move a pull request from waiting on the "
            "author to waiting on reviewers."
        )
    comment_id = reply.comment_id
    markers = [command_reply_marker(comment_id)]
    if kind == "routed":
        markers.append(override_ack_marker(comment_id, reply.head_sha))
    return "\n".join([
        *markers,
        f"{mention}{message}",
        "",
    ])


def command_reply_exists(
    comments: Sequence[IssueComment],
    comment_id: int,
) -> bool:
    marker = command_reply_marker(comment_id)
    return any(
        _is_dashboard_app_comment(comment)
        and marker in comment.body
        for comment in comments
    )


def append_command_ack_reply(
    source: DashboardOverrideInput,
    facts: DashboardFacts,
    route: DashboardRoute,
) -> DashboardFacts:
    """Queue the reply that acknowledges an override command.

    The reply carries the acknowledgement marker, which records the head the
    command bound to and stops the command from being processed again. Every
    authorized command gets a reply because the command forces the reviewer
    route even when no discussion or failing check was cleared.
    """
    command_id = facts.dashboard_override_command_id
    if not command_id:
        return facts
    if command_id in _replied_command_ids(source.issue_comments):
        return facts
    replies = facts.dashboard_command_replies
    if any(reply.comment_id == command_id for reply in replies):
        return facts
    reply = DashboardCommandReply(
        comment_id=command_id,
        kind="routed",
        head_sha=facts.dashboard_override_head_sha,
        user=facts.dashboard_override_command_user or facts.author,
        route=route,
        held_gates=(
            outstanding_gate_phrase(facts)
            if facts.route_held_for_gates
            else ""
        ),
    )
    return facts.with_changes(dashboard_command_replies=(*replies, reply))
