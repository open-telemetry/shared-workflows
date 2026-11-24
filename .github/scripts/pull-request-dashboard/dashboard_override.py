"""Recognize explicit routing overrides and prepare dashboard command replies."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from dashboard_contracts import (
    DashboardCommandReply,
    DashboardFacts,
    DashboardRoute,
    HandoffTarget,
    RoutingHandoff,
)
from dashboard_status import DASHBOARD_APP_SLUG
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
REVIEWER_HANDOFF_CLEARED_MARKER_PREFIX = (
    "<!-- pull-request-dashboard-reviewer-handoff-cleared:"
)
_REVIEWER_HANDOFF_CLEARED_MARKER_RE = re.compile(
    r"<!-- pull-request-dashboard-reviewer-handoff-cleared:(\d+):([^\s>]+) -->"
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


def _inspect_commands(
    comments: Sequence[IssueComment],
    author: str,
    reviewers: set[str] | None,
    min_command_id: int,
    *,
    author_can_act: bool,
) -> tuple[
    tuple[IssueComment, HandoffTarget] | None,
    tuple[DashboardCommandReply, ...],
]:
    replied_ids = _replied_command_ids(comments)
    winner: tuple[IssueComment, HandoffTarget] | None = None
    replies: list[DashboardCommandReply] = []
    for comment in comments:
        comment_id = comment.database_id
        if comment_id in replied_ids:
            continue
        subcommand = parse_dashboard_command(comment)
        if subcommand is None:
            continue

        commenter = comment.actor.login
        if subcommand not in (DASHBOARD_OVERRIDE_SUBCOMMAND, AUTHOR_OVERRIDE_SUBCOMMAND):
            kind = "unknown_command"
        elif not is_authorized_commander(commenter, author, reviewers):
            kind = "unauthorized"
        elif subcommand == AUTHOR_OVERRIDE_SUBCOMMAND and not author_can_act:
            kind = "author_unavailable"
        else:
            if comment_id >= min_command_id and (
                winner is None or comment_id > winner[0].database_id
            ):
                target = (
                    HandoffTarget.AUTHOR
                    if subcommand == AUTHOR_OVERRIDE_SUBCOMMAND
                    else HandoffTarget.REVIEWERS
                )
                winner = (comment, target)
            continue

        replies.append(DashboardCommandReply(comment_id, kind, commenter, subcommand))
    return winner, tuple(replies)


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
    previous_facts = previous_facts or DashboardFacts()
    previous = previous_facts.handoff
    acknowledged, acknowledged_cutoff = _read_handoff_markers(source.issue_comments)
    winner, replies = _inspect_commands(
        source.issue_comments,
        author,
        reviewers,
        max(acknowledged.command_id + 1, previous.command_id),
        author_can_act=author_can_act,
    )
    new_reviewer_cutoff = ""
    command_id = 0
    command_user = ""
    if winner is not None:
        command, target = winner
        command_id = command.database_id
        command_user = command.actor.login
        same_command = command_id == previous.command_id and target is previous.target
        if same_command and (previous.head_sha or target is HandoffTarget.REVIEWERS):
            handoff = replace(
                previous, since=previous.since or _effective_command_timestamp(command)
            )
        else:
            handoff = RoutingHandoff(
                command_id=command_id,
                head_sha=head_sha,
                since=_effective_command_timestamp(command),
                target=target,
                persistent=bool(head_sha and target is HandoffTarget.REVIEWERS),
            )
        if not same_command and target is HandoffTarget.REVIEWERS:
            new_reviewer_cutoff = handoff.since
    elif previous.command_id > acknowledged.command_id:
        # The newer command no longer applies. Keep its ID to retire older handoffs.
        handoff = previous.release()
    else:
        handoff = acknowledged
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
        command_id=command_id,
        command_user=command_user,
        handoff=handoff,
        top_level_feedback_cutoff=_latest_valid_timestamp(
            previous_facts.dashboard_top_level_feedback_cutoff,
            new_reviewer_cutoff,
            acknowledged_cutoff,
        ),
        command_replies=replies,
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


def _read_handoff_markers(
    comments: Sequence[IssueComment],
    *,
    rendered_status_body: str = "",
) -> tuple[RoutingHandoff, str]:
    """Read the newest app-owned binding and the independent feedback cutoff."""
    oldest = datetime.min.replace(tzinfo=timezone.utc)
    bodies = [
        (
            comment.body,
            comment.created_at,
            parse_ts(
                comment.content_updated_at or comment.updated_at or comment.created_at
            ) or oldest,
        )
        for comment in comments
        if _is_dashboard_app_comment(comment)
    ]
    if rendered_status_body:
        bodies.insert(
            0, (rendered_status_body, "", datetime.max.replace(tzinfo=timezone.utc))
        )

    acknowledgements = [
        (match, created_at)
        for body, created_at, _ in bodies
        for match in _OVERRIDE_ACK_MARKER_RE.finditer(body)
    ]
    ack, created_at = max(
        acknowledgements,
        key=lambda item: (
            int(item[0]["id"]), bool(item[0]["head"]), bool(item[0]["since"])
        ),
        default=(None, ""),
    )
    # A legacy clearance can survive without its acknowledgement.
    clearances = [
        (int(match[1]), updated_at, match[2])
        for body, _, updated_at in bodies
        for match in _REVIEWER_HANDOFF_CLEARED_MARKER_RE.finditer(body)
    ]
    cleared_id, _, cleared_head = max(
        reversed(clearances),
        key=lambda item: (item[0], item[1]),
        default=(0, oldest, ""),
    )
    handoff = RoutingHandoff()
    if cleared_id > (int(ack["id"]) if ack else 0):
        handoff = RoutingHandoff(command_id=cleared_id, head_sha=cleared_head, released=True)
        created_at = ""
    elif ack:
        command_id = int(ack["id"])
        head_sha = ack["head"] or ""
        target = HandoffTarget.AUTHOR if ack["author"] else HandoffTarget.REVIEWERS
        handoff = RoutingHandoff(
            command_id=command_id,
            head_sha=head_sha,
            since=ack["since"] or "",
            target=target,
            released=(
                any(
                    match["author"] == "author-released"
                    and int(match["id"]) == command_id
                    and (match["head"] or "") == head_sha
                    for match, _ in acknowledgements
                )
                if target is HandoffTarget.AUTHOR
                else (cleared_id, cleared_head) == (command_id, head_sha)
            ),
            persistent=bool(
                target is HandoffTarget.REVIEWERS and head_sha and any(
                    persistent_handoff_marker(command_id, head_sha) in body
                    for body, _, _ in bodies
                )
            ),
        )
    if handoff.command_id and not handoff.since:
        handoff = replace(
            handoff,
            since=_override_command_effective_at(comments, handoff.command_id) or created_at,
        )
    acknowledged_ids = {int(match["id"]) for match, _ in acknowledgements}
    acknowledged_ids.update(command_id for command_id, _, _ in clearances)
    cutoff = max(
        (
            (parsed, int(match[1]), match[2])
            for body, _, _ in bodies
            for match in _TOP_LEVEL_FEEDBACK_CUTOFF_MARKER_RE.finditer(body)
            if int(match[1]) in acknowledged_ids
            and (parsed := parse_ts(match[2])) is not None
        ),
        key=lambda item: (item[0], item[1]),
        default=(oldest, 0, ""),
    )[2]
    return handoff, cutoff


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
        markers.append(
            f"{REVIEWER_HANDOFF_CLEARED_MARKER_PREFIX}{handoff.command_id}:{handoff.head_sha} -->"
        )
    return markers


def preserve_released_handoff_markers(body: str, comments: Sequence[IssueComment]) -> str:
    handoff, cutoff = _read_handoff_markers(comments, rendered_status_body=body)
    markers = released_handoff_markers(handoff, cutoff)
    if all(marker in body for marker in markers):
        return body
    prefixes = (
        OVERRIDE_ACK_MARKER_PREFIX,
        TOP_LEVEL_FEEDBACK_CUTOFF_MARKER_PREFIX,
        REVIEWER_HANDOFF_CLEARED_MARKER_PREFIX,
    )
    lines = [line for line in body.splitlines() if not line.startswith(prefixes)]
    lines[2:2] = markers
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
        # Routing replies are status updates for everyone following the pull
        # request, so they do not mention anyone.
        mention = ""
        message = (
            "This pull request is now waiting on the author. It returns to "
            "reviewers when the author addresses review feedback or pushes a new "
            "commit. Comment `/dashboard route:reviewers` to hand it back sooner."
        )
    elif kind == "routed":
        mention = ""
        # DashboardCommandReply refuses a routed reply without a route.
        route = reply.route.value
        held_gates = reply.held_gates
        legacy = route in PRE_REVIEW_ROUTES
        if legacy:
            # New handoffs persist across pushes. A pre-review route can remain
            # only for a legacy acknowledgement without the persistence marker.
            message = (
                "This request applied to an earlier head and is no longer active. "
                "Comment `/dashboard route:reviewers` again to hand the current "
                "head to reviewers."
            )
        elif held_gates:
            message = (
                "The reviewer handoff is recorded and is waiting on "
                f"{held_gates}."
            )
        elif route == "maintainer":
            message = (
                "This pull request has the approvals it needs and is now waiting "
                "on maintainers."
            )
        else:
            message = "This pull request is now waiting on reviewers."
        if reply.persistent_handoff:
            message = (
                f"{message} The handoff stays active across pushes until a "
                "reviewer leaves new actionable feedback."
            )
        has_handoff_timestamp = parse_ts(reply.since) is not None
        if has_top_level_feedback_cutoff and has_handoff_timestamp:
            message = (
                f"{message} Top-level feedback posted before this request no "
                "longer counts; unresolved review threads stay open."
            )
        elif has_top_level_feedback_cutoff:
            message = (
                f"{message} Top-level feedback retired by an earlier request still "
                "doesn't count, but no newer feedback was retired because the "
                "dashboard couldn't determine when this command was posted; "
                "unresolved review threads stay open."
            )
        else:
            message = (
                f"{message} No top-level feedback was retired because the "
                "dashboard couldn't determine when this command was posted; "
                "unresolved review threads stay open."
            )
        if not legacy:
            message = (
                f"{message} Comment `/dashboard route:author` to hand it back to "
                "the author."
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
