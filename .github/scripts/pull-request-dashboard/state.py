from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from dashboard_contracts import (
    DashboardCommandReply,
    DashboardFacts,
    DashboardRoute,
    DashboardState,
    EvaluationSuccess,
    ReviewerSummary,
    StoredDashboardResult,
    freeze_json_object,
    thaw_json,
)
from github_cli import detect_repo, normalize_repo, repo_state_key
import state_branch


DASHBOARD_MARKDOWN_FILE = "pull-request-dashboard.md"
BACKFILL_STATE_FILE = "backfill-state.json"
AUTHOR_NUDGE_STATE_FILE = "author-nudge-state.json"
COPILOT_REVIEW_REQUEST_STATE_FILE = "copilot-review-request-state.json"
STATUS_COMMENT_ROLLOUT_STATE_FILE = "status-comment-rollout-state.json"
DELIVERY_VERSIONS_FILE = "delivery-versions.json"

# These monotonic versions jointly order delivery compatibility. Increment the
# relevant version whenever its stored shape, meaning, or delivered behavior
# changes. A worker lower in any component skips delivery; after claiming the
# current vector, ordinary state loaders may regenerate mismatched disposable
# caches. Every constant ending in _STATE_VERSION or _REVISION is included.
# dashboard-state.json: accepted PR routing results and backfill readiness.
DASHBOARD_STATE_VERSION = 13
# backfill-state.json: round-robin cursor used by full dashboard refreshes.
BACKFILL_STATE_VERSION = 3
# notification-state.json: pending and delivered Slack notification records.
NOTIFICATION_STATE_VERSION = 3
# author-nudge-state.json: waiting episodes and delivered author reminders.
AUTHOR_NUDGE_STATE_VERSION = 3
# copilot-review-request-state.json: pending and delivered review requests.
COPILOT_REVIEW_REQUEST_STATE_VERSION = 6
# status-comment-rollout-state.json: target/completed renderer revisions and queue.
STATUS_COMMENT_ROLLOUT_STATE_VERSION = 2
# Rendered status-comment behavior. Increment when existing comments need to
# adopt a change; hourly runs durably roll it out to all open PRs.
STATUS_COMMENT_REVISION = 16
INITIAL_BACKFILL_COMPLETE_KEY = "initial_backfill_complete"
_state_dir: Path | None = None


def set_state_dir(path: Path) -> None:
    global _state_dir
    _state_dir = path


def state_dir() -> Path:
    if _state_dir is None:
        raise RuntimeError("state directory has not been initialized")
    return _state_dir


def dashboard_state_path() -> Path:
    return state_dir() / "dashboard-state.json"


def notification_state_path() -> Path:
    return state_dir() / "notification-state.json"


def author_nudge_state_path() -> Path:
    return state_dir() / AUTHOR_NUDGE_STATE_FILE


def copilot_review_request_state_path() -> Path:
    return state_dir() / COPILOT_REVIEW_REQUEST_STATE_FILE


def backfill_state_path() -> Path:
    return state_dir() / BACKFILL_STATE_FILE


def status_comment_rollout_state_path() -> Path:
    return state_dir() / STATUS_COMMENT_ROLLOUT_STATE_FILE


def delivery_versions_path() -> Path:
    return state_dir() / DELIVERY_VERSIONS_FILE


def dashboard_markdown_path() -> Path:
    return state_dir() / DASHBOARD_MARKDOWN_FILE


def empty_state() -> DashboardState:
    return DashboardState()


def initial_backfill_complete(state: DashboardState | None) -> bool:
    return bool(state and state.initial_backfill_complete)


def empty_backfill_state() -> dict[str, Any]:
    return {"version": BACKFILL_STATE_VERSION, "cursor": {}}


def empty_status_comment_rollout_state() -> dict[str, Any]:
    return {
        "version": STATUS_COMMENT_ROLLOUT_STATE_VERSION,
        "target_revision": 0,
        "completed_revision": 0,
        "pending_pr_numbers": [],
        "draft_reconciliation_cursor": 0,
    }


def current_delivery_versions() -> dict[str, int]:
    return {
        name: value
        for name, value in globals().items()
        if name.endswith(("_STATE_VERSION", "_REVISION"))
    }


def load_state_file(
    path: Path,
    current_version: int,
    *,
    compatible_versions: tuple[int, ...] = (),
) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(
            f"warning: ignoring unreadable state file {path}: {e!r}",
            file=sys.stderr,
        )
        return None
    if not isinstance(data, dict):
        return None
    if data.get("version") not in (current_version, *compatible_versions):
        print(
            f"state version changed; regenerating {path}",
            file=sys.stderr,
        )
        return None
    data = {k: v for k, v in data.items() if not str(k).startswith("_")}
    data["version"] = current_version
    return data


def save_state_file(path: Path, state: dict[str, Any], version: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    stored = {k: v for k, v in state.items() if not k.startswith("_")}
    stored["version"] = version
    path.write_text(json.dumps(stored, sort_keys=True, indent=2), encoding="utf-8")


def load_backfill_state() -> dict[str, Any]:
    state = load_state_file(backfill_state_path(), BACKFILL_STATE_VERSION)
    if state is None:
        return empty_backfill_state()
    cursor = state.get("cursor")
    if not isinstance(cursor, dict):
        state["cursor"] = {}
    state.pop("prs", None)
    return state


def save_backfill_state(state: dict[str, Any]) -> None:
    stored = {k: v for k, v in state.items() if k != "prs"}
    stored.setdefault("cursor", {})
    save_state_file(backfill_state_path(), stored, BACKFILL_STATE_VERSION)


def load_status_comment_rollout_state() -> dict[str, Any]:
    state = load_state_file(
        status_comment_rollout_state_path(),
        STATUS_COMMENT_ROLLOUT_STATE_VERSION,
        compatible_versions=(1,),
    )
    if state is None:
        return empty_status_comment_rollout_state()
    pending = state.get("pending_pr_numbers")
    try:
        target_revision = max(int(state.get("target_revision") or 0), 0)
        completed_revision = max(int(state.get("completed_revision") or 0), 0)
        draft_reconciliation_cursor = max(
            int(state.get("draft_reconciliation_cursor") or 0),
            0,
        )
    except (TypeError, ValueError):
        return empty_status_comment_rollout_state()
    return {
        "version": STATUS_COMMENT_ROLLOUT_STATE_VERSION,
        "target_revision": target_revision,
        "completed_revision": completed_revision,
        "draft_reconciliation_cursor": draft_reconciliation_cursor,
        "pending_pr_numbers": (
            [
                number
                for number in pending
                if isinstance(number, int) and not isinstance(number, bool) and number > 0
            ]
            if isinstance(pending, list)
            else []
        ),
    }


def save_status_comment_rollout_state(state: dict[str, Any]) -> None:
    save_state_file(
        status_comment_rollout_state_path(),
        {
            "target_revision": int(state.get("target_revision") or 0),
            "completed_revision": int(state.get("completed_revision") or 0),
            "draft_reconciliation_cursor": int(
                state.get("draft_reconciliation_cursor") or 0
            ),
            "pending_pr_numbers": list(
                dict.fromkeys(state.get("pending_pr_numbers") or [])
            ),
        },
        STATUS_COMMENT_ROLLOUT_STATE_VERSION,
    )


def load_delivery_versions() -> dict[str, int] | None:
    path = delivery_versions_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"warning: unreadable delivery versions {path}: {e!r}", file=sys.stderr)
        return None
    if not isinstance(data, dict):
        return None
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value < 0
        for value in data.values()
    ):
        return None
    return data


def save_delivery_versions(versions: dict[str, int]) -> None:
    path = delivery_versions_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(versions, sort_keys=True, indent=2),
        encoding="utf-8",
    )


def claim_delivery_versions() -> bool:
    active_versions = load_delivery_versions()
    if active_versions is None:
        print("delivery versions are unreadable; skipping delivery", file=sys.stderr)
        return False
    current_versions = current_delivery_versions()
    if active_versions.keys() - current_versions.keys():
        return False
    if any(
        active_versions.get(name, 0) > version
        for name, version in current_versions.items()
    ):
        return False
    if active_versions != current_versions:
        save_delivery_versions(current_versions)
    return True


def enqueue_status_comment_update(pr_number: int) -> None:
    state = load_status_comment_rollout_state()
    pending = state["pending_pr_numbers"]
    if pr_number not in pending:
        pending.append(pr_number)
    save_status_comment_rollout_state(state)


_MISSING = object()


def _string(value: Any, field_name: str, default: str = "") -> str:
    if value is _MISSING:
        return default
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    return value


def _boolean(value: Any, field_name: str, default: bool = False) -> bool:
    if value is _MISSING:
        return default
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean")
    return value


def _integer(value: Any, field_name: str, default: int = 0) -> int:
    if value is _MISSING:
        return default
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer")
    return value


def _optional_integer(value: Any, field_name: str) -> int | None:
    return None if value is None else _integer(value, field_name)


def _optional_string(value: Any, field_name: str) -> str | None:
    return None if value is None else _string(value, field_name)


def _string_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    if value is _MISSING:
        return ()
    if not isinstance(value, list) or any(
        not isinstance(item, str)
        for item in value
    ):
        raise ValueError(f"{field_name} must be an array of strings")
    return tuple(value)


def _decode_reviewer(value: Any) -> ReviewerSummary:
    if not isinstance(value, dict):
        raise ValueError("facts.reviewers entries must be objects")
    return ReviewerSummary(
        login=_string(value.get("login", _MISSING), "facts.reviewers.login"),
        approved=_boolean(
            value.get("approved", _MISSING),
            "facts.reviewers.approved",
        ),
        approved_non_team=_boolean(
            value.get("approved_non_team", _MISSING),
            "facts.reviewers.approved_non_team",
        ),
        pending_review=_boolean(
            value.get("pending_review", _MISSING),
            "facts.reviewers.pending_review",
        ),
        changes_requested=_boolean(
            value.get("changes_requested", _MISSING),
            "facts.reviewers.changes_requested",
        ),
        open_thread=_boolean(
            value.get("open_thread", _MISSING),
            "facts.reviewers.open_thread",
        ),
        top_level_feedback=_boolean(
            value.get("top_level_feedback", _MISSING),
            "facts.reviewers.top_level_feedback",
        ),
    )


def _encode_reviewer(reviewer: ReviewerSummary) -> dict[str, Any]:
    return {
        "login": reviewer.login,
        "approved": reviewer.approved,
        "approved_non_team": reviewer.approved_non_team,
        "pending_review": reviewer.pending_review,
        "changes_requested": reviewer.changes_requested,
        "open_thread": reviewer.open_thread,
        "top_level_feedback": reviewer.top_level_feedback,
    }


def _decode_command_reply(value: Any) -> DashboardCommandReply:
    if not isinstance(value, dict):
        raise ValueError("facts.dashboard_command_replies entries must be objects")
    route_value = value.get("route")
    route = (
        DashboardRoute(_string(route_value, "facts.dashboard_command_replies.route"))
        if route_value is not None
        else None
    )
    return DashboardCommandReply(
        comment_id=_integer(
            value.get("comment_id", _MISSING),
            "facts.dashboard_command_replies.comment_id",
        ),
        kind=_string(
            value.get("kind", _MISSING),
            "facts.dashboard_command_replies.kind",
        ),
        user=_string(
            value.get("user", _MISSING),
            "facts.dashboard_command_replies.user",
        ),
        subcommand=_string(
            value.get("subcommand", _MISSING),
            "facts.dashboard_command_replies.subcommand",
        ),
        head_sha=_string(
            value.get("head_sha", _MISSING),
            "facts.dashboard_command_replies.head_sha",
        ),
        route=route,
        held_gates=_string(
            value.get("held_gates", _MISSING),
            "facts.dashboard_command_replies.held_gates",
        ),
        since=_string(
            value.get("since", _MISSING),
            "facts.dashboard_command_replies.since",
        ),
    )


def _encode_command_reply(reply: DashboardCommandReply) -> dict[str, Any]:
    if reply.kind in ("routed", "cleared_by_feedback"):
        stored: dict[str, Any] = {
            "comment_id": reply.comment_id,
            "kind": reply.kind,
            "head_sha": reply.head_sha,
            "user": reply.user,
        }
        if reply.since:
            stored["since"] = reply.since
        if reply.kind == "routed":
            # DashboardCommandReply refuses a routed reply without a route.
            stored["route"] = reply.route.value
            stored["held_gates"] = reply.held_gates
        return stored
    return {
        "comment_id": reply.comment_id,
        "kind": reply.kind,
        "user": reply.user,
        "subcommand": reply.subcommand,
    }


def decode_dashboard_facts(value: Any) -> DashboardFacts:
    if not isinstance(value, dict):
        raise ValueError("dashboard result facts must be an object")
    raw_replies = value.get("dashboard_command_replies", _MISSING)
    if raw_replies is _MISSING:
        raw_replies = []
    if not isinstance(raw_replies, list):
        raise ValueError("facts.dashboard_command_replies must be an array")
    raw_reviewers = value.get("reviewers", _MISSING)
    if raw_reviewers is _MISSING:
        raw_reviewers = []
    if not isinstance(raw_reviewers, list):
        raise ValueError("facts.reviewers must be an array")
    return DashboardFacts(
        author=_string(value.get("author", _MISSING), "facts.author"),
        assignees=_string_tuple(
            value.get("assignees", _MISSING),
            "facts.assignees",
        ),
        head_sha=_string(value.get("head_sha", _MISSING), "facts.head_sha"),
        routing_input_fingerprint=_string(
            value.get("routing_input_fingerprint", _MISSING),
            "facts.routing_input_fingerprint",
        ),
        copilot_request_fingerprint=_string(
            value.get("copilot_request_fingerprint", _MISSING),
            "facts.copilot_request_fingerprint",
        ),
        dashboard_override_command_id=_integer(
            value.get("dashboard_override_command_id", _MISSING),
            "facts.dashboard_override_command_id",
        ),
        dashboard_override_command_user=_string(
            value.get("dashboard_override_command_user", _MISSING),
            "facts.dashboard_override_command_user",
        ),
        dashboard_override_bound_command_id=_integer(
            value.get("dashboard_override_bound_command_id", _MISSING),
            "facts.dashboard_override_bound_command_id",
        ),
        dashboard_override_head_sha=_string(
            value.get("dashboard_override_head_sha", _MISSING),
            "facts.dashboard_override_head_sha",
        ),
        dashboard_override_since=_string(
            value.get("dashboard_override_since", _MISSING),
            "facts.dashboard_override_since",
        ),
        dashboard_override_cleared_by_feedback=_boolean(
            value.get("dashboard_override_cleared_by_feedback", _MISSING),
            "facts.dashboard_override_cleared_by_feedback",
        ),
        dashboard_command_replies=tuple(
            _decode_command_reply(reply)
            for reply in raw_replies
        ),
        copilot_review_requested=_boolean(
            value.get("copilot_review_requested", _MISSING),
            "facts.copilot_review_requested",
        ),
        copilot_review_exists=_boolean(
            value.get("copilot_review_exists", _MISSING),
            "facts.copilot_review_exists",
        ),
        copilot_review_stale=_boolean(
            value.get("copilot_review_stale", _MISSING),
            "facts.copilot_review_stale",
        ),
        copilot_review_needed=_boolean(
            value.get("copilot_review_needed", _MISSING),
            "facts.copilot_review_needed",
        ),
        is_maintenance_bot=_boolean(
            value.get("is_maintenance_bot", _MISSING),
            "facts.is_maintenance_bot",
        ),
        is_draft=_boolean(
            value.get("is_draft", _MISSING),
            "facts.is_draft",
        ),
        approval_count=_integer(
            value.get("approval_count", _MISSING),
            "facts.approval_count",
        ),
        conflicts=_string(
            value.get("conflicts", _MISSING),
            "facts.conflicts",
            "unknown",
        ),
        created_at=_string(
            value.get("created_at", _MISSING),
            "facts.created_at",
        ),
        last_activity_at=_string(
            value.get("last_activity_at", _MISSING),
            "facts.last_activity_at",
        ),
        last_author_activity_at=_string(
            value.get("last_author_activity_at", _MISSING),
            "facts.last_author_activity_at",
        ),
        last_approver_activity_at=_string(
            value.get("last_approver_activity_at", _MISSING),
            "facts.last_approver_activity_at",
        ),
        ci_failing_count=_optional_integer(
            value.get("ci_failing_count"),
            "facts.ci_failing_count",
        ),
        ci_failing_since=_optional_string(
            value.get("ci_failing_since"),
            "facts.ci_failing_since",
        ),
        ci_pending_count=_optional_integer(
            value.get("ci_pending_count"),
            "facts.ci_pending_count",
        ),
        non_blocking_check_failures=_string_tuple(
            value.get("non_blocking_check_failures", _MISSING),
            "facts.non_blocking_check_failures",
        ),
        copilot_first_review_missing_since=_optional_string(
            value.get("copilot_first_review_missing_since"),
            "facts.copilot_first_review_missing_since",
        ),
        copilot_review_outstanding=_boolean(
            value.get("copilot_review_outstanding", _MISSING),
            "facts.copilot_review_outstanding",
        ),
        copilot_review_unreported=_boolean(
            value.get("copilot_review_unreported", _MISSING),
            "facts.copilot_review_unreported",
        ),
        copilot_review_request_needed=_boolean(
            value.get("copilot_review_request_needed", _MISSING),
            "facts.copilot_review_request_needed",
        ),
        required_checks_settled=_boolean(
            value.get("required_checks_settled", _MISSING),
            "facts.required_checks_settled",
        ),
        route_held_since=_optional_string(
            value.get("route_held_since"),
            "facts.route_held_since",
        ),
        route_hold_expired=_boolean(
            value.get("route_hold_expired", _MISSING),
            "facts.route_hold_expired",
        ),
        route_held_for_gates=_boolean(
            value.get("route_held_for_gates", _MISSING),
            "facts.route_held_for_gates",
        ),
        waiting_since=_string(
            value.get("waiting_since", _MISSING),
            "facts.waiting_since",
        ),
        waiting_age_basis=_string(
            value.get("waiting_age_basis", _MISSING),
            "facts.waiting_age_basis",
        ),
        author_nudge_episode_id=_optional_string(
            value.get("author_nudge_episode_id"),
            "facts.author_nudge_episode_id",
        ),
        author_action_review_thread_urls=_string_tuple(
            value.get("author_action_review_thread_urls", _MISSING),
            "facts.author_action_review_thread_urls",
        ),
        author_action_top_level_feedback_urls=_string_tuple(
            value.get("author_action_top_level_feedback_urls", _MISSING),
            "facts.author_action_top_level_feedback_urls",
        ),
        reviewers=tuple(_decode_reviewer(reviewer) for reviewer in raw_reviewers),
    )


def encode_dashboard_facts(facts: DashboardFacts) -> dict[str, Any]:
    stored: dict[str, Any] = {
        "author": facts.author,
        "assignees": list(facts.assignees),
        "head_sha": facts.head_sha,
        "routing_input_fingerprint": facts.routing_input_fingerprint,
        "copilot_request_fingerprint": facts.copilot_request_fingerprint,
        "dashboard_override_command_id": facts.dashboard_override_command_id,
        "dashboard_override_command_user": facts.dashboard_override_command_user,
        "dashboard_override_head_sha": facts.dashboard_override_head_sha,
        "dashboard_command_replies": [
            _encode_command_reply(reply)
            for reply in facts.dashboard_command_replies
        ],
        "copilot_review_requested": facts.copilot_review_requested,
        "copilot_review_exists": facts.copilot_review_exists,
        "copilot_review_stale": facts.copilot_review_stale,
        "copilot_review_needed": facts.copilot_review_needed,
        "is_maintenance_bot": facts.is_maintenance_bot,
        "is_draft": facts.is_draft,
        "approval_count": facts.approval_count,
        "conflicts": facts.conflicts,
        "created_at": facts.created_at,
        "last_activity_at": facts.last_activity_at,
        "last_author_activity_at": facts.last_author_activity_at,
        "last_approver_activity_at": facts.last_approver_activity_at,
        "copilot_review_outstanding": facts.copilot_review_outstanding,
        "copilot_review_unreported": facts.copilot_review_unreported,
        "copilot_review_request_needed": facts.copilot_review_request_needed,
        "required_checks_settled": facts.required_checks_settled,
        "route_hold_expired": facts.route_hold_expired,
        "route_held_for_gates": facts.route_held_for_gates,
        "waiting_since": facts.waiting_since,
        "waiting_age_basis": facts.waiting_age_basis,
        "author_action_review_thread_urls": list(
            facts.author_action_review_thread_urls
        ),
        "author_action_top_level_feedback_urls": list(
            facts.author_action_top_level_feedback_urls
        ),
        "reviewers": [
            _encode_reviewer(reviewer)
            for reviewer in facts.reviewers
        ],
    }
    if facts.dashboard_override_bound_command_id:
        stored["dashboard_override_bound_command_id"] = (
            facts.dashboard_override_bound_command_id
        )
    if facts.dashboard_override_since:
        stored["dashboard_override_since"] = facts.dashboard_override_since
    if facts.dashboard_override_cleared_by_feedback:
        stored["dashboard_override_cleared_by_feedback"] = True
    if facts.ci_failing_count is not None:
        stored["ci_failing_count"] = facts.ci_failing_count
    if facts.ci_failing_since is not None:
        stored["ci_failing_since"] = facts.ci_failing_since
    if facts.ci_pending_count is not None:
        stored["ci_pending_count"] = facts.ci_pending_count
    if facts.non_blocking_check_failures:
        stored["non_blocking_check_failures"] = list(
            facts.non_blocking_check_failures
        )
    if facts.copilot_first_review_missing_since is not None:
        stored["copilot_first_review_missing_since"] = (
            facts.copilot_first_review_missing_since
        )
    if facts.route_held_since is not None:
        stored["route_held_since"] = facts.route_held_since
    if facts.author_nudge_episode_id is not None:
        stored["author_nudge_episode_id"] = facts.author_nudge_episode_id
    return stored


def decode_stored_result(
    value: Any,
    *,
    pr_number_hint: int | None = None,
) -> StoredDashboardResult:
    if not isinstance(value, dict):
        raise ValueError("dashboard result must be an object")
    if _boolean(value.get("failed", _MISSING), "dashboard result failed"):
        raise ValueError("failed dashboard results cannot be stored")
    pr_number = _integer(
        value.get("pr_number", _MISSING),
        "dashboard result pr_number",
        pr_number_hint or 0,
    )
    if pr_number_hint is not None and pr_number != pr_number_hint:
        raise ValueError("dashboard result pr_number does not match its state key")
    route = DashboardRoute(
        _string(
            value.get("route", _MISSING),
            "dashboard result route",
            "unknown",
        )
    )
    history = value.get("top_level_history", _MISSING)
    if history is _MISSING:
        history = {}
    if not isinstance(history, dict):
        raise ValueError("dashboard result top_level_history must be an object")
    return StoredDashboardResult(
        pr_number=pr_number,
        pr_url=_string(
            value.get("pr_url", _MISSING),
            "dashboard result pr_url",
        ),
        route=route,
        facts=decode_dashboard_facts(
            value["facts"] if "facts" in value else {}
        ),
        top_level_history=freeze_json_object(history),
    )


def encode_stored_result(result: StoredDashboardResult) -> dict[str, Any]:
    return {
        "pr_number": result.pr_number,
        "pr_url": result.pr_url,
        "failed": False,
        "route": result.route.value,
        "facts": encode_dashboard_facts(result.facts),
        "top_level_history": thaw_json(result.top_level_history),
    }


def decode_dashboard_state(value: Mapping[str, Any]) -> DashboardState:
    raw_prs = value.get("prs")
    if not isinstance(raw_prs, dict):
        raw_prs = {}
    results: list[StoredDashboardResult] = []
    decoded_pr_numbers: set[int] = set()
    for key, raw_result in raw_prs.items():
        try:
            pr_number = int(key)
            if pr_number <= 0:
                raise ValueError("PR number must be positive")
            if key != str(pr_number):
                raise ValueError("PR number key must use canonical decimal form")
            if pr_number in decoded_pr_numbers:
                raise ValueError("duplicate normalized PR number")
            result = decode_stored_result(
                raw_result,
                pr_number_hint=pr_number,
            )
            results.append(result)
            decoded_pr_numbers.add(pr_number)
        except (TypeError, ValueError) as error:
            print(
                f"warning: ignoring malformed dashboard result {key!r}: {error}",
                file=sys.stderr,
            )
    raw_draft_pr_numbers = value.get("draft_pr_numbers")
    draft_pr_numbers = frozenset(
        number
        for number in (
            raw_draft_pr_numbers
            if isinstance(raw_draft_pr_numbers, list)
            else []
        )
        if isinstance(number, int)
        and not isinstance(number, bool)
        and number > 0
        and number not in decoded_pr_numbers
    )
    return DashboardState(
        initial_backfill_complete=(
            value.get(INITIAL_BACKFILL_COMPLETE_KEY) is True
        ),
        results=tuple(results),
        draft_pr_numbers=draft_pr_numbers,
    )


def encode_dashboard_state(state: DashboardState) -> dict[str, Any]:
    return {
        "version": DASHBOARD_STATE_VERSION,
        INITIAL_BACKFILL_COMPLETE_KEY: state.initial_backfill_complete,
        "draft_pr_numbers": sorted(state.draft_pr_numbers),
        "prs": {
            str(result.pr_number): encode_stored_result(result)
            for result in state.results
        },
    }


def load_dashboard_state_cache() -> DashboardState | None:
    state = load_state_file(
        dashboard_state_path(),
        DASHBOARD_STATE_VERSION,
        compatible_versions=(11, 12),
    )
    if state is None:
        return None
    return decode_dashboard_state(state)


def load_accepted_dashboard_state(
    repo: str,
    state_branch_name: str,
    *,
    required: bool = False,
) -> DashboardState | None:
    with state_branch.accepted_state_dir(state_branch_name, required=required) as checkout_dir:
        if checkout_dir is None:
            return None
        set_state_dir(checkout_dir / repo_state_key(repo))
        return load_dashboard_state_cache()


def save_dashboard_state_cache(state: DashboardState) -> None:
    save_state_file(
        dashboard_state_path(),
        encode_dashboard_state(state),
        DASHBOARD_STATE_VERSION,
    )


def load_notification_state_file(path: Path) -> dict[str, Any] | None:
    state = load_state_file(path, NOTIFICATION_STATE_VERSION)
    if state is not None and not isinstance(state.get("prs"), dict):
        state["prs"] = {}
    return state


def _save_notification_state_file(state: dict[str, Any]) -> None:
    save_state_file(notification_state_path(), state, NOTIFICATION_STATE_VERSION)


def load_notifications() -> dict[str, Any] | None:
    state = load_notification_state_file(notification_state_path())
    if state is None:
        return None
    return state["prs"]


def save_notifications(notifications: dict[str, Any]) -> None:
    _save_notification_state_file({"prs": notifications})


def load_author_nudge_state_file(path: Path) -> dict[str, Any]:
    state = load_state_file(
        path,
        AUTHOR_NUDGE_STATE_VERSION,
        compatible_versions=(2,),
    )
    if state is None or not isinstance(state.get("prs"), dict):
        return {}
    return state["prs"]


def union_merge_author_nudges(
    baseline_nudges: dict[str, Any],
    retry_snapshot_nudges: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(baseline_nudges)
    for key, retry_entry in retry_snapshot_nudges.items():
        nudged_at = (retry_entry or {}).get("nudged_at") or ""
        waiting_since = (retry_entry or {}).get("waiting_since") or ""
        baseline_entry = dict(baseline_nudges.get(key) or {})
        baseline_waiting_since = baseline_entry.get("waiting_since") or ""
        if nudged_at and waiting_since and waiting_since == baseline_waiting_since:
            merged[key] = {
                "waiting_since": waiting_since,
                "nudged_at": nudged_at,
            }
            completions = list(baseline_entry.get("completions") or [])
            if completions:
                merged[key]["completions"] = completions
            episode_id = (
                (retry_entry or {}).get("episode_id")
                or baseline_entry.get("episode_id")
                or ""
            )
            if episode_id:
                merged[key]["episode_id"] = episode_id
        elif nudged_at:
            episode_id = (
                (retry_entry or {}).get("episode_id")
                or f"legacy-nudge:{nudged_at}"
            )
            completions = list(baseline_entry.get("completions") or [])
            if not any(
                completion.get("episode_id") == episode_id
                for completion in completions
            ):
                completions.append({
                    "episode_id": episode_id,
                    "completed_at": nudged_at,
                    "kind": "routing_changed",
                })
            baseline_entry["completions"] = completions
            merged[key] = baseline_entry
    return merged


def load_author_nudges(retry_snapshot_path: Path | None = None) -> dict[str, Any]:
    nudges = load_author_nudge_state_file(author_nudge_state_path())
    if retry_snapshot_path and retry_snapshot_path.exists():
        nudges = union_merge_author_nudges(
            nudges,
            load_author_nudge_state_file(retry_snapshot_path),
        )
    return nudges


def save_author_nudges(nudges: dict[str, Any]) -> None:
    save_state_file(
        author_nudge_state_path(),
        {"prs": nudges},
        AUTHOR_NUDGE_STATE_VERSION,
    )


def load_copilot_review_request_state_file(path: Path) -> dict[str, Any]:
    state = load_state_file(
        path,
        COPILOT_REVIEW_REQUEST_STATE_VERSION,
    )
    if state is None or not isinstance(state.get("prs"), dict):
        return {}
    return state["prs"]


def union_merge_copilot_review_requests(
    baseline_requests: dict[str, Any],
    retry_snapshot_requests: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(baseline_requests)
    for key, retry_entry in retry_snapshot_requests.items():
        baseline_entry = merged.get(key) or {}
        if (
            (retry_entry or {}).get("requested_at")
            and retry_entry.get("head_sha") == baseline_entry.get("head_sha")
            and retry_entry.get("observed_at")
            and retry_entry.get("observed_at") == baseline_entry.get("observed_at")
        ):
            merged[key] = retry_entry
    return merged


def load_copilot_review_requests(retry_snapshot_path: Path | None = None) -> dict[str, Any]:
    requests = load_copilot_review_request_state_file(copilot_review_request_state_path())
    if retry_snapshot_path and retry_snapshot_path.exists():
        requests = union_merge_copilot_review_requests(
            requests,
            load_copilot_review_request_state_file(retry_snapshot_path),
        )
    return requests


def save_copilot_review_requests(requests: dict[str, Any]) -> None:
    save_state_file(
        copilot_review_request_state_path(),
        {"prs": requests},
        COPILOT_REVIEW_REQUEST_STATE_VERSION,
    )


def union_merge_notifications(
    baseline_notifications: dict[str, Any], retry_snapshot_notifications: dict[str, Any]
) -> dict[str, Any]:
    """Union-merge `retry_snapshot_notifications` into `baseline_notifications`.

    For each PR, the entry with the newer `last_notified_at` wins.
    Used by the workflow's CAS retry loop: an earlier attempt's
    just-sent notification state is carried into the next attempt so
    the cadence gate sees those pings as already-notified after a
    reset to the remote tip.
    """
    merged_notifications = dict(baseline_notifications)
    for pr_key, retry_entry in retry_snapshot_notifications.items():
        base_entry = merged_notifications.get(pr_key)
        if base_entry is None:
            merged_notifications[pr_key] = retry_entry
            continue
        retry_ts = (retry_entry or {}).get("last_notified_at") or ""
        base_ts = base_entry.get("last_notified_at") or ""
        if retry_ts > base_ts:
            merged_notifications[pr_key] = retry_entry
    return merged_notifications


def stored_result(result: EvaluationSuccess) -> StoredDashboardResult:
    return StoredDashboardResult.from_evaluation(result)


def results_from_dashboard_state(
    state: DashboardState,
    open_pr_numbers: set[int],
) -> tuple[StoredDashboardResult, ...]:
    return state.results_for(open_pr_numbers)


def dashboard_state_from_results(
    results: Iterable[EvaluationSuccess | StoredDashboardResult],
) -> DashboardState:
    stored = tuple(
        result
        if isinstance(result, StoredDashboardResult)
        else stored_result(result)
        for result in results
    )
    return DashboardState(results=stored)


def update_dashboard_state_for_pr(
    state: DashboardState,
    number: int,
    result: EvaluationSuccess | None,
) -> DashboardState:
    return state.with_result(
        number,
        stored_result(result) if result is not None else None,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Read accepted PR dashboard state.")
    parser.add_argument("--repo", help="target repository name, e.g. opentelemetry-java-instrumentation")
    parser.add_argument("--state-branch", required=True, help="git branch used for workflow state")
    args = parser.parse_args()

    repo = normalize_repo(args.repo) if args.repo else detect_repo()
    dashboard_state = load_accepted_dashboard_state(repo, args.state_branch)
    print("true" if initial_backfill_complete(dashboard_state) else "false")
    return 0


if __name__ == "__main__":
    sys.exit(main())
