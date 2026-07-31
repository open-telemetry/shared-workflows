"""Dispatch dashboard refreshes for pull requests whose checks changed.

Check webhooks are not subscribed. They arrive once per check suite, about ten
per push, and on a fork head they carry no pull request number, so resolving one
meant a second workflow job per event. The dashboard's own runs also reached
that fallback and dispatched each other. One rollup query per repository answers
the same question directly, keyed by pull request number.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import state_branch
from github_cli import gh_graphql, run_gh

STATE_VERSION = 1
STATE_FILENAME = "check-rollup-poll.json"
WORKFLOW_REPOSITORY = "open-telemetry/shared-workflows"
WORKFLOW_ID = "pull-request-dashboard.yml"
PAGE_SIZE = 100

OPEN_PR_ROLLUP_QUERY = """
query($owner: String!, $name: String!, $after: String) {
    repository(owner: $owner, name: $name) {
        pullRequests(states: OPEN, first: %d, after: $after) {
            pageInfo {
                hasNextPage
                endCursor
            }
            nodes {
                number
                headRefOid
                commits(last: 1) {
                    nodes {
                        commit {
                            statusCheckRollup {
                                state
                            }
                        }
                    }
                }
            }
        }
    }
}
""" % PAGE_SIZE


def rollup_signature(node: dict[str, Any]) -> str:
    commit_nodes = ((node.get("commits") or {}).get("nodes")) or []
    commit = (commit_nodes[0] if commit_nodes else {}).get("commit") or {}
    # A pull request with no checks at all reports no rollup.
    rollup = commit.get("statusCheckRollup") or {}
    return f"{node.get('headRefOid') or ''}:{rollup.get('state') or 'NONE'}"


def fetch_repository_signatures(owner: str, name: str) -> dict[int, str]:
    signatures: dict[int, str] = {}
    after: str | None = None
    while True:
        data = gh_graphql(OPEN_PR_ROLLUP_QUERY, {"owner": owner, "name": name, "after": after})
        repository = (data.get("data") or {}).get("repository") or {}
        pull_requests = repository.get("pullRequests") or {}
        for node in pull_requests.get("nodes") or []:
            number = node.get("number")
            if isinstance(number, int):
                signatures[number] = rollup_signature(node)
        page_info = pull_requests.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            return signatures
        after = page_info.get("endCursor") or None
        if after is None:
            return signatures


def changed_pull_requests(
    previous: dict[int, str] | None,
    current: dict[int, str],
    limit: int,
) -> list[int]:
    # Without a baseline every open pull request looks changed, which on a first
    # poll would dispatch a refresh for the whole repository.
    if previous is None:
        return []
    changed = sorted(
        (number for number, signature in current.items() if previous.get(number) != signature),
        reverse=True,
    )
    return changed[:limit]


def load_poll_state(path: Path) -> dict[str, dict[int, str]]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        print(f"{path} is not valid JSON; rebuilding the baseline", file=sys.stderr)
        return {}
    if raw.get("version") != STATE_VERSION:
        return {}
    return {
        repo: {int(number): signature for number, signature in (signatures or {}).items()}
        for repo, signatures in (raw.get("repositories") or {}).items()
    }


def save_poll_state(path: Path, state: dict[str, dict[int, str]]) -> None:
    payload = {
        "version": STATE_VERSION,
        "repositories": {
            repo: {str(number): signature for number, signature in sorted(signatures.items())}
            for repo, signatures in sorted(state.items())
        },
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def dispatch_refresh(repository: str, pr_number: int, token: str | None) -> None:
    run_gh(
        [
            "gh", "workflow", "run", WORKFLOW_ID,
            "--repo", WORKFLOW_REPOSITORY,
            "-f", f"repository={repository}",
            "-f", f"pr_number={pr_number}",
            "-f", "trigger_event=poll",
        ],
        token=token,
    )


def poll_repositories(
    repositories: list[str],
    owner: str,
    state: dict[str, dict[int, str]],
    limit: int,
) -> list[tuple[str, int]]:
    changed: list[tuple[str, int]] = []
    for repository in repositories:
        current = fetch_repository_signatures(owner, repository)
        for pr_number in changed_pull_requests(state.get(repository), current, limit):
            changed.append((repository, pr_number))
        state[repository] = current
    return changed


def configured_repositories(config: Path) -> list[str]:
    entries = json.loads(config.read_text(encoding="utf-8"))
    return [entry["name"] for entry in entries if entry.get("name")]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="repositories.json")
    parser.add_argument("--state-branch", required=True, help="git branch used for poll state")
    parser.add_argument("--owner", default="open-telemetry")
    parser.add_argument(
        "--max-dispatches-per-repository",
        type=int,
        default=25,
        help="bounds a runaway refresh burst when many pull requests change at once",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    repositories = configured_repositories(args.config)
    # Reads use the dashboard app token in GH_TOKEN; dispatching needs the app
    # installed on this repository, and a GITHUB_TOKEN dispatch would not start
    # a workflow run at all.
    dispatch_token = os.environ.get("DISPATCH_TOKEN") or None
    changed: list[tuple[str, int]] = []

    def update_state(state_dir: Path) -> int:
        nonlocal changed
        state_path = state_dir / STATE_FILENAME
        state = load_poll_state(state_path)
        # A push conflict replays this, so the list is rebuilt rather than
        # appended to.
        changed = poll_repositories(
            repositories,
            args.owner,
            state,
            args.max_dispatches_per_repository,
        )
        save_poll_state(state_path, state)
        return 0

    with state_branch.temporary_state_dir() as state_dir:
        status = state_branch.push_state_changes(
            state_dir,
            "Update check rollup poll state",
            lambda: update_state(state_dir),
            state_branch=args.state_branch,
            add_paths=[STATE_FILENAME],
        )

    # Dispatching only after the new signatures are stored keeps a failed push
    # from replaying the same refreshes on the next poll.
    if status != 0:
        return status
    for repository, pr_number in changed:
        print(f"{repository}#{pr_number} checks changed", file=sys.stderr)
        if not args.dry_run:
            dispatch_refresh(repository, pr_number, dispatch_token)
    print(f"dispatched {len(changed)} refresh(es)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
