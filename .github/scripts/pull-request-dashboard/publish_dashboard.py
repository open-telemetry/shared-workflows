#!/usr/bin/env python3
"""Render and publish the accepted PR dashboard state to the dashboard issue."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from github_cli import detect_repo, gh_graphql, list_open_prs, normalize_repo, repo_state_key, run_gh
from render import render_pr_tables
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


def parse_labels_to_display_json(raw: str) -> list[str]:
    try:
        labels_to_display = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"--labels-to-display-json must be valid JSON: {e.msg} at char {e.pos}"
        ) from e
    if not isinstance(labels_to_display, list) or any(
        not isinstance(pattern, str) or not pattern.strip()
        for pattern in labels_to_display
    ):
        raise RuntimeError(
            "--labels-to-display-json must be a JSON array of non-blank strings"
        )
    return labels_to_display


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
    results: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        pr
        for pr in prs
        if pr.get("isDraft") or pr.get("number") in results
    ]


def render_dashboard_markdown(
    repo: str,
    large_repo: bool,
    labels_to_display: list[str] | None = None,
) -> Path:
    dashboard_state = load_dashboard_state_cache()
    if dashboard_state is None:
        raise RuntimeError("dashboard state not found")

    prs = list_open_prs(repo)
    open_non_draft_pr_numbers = {p["number"] for p in prs if not p.get("isDraft")}
    results = results_from_dashboard_state(dashboard_state, open_non_draft_pr_numbers)
    md = render_pr_tables(
        publishable_prs(prs, results),
        results,
        max_rows_per_section=LARGE_REPO_MAX_ROWS_PER_SECTION if large_repo else None,
        skip_drafts=large_repo,
        labels_to_display=labels_to_display,
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
) -> None:
    with state_branch.accepted_state_dir(state_branch_name, required=True) as state_dir:
        if state_dir is None:
            raise RuntimeError(f"required state branch not found: {state_branch_name}")
        set_state_dir(state_dir / repo_state_key(repo))
        publish_dashboard(
            repo,
            render_dashboard_markdown(repo, large_repo, labels_to_display),
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
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
