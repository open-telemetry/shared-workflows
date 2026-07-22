from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from github_cli import detect_repo, normalize_repo, repo_state_key
import state_branch


DASHBOARD_MARKDOWN_FILE = "pull-request-dashboard.md"
BACKFILL_STATE_FILE = "backfill-state.json"
AUTHOR_NUDGE_STATE_FILE = "author-nudge-state.json"
COPILOT_REVIEW_REQUEST_STATE_FILE = "copilot-review-request-state.json"
STATUS_COMMENT_ROLLOUT_STATE_FILE = "status-comment-rollout-state.json"
DELIVERY_REVISION_STATE_FILE = "delivery-revision-state.json"

# Each state version describes one JSON file's schema, not rollout order. Bump
# only the version whose stored shape or meaning changed. Schema mismatches for
# the ordinary state files below regenerate that disposable workflow cache.
# dashboard-state.json: accepted PR routing results and backfill readiness.
DASHBOARD_STATE_VERSION = 5
# backfill-state.json: round-robin cursor used by full dashboard refreshes.
BACKFILL_STATE_VERSION = 3
# notification-state.json: pending and delivered Slack notification records.
NOTIFICATION_STATE_VERSION = 3
# author-nudge-state.json: waiting episodes and delivered author reminders.
AUTHOR_NUDGE_STATE_VERSION = 2
# copilot-review-request-state.json: pending and delivered review requests.
COPILOT_REVIEW_REQUEST_STATE_VERSION = 3
# status-comment-rollout-state.json: target/completed renderer revisions and queue.
STATUS_COMMENT_ROLLOUT_STATE_VERSION = 1
# delivery-revision-state.json: active delivery revision. This safety state is
# not regenerated on a mismatch. If its schema changes, add an explicit
# migration from older versions; incompatible workers fail closed.
DELIVERY_REVISION_STATE_VERSION = 1
# Bump alongside any state version or delivery behavior change that makes older
# queued workers unsafe. Higher revisions activate, equal revisions resume, and
# lower revisions skip all delivery side effects.
DELIVERY_REVISION = 1
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


def delivery_revision_state_path() -> Path:
    return state_dir() / DELIVERY_REVISION_STATE_FILE


def dashboard_markdown_path() -> Path:
    return state_dir() / DASHBOARD_MARKDOWN_FILE


def empty_state() -> dict[str, Any]:
    return {
        "version": DASHBOARD_STATE_VERSION,
        INITIAL_BACKFILL_COMPLETE_KEY: False,
        "prs": {},
    }


def initial_backfill_complete(state: dict[str, Any] | None) -> bool:
    return bool(state and state.get(INITIAL_BACKFILL_COMPLETE_KEY) is True)


def empty_backfill_state() -> dict[str, Any]:
    return {"version": BACKFILL_STATE_VERSION, "cursor": {}}


def empty_status_comment_rollout_state() -> dict[str, Any]:
    return {
        "version": STATUS_COMMENT_ROLLOUT_STATE_VERSION,
        "target_revision": 0,
        "completed_revision": 0,
        "pending_pr_numbers": [],
    }


def empty_delivery_revision_state() -> dict[str, Any]:
    return {
        "version": DELIVERY_REVISION_STATE_VERSION,
        "active_revision": 0,
    }


def load_state_file(
    path: Path,
    current_version: int,
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
    if data.get("version") != current_version:
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
    )
    if state is None:
        return empty_status_comment_rollout_state()
    pending = state.get("pending_pr_numbers")
    try:
        target_revision = max(int(state.get("target_revision") or 0), 0)
        completed_revision = max(int(state.get("completed_revision") or 0), 0)
    except (TypeError, ValueError):
        return empty_status_comment_rollout_state()
    return {
        "version": STATUS_COMMENT_ROLLOUT_STATE_VERSION,
        "target_revision": target_revision,
        "completed_revision": completed_revision,
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
            "pending_pr_numbers": sorted(set(state.get("pending_pr_numbers") or [])),
        },
        STATUS_COMMENT_ROLLOUT_STATE_VERSION,
    )


def load_delivery_revision_state() -> dict[str, Any] | None:
    path = delivery_revision_state_path()
    if not path.exists():
        return empty_delivery_revision_state()
    state = load_state_file(path, DELIVERY_REVISION_STATE_VERSION)
    if state is None:
        return None
    try:
        active_revision = max(int(state.get("active_revision") or 0), 0)
    except (TypeError, ValueError):
        return None
    return {
        "version": DELIVERY_REVISION_STATE_VERSION,
        "active_revision": active_revision,
    }


def save_delivery_revision_state(state: dict[str, Any]) -> None:
    save_state_file(
        delivery_revision_state_path(),
        {"active_revision": int(state.get("active_revision") or 0)},
        DELIVERY_REVISION_STATE_VERSION,
    )


def claim_delivery_revision() -> bool:
    state = load_delivery_revision_state()
    if state is None:
        print("delivery revision state is unreadable; skipping delivery", file=sys.stderr)
        return False
    active_revision = state["active_revision"]
    if active_revision > DELIVERY_REVISION:
        return False
    if active_revision < DELIVERY_REVISION:
        save_delivery_revision_state({"active_revision": DELIVERY_REVISION})
    return True


def enqueue_status_comment_update(pr_number: int) -> None:
    state = load_status_comment_rollout_state()
    pending = set(state["pending_pr_numbers"])
    pending.add(pr_number)
    state["pending_pr_numbers"] = sorted(pending)
    save_status_comment_rollout_state(state)


def load_dashboard_state_cache() -> dict[str, Any] | None:
    state = load_state_file(dashboard_state_path(), DASHBOARD_STATE_VERSION)
    if state is None:
        return None
    prs = state.get("prs")
    return {
        "version": DASHBOARD_STATE_VERSION,
        INITIAL_BACKFILL_COMPLETE_KEY: initial_backfill_complete(state),
        "prs": prs if isinstance(prs, dict) else {},
    }


def load_accepted_dashboard_state(
    repo: str,
    state_branch_name: str,
    *,
    required: bool = False,
) -> dict[str, Any] | None:
    with state_branch.accepted_state_dir(state_branch_name, required=required) as checkout_dir:
        if checkout_dir is None:
            return None
        set_state_dir(checkout_dir / repo_state_key(repo))
        return load_dashboard_state_cache()


def save_dashboard_state_cache(state: dict[str, Any]) -> None:
    prs = state.get("prs")
    stored = {
        INITIAL_BACKFILL_COMPLETE_KEY: initial_backfill_complete(state),
        "prs": prs if isinstance(prs, dict) else {},
    }
    save_state_file(dashboard_state_path(), stored, DASHBOARD_STATE_VERSION)


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
    state = load_state_file(path, AUTHOR_NUDGE_STATE_VERSION)
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
        baseline_waiting_since = (baseline_nudges.get(key) or {}).get("waiting_since") or ""
        if nudged_at and waiting_since and waiting_since == baseline_waiting_since:
            merged[key] = {
                "waiting_since": waiting_since,
                "nudged_at": nudged_at,
            }
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


def stored_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "pr_number": result.get("pr_number"),
        "pr_url": result.get("pr_url") or "",
        "failed": bool(result.get("failed")),
        "route": result.get("route") or "unknown",
        "facts": result.get("facts") or {},
        "top_level_history": result.get("top_level_history") or {},
    }


def results_from_dashboard_state(state: dict[str, Any], open_pr_numbers: set[int]) -> dict[int, dict[str, Any]]:
    results: dict[int, dict[str, Any]] = {}
    for key, value in (state.get("prs") or {}).items():
        if not isinstance(value, dict):
            continue
        try:
            number = int(key)
        except ValueError:
            continue
        if number in open_pr_numbers:
            results[number] = value
    return results


def dashboard_state_from_results(results: dict[int, dict[str, Any]]) -> dict[str, Any]:
    return {
        "version": DASHBOARD_STATE_VERSION,
        INITIAL_BACKFILL_COMPLETE_KEY: False,
        "prs": {str(number): stored_result(result) for number, result in sorted(results.items())},
    }


def update_dashboard_state_for_pr(
    state: dict[str, Any],
    number: int,
    result: dict[str, Any] | None,
) -> dict[str, Any]:
    prs = dict(state.get("prs") or {})
    key = str(number)
    if result is None:
        prs.pop(key, None)
    else:
        prs[key] = stored_result(result)
    return {
        "version": DASHBOARD_STATE_VERSION,
        INITIAL_BACKFILL_COMPLETE_KEY: initial_backfill_complete(state),
        "prs": prs,
    }


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
