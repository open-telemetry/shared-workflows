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
                                contexts(first: 1) {
                                    totalCount
                                }
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
    contexts = rollup.get("contexts") or {}
    # The context count catches checks appearing or disappearing while the
    # aggregate state holds steady. Per-context states are not compared: the
    # connection caps `first` at 100, most pull requests on the busiest
    # repository carry more, and paging them every poll costs more than the
    # hourly backfill that already recomputes each context.
    return f"{node.get('headRefOid') or ''}:{rollup.get('state') or 'NONE'}:{contexts.get('totalCount') or 0}"


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
    previous: dict[int, str],
    current: dict[int, str],
    limit: int,
) -> list[int]:
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
) -> list[tuple[str, int, str]]:
    changed: list[tuple[str, int, str]] = []
    for repository in repositories:
        current = fetch_repository_signatures(owner, repository)
        previous = state.get(repository)
        if previous is None:
            # Without a baseline every open pull request looks changed, which on
            # a first poll would refresh the whole repository.
            state[repository] = current
            continue
        # Only closed pull requests are retired here. A changed signature is
        # recorded once its refresh is dispatched, so one that was capped or
        # failed to dispatch still looks changed on the next poll.
        state[repository] = {
            number: signature for number, signature in previous.items() if number in current
        }
        changed.extend(
            (repository, number, current[number])
            for number in changed_pull_requests(previous, current, limit)
        )
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

    if args.dry_run:
        return report_changes(repositories, args)

    dispatched = 0

    def update_state(state_dir: Path) -> int:
        nonlocal dispatched
        state_path = state_dir / STATE_FILENAME
        state = load_poll_state(state_path)
        changed = poll_repositories(
            repositories,
            args.owner,
            state,
            args.max_dispatches_per_repository,
        )
        # Dispatching before the push means a rejected push replays these
        # refreshes. The workflow's concurrency group runs one poll at a time
        # and nothing else writes this branch, so a conflict is not expected,
        # and a repeated refresh is harmless where a dropped one is not.
        dispatched = 0
        for repository, pr_number, signature in changed:
            print(f"{repository}#{pr_number} checks changed", file=sys.stderr)
            try:
                dispatch_refresh(repository, pr_number, dispatch_token)
            except RuntimeError as e:
                # Leaving the old signature in place retries this refresh.
                print(f"{repository}#{pr_number} dispatch failed: {e}", file=sys.stderr)
                continue
            state.setdefault(repository, {})[pr_number] = signature
            dispatched += 1
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

    print(f"dispatched {dispatched} refresh(es)", file=sys.stderr)
    return status


def report_changes(repositories: list[str], args: argparse.Namespace) -> int:
    with state_branch.accepted_state_dir(args.state_branch, required=False) as state_dir:
        state = load_poll_state(state_dir / STATE_FILENAME) if state_dir else {}
    changed = poll_repositories(repositories, args.owner, state, args.max_dispatches_per_repository)
    for repository, pr_number, _ in changed:
        print(f"{repository}#{pr_number} checks changed", file=sys.stderr)
    print(f"would dispatch {len(changed)} refresh(es)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
