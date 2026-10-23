#!/usr/bin/env python3
"""Render and publish the accepted PR dashboard state to the dashboard issue."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from github_cli import (
    detect_repo,
    fetch_latest_draft_transitions,
    gh_api,
    gh_graphql,
    list_open_prs,
    load_team_members,
    normalize_repo,
    repo_state_key,
    run_gh,
)
from dashboard_contracts import StoredDashboardResult
from pull_request_author import effective_author, is_copilot_author
from pull_request_source import normalize_commits, normalize_pull_request
from render import AuthorTeams, render_pr_tables
from state import (
    dashboard_markdown_path,
    load_dashboard_state_cache,
    results_from_dashboard_state,
    set_state_dir,
)
import state_branch


DASHBOARD_TITLE = "Pull Request Dashboard"
DASHBOARD_LABEL = "dashboard"
DASHBOARD_LABEL_COLOR = "cfd3d7"
DASHBOARD_LABEL_DESCRIPTION = "Pull request dashboard"
ISSUE_BODY_MAX_CHARS = 65536
LARGE_REPO_MAX_ROWS_PER_SECTION = 100


def _parse_string_list_json(raw: str, source: str) -> list[str]:
    try:
        values = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"{source} must be valid JSON: {e.msg} at char {e.pos}"
        ) from e
    if not isinstance(values, list) or any(
        not isinstance(value, str) or not value.strip()
        for value in values
    ):
        raise RuntimeError(
            f"{source} must be a JSON array of non-blank strings"
        )
    return values


def parse_labels_to_display_json(raw: str) -> list[str]:
    return _parse_string_list_json(raw, "--labels-to-display-json")


@dataclass(frozen=True)
class AuthorTeamSlugs:
    maintainers: tuple[str, ...] = ()
    approvers: tuple[str, ...] = ()
    triagers: tuple[str, ...] = ()

    # The queue processor passes teams through the environment rather than
    # flags so that stable-channel scripts without these options still run.
    @classmethod
    def from_environment(cls, environ: Mapping[str, str]) -> AuthorTeamSlugs:
        def slugs(name: str) -> tuple[str, ...]:
            return tuple(_parse_string_list_json(environ.get(name) or "[]", name))

        return cls(
            maintainers=slugs("MAINTAINER_TEAMS_JSON"),
            approvers=slugs("APPROVER_TEAMS_JSON"),
            triagers=slugs("TRIAGER_TEAMS_JSON"),
        )


def load_author_teams(repo: str, slugs: AuthorTeamSlugs) -> AuthorTeams:
    org = repo.split("/", 1)[0]

    def members(team_slugs: tuple[str, ...]) -> frozenset[str]:
        if not team_slugs:
            return frozenset()
        return frozenset(load_team_members(org, list(team_slugs)))

    return AuthorTeams(
        maintainers=members(slugs.maintainers),
        approvers=members(slugs.approvers),
        triagers=members(slugs.triagers),
    )


# GraphQL is used instead of the REST `/repos/{repo}/issues` list endpoint
# because that endpoint has been observed to sometimes omit the existing
# open, `dashboard`-labeled issue from its results (across every sort, page
# size, and `since` variant), even though `GET /repos/{repo}/issues/<n>` and
# the GraphQL `repository.issues` connection both still return it. When that
# happens via REST, this script would create a duplicate dashboard issue
# instead of updating the existing one.
_FIND_DASHBOARD_ISSUE_QUERY = """
query ($owner: String!, $name: String!, $label: String!, $after: String) {
  repository(owner: $owner, name: $name) {
    issues(
      first: 100
      after: $after
      states: OPEN
      filterBy: { labels: [$label] }
      orderBy: { field: CREATED_AT, direction: ASC }
    ) {
      pageInfo { hasNextPage endCursor }
      nodes { number title }
    }
  }
}
"""


def find_dashboard_issue(repo: str) -> int | None:
    owner, _, name = repo.partition("/")
    after: str | None = None
    while True:
        data = gh_graphql(
            _FIND_DASHBOARD_ISSUE_QUERY,
            {"owner": owner, "name": name, "label": DASHBOARD_LABEL, "after": after},
        )
        connection = data["data"]["repository"]["issues"]
        for node in connection["nodes"]:
            if node["title"] == DASHBOARD_TITLE:
                return node["number"]
        page_info = connection["pageInfo"]
        if not page_info["hasNextPage"]:
            return None
        after = page_info["endCursor"] or ""


def ensure_dashboard_label(repo: str) -> None:
    label = run_gh([
        "gh",
        "label",
        "list",
        "--repo",
        repo,
        "--limit",
        "1000",
        "--json",
        "name",
        "--jq",
        f'.[] | select(.name == "{DASHBOARD_LABEL}") | .name',
    ]).strip()
    if label:
        return

    print("creating dashboard label", file=sys.stderr)
    run_gh([
        "gh",
        "label",
        "create",
        DASHBOARD_LABEL,
        "--repo",
        repo,
        "--color",
        DASHBOARD_LABEL_COLOR,
        "--description",
        DASHBOARD_LABEL_DESCRIPTION,
    ])


def publish_dashboard(repo: str, dashboard_body: Path) -> None:
    if not dashboard_body.exists():
        raise RuntimeError(f"dashboard markdown not found: {dashboard_body}")

    number = find_dashboard_issue(repo)
    if number is not None:
        print(f"publishing dashboard issue #{number}", file=sys.stderr)
        run_gh([
            "gh",
            "issue",
            "edit",
            str(number),
            "--repo",
            repo,
            "--body-file",
            str(dashboard_body),
        ])
        return

    print("creating dashboard issue", file=sys.stderr)
    ensure_dashboard_label(repo)
    run_gh([
        "gh",
        "issue",
        "create",
        "--repo",
        repo,
        "--title",
        DASHBOARD_TITLE,
        "--label",
        DASHBOARD_LABEL,
        "--body-file",
        str(dashboard_body),
    ])


def publishable_prs(
    prs: list[dict[str, Any]],
    results: Iterable[StoredDashboardResult],
) -> list[dict[str, Any]]:
    result_numbers = {result.pr_number for result in results}
    return [
        pr
        for pr in prs
        if pr.get("isDraft") or pr.get("number") in result_numbers
    ]


def add_draft_authors(repo: str, prs: list[dict[str, Any]]) -> None:
    for pr in prs:
        if not pr.get("isDraft"):
            continue
        metadata = normalize_pull_request(pr)
        author = effective_author(metadata)
        if is_copilot_author(author):
            commits = normalize_commits(
                gh_api(f"/repos/{repo}/pulls/{metadata.number}/commits?per_page=1")
            )
            author = effective_author(metadata, commits)
        pr["effectiveAuthor"] = author


def add_draft_since(repo: str, prs: list[dict[str, Any]]) -> None:
    drafts = [pr for pr in prs if pr.get("isDraft")]
    if not drafts:
        return
    transitions = fetch_latest_draft_transitions(
        repo,
        [pr["number"] for pr in drafts],
    )
    for pr in drafts:
        pr["draftSince"] = transitions.get(pr["number"]) or pr.get("createdAt")


def render_dashboard_markdown(
    repo: str,
    large_repo: bool,
    labels_to_display: list[str] | None = None,
    author_team_slugs: AuthorTeamSlugs = AuthorTeamSlugs(),
) -> Path:
    dashboard_state = load_dashboard_state_cache()
    if dashboard_state is None:
        raise RuntimeError("dashboard state not found")

    author_teams = load_author_teams(repo, author_team_slugs)
    prs = list_open_prs(repo)
    if not large_repo:
        add_draft_authors(repo, prs)
        add_draft_since(repo, prs)
    open_non_draft_pr_numbers = {p["number"] for p in prs if not p.get("isDraft")}
    results = results_from_dashboard_state(dashboard_state, open_non_draft_pr_numbers)
    md = render_pr_tables(
        publishable_prs(prs, results),
        results,
        max_rows_per_section=LARGE_REPO_MAX_ROWS_PER_SECTION if large_repo else None,
        skip_drafts=large_repo,
        labels_to_display=labels_to_display,
        author_teams=author_teams,
    )
    output_path = dashboard_markdown_path()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(md, encoding="utf-8")
    print(
        f"rendered dashboard markdown to {output_path.resolve()} "
        f"({len(md)} chars, GitHub issue-body limit is {ISSUE_BODY_MAX_CHARS})",
        file=sys.stderr,
    )
    if len(md) > ISSUE_BODY_MAX_CHARS:
        raise RuntimeError(
            f"rendered dashboard markdown is {len(md)} chars, which exceeds "
            f"GitHub's {ISSUE_BODY_MAX_CHARS}-character issue-body limit"
        )
    return output_path


def publish_accepted_dashboard(
    repo: str,
    state_branch_name: str,
    large_repo: bool,
    labels_to_display: list[str] | None = None,
    author_team_slugs: AuthorTeamSlugs = AuthorTeamSlugs(),
) -> None:
    with state_branch.accepted_state_dir(state_branch_name, required=True) as state_dir:
        if state_dir is None:
            raise RuntimeError(f"required state branch not found: {state_branch_name}")
        set_state_dir(state_dir / repo_state_key(repo))
        publish_dashboard(
            repo,
            render_dashboard_markdown(
                repo,
                large_repo,
                labels_to_display,
                author_team_slugs,
            ),
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", help="target repository name, e.g. opentelemetry-java-instrumentation")
    parser.add_argument(
        "--state-branch",
        required=True,
        help="git branch used for workflow state",
    )
    parser.add_argument(
        "--large-repo",
        action="store_true",
        help="apply large-repo rendering presets",
    )
    parser.add_argument(
        "--labels-to-display-json",
        default="[]",
        help="JSON array of label name patterns to display",
    )
    args = parser.parse_args()

    repo = normalize_repo(args.repo) if args.repo else detect_repo()
    publish_accepted_dashboard(
        repo,
        args.state_branch,
        args.large_repo,
        parse_labels_to_display_json(args.labels_to_display_json),
        AuthorTeamSlugs.from_environment(os.environ),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
