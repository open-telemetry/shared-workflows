from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any, TypedDict

from classification_policy import (
    ActionDecision,
    AuthorCommentDecision,
    ClassificationFailure,
    ClassificationResult,
    DiscussionAction,
    DiscussionClassifications,
    classification_result_to_record,
    is_automation_command_comment,
    is_conflict_resolution_comment,
)
from pull_request_activity import (
    is_substantive_activity,
    reviewer_actor_login,
    role_for,
)
from pull_request_source import ReviewThread, ReviewThreadComment
from utils import truncate


POSITIVE_ACK_REACTIONS = {"THUMBS_UP", "HOORAY", "HEART", "ROCKET"}


class LifecycleMode(Enum):
    CLASSIFY = "classify"
    REVIEWER_HANDOFF = "reviewer-handoff"


@dataclass(frozen=True)
class DiscussionInput:
    review_threads: tuple[ReviewThread, ...]
    events: tuple[Mapping[str, Any], ...]
    author: str
    reviewers: frozenset[str]
    conflicts: str


@dataclass(frozen=True)
class PreparedDiscussions:
    review_threads: tuple[dict[str, Any], ...]
    top_level_items: tuple[dict[str, Any], ...]
    top_level_author_comment_items: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class DiscussionLifecycleOutcome:
    prepared: PreparedDiscussions
    classifications: DiscussionClassifications
    pending_actions: dict[str, dict[str, Any]]
    top_level_history: dict[str, dict[str, Any]]
    failed_classifications: tuple[ClassificationFailure, ...]
    mode: LifecycleMode

    def dashboard_fields(self) -> dict[str, Any]:
        fields = {
            "review_threads": list(self.prepared.review_threads),
            "top_level_items": list(self.prepared.top_level_items),
            "top_level_author_comment_items": list(
                self.prepared.top_level_author_comment_items
            ),
            "review_thread_classifications": list(
                map(
                    classification_result_to_record,
                    self.classifications.review_threads,
                )
            ),
            "top_level_classifications": list(
                map(
                    classification_result_to_record,
                    self.classifications.top_level_items,
                )
            ),
            "top_level_author_comment_classifications": list(
                map(
                    classification_result_to_record,
                    self.classifications.top_level_author_comments,
                )
            ),
        }
        if not self.failed_classifications:
            fields["pending_actions"] = self.pending_actions
            fields["top_level_history"] = self.top_level_history
        return fields


class AuthorCommentOutcome(TypedDict):
    source_id: int
    action: str
    timestamp: str
    feedback_id: str


class AuthorCommentSourceState(TypedDict):
    current: set[int]
    classified: set[int]


def _discussion_comment(
    timestamp: str,
    actor: str,
    author: str,
    reviewers: set[str],
    body: str,
    positive_reactors: set[str] | None = None,
) -> dict[str, Any]:
    return {
        "timestamp": timestamp,
        "actor": actor,
        "actor_role": role_for(actor, author, reviewers),
        "body": truncate(body),
        "positive_reactors": sorted(positive_reactors or set()),
    }


def _add_discussion_facts(
    discussion: dict[str, Any],
    comments: list[dict[str, Any]],
    conflicts: str,
) -> dict[str, Any]:
    discussion["discussion_facts"] = {
        "latest_comment_role": comments[-1].get("actor_role"),
        "current_conflicts": conflicts,
    }
    return discussion


def _positive_reaction_logins(
    comment: ReviewThreadComment,
) -> set[str]:
    logins: set[str] = set()
    for group in comment.reaction_groups:
        if group.content not in POSITIVE_ACK_REACTIONS:
            continue
        for user_login in group.user_logins:
            login = user_login.lower()
            if login:
                logins.add(login)
    return logins


def _group_review_threads(source: DiscussionInput) -> list[dict[str, Any]]:
    discussions: list[dict[str, Any]] = []
    reviewers = set(source.reviewers)
    for discussion in source.review_threads:
        if discussion.is_resolved or discussion.is_outdated:
            continue
        raw_comments = discussion.comments
        thread_url = raw_comments[0].url if raw_comments else ""
        ordered = sorted(raw_comments, key=lambda comment: comment.created_at)
        comments = [
            _discussion_comment(
                comment.created_at,
                reviewer_actor_login(comment.actor),
                source.author,
                reviewers,
                comment.body,
                _positive_reaction_logins(comment),
            )
            for comment in ordered
        ]
        comments = [comment for comment in comments if comment["timestamp"]]
        if not comments or all(
            comment["actor_role"] == "author" for comment in comments
        ):
            continue
        discussions.append(
            _add_discussion_facts(
                {
                    "discussion_id": (
                        discussion.node_id
                        or f"review-discussion-{len(discussions) + 1}"
                    ),
                    "discussion_kind": "review-comment-thread",
                    "path": discussion.path or None,
                    "line": discussion.line,
                    "resolved": False,
                    "discussion_url": thread_url,
                    "comments": comments,
                },
                comments,
                source.conflicts,
            )
        )
    discussions.sort(key=lambda thread: thread["comments"][-1]["timestamp"])
    return discussions


def _derive_top_level_items(source: DiscussionInput) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for event in source.events:
        source_kind = event.get("kind") or ""
        if source_kind not in ("issue-comment", "review-state"):
            continue
        state = event.get("state") or ""
        if state == "DISMISSED":
            continue
        body = truncate(event.get("body") or "")
        if source_kind == "review-state" and not body:
            continue
        root_timestamp = event.get("created_timestamp") or event.get("timestamp") or ""
        comment = {
            "timestamp": root_timestamp,
            "actor": event.get("actor") or "",
            "actor_role": event.get("actor_role"),
            "body": body,
            "positive_reactors": [],
        }
        if (
            event.get("source_id") is not None
            and comment["actor"]
            and comment["timestamp"]
            and comment["actor_role"] in ("approver", "outsider")
            and comment["body"]
            and not is_automation_command_comment(comment["body"])
            and not (
                state != "CHANGES_REQUESTED"
                and source.conflicts == "no"
                and is_conflict_resolution_comment(comment["body"])
            )
        ):
            items.append(
                _add_discussion_facts(
                    {
                        "discussion_id": (
                            f"pr-issue-comment-{event['source_id']}"
                            if source_kind == "issue-comment"
                            else f"pr-review-{event['source_id']}"
                        ),
                        "discussion_kind": "top-level-feedback",
                        "source_kind": source_kind,
                        "source_id": event["source_id"],
                        "discussion_url": event.get("discussion_url") or "",
                        "requester": comment["actor"],
                        "pr_author": source.author,
                        "review_state": state or None,
                        "root_timestamp": root_timestamp,
                        "path": None,
                        "line": None,
                        "resolved": False,
                        "comments": [comment],
                    },
                    [comment],
                    source.conflicts,
                )
            )
    items.sort(key=lambda item: item["root_timestamp"])
    return items


def _derive_top_level_author_comment_items(
    source: DiscussionInput,
    top_level_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not top_level_items:
        return []
    earliest_root_timestamp = min(
        item.get("root_timestamp") or "" for item in top_level_items
    )
    items: list[dict[str, Any]] = []
    for event in source.events:
        timestamp = event.get("created_timestamp") or event.get("timestamp") or ""
        if (
            event.get("kind") != "issue-comment"
            or event.get("actor_role") != "author"
            or event.get("source_id") is None
            or timestamp <= earliest_root_timestamp
            or not is_substantive_activity(event)
        ):
            continue
        comment = {
            "timestamp": timestamp,
            "actor": event.get("actor") or "",
            "actor_role": "author",
            "body": truncate(event.get("body") or ""),
            "positive_reactors": [],
        }
        candidate_feedback = [
            {
                "discussion_id": item["discussion_id"],
                "body": "\n\n".join(
                    item_comment.get("body") or ""
                    for item_comment in (item.get("comments") or [])
                ),
            }
            for item in top_level_items
            if (item.get("root_timestamp") or "") < timestamp
        ]
        items.append(
            _add_discussion_facts(
                {
                    "discussion_id": f"pr-author-reply-{event['source_id']}",
                    "discussion_kind": "top-level-author-reply",
                    "source_id": event["source_id"],
                    "candidate_feedback": candidate_feedback,
                    "comments": [comment],
                },
                [comment],
                source.conflicts,
            )
        )
    return items


def prepare_discussions(source: DiscussionInput) -> PreparedDiscussions:
    review_threads = _group_review_threads(source)
    top_level_items = _derive_top_level_items(source)
    top_level_author_comment_items = _derive_top_level_author_comment_items(
        source,
        top_level_items,
    )
    return PreparedDiscussions(
        tuple(review_threads),
        tuple(top_level_items),
        tuple(top_level_author_comment_items),
    )


def _discussions_by_id(
    discussions: tuple[dict[str, Any], ...],
) -> dict[str, dict[str, Any]]:
    return {discussion["discussion_id"]: discussion for discussion in discussions}


def _author_comment_source_state(
    items: tuple[dict[str, Any], ...],
    classifications: tuple[ClassificationResult, ...],
) -> AuthorCommentSourceState:
    by_id = _discussions_by_id(items)
    current = {
        source_id
        for item in items
        if isinstance(source_id := item.get("source_id"), int)
    }
    classified = {
        source_id
        for classification in classifications
        if not classification.failed
        and not classification.deferred
        and (
            source_id := (
                by_id.get(classification.identity.discussion_id) or {}
            ).get("source_id")
        )
        in current
    }
    return {"current": current, "classified": classified}


def _author_comment_outcomes(
    items: tuple[dict[str, Any], ...],
    classifications: tuple[ClassificationResult, ...],
) -> list[AuthorCommentOutcome]:
    by_id = _discussions_by_id(items)
    outcomes: list[AuthorCommentOutcome] = []
    for classification in classifications:
        if classification.failed:
            continue
        decision = classification.decision
        if not isinstance(decision, AuthorCommentDecision):
            raise TypeError(
                "author-comment classifications require AuthorCommentDecision"
            )
        discussion = by_id.get(classification.identity.discussion_id)
        comments = (discussion or {}).get("comments") or []
        timestamp = comments[-1].get("timestamp") if comments else ""
        source_id = (discussion or {}).get("source_id")
        if not isinstance(source_id, int) or not timestamp:
            continue
        for feedback_outcome in decision.feedback_outcomes:
            action = feedback_outcome.action
            feedback_id = feedback_outcome.feedback_id
            if action not in (
                DiscussionAction.AUTHOR,
                DiscussionAction.NONE,
                DiscussionAction.UNCLEAR,
            ):
                continue
            outcomes.append(
                {
                    "source_id": source_id,
                    "action": action.value,
                    "timestamp": timestamp,
                    "feedback_id": feedback_id,
                }
            )
    outcomes.sort(
        key=lambda outcome: (
            outcome["timestamp"],
            outcome["source_id"],
            outcome["feedback_id"],
        )
    )
    return outcomes


def _author_reply_is_superseded(
    outcomes: list[AuthorCommentOutcome],
    source_id: int | None,
    timestamp: str,
    feedback_id: str,
) -> bool:
    return any(
        outcome["feedback_id"] == feedback_id
        and outcome["action"] == "author"
        and (
            outcome["timestamp"] > timestamp
            or (
                outcome["timestamp"] == timestamp
                and (
                    source_id is None
                    or outcome["source_id"] >= source_id
                )
            )
        )
        for outcome in outcomes
    )


def _should_restore_author_reply(
    outcomes: list[AuthorCommentOutcome],
    source_state: AuthorCommentSourceState | None,
    source_id: int | None,
    timestamp: str,
    feedback_id: str,
) -> bool:
    if source_state is not None and source_id is not None:
        if (
            source_id not in source_state["current"]
            or source_id in source_state["classified"]
        ):
            return False
    return not _author_reply_is_superseded(
        outcomes,
        source_id,
        timestamp,
        feedback_id,
    )


def _completed_author_reply_after(
    feedback_id: str,
    root_timestamp: str,
    outcomes: list[AuthorCommentOutcome],
) -> tuple[str, int | None] | None:
    for outcome in outcomes:
        if (
            outcome["timestamp"] > root_timestamp
            and outcome["action"] == "none"
            and outcome["feedback_id"] == feedback_id
            and not _author_reply_is_superseded(
                outcomes,
                outcome["source_id"],
                outcome["timestamp"],
                feedback_id,
            )
        ):
            return outcome["timestamp"], outcome["source_id"]
    return None


def _latest_author_comment_handoff(
    feedback_id: str,
    root_timestamp: str,
    outcomes: list[AuthorCommentOutcome],
) -> dict[str, str] | None:
    relevant_outcomes = [
        outcome
        for outcome in outcomes
        if outcome["timestamp"] > root_timestamp
        and outcome["action"] in ("author", "none")
        and feedback_id == outcome["feedback_id"]
    ]
    if not relevant_outcomes or relevant_outcomes[-1]["action"] != "author":
        return None
    latest_action = relevant_outcomes[-1]["action"]
    since = relevant_outcomes[-1]["timestamp"]
    for outcome in reversed(relevant_outcomes[:-1]):
        if outcome["action"] != latest_action:
            break
        since = outcome["timestamp"]
    return {"action": latest_action, "timestamp": since}


def _collect_author_evidence(
    discussion: dict[str, Any],
    previous_entry: dict[str, Any],
    author_comment_outcomes: list[AuthorCommentOutcome],
    author_comment_source_state: AuthorCommentSourceState | None,
) -> tuple[dict[str, str], int | None]:
    root_timestamp = discussion.get("root_timestamp") or ""
    evidence: dict[str, str] = {}
    reply_source_id: int | None = None
    previous_reply = (previous_entry.get("evidence") or {}).get("reply") or ""
    previous_reply_source_id = previous_entry.get("reply_source_id")
    if (
        previous_reply > root_timestamp
        and _should_restore_author_reply(
            author_comment_outcomes,
            author_comment_source_state,
            previous_reply_source_id
            if isinstance(previous_reply_source_id, int)
            else None,
            previous_reply,
            discussion["discussion_id"],
        )
    ):
        evidence["reply"] = previous_reply
        reply_source_id = previous_reply_source_id

    completed_reply = _completed_author_reply_after(
        discussion["discussion_id"],
        root_timestamp,
        author_comment_outcomes,
    )
    if completed_reply:
        timestamp, source_id = completed_reply
        if (
            not evidence.get("reply")
            or timestamp < evidence["reply"]
            or (timestamp == evidence["reply"] and reply_source_id is None)
        ):
            evidence["reply"] = timestamp
            reply_source_id = source_id
    return evidence, reply_source_id


def _pending_action_for(action: str) -> str:
    return "author" if action == "unclear" else action


def _review_thread_pending_actions(
    review_threads: tuple[dict[str, Any], ...],
    classifications: tuple[ClassificationResult, ...],
) -> dict[str, dict[str, Any]]:
    by_id = _discussions_by_id(review_threads)
    pending_actions: dict[str, dict[str, Any]] = {}
    for classification in classifications:
        decision = classification.decision
        if not isinstance(decision, ActionDecision):
            raise TypeError("review-thread classifications require ActionDecision")
        action = decision.action
        discussion_id = classification.identity.discussion_id
        discussion = by_id.get(discussion_id)
        comments = (discussion or {}).get("comments") or []
        if action is not DiscussionAction.NONE and comments:
            entry = {
                "action": _pending_action_for(action.value),
                "since": (
                    classification.since
                    or comments[-1].get("timestamp")
                    or ""
                ),
            }
            if classification.ignored_last_comment:
                entry["ignored_last_comment"] = True
            pending_actions[discussion_id] = entry
    return pending_actions


def _advance_top_level_actions(
    top_level_items: tuple[dict[str, Any], ...],
    classifications: tuple[ClassificationResult, ...],
    previous_history: dict[str, dict[str, Any]],
    author_comment_outcomes: list[AuthorCommentOutcome],
    author_comment_source_state: AuthorCommentSourceState | None,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    by_id = _discussions_by_id(top_level_items)
    pending_actions: dict[str, dict[str, Any]] = {}
    top_level_history: dict[str, dict[str, Any]] = {}
    for classification in classifications:
        discussion = by_id.get(classification.identity.discussion_id)
        decision = classification.decision
        if not discussion:
            continue
        if not isinstance(decision, ActionDecision):
            raise TypeError(
                "top-level classifications require ActionDecision"
            )
        action = decision.action
        root_timestamp = discussion.get("root_timestamp") or ""
        if action not in (
            DiscussionAction.AUTHOR,
            DiscussionAction.UNCLEAR,
        ):
            continue
        previous_entry = previous_history.get(discussion["discussion_id"]) or {}
        evidence, reply_source_id = _collect_author_evidence(
            discussion,
            previous_entry,
            author_comment_outcomes,
            author_comment_source_state,
        )
        if evidence:
            top_level_history[discussion["discussion_id"]] = {"evidence": evidence}
            if reply_source_id is not None:
                top_level_history[discussion["discussion_id"]]["reply_source_id"] = (
                    reply_source_id
                )
        if evidence.get("reply"):
            continue
        handoff = _latest_author_comment_handoff(
            discussion["discussion_id"],
            root_timestamp,
            author_comment_outcomes,
        )
        if handoff is not None:
            pending_actions[discussion["discussion_id"]] = {
                "action": _pending_action_for(handoff["action"]),
                "since": handoff["timestamp"],
            }
            continue
        pending_actions[discussion["discussion_id"]] = {
            "action": _pending_action_for(action.value),
            "since": root_timestamp,
        }
    return pending_actions, top_level_history


def resolve_discussions(
    prepared: PreparedDiscussions,
    classifications: DiscussionClassifications | None,
    previous_history: dict[str, dict[str, Any]] | None = None,
    *,
    mode: LifecycleMode = LifecycleMode.CLASSIFY,
) -> DiscussionLifecycleOutcome:
    history = dict(previous_history or {})
    if mode is LifecycleMode.REVIEWER_HANDOFF:
        if classifications is not None:
            raise ValueError("reviewer handoff must not include classifications")
        return DiscussionLifecycleOutcome(
            prepared,
            DiscussionClassifications.empty(),
            {},
            history,
            (),
            mode,
        )
    if classifications is None:
        raise ValueError("classified lifecycle requires classifications")

    failed_classifications = tuple(
        classification
        for classification in (
            classifications.review_threads
            + classifications.top_level_items
            + classifications.top_level_author_comments
        )
        if isinstance(classification, ClassificationFailure)
    )
    if failed_classifications:
        return DiscussionLifecycleOutcome(
            prepared,
            classifications,
            {},
            {},
            failed_classifications,
            mode,
        )

    author_comment_outcomes = _author_comment_outcomes(
        prepared.top_level_author_comment_items,
        classifications.top_level_author_comments,
    )
    author_comment_source_state = _author_comment_source_state(
        prepared.top_level_author_comment_items,
        classifications.top_level_author_comments,
    )
    review_thread_pending_actions = _review_thread_pending_actions(
        prepared.review_threads,
        classifications.review_threads,
    )
    top_level_pending_actions, top_level_history = _advance_top_level_actions(
        prepared.top_level_items,
        classifications.top_level_items,
        history,
        author_comment_outcomes,
        author_comment_source_state,
    )
    return DiscussionLifecycleOutcome(
        prepared,
        classifications,
        review_thread_pending_actions | top_level_pending_actions,
        top_level_history,
        (),
        mode,
    )
