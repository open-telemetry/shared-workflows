"""Resolve a check or status event's head commit to an open pull request."""

from __future__ import annotations

import argparse
from typing import Any

from github_cli import gh_api, gh_graphql

HEAD_PULL_REQUEST_SEARCH = """
query($searchQuery: String!, $after: String) {
    search(query: $searchQuery, type: ISSUE, first: 100, after: $after) {
        nodes {
            ... on PullRequest {
                number
                headRefOid
                state
            }
        }
        pageInfo {
            hasNextPage
            endCursor
        }
    }
}
"""


def resolve_head_pull_request(
    repo: str, head_sha: str, *, token: str | None = None
) -> int | None:
    associated = gh_api(f"repos/{repo}/commits/{head_sha}/pulls", token=token)
    matches = [
        pull["number"]
        for pull in associated
        if pull.get("state") == "open"
        and (pull.get("head") or {}).get("sha") == head_sha
        and isinstance(pull.get("number"), int)
    ]
    if matches:
        return min(matches)

    return search_open_head_pull_request(repo, head_sha, token=token)


def search_open_head_pull_request(
    repo: str, head_sha: str, *, token: str | None = None
) -> int | None:
    # The commit association endpoint omits heads from forks when queried on
    # the base repository. Search PRs and confirm their live head SHA instead.
    query = f"repo:{repo} is:pr is:open {head_sha}"
    after: str | None = None
    matches: list[int] = []
    while True:
        result: dict[str, Any] = gh_graphql(
            HEAD_PULL_REQUEST_SEARCH, {"searchQuery": query, "after": after}, token=token
        )
        search = result["data"]["search"]
        matches.extend(
            node["number"]
            for node in search["nodes"]
            if node
            and node.get("state") == "OPEN"
            and node.get("headRefOid") == head_sha
        )
        page = search["pageInfo"]
        if not page["hasNextPage"]:
            return min(matches) if matches else None
        after = page["endCursor"]
        if not after:
            raise RuntimeError(
                "head pull request search has another page without a cursor"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--head-sha", required=True)
    args = parser.parse_args()
    number = resolve_head_pull_request(args.repo, args.head_sha)
    if number is not None:
        print(number)


if __name__ == "__main__":
    main()
