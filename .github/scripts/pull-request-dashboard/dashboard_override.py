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
from dashboard_status import (
    DASHBOARD_APP_SLUG,
    reviewer_handoff_cleared_marker,
    status_reviewer_handoff_clearance,
)
from pull_request_source import IssueComment
from route_presentation import outstanding_gate_phrase
from utils import parse_ts


DASHBOARD_COMMAND_PREFIX = "/dashboard"
DASHBOARD_OVERRIDE_COMMAND = "/dashboard route:reviewers"
DASHBOARD_OVERRIDE_SUBCOMMAND = "route:reviewers"
COMMAND_REPLY_MARKER_PREFIX = "<!-- pull-request-dashboard-command-reply:"
_COMMAND_REPLY_MARKER_RE = re.compile(
    r"<!-- pull-request-dashboard-command-reply:(\d+) -->"
)
OVERRIDE_ACK_MARKER_PREFIX = "<!-- pull-request-dashboard-override-ack:"
# The acknowledgement records which head the command bound to and its effective
# content timestamp. The head makes the handoff a direct comparison against the
# current one.
# The head is optional because acknowledgements written before the dashboard
# recorded it still have to retire their command.
_OVERRIDE_ACK_MARKER_RE = re.compile(
    r"<!-- pull-request-dashboard-override-ack:"
    r"(\d+)(?::([^:\s>]+))?(?::([^\s>]+))? -->"
)
PRE_REVIEW_ROUTES = ("author",)


@dataclass(frozen=True)
class DashboardOverrideFacts:
    command_id: int
    command_user: str
    bound_command_id: int
    head_sha: str
    since: str
    cleared_by_feedback: bool
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
) -> tuple[int, str, str]:
    comments = source.issue_comments
    acknowledged_id = _acknowledged_override_command_id(comments)
    replied_ids = _replied_command_ids(comments)
    best_id = 0
    best_user = ""
    best_created_at = ""
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
            best_id = comment_id
            best_user = commenter
            best_created_at = _effective_command_timestamp(comment)
    return best_id, best_user, best_created_at


def _effective_command_timestamp(comment: IssueComment) -> str:
    created_at = parse_ts(comment.created_at)
    content_updated_at = parse_ts(comment.content_updated_at)
    if content_updated_at is not None and (
        created_at is None or content_updated_at >= created_at
    ):
        return comment.content_updated_at
    return comment.created_at


def dashboard_override_facts(
    source: DashboardOverrideInput,
    author: str,
    reviewers: set[str] | None = None,
    head_sha: str = "",
    previous_facts: DashboardFacts | None = None,
) -> DashboardOverrideFacts:
    command_id, command_user, command_created_at = latest_authorized_command(
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
    previous_bound_command_id = (
        previous_facts.dashboard_override_bound_command_id
        if previous_facts is not None
        else 0
    )
    previous_since = (
        previous_facts.dashboard_override_since
        if previous_facts is not None
        else ""
    )
    previous_cleared = (
        previous_facts.dashboard_override_cleared_by_feedback
        if previous_facts is not None
        else False
    )
    if command_id:
        bound_command_id = command_id
        if command_id == previous_command_id:
            bound_head = previous_head_sha
        else:
            bound_head = head_sha
        acknowledged_since = ""
        acknowledgement_created_at = ""
    else:
        (
            bound_command_id,
            bound_head,
            acknowledged_since,
            acknowledgement_created_at,
        ) = acknowledged_override(source.issue_comments)
    previous_binding_matches = bool(
        bound_command_id == previous_bound_command_id
        and bound_head
        and bound_head == previous_head_sha
    )
    override_since = (
        (previous_since if previous_binding_matches else "")
        or command_created_at
        or acknowledged_since
        or _override_command_effective_at(source.issue_comments, bound_command_id)
        or acknowledgement_created_at
    )
    cleared_command_id, cleared_head = status_reviewer_handoff_clearance(
        source.issue_comments
    )
    previous_clearance_applies = previous_binding_matches and previous_cleared
    cleared_by_feedback = bool(
        previous_clearance_applies
        or (
            bound_command_id
            and bound_command_id == cleared_command_id
            and bound_head
            and bound_head == cleared_head
        )
    )
    return DashboardOverrideFacts(
        command_id=command_id,
        command_user=command_user,
        bound_command_id=bound_command_id,
        # A pending command keeps its first observed binding until delivery
        # records it in the acknowledgement. An acknowledged command keeps the
        # binding from that acknowledgement.
        head_sha=bound_head,
        since=override_since,
        cleared_by_feedback=cleared_by_feedback,
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


def acknowledged_override(
    comments: Sequence[IssueComment],
) -> tuple[int, str, str, str]:
    """Newest acknowledged override command id, bound head SHA, and cutoff times.

    The head is empty when the newest acknowledgement predates the dashboard
    recording it. An unknown head ends the handoff instead of guessing at one, so
    the author runs the command again.
    """
    best_id = 0
    best_head = ""
    best_since = ""
    best_created_at = ""
    for comment in comments or []:
        if not _is_dashboard_app_comment(comment):
            continue
        for match in _OVERRIDE_ACK_MARKER_RE.finditer(comment.body):
            comment_id = int(match.group(1))
            head = match.group(2) or ""
            since = match.group(3) or ""
            if (
                comment_id > best_id
                or (
                    comment_id == best_id
                    and (bool(head), bool(since))
                    > (bool(best_head), bool(best_since))
                )
            ):
                best_id = comment_id
                best_head = head
                best_since = since
                best_created_at = comment.created_at
    return best_id, best_head, best_since, best_created_at


def _override_command_effective_at(
    comments: Sequence[IssueComment],
    command_id: int,
) -> str:
    if not command_id:
        return ""
    for comment in comments:
        if comment.database_id == command_id:
            return _effective_command_timestamp(comment)
    return ""


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


def override_ack_marker(
    comment_id: int,
    head_sha: str = "",
    override_since: str = "",
) -> str:
    head = f":{head_sha}" if head_sha else ""
    since = f":{override_since}" if head_sha and override_since else ""
    return f"{OVERRIDE_ACK_MARKER_PREFIX}{comment_id}{head}{since} -->"


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
    elif kind == "cleared_by_feedback":
        message = (
            "newer actionable reviewer feedback ended the reviewer handoff, so "
            "this pull request is routed normally again."
        )
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
    if kind in ("routed", "cleared_by_feedback"):
        markers.append(
            override_ack_marker(
                comment_id,
                reply.head_sha,
                reply.since,
            )
        )
    if kind == "cleared_by_feedback" and reply.head_sha:
        markers.append(reviewer_handoff_cleared_marker(comment_id, reply.head_sha))
    return "\n".join([
        *markers,
        f"{mention}{message}",
        "",
    ])


def command_reply_exists(
    comments: Sequence[IssueComment],
    reply: DashboardCommandReply,
) -> bool:
    marker = (
        reviewer_handoff_cleared_marker(reply.comment_id, reply.head_sha)
        if reply.kind == "cleared_by_feedback" and reply.head_sha
        else command_reply_marker(reply.comment_id)
    )
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

    The reply carries the acknowledgement marker, which records the bound head
    and feedback cutoff and stops the command from being processed again. A
    command superseded by reviewer feedback is acknowledged in the status comment
    instead of producing another top-level comment.
    """
    cleared_by_feedback = facts.dashboard_override_cleared_by_feedback
    command_id = (
        facts.dashboard_override_command_id
        or (
            facts.dashboard_override_bound_command_id
            if cleared_by_feedback
            else 0
        )
    )
    if not command_id:
        return facts
    override_since = (
        facts.dashboard_override_since
        or _override_command_effective_at(source.issue_comments, command_id)
    )
    if cleared_by_feedback:
        return facts.with_changes(dashboard_override_since=override_since)
    kind = "routed"
    replies = facts.dashboard_command_replies
    reply = DashboardCommandReply(
        comment_id=command_id,
        kind=kind,
        head_sha=facts.dashboard_override_head_sha,
        user=facts.dashboard_override_command_user or facts.author,
        route=route,
        held_gates=(
            outstanding_gate_phrase(facts)
            if facts.route_held_for_gates
            else ""
        ),
        since=override_since,
    )
    if command_reply_exists(source.issue_comments, reply):
        return facts
    if any(
        queued.comment_id == command_id and queued.kind == kind
        for queued in replies
    ):
        return facts
    return facts.with_changes(
        dashboard_override_since=override_since,
        dashboard_command_replies=(*replies, reply),
    )
