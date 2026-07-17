from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Any
from urllib.parse import quote, urlparse


GH_RETRY_ATTEMPTS = 4
GH_RETRY_DELAY_SECONDS = 1.5
DEFAULT_OWNER = "open-telemetry"


def normalize_repo(repo: str) -> str:
    return repo if "/" in repo else f"{DEFAULT_OWNER}/{repo}"


def repo_state_key(repo: str) -> str:
    return normalize_repo(repo).split("/", 1)[1]


class TransientGhError(RuntimeError):
    pass


_RETRYABLE_GH_ERROR_FRAGMENTS = (
    "http 5",
    "gateway timeout",
    "timeout",
    "temporarily unavailable",
    "connection reset",
    "connection refused",
)


def is_retryable_gh_error(stderr: str) -> bool:
    text = stderr.lower()
    return any(fragment in text for fragment in _RETRYABLE_GH_ERROR_FRAGMENTS)


def sleep_for_retry(attempt: int) -> None:
    time.sleep(GH_RETRY_DELAY_SECONDS * (attempt + 1))


def run_gh(
    cmd: list[str],
    token: str | None = None,
    input_text: str | None = None,
    allowed_exit_codes: frozenset[int] | set[int] = frozenset({0}),
) -> str:
    env = {**os.environ, "GH_TOKEN": token} if token else None
    last_stderr = ""
    for attempt in range(GH_RETRY_ATTEMPTS):
        proc = subprocess.run(
            cmd,
            input=input_text,
            capture_output=True,
            text=True,
            check=False,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        if proc.returncode in allowed_exit_codes:
            return proc.stdout
        last_stderr = proc.stderr.strip()
        if attempt == GH_RETRY_ATTEMPTS - 1 or not is_retryable_gh_error(last_stderr):
            break
        sleep_for_retry(attempt)
    message = f"{' '.join(cmd)} failed: {last_stderr}"
    if is_retryable_gh_error(last_stderr):
        raise TransientGhError(message)
    raise RuntimeError(message)


def run_gh_json(cmd: list[str], token: str | None = None, input_text: str | None = None) -> Any:
    return json.loads(run_gh(cmd, token=token, input_text=input_text) or "null")


def gh_api(path: str, paginate: bool = False, token: str | None = None) -> Any:
    cmd = ["gh", "api", "-H", "Accept: application/vnd.github+json"]
    if paginate:
        cmd += ["--paginate", "--slurp"]
    cmd.append(path)
    data = run_gh_json(cmd, token=token)
    if paginate and isinstance(data, list):
        flat: list[Any] = []
        for page in data:
            if isinstance(page, list):
                flat.extend(page)
            else:
                flat.append(page)
        return flat
    return data


def gh_graphql(query: str, fields: dict[str, Any], token: str | None = None) -> dict[str, Any]:
    cmd = ["gh", "api", "graphql", "-f", f"query={query}"]
    for name, value in fields.items():
        if value is None:
            continue
        cmd.extend(["-F", f"{name}={value}"])
    return run_gh_json(cmd, token=token)


def gh_pr_view(repo: str, number: int) -> dict[str, Any]:
    fields = ",".join([
        "id", "number", "title", "url", "author", "state", "isDraft",
        "mergeable", "mergeStateStatus", "createdAt", "updatedAt",
        "reviewDecision", "assignees", "baseRefName",
    ])
    cmd = ["gh", "pr", "view", str(number), "--repo", repo, "--json", fields]
    last: dict[str, Any] = {}
    for attempt in range(GH_RETRY_ATTEMPTS):
        last = run_gh_json(cmd) or {}
        if last.get("mergeable") not in (None, "", "UNKNOWN"):
            return last
        if attempt < GH_RETRY_ATTEMPTS - 1:
            sleep_for_retry(attempt)
    return last


PR_METADATA_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $after: String) {
    repository(owner: $owner, name: $name) {
        pullRequest(number: $number) {
            lastEditedAt
            editor {
                login
            }
            reviews(first: 100, after: $after) {
                pageInfo {
                    hasNextPage
                    endCursor
                }
                nodes {
                    fullDatabaseId
                    url
                    body
                    state
                    submittedAt
                    updatedAt
                    author {
                        login
                    }
                }
            }
        }
    }
}
"""


PR_TITLE_EDITS_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $after: String) {
    repository(owner: $owner, name: $name) {
        pullRequest(number: $number) {
            timelineItems(
                first: 100,
                after: $after,
                itemTypes: [RENAMED_TITLE_EVENT]
            ) {
                pageInfo {
                    hasNextPage
                    endCursor
                }
                nodes {
                    ... on RenamedTitleEvent {
                        actor {
                            login
                        }
                        createdAt
                    }
                }
            }
        }
    }
}
"""


def fetch_pr_title_edits(owner: str, repo_name: str, number: int) -> list[dict[str, Any]]:
    edits: list[dict[str, Any]] = []
    after: str | None = None
    while True:
        data = gh_graphql(
            PR_TITLE_EDITS_QUERY,
            {"owner": owner, "name": repo_name, "number": number, "after": after},
        )
        pull_request = (((data.get("data") or {}).get("repository") or {}).get("pullRequest") or {})
        connection = pull_request.get("timelineItems") or {}
        edits.extend(
            {
                "actor": node.get("actor") or {},
                "createdAt": node.get("createdAt") or "",
            }
            for node in connection.get("nodes") or []
            if node.get("createdAt")
        )
        page_info = connection.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            return edits
        after = page_info.get("endCursor") or None
        if after is None:
            return edits


def fetch_pr_review_data(owner: str, repo_name: str, number: int) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    reviews: list[dict[str, Any]] = []
    after: str | None = None
    while True:
        data = gh_graphql(
            PR_METADATA_QUERY,
            {"owner": owner, "name": repo_name, "number": number, "after": after},
        )
        pull_request = (((data.get("data") or {}).get("repository") or {}).get("pullRequest") or {})
        metadata["lastEditedAt"] = pull_request.get("lastEditedAt")
        metadata["editor"] = pull_request.get("editor")
        connection = pull_request.get("reviews") or {}
        for review in connection.get("nodes") or []:
            # The numeric database id is the stable `source_id` used downstream
            # to build discussion ids and exclusion keys, all of which require an
            # int. `fullDatabaseId` is only null for unsubmitted PENDING reviews,
            # which the API never returns for a token that is not their author,
            # so a review without a usable id is dropped rather than given a
            # synthetic id that would be filtered back out (or collide).
            try:
                database_id = int(review.get("fullDatabaseId"))
            except (TypeError, ValueError):
                continue
            reviews.append({
                "id": database_id,
                "url": review.get("url") or "",
                "user": review.get("author") or {},
                "state": review.get("state") or "",
                "body": review.get("body") or "",
                "submitted_at": review.get("submittedAt") or "",
                "updated_at": review.get("updatedAt") or "",
            })
        page_info = connection.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        after = page_info.get("endCursor") or None
        if after is None:
            break
    return {"reviews": reviews, "pr_metadata": metadata}


PR_CHECKS_QUERY = """
query($id: ID!, $after: String) {
    node(id: $id) {
        ... on PullRequest {
            commits(last: 1) {
                nodes {
                    commit {
                        statusCheckRollup {
                            contexts(first: 100, after: $after) {
                                nodes {
                                    __typename
                                    ... on StatusContext {
                                        context
                                        state
                                        targetUrl
                                        createdAt
                                        description
                                        isRequired(pullRequestId: $id)
                                    }
                                    ... on CheckRun {
                                        name
                                        status
                                        conclusion
                                        startedAt
                                        completedAt
                                        detailsUrl
                                        url
                                        isRequired(pullRequestId: $id)
                                        checkSuite {
                                            app {
                                                databaseId
                                            }
                                            workflowRun {
                                                workflow {
                                                    name
                                                }
                                            }
                                        }
                                    }
                                }
                                pageInfo {
                                    hasNextPage
                                    endCursor
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
"""


def check_bucket(state: str) -> str:
    if state == "SUCCESS":
        return "pass"
    if state in ("SKIPPED", "NEUTRAL"):
        return "skipping"
    if state in ("ERROR", "FAILURE", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE"):
        return "fail"
    if state == "CANCELLED":
        return "cancel"
    return "pending"


def normalize_required_check(node: dict[str, Any]) -> dict[str, Any]:
    is_status = node.get("__typename") == "StatusContext"
    suite = node.get("checkSuite") or {}
    app = suite.get("app") or {}
    workflow_run = suite.get("workflowRun") or {}
    workflow = workflow_run.get("workflow") or {}
    state = (
        node.get("state")
        if is_status
        else node.get("conclusion") or node.get("status")
    ) or ""
    return {
        "name": (node.get("context") if is_status else node.get("name")) or "",
        "state": state,
        "bucket": check_bucket(state),
        "workflow": workflow.get("name") or "",
        "description": node.get("description") or "",
        "link": (node.get("targetUrl") if is_status else node.get("detailsUrl")) or "",
        "started_at": (node.get("createdAt") if is_status else node.get("startedAt")) or "",
        "completed_at": (node.get("createdAt") if is_status else node.get("completedAt")) or "",
        "check_run_id": None if is_status else check_run_id(node["url"]),
        "integration_id": None if is_status else app.get("databaseId"),
        "status_context": is_status,
    }


def check_run_id(url: str) -> int:
    return int(urlparse(url).path.rstrip("/").rsplit("/", 1)[-1])


def check_attempt_order(check: dict[str, Any]) -> tuple[int, int | str]:
    if check["status_context"]:
        return 0, check["started_at"]
    return 1, check["check_run_id"] or 0


def gh_pr_checks(repo: str, pr_id: str) -> list[dict[str, Any]] | None:
    del repo
    checks_by_identity: dict[tuple[str, int | None], dict[str, Any]] = {}
    after: str | None = None
    try:
        while True:
            data = gh_graphql(PR_CHECKS_QUERY, {"id": pr_id, "after": after})
            pull_request = (data.get("data") or {}).get("node") or {}
            commits = pull_request.get("commits") or {}
            commit_nodes = commits.get("nodes") or []
            commit = (commit_nodes[0] if commit_nodes else {}).get("commit") or {}
            rollup = commit.get("statusCheckRollup") or {}
            contexts = rollup.get("contexts") or {}
            for node in contexts.get("nodes") or []:
                if not node.get("isRequired"):
                    continue
                check = normalize_required_check(node)
                identity = (check["name"], check["integration_id"])
                previous = checks_by_identity.get(identity)
                if previous is None or check_attempt_order(check) >= check_attempt_order(previous):
                    checks_by_identity[identity] = check
            page_info = contexts.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                return list(checks_by_identity.values())
            after = page_info.get("endCursor") or None
            if after is None:
                return list(checks_by_identity.values())
    except RuntimeError:
        return None


def gh_required_check_contexts(repo: str, base_branch: str) -> list[dict[str, Any]] | None:
    encoded_branch = quote(base_branch, safe="")
    try:
        rules = gh_api(
            f"/repos/{repo}/rules/branches/{encoded_branch}?per_page=100",
            paginate=True,
        )
    except RuntimeError:
        return None
    contexts: list[dict[str, Any]] = []
    for rule in rules or []:
        if rule.get("type") != "required_status_checks":
            continue
        parameters = rule.get("parameters") or {}
        for check in parameters.get("required_status_checks") or []:
            context = check.get("context") or ""
            requirement = {
                "context": context,
                "integration_id": check.get("integration_id"),
            }
            if context and requirement not in contexts:
                contexts.append(requirement)
    return contexts


def include_missing_required_checks(
    checks: list[dict[str, Any]] | None,
    required_contexts: list[dict[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    if checks is None or required_contexts is None:
        return None
    complete = list(checks)
    for requirement in required_contexts:
        context = requirement["context"]
        integration_id = requirement.get("integration_id")
        reported = any(
            check.get("name") == context
            and (
                integration_id is None
                or check.get("integration_id") == integration_id
            )
            for check in checks
        )
        if reported:
            continue
        complete.append({
            "name": context,
            "state": "EXPECTED",
            "bucket": "pending",
            "workflow": "",
            "description": "Required check has not reported yet.",
            "link": "",
            "started_at": "",
            "completed_at": "",
            "integration_id": integration_id,
            "status_context": False,
        })
    return complete


def list_open_prs(repo: str) -> list[dict[str, Any]]:
    return run_gh_json([
        "gh", "pr", "list", "--repo", repo, "--state", "open", "--limit", "500",
        "--json", "number,title,author,isDraft,updatedAt,url",
    ])


def detect_repo() -> str:
    proc = subprocess.run(
        ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
        capture_output=True,
        text=True,
        check=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.stdout.strip()


def load_reviewer_set(org: str, approver_team_slugs: list[str]) -> set[str]:
    token = os.environ.get("PR_DASHBOARD_TOKEN") or None
    reviewers: set[str] = set()
    for slug in approver_team_slugs:
        members = gh_api(
            f"/orgs/{org}/teams/{slug}/members?per_page=100",
            paginate=True,
            token=token,
        )
        reviewers.update(m["login"] for m in members)
    if not reviewers:
        raise RuntimeError(
            f"no reviewers found in teams {approver_team_slugs}; "
            f"the dashboard app token must have org members read permission"
        )
    return {r.lower() for r in reviewers}


REVIEW_THREADS_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $after: String) {
    repository(owner: $owner, name: $name) {
        pullRequest(number: $number) {
            # Keep this page small to limit GitHub GraphQL rate-limit cost;
            # pagination still fetches every review thread.
            reviewThreads(first: 10, after: $after) {
                pageInfo {
                    hasNextPage
                    endCursor
                }
                nodes {
                    id
                    isResolved
                    isOutdated
                    path
                    line
                    # Keep this page small to limit GitHub GraphQL rate-limit cost;
                    # pagination still fetches every comment for long threads.
                    comments(first: 10) {
                        pageInfo {
                            hasNextPage
                            endCursor
                        }
                        nodes {
                            id
                            url
                            body
                            createdAt
                            updatedAt
                            author {
                                login
                            }
                            reactionGroups {
                                content
                                # Keep this page small to limit GitHub GraphQL
                                # rate-limit cost for this nested connection.
                                users(first: 10) {
                                    nodes {
                                        login
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
"""

REVIEW_THREAD_COMMENTS_QUERY = """
query($thread_id: ID!, $after: String) {
    node(id: $thread_id) {
        ... on PullRequestReviewThread {
            comments(first: 100, after: $after) {
                pageInfo {
                    hasNextPage
                    endCursor
                }
                nodes {
                    id
                    url
                    body
                    createdAt
                    updatedAt
                    author {
                        login
                    }
                    reactionGroups {
                        content
                        # This paginated path is uncommon; keep the same cap
                        # used by the initial review-thread comment query.
                        users(first: 10) {
                            nodes {
                                login
                            }
                        }
                    }
                }
            }
        }
    }
}
"""


def fetch_remaining_review_thread_comments(thread_id: str, after: str | None) -> list[dict[str, Any]]:
    comments: list[dict[str, Any]] = []
    while after:
        data = gh_graphql(
            REVIEW_THREAD_COMMENTS_QUERY,
            {"thread_id": thread_id, "after": after},
        )
        connection = (((data.get("data") or {}).get("node") or {}).get("comments") or {})
        comments.extend(connection.get("nodes") or [])
        page_info = connection.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        after = page_info.get("endCursor") or ""
    return comments


def fetch_review_threads(owner: str, repo_name: str, number: int) -> list[dict[str, Any]]:
    threads: list[dict[str, Any]] = []
    after: str | None = None
    while True:
        data = gh_graphql(
            REVIEW_THREADS_QUERY,
            {"owner": owner, "name": repo_name, "number": number, "after": after},
        )
        page = (((data.get("data") or {}).get("repository") or {}).get("pullRequest") or {}).get("reviewThreads") or {}
        for thread in page.get("nodes") or []:
            comments = thread.get("comments") or {}
            page_info = comments.get("pageInfo") or {}
            if page_info.get("hasNextPage"):
                nodes = list(comments.get("nodes") or [])
                nodes.extend(fetch_remaining_review_thread_comments(
                    thread.get("id") or "",
                    page_info.get("endCursor") or "",
                ))
                comments["nodes"] = nodes
                comments["pageInfo"] = {"hasNextPage": False, "endCursor": ""}
                thread["comments"] = comments
            threads.append(thread)
        page_info = page.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            return threads
        after = page_info.get("endCursor") or ""
