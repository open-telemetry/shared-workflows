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
AUTHOR_FOLLOW_UP_STATE_FILE = "author-follow-up-state.json"
# State files are disposable workflow caches, not durable user data. Bump only
# the version for the state shape whose meaning changed.
DASHBOARD_STATE_VERSION = 4
BACKFILL_STATE_VERSION = 3
NOTIFICATION_STATE_VERSION = 3
AUTHOR_FOLLOW_UP_STATE_VERSION = 2
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


def author_follow_up_state_path() -> Path:
    return state_dir() / AUTHOR_FOLLOW_UP_STATE_FILE


def backfill_state_path() -> Path:
    return state_dir() / BACKFILL_STATE_FILE


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


def load_author_follow_up_state_file(path: Path) -> dict[str, Any] | None:
    state = load_state_file(path, AUTHOR_FOLLOW_UP_STATE_VERSION)
    if state is not None and not isinstance(state.get("prs"), dict):
        state["prs"] = {}
    return state


def load_author_follow_ups() -> dict[str, Any]:
    state = load_author_follow_up_state_file(author_follow_up_state_path())
    if state is None:
        return {}
    return state["prs"]


def save_author_follow_ups(follow_ups: dict[str, Any]) -> None:
    save_state_file(
        author_follow_up_state_path(),
        {"prs": follow_ups},
        AUTHOR_FOLLOW_UP_STATE_VERSION,
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
