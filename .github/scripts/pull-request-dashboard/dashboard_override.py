"""Recognize explicit routing overrides and prepare dashboard command replies."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from dashboard_contracts import (
    DashboardCommandReply,
    DashboardFacts,
    DashboardRoute,
)
from dashboard_status import (
    DASHBOARD_APP_SLUG,
    status_reviewer_handoff_clearance,
)
from pull_request_source import IssueComment
from route_presentation import held_gate_phrase
from utils import parse_ts


DASHBOARD_COMMAND_PREFIX = "/dashboard"
DASHBOARD_OVERRIDE_COMMAND = "/dashboard route:reviewers"
DASHBOARD_OVERRIDE_SUBCOMMAND = "route:reviewers"
AUTHOR_OVERRIDE_SUBCOMMAND = "route:author"
COMMAND_REPLY_MARKER_PREFIX = "<!-- pull-request-dashboard-command-reply:"
_COMMAND_REPLY_MARKER_RE = re.compile(
    r"<!-- pull-request-dashboard-command-reply:(\d+) -->"
)
OVERRIDE_ACK_MARKER_PREFIX = "<!-- pull-request-dashboard-override-ack:"
# The acknowledgement records the head observed with the command and its
# effective content timestamp. The head identifies the command binding used by
# durable clearance markers.
# The head is optional because acknowledgements written before the dashboard
# recorded it still have to retire their command.
_OVERRIDE_ACK_MARKER_RE = re.compile(
    r"<!-- pull-request-dashboard-override-ack:"
    r"(?:(?P<author>author(?:-released)?):)?"
    r"(?P<id>\d+)(?::(?P<head>[^:\s>]+))?(?::(?P<since>[^\s>]+))? -->"
)
TOP_LEVEL_FEEDBACK_CUTOFF_MARKER_PREFIX = (
    "<!-- pull-request-dashboard-top-level-feedback-cutoff:"
)
_TOP_LEVEL_FEEDBACK_CUTOFF_MARKER_RE = re.compile(
    r"<!-- pull-request-dashboard-top-level-feedback-cutoff:"
    r"(\d+):([^\s>]+) -->"
)
PERSISTENT_HANDOFF_MARKER_PREFIX = (
    "<!-- pull-request-dashboard-persistent-reviewer-handoff:"
)
_PERSISTENT_HANDOFF_MARKER_RE = re.compile(
    r"<!-- pull-request-dashboard-persistent-reviewer-handoff:"
    r"(\d+):([^:\s>]+) -->"
)
PRE_REVIEW_ROUTES = ("author",)


@dataclass(frozen=True)
class DashboardOverrideFacts:
    command_id: int
    command_user: str
    bound_command_id: int
    head_sha: str
    since: str
    top_level_feedback_cutoff: str
    persistent_handoff: bool
    cleared_by_feedback: bool
    to_author: bool
    author_released: bool
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
        "author to waiting on reviewers. Top-level feedback through that command "
        "is retired; unresolved review threads remain open. The handoff remains "
        "active across pushes until newer actionable human feedback arrives."
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
    *,
    author_can_act: bool = True,
) -> IssueComment | None:
    comments = source.issue_comments
    acknowledged_id = acknowledged_override(comments)[0]
    replied_ids = _replied_command_ids(comments)
    best = None
    for comment in comments:
        subcommand = parse_dashboard_command(comment)
        if subcommand != DASHBOARD_OVERRIDE_SUBCOMMAND and not (
            subcommand == AUTHOR_OVERRIDE_SUBCOMMAND and author_can_act
        ):
            continue
        commenter = comment.actor.login
        if not is_authorized_commander(commenter, author, reviewers):
            continue
        comment_id = comment.database_id
        if comment_id <= acknowledged_id or comment_id in replied_ids:
            continue
        if best is None or comment_id > best.database_id:
            best = comment
    return best


def _effective_command_timestamp(comment: IssueComment) -> str:
    return comment.effective_content_timestamp


def _latest_valid_timestamp(*values: str) -> str:
    candidates = [
        (parsed, value)
        for value in values
        if (parsed := parse_ts(value)) is not None
    ]
    if not candidates:
        return ""
    return max(candidates, key=lambda item: item[0])[1]


def dashboard_override_facts(
    source: DashboardOverrideInput,
    author: str,
    reviewers: set[str] | None = None,
    head_sha: str = "",
    previous_facts: DashboardFacts | None = None,
    *,
    author_can_act: bool = True,
) -> DashboardOverrideFacts:
    command = latest_authorized_command(
        source,
        author,
        reviewers,
        author_can_act=author_can_act,
    )
    command_id = command.database_id if command else 0
    command_user = command.actor.login if command else ""
    command_created_at = _effective_command_timestamp(command) if command else ""
    previous = previous_facts or DashboardFacts()
    previous_command_id = previous.dashboard_override_command_id
    previous_head_sha = previous.dashboard_override_head_sha
    previous_binding_command_id = (
        previous.dashboard_override_bound_command_id or previous_command_id
    )
    if command is not None:
        to_author = parse_dashboard_command(command) == AUTHOR_OVERRIDE_SUBCOMMAND
        author_released = False
        bound_command_id = command_id
        if (
            command_id == previous_command_id
            and to_author == previous.dashboard_override_to_author
            and (previous_head_sha or not to_author)
        ):
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
            author_marker,
        ) = acknowledged_override(source.issue_comments)
        to_author = bool(author_marker)
        author_released = author_marker == "author-released"
    previous_binding_matches = bool(
        bound_command_id == previous_binding_command_id
        and bound_head
        and bound_head == previous_head_sha
        and to_author == previous.dashboard_override_to_author
    )
    author_released = to_author and (
        author_released
        or bool(bound_head and head_sha and bound_head != head_sha)
        or (
            previous_binding_matches
            and previous.dashboard_override_author_released
        )
    )
    override_since = (
        (previous.dashboard_override_since if previous_binding_matches else "")
        or command_created_at
        or acknowledged_since
        or _override_command_effective_at(source.issue_comments, bound_command_id)
        or acknowledgement_created_at
    )
    existing_command_binding = bool(
        command_id
        and command_id == previous_binding_command_id
        and to_author == previous.dashboard_override_to_author
    )
    new_command_binding = bool(command_id and not existing_command_binding)
    top_level_feedback_cutoff = _latest_valid_timestamp(
        previous.dashboard_top_level_feedback_cutoff,
        command_created_at if new_command_binding and not to_author else "",
        acknowledged_top_level_feedback_cutoff(source.issue_comments),
    )
    persistent_handoff = bool(
        not to_author
        and bound_head
        and (
            new_command_binding
            or (previous_binding_matches and previous.dashboard_override_persistent)
            or acknowledges_persistent_handoff(
                source.issue_comments,
                bound_command_id,
                bound_head,
            )
        )
    )
    cleared_command_id, cleared_head = status_reviewer_handoff_clearance(
        source.issue_comments
    )
    cleared_by_feedback = bool(
        not to_author
        and (
            (previous_binding_matches and previous.dashboard_override_cleared_by_feedback)
            or (
                bound_command_id
                and bound_command_id == cleared_command_id
                and bound_head
                and bound_head == cleared_head
            )
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
        top_level_feedback_cutoff=top_level_feedback_cutoff,
        persistent_handoff=persistent_handoff,
        cleared_by_feedback=cleared_by_feedback,
        to_author=to_author,
        author_released=author_released,
        command_replies=pending_command_replies(
            source, author, reviewers, author_can_act=author_can_act
        ),
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


def acknowledges_override(text: str, command_id: int, head_sha: str) -> bool:
    """Return whether the text already acknowledges this command and bound head.

    The acknowledgement's cutoff timestamp is ignored, so a marker that records
    a different cutoff for the same binding still counts as present.
    """
    return any(
        int(match["id"]) == command_id and (match["head"] or "") == head_sha
        for match in _OVERRIDE_ACK_MARKER_RE.finditer(text)
    )


def acknowledged_top_level_feedback_cutoff(
    comments: Sequence[IssueComment],
    command_id: int = 0,
) -> str:
    # A cutoff must belong to a command the app acknowledged.
    command_ids = {command_id} if command_id else {
        int(match["id"])
        for comment in comments
        if _is_dashboard_app_comment(comment)
        for match in _OVERRIDE_ACK_MARKER_RE.finditer(comment.body)
    }
    candidates: list[tuple[datetime, int, str]] = []
    for comment in comments or []:
        if not _is_dashboard_app_comment(comment):
            continue
        for match in _TOP_LEVEL_FEEDBACK_CUTOFF_MARKER_RE.finditer(comment.body):
            marker_command_id = int(match.group(1))
            if marker_command_id not in command_ids:
                continue
            value = match.group(2)
            parsed = parse_ts(value)
            if parsed is not None:
                candidates.append((parsed, marker_command_id, value))
    if not candidates:
        return ""
    return max(candidates, key=lambda item: (item[0], item[1]))[2]


def acknowledges_persistent_handoff(
    comments: Sequence[IssueComment],
    command_id: int,
    head_sha: str,
) -> bool:
    if not command_id or not head_sha:
        return False
    return any(
        _is_dashboard_app_comment(comment)
        and any(
            int(match.group(1)) == command_id
            and match.group(2) == head_sha
            for match in _PERSISTENT_HANDOFF_MARKER_RE.finditer(comment.body)
        )
        for comment in comments or []
    )


def acknowledged_override(
    comments: Sequence[IssueComment],
) -> tuple[int, str, str, str, str]:
    """Newest acknowledged override binding, timestamps, and author marker kind.

    A headless acknowledgement retires its command without activating a handoff.
    """
    best_id = 0
    best_head = ""
    best_since = ""
    best_created_at = ""
    best_author_marker = ""
    best_rank = (0, False, False, False)
    for comment in comments or []:
        if not _is_dashboard_app_comment(comment):
            continue
        for match in _OVERRIDE_ACK_MARKER_RE.finditer(comment.body):
            comment_id = int(match["id"])
            head = match["head"] or ""
            since = match["since"] or ""
            author_marker = match["author"] or ""
            rank = (comment_id, author_marker == "author-released", bool(head), bool(since))
            if rank > best_rank:
                best_rank = rank
                best_id = comment_id
                best_head = head
                best_since = since
                best_created_at = comment.created_at
                best_author_marker = author_marker
    return best_id, best_head, best_since, best_created_at, best_author_marker


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


def pending_command_replies(
    source: DashboardOverrideInput,
    author: str,
    reviewers: set[str] | None = None,
    *,
    author_can_act: bool = True,
) -> tuple[DashboardCommandReply, ...]:
    """Return replies owed to unsupported or unauthorized `/dashboard` commands.

    The override flow handles authorized routing commands with a valid target.
    Commands that already have a reply comment are skipped.
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
        if subcommand in (DASHBOARD_OVERRIDE_SUBCOMMAND, AUTHOR_OVERRIDE_SUBCOMMAND):
            if not is_authorized_commander(commenter, author, reviewers):
                kind = "unauthorized"
            elif subcommand == AUTHOR_OVERRIDE_SUBCOMMAND and not author_can_act:
                kind = "author_unavailable"
            else:
                continue
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
    *,
    to_author: bool = False,
    author_released: bool = False,
) -> str:
    head = f":{head_sha}" if head_sha else ""
    since = f":{override_since}" if head_sha and override_since else ""
    target = ("author-released:" if author_released else "author:") if to_author else ""
    return f"{OVERRIDE_ACK_MARKER_PREFIX}{target}{comment_id}{head}{since} -->"


def top_level_feedback_cutoff_marker(
    comment_id: int,
    cutoff: str,
) -> str:
    return f"{TOP_LEVEL_FEEDBACK_CUTOFF_MARKER_PREFIX}{comment_id}:{cutoff} -->"


def persistent_handoff_marker(
    comment_id: int,
    head_sha: str,
) -> str:
    return f"{PERSISTENT_HANDOFF_MARKER_PREFIX}{comment_id}:{head_sha} -->"


def render_command_reply(reply: DashboardCommandReply) -> str:
    user = reply.user
    mention = f"@{user}, " if user else ""
    kind = reply.kind
    to_author = reply.subcommand == AUTHOR_OVERRIDE_SUBCOMMAND
    has_top_level_feedback_cutoff = parse_ts(reply.top_level_feedback_cutoff) is not None
    if kind == "unauthorized":
        message = (
            "only the pull request author or a member of an approving team can "
            f"use `/dashboard {reply.subcommand}`."
        )
    elif kind == "author_unavailable":
        message = "this pull request has no human author the dashboard can route to."
    elif kind == "routed" and to_author:
        message = (
            "this pull request is waiting on the author. Normal routing resumes "
            "when the author addresses review feedback or the head commit changes. "
            "Use `/dashboard route:reviewers` to hand it back explicitly."
        )
    elif kind == "routed":
        # DashboardCommandReply refuses a routed reply without a route.
        route = reply.route.value
        held_gates = reply.held_gates
        if route in PRE_REVIEW_ROUTES:
            # New handoffs persist across pushes. A pre-review route can remain
            # only for a legacy acknowledgement without the persistence marker.
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
        if reply.persistent_handoff:
            message = (
                f"{message} The handoff remains active across pushes until newer "
                "actionable human feedback arrives."
            )
        has_handoff_timestamp = parse_ts(reply.since) is not None
        if has_top_level_feedback_cutoff and has_handoff_timestamp:
            message = (
                f"{message} Top-level feedback through this request will not return; "
                "unresolved review threads remain open."
            )
        elif has_top_level_feedback_cutoff:
            message = (
                f"{message} The existing top-level feedback cutoff remains in effect, "
                "but no additional top-level feedback was retired because the "
                "dashboard could not determine a safe command time; unresolved review "
                "threads remain open."
            )
        else:
            message = (
                f"{message} No top-level feedback was retired because the dashboard "
                "could not determine a safe command time; unresolved review threads "
                "remain open."
            )
    elif kind == "unknown_command":
        subcommand = reply.subcommand
        attempted = DASHBOARD_COMMAND_PREFIX + (f" {subcommand}" if subcommand else "")
        message = (
            f"`{attempted}` is not a recognized dashboard command. Use "
            "`/dashboard route:author` or `/dashboard route:reviewers`."
        )
    else:
        # A reply the dashboard never posts, such as a cleared_by_feedback reply
        # left in older cached state, must not be rendered as some other kind.
        raise ValueError(f"undeliverable dashboard command reply kind: {kind!r}")
    comment_id = reply.comment_id
    markers = [command_reply_marker(comment_id)]
    if kind == "routed":
        markers.append(
            override_ack_marker(
                comment_id,
                reply.head_sha,
                reply.since,
                to_author=to_author,
            )
        )
        if has_top_level_feedback_cutoff:
            markers.append(
                top_level_feedback_cutoff_marker(
                    comment_id,
                    reply.top_level_feedback_cutoff,
                )
            )
        if reply.persistent_handoff and reply.head_sha:
            markers.append(
                persistent_handoff_marker(comment_id, reply.head_sha)
            )
    return "\n".join([
        *markers,
        f"{mention}{message}",
        "",
    ])


def command_reply_exists(
    comments: Sequence[IssueComment],
    reply: DashboardCommandReply,
) -> bool:
    marker = command_reply_marker(reply.comment_id)
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

    The reply records the command binding and any existing feedback cutoff.
    Released handoffs are recorded in the status comment instead.
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
    if cleared_by_feedback or facts.dashboard_override_author_released:
        return facts.with_changes(dashboard_override_since=override_since)
    if facts.dashboard_override_to_author and (
        route is not DashboardRoute.AUTHOR or not facts.dashboard_override_head_sha
    ):
        return facts
    if facts.dashboard_override_persistent and route is DashboardRoute.AUTHOR:
        return facts.with_changes(dashboard_override_since=override_since)
    kind = "routed"
    replies = facts.dashboard_command_replies
    reply = DashboardCommandReply(
        comment_id=command_id,
        kind=kind,
        subcommand=(
            AUTHOR_OVERRIDE_SUBCOMMAND if facts.dashboard_override_to_author else ""
        ),
        head_sha=facts.dashboard_override_head_sha,
        user=facts.dashboard_override_command_user or facts.author,
        route=route,
        held_gates=(
            held_gate_phrase(facts)
            if facts.route_held_for_gates
            else ""
        ),
        since=override_since,
        top_level_feedback_cutoff=(
            facts.dashboard_top_level_feedback_cutoff
        ),
        persistent_handoff=facts.dashboard_override_persistent,
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
