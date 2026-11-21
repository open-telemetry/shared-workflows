"""Recognize explicit routing overrides and prepare dashboard command replies."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from dashboard_contracts import (
    DashboardCommandReply,
    DashboardFacts,
    DashboardRoute,
    HandoffTarget,
    RoutingHandoff,
)
from dashboard_status import (
    DASHBOARD_APP_SLUG,
    reviewer_handoff_cleared_marker,
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
    handoff: RoutingHandoff
    top_level_feedback_cutoff: str
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
    acknowledged_id = acknowledged_handoff(comments).command_id
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
    previous_facts = previous_facts or DashboardFacts()
    previous = previous_facts.handoff
    new_reviewer_cutoff = ""
    if command is not None:
        target = (
            HandoffTarget.AUTHOR
            if parse_dashboard_command(command) == AUTHOR_OVERRIDE_SUBCOMMAND
            else HandoffTarget.REVIEWERS
        )
        same_command = command.database_id == previous.command_id and target is previous.target
        if same_command and (previous.head_sha or target is HandoffTarget.REVIEWERS):
            handoff = replace(
                previous, since=previous.since or _effective_command_timestamp(command)
            )
        else:
            handoff = RoutingHandoff(
                command_id=command.database_id,
                head_sha=head_sha,
                since=_effective_command_timestamp(command),
                target=target,
                persistent=bool(head_sha and target is HandoffTarget.REVIEWERS),
            )
        if not same_command and target is HandoffTarget.REVIEWERS:
            new_reviewer_cutoff = handoff.since
    else:
        handoff = acknowledged_handoff(source.issue_comments)
        if handoff.matches(previous):
            handoff = replace(
                handoff,
                since=previous.since or handoff.since,
                released=handoff.released or previous.released,
                persistent=handoff.persistent or previous.persistent,
            )
    if (
        handoff.target is HandoffTarget.AUTHOR
        and handoff.head_sha and head_sha and handoff.head_sha != head_sha
    ):
        handoff = handoff.release()
    return DashboardOverrideFacts(
        command_id=command.database_id if command else 0,
        command_user=command.actor.login if command else "",
        handoff=handoff,
        top_level_feedback_cutoff=_latest_valid_timestamp(
            previous_facts.dashboard_top_level_feedback_cutoff,
            new_reviewer_cutoff,
            acknowledged_top_level_feedback_cutoff(source.issue_comments),
        ),
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


def acknowledged_handoff(
    comments: Sequence[IssueComment],
) -> RoutingHandoff:
    best = RoutingHandoff()
    created_at = ""
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
                best = RoutingHandoff(
                    command_id=comment_id,
                    head_sha=head,
                    since=since,
                    target=HandoffTarget.AUTHOR if author_marker else HandoffTarget.REVIEWERS,
                    released=author_marker == "author-released",
                )
                created_at = comment.created_at
    if not best.command_id:
        return best
    best = replace(
        best,
        since=(
            best.since
            or _override_command_effective_at(comments, best.command_id)
            or created_at
        ),
    )
    if best.target is HandoffTarget.AUTHOR or not best.head_sha:
        return best
    return replace(
        best,
        persistent=acknowledges_persistent_handoff(comments, best.command_id, best.head_sha),
        released=status_reviewer_handoff_clearance(comments) == (best.command_id, best.head_sha),
    )


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


def released_handoff_markers(handoff: RoutingHandoff, cutoff: str) -> list[str]:
    if not (handoff.released and handoff.command_id and handoff.head_sha):
        return []
    to_author = handoff.target is HandoffTarget.AUTHOR
    markers = [
        override_ack_marker(
            handoff.command_id, handoff.head_sha, handoff.since,
            to_author=to_author, author_released=to_author,
        ),
    ]
    if cutoff:
        markers.append(top_level_feedback_cutoff_marker(handoff.command_id, cutoff))
    if not to_author:
        markers.append(reviewer_handoff_cleared_marker(handoff.command_id, handoff.head_sha))
    return markers


def preserve_released_handoff_markers(body: str, comments: Sequence[IssueComment]) -> str:
    # A clearance can outlive the command acknowledgement. Restore both before
    # replacing the managed comment.
    acknowledged = acknowledged_handoff(comments)
    command_id, head = status_reviewer_handoff_clearance(comments)
    handoff = acknowledged
    if command_id and (
        command_id > handoff.command_id
        or (command_id == handoff.command_id and handoff.target is HandoffTarget.REVIEWERS)
    ):
        handoff = RoutingHandoff(
            command_id, head,
            handoff.since if (command_id, head) == (handoff.command_id, handoff.head_sha) else "",
            released=True,
        )
    if not handoff.released:
        return body
    rendered = replace(comments[0], body=body)
    current = acknowledged_handoff((rendered,))
    preferred = handoff
    if current.command_id > handoff.command_id or (
        current.command_id == handoff.command_id and current.released
    ):
        preferred = current
    cutoff = _latest_valid_timestamp(
        acknowledged_top_level_feedback_cutoff((*comments, rendered)),
        acknowledged_top_level_feedback_cutoff(comments, handoff.command_id),
    )
    missing = [
        marker for marker in released_handoff_markers(preferred, cutoff)
        if marker not in body and not (
            marker.startswith(OVERRIDE_ACK_MARKER_PREFIX)
            and preferred.target is HandoffTarget.REVIEWERS
            and acknowledges_override(body, preferred.command_id, preferred.head_sha)
        )
    ]
    if not missing:
        return body
    lines = body.splitlines()
    lines[2:2] = missing
    return "\n".join(lines)


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
    handoff = facts.handoff
    command_id = facts.dashboard_override_command_id
    if not command_id or handoff.released:
        return facts
    to_author = handoff.target is HandoffTarget.AUTHOR
    if to_author and (
        route is not DashboardRoute.AUTHOR or not handoff.head_sha
    ):
        return facts
    if handoff.persistent and route is DashboardRoute.AUTHOR:
        return facts
    kind = "routed"
    replies = facts.dashboard_command_replies
    reply = DashboardCommandReply(
        comment_id=command_id,
        kind=kind,
        subcommand=(
            AUTHOR_OVERRIDE_SUBCOMMAND if to_author else ""
        ),
        head_sha=handoff.head_sha,
        user=facts.dashboard_override_command_user or facts.author,
        route=route,
        held_gates=(
            held_gate_phrase(facts)
            if facts.route_held_for_gates
            else ""
        ),
        since=handoff.since,
        top_level_feedback_cutoff=(
            facts.dashboard_top_level_feedback_cutoff
        ),
        persistent_handoff=handoff.persistent,
    )
    if command_reply_exists(source.issue_comments, reply):
        return facts
    if any(
        queued.comment_id == command_id and queued.kind == kind
        for queued in replies
    ):
        return facts
    return facts.with_changes(
        dashboard_command_replies=(*replies, reply),
    )
