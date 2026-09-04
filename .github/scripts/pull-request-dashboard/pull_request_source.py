"""Fetch and normalize the GitHub source data for one pull request."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

import github_cli as _github_cli
from dashboard_status import DASHBOARD_APP_SLUG
from github_cli import (
    code_scanning_tools,
    include_missing_required_checks,
    merge_code_scanning_checks,
    required_check_contexts,
    required_code_scanning_checks,
    unreported_required_contexts,
)
from utils import is_copilot_reviewer_login, parse_ts


def gh_pr_view(repo: str, number: int) -> dict[str, Any]:
    return _github_cli.gh_pr_view(repo, number)


def fetch_pr_issue_comments(
    owner: str,
    repo_name: str,
    number: int,
) -> list[dict[str, Any]]:
    return _github_cli.fetch_pr_issue_comments(owner, repo_name, number)


def fetch_pr_reviews(
    owner: str,
    repo_name: str,
    number: int,
) -> list[dict[str, Any]]:
    return _github_cli.fetch_pr_reviews(owner, repo_name, number)


def fetch_review_requests(
    owner: str,
    repo_name: str,
    number: int,
) -> list[dict[str, Any]]:
    return _github_cli.fetch_review_requests(owner, repo_name, number)


def fetch_review_threads(
    owner: str,
    repo_name: str,
    number: int,
) -> list[dict[str, Any]]:
    return _github_cli.fetch_review_threads(owner, repo_name, number)


def gh_api(path: str, paginate: bool = False) -> Any:
    return _github_cli.gh_api(path, paginate)


def gh_branch_rules(
    repo: str,
    base_branch: str,
) -> list[dict[str, Any]] | None:
    return _github_cli.gh_branch_rules(repo, base_branch)


def gh_pr_check_rollup(
    repo: str,
    pr_id: str,
    non_blocking_check_patterns: list[str],
) -> dict[str, Any] | None:
    return _github_cli.gh_pr_check_rollup(
        repo,
        pr_id,
        non_blocking_check_patterns,
    )


def settled_check_suite_app_ids(repo: str, head_sha: str) -> set[int]:
    return _github_cli.settled_check_suite_app_ids(repo, head_sha)


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({
            str(key): _freeze_json(item)
            for key, item in value.items()
        })
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"value is not JSON-compatible: {type(value).__name__}")


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _thaw_json(item)
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _items(value: Any) -> Sequence[Any]:
    if isinstance(value, Mapping):
        nodes = value.get("nodes")
        return nodes if isinstance(nodes, (list, tuple)) else ()
    return value if isinstance(value, (list, tuple)) else ()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _integer(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


@dataclass(frozen=True)
class Actor:
    login: str = ""
    kind: str = ""

    @property
    def reviewer_login(self) -> str:
        if is_copilot_reviewer_login(self.login):
            return "copilot-pull-request-reviewer[bot]"
        return self.login

    @property
    def is_copilot_reviewer(self) -> bool:
        return is_copilot_reviewer_login(self.login)

    @property
    def is_bot(self) -> bool:
        low = self.login.lower()
        return (
            self.kind.lower() in ("app", "bot")
            or low.startswith("app/")
            or low.endswith("[bot]")
        )


@dataclass(frozen=True)
class PullRequestMetadata:
    number: int
    node_id: str = ""
    state: str = ""
    is_draft: bool = False
    title: str = ""
    body: str = ""
    url: str = ""
    author: Actor = field(default_factory=Actor)
    assignees: tuple[Actor, ...] = ()
    mergeable: str = ""
    merge_state_status: str = ""
    created_at: str = ""
    updated_at: str = ""
    head_sha: str = ""
    head_branch: str = ""
    base_branch: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "assignees", tuple(self.assignees))

    @property
    def conflicts(self) -> str:
        if (
            self.mergeable == "CONFLICTING"
            or self.merge_state_status == "DIRTY"
        ):
            return "yes"
        if self.mergeable in ("", "UNKNOWN"):
            return "unknown"
        return "no"


@dataclass(frozen=True)
class Commit:
    sha: str = ""
    author: Actor = field(default_factory=Actor)
    committer: Actor = field(default_factory=Actor)
    author_name: str = ""
    authored_at: str = ""
    committed_at: str = ""
    message: str = ""
    parent_count: int = 0


@dataclass(frozen=True)
class IssueComment:
    database_id: int = 0
    url: str = ""
    body: str = ""
    created_at: str = ""
    updated_at: str = ""
    content_updated_at: str = ""
    minimized: bool = False
    actor: Actor = field(default_factory=Actor)
    performed_via_app_slug: str = ""

    @property
    def effective_content_timestamp(self) -> str:
        created_at = parse_ts(self.created_at)
        content_updated_at = parse_ts(self.content_updated_at)
        if content_updated_at is not None and (
            created_at is None or content_updated_at >= created_at
        ):
            return self.content_updated_at
        if created_at is not None:
            return self.created_at
        return self.updated_at if parse_ts(self.updated_at) is not None else ""

    def is_from_app(self, app_slug: str) -> bool:
        return (
            self.performed_via_app_slug == app_slug
            or self.actor.login == f"{app_slug}[bot]"
        )


@dataclass(frozen=True)
class ReviewComment:
    database_id: int = 0
    url: str = ""
    body: str = ""
    created_at: str = ""
    updated_at: str = ""
    path: str = ""
    actor: Actor = field(default_factory=Actor)


@dataclass(frozen=True)
class Review:
    database_id: int = 0
    commit_id: str = ""
    finding_count: int = 0
    url: str = ""
    actor: Actor = field(default_factory=Actor)
    state: str = ""
    body: str = ""
    submitted_at: str = ""
    updated_at: str = ""
    content_updated_at: str = ""

    @property
    def effective_content_timestamp(self) -> str:
        submitted_at = parse_ts(self.submitted_at)
        content_updated_at = parse_ts(self.content_updated_at)
        if content_updated_at is not None and (
            submitted_at is None or content_updated_at >= submitted_at
        ):
            return self.content_updated_at
        return self.submitted_at


@dataclass(frozen=True)
class ReviewRequest:
    kind: str = ""
    login: str = ""

    @property
    def is_copilot_reviewer(self) -> bool:
        return is_copilot_reviewer_login(self.login)

    @property
    def is_human(self) -> bool:
        return self.kind.lower() == "user"


@dataclass(frozen=True)
class ReactionGroup:
    content: str = ""
    user_logins: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "user_logins", tuple(self.user_logins))


@dataclass(frozen=True)
class ReviewThreadComment:
    node_id: str = ""
    url: str = ""
    body: str = ""
    created_at: str = ""
    updated_at: str = ""
    actor: Actor = field(default_factory=Actor)
    reaction_groups: tuple[ReactionGroup, ...] = ()

    @property
    def effective_content_timestamp(self) -> str:
        created_at = parse_ts(self.created_at)
        updated_at = parse_ts(self.updated_at)
        if updated_at is not None and (
            created_at is None or updated_at >= created_at
        ):
            return self.updated_at
        return self.created_at

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "reaction_groups",
            tuple(self.reaction_groups),
        )


@dataclass(frozen=True)
class ReviewThread:
    node_id: str = ""
    is_resolved: bool = False
    is_outdated: bool = False
    path: str = ""
    line: int | None = None
    comments: tuple[ReviewThreadComment, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "comments", tuple(self.comments))


@dataclass(frozen=True)
class Check:
    name: str = ""
    state: str = ""
    bucket: str = ""
    workflow: str = ""
    workflow_run_id: int | None = None
    description: str = ""
    link: str = ""
    started_at: str = ""
    completed_at: str = ""
    check_run_id: int | None = None
    integration_id: int | None = None
    is_status_context: bool = False


@dataclass(frozen=True)
class NonBlockingFailure:
    name: str = ""
    bucket: str = ""
    completed_at: str = ""


@dataclass(frozen=True)
class RoutingFingerprintProjection:
    """The historical routing hash inputs, frozen at the source boundary."""

    base_branch: str
    conflicts: str
    checks: Any
    issue_comments: tuple[Any, ...]
    pr_title: str
    pr_body: str
    review_comments: tuple[Any, ...]
    review_requests: tuple[Any, ...]
    reviews: tuple[Any, ...]
    review_threads: tuple[Any, ...]

    @classmethod
    def from_transport(
        cls,
        aggregate: Mapping[str, Any],
    ) -> RoutingFingerprintProjection:
        pr = _mapping(aggregate.get("pr"))
        normalized_pr = normalize_pull_request(pr)
        issue_comments = [
            comment
            for comment in _items(aggregate.get("issue_comments"))
            if (
                _mapping(_mapping(comment).get("user")).get("login")
                != f"{DASHBOARD_APP_SLUG}[bot]"
            )
        ]
        checks = aggregate.get("checks")
        return cls(
            base_branch=normalized_pr.base_branch,
            conflicts=normalized_pr.conflicts,
            checks=None if checks is None else _freeze_json(checks),
            issue_comments=tuple(_freeze_json(issue_comments)),
            pr_title=normalized_pr.title,
            pr_body=normalized_pr.body.replace("\r\n", "\n"),
            review_comments=tuple(_freeze_json(
                _items(aggregate.get("review_comments"))
            )),
            review_requests=tuple(_freeze_json(
                _items(aggregate.get("review_requests"))
            )),
            reviews=tuple(_freeze_json(_items(aggregate.get("reviews")))),
            review_threads=tuple(_freeze_json(
                _items(aggregate.get("review_threads"))
            )),
        )

    def routing_inputs(self) -> dict[str, Any]:
        return {
            "base_branch": self.base_branch,
            "checks": _thaw_json(self.checks),
            "conflicts": self.conflicts,
            "issue_comments": _thaw_json(self.issue_comments),
            "pr_text": {
                "body": self.pr_body,
                "title": self.pr_title,
            },
            "review_comments": _thaw_json(self.review_comments),
            "review_requests": _thaw_json(self.review_requests),
            "reviews": _thaw_json(self.reviews),
            "review_threads": _thaw_json(self.review_threads),
        }

    def copilot_request_inputs(self) -> dict[str, Any]:
        inputs = self.routing_inputs()
        inputs.pop("checks")
        return inputs


@dataclass(frozen=True)
class PullRequestSource:
    pull_request: PullRequestMetadata
    commits: tuple[Commit, ...] = ()
    issue_comments: tuple[IssueComment, ...] = ()
    review_comments: tuple[ReviewComment, ...] = ()
    reviews: tuple[Review, ...] = ()
    review_requests: tuple[ReviewRequest, ...] = ()
    review_threads: tuple[ReviewThread, ...] = ()
    checks: tuple[Check, ...] | None = ()
    non_blocking_failures: tuple[NonBlockingFailure, ...] = ()
    fingerprint: RoutingFingerprintProjection | None = None

    def __post_init__(self) -> None:
        for name in (
            "commits",
            "issue_comments",
            "review_comments",
            "reviews",
            "review_requests",
            "review_threads",
            "non_blocking_failures",
        ):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        if self.checks is not None:
            object.__setattr__(self, "checks", tuple(self.checks))


def normalize_actor(value: Any) -> Actor:
    if isinstance(value, Actor):
        return value
    if isinstance(value, str):
        return Actor(login=value.strip())
    actor = _mapping(value)
    kind = _text(actor.get("__typename") or actor.get("type"))
    login = _text(actor.get("login") or actor.get("slug"))
    if kind.lower() == "bot" and login and not login.endswith("[bot]"):
        login = f"{login}[bot]"
    return Actor(login=login, kind=kind)


def normalize_pull_request(
    value: Any,
    *,
    number: int = 0,
) -> PullRequestMetadata:
    pr = _mapping(value)
    base = _mapping(pr.get("base"))
    head = _mapping(pr.get("head"))
    mergeable_value = pr.get("mergeable")
    if isinstance(mergeable_value, bool):
        mergeable = "MERGEABLE" if mergeable_value else "CONFLICTING"
    else:
        mergeable = _text(mergeable_value)
    assignees = tuple(
        normalize_actor(actor)
        for actor in _items(pr.get("assignees"))
    )
    return PullRequestMetadata(
        number=_integer(pr.get("number")) or number,
        node_id=_text(pr.get("node_id") or pr.get("id")),
        state=_text(pr.get("state")).upper(),
        is_draft=bool(pr.get("isDraft") or pr.get("draft")),
        title=str(pr.get("title") or ""),
        body=str(pr.get("body") or ""),
        url=_text(pr.get("html_url") or pr.get("url")),
        author=normalize_actor(pr.get("author") or pr.get("user")),
        assignees=assignees,
        mergeable=mergeable,
        merge_state_status=_text(
            pr.get("mergeStateStatus")
            or pr.get("merge_state_status")
            or pr.get("mergeable_state")
        ).upper(),
        created_at=_text(pr.get("createdAt") or pr.get("created_at")),
        updated_at=_text(pr.get("updatedAt") or pr.get("updated_at")),
        head_sha=_text(
            pr.get("headRefOid")
            or pr.get("head_sha")
            or head.get("sha")
        ),
        head_branch=_text(
            pr.get("headRefName")
            or pr.get("head_branch")
            or head.get("ref")
        ),
        base_branch=_text(
            pr.get("baseRefName")
            or pr.get("base_branch")
            or base.get("ref")
        ),
    )


def normalize_commits(values: Any) -> tuple[Commit, ...]:
    commits: list[Commit] = []
    for value in _items(values):
        item = _mapping(value)
        commit = _mapping(item.get("commit"))
        author = _mapping(commit.get("author"))
        committer = _mapping(commit.get("committer"))
        commits.append(Commit(
            sha=_text(item.get("sha") or item.get("oid")),
            author=normalize_actor(item.get("author")),
            committer=normalize_actor(item.get("committer")),
            author_name=_text(author.get("name")),
            authored_at=_text(author.get("date")),
            committed_at=_text(committer.get("date")),
            message=str(commit.get("message") or item.get("message") or ""),
            parent_count=len(_items(item.get("parents"))),
        ))
    return tuple(commits)


def normalize_issue_comments(values: Any) -> tuple[IssueComment, ...]:
    comments: list[IssueComment] = []
    for value in _items(values):
        item = _mapping(value)
        app = _mapping(item.get("performed_via_github_app"))
        created_at = _text(item.get("created_at") or item.get("createdAt"))
        updated_at = _text(
            item.get("updated_at")
            or item.get("lastEditedAt")
            or created_at
        )
        comments.append(IssueComment(
            database_id=_integer(
                item.get("fullDatabaseId")
                or item.get("databaseId")
                or item.get("id")
            ),
            url=_text(item.get("html_url") or item.get("url")),
            body=str(item.get("body") or ""),
            created_at=created_at,
            updated_at=updated_at,
            content_updated_at=_text(
                item.get("content_updated_at")
                or item.get("lastEditedAt")
                or updated_at
            ),
            minimized=bool(item.get("minimized") or item.get("isMinimized")),
            actor=normalize_actor(item.get("user") or item.get("author")),
            performed_via_app_slug=_text(app.get("slug")),
        ))
    return tuple(comments)


def normalize_review_comments(values: Any) -> tuple[ReviewComment, ...]:
    comments: list[ReviewComment] = []
    for value in _items(values):
        item = _mapping(value)
        comments.append(ReviewComment(
            database_id=_integer(
                item.get("fullDatabaseId")
                or item.get("databaseId")
                or item.get("id")
            ),
            url=_text(item.get("html_url") or item.get("url")),
            body=str(item.get("body") or ""),
            created_at=_text(item.get("created_at") or item.get("createdAt")),
            updated_at=_text(item.get("updated_at") or item.get("updatedAt")),
            path=_text(item.get("path")),
            actor=normalize_actor(item.get("user") or item.get("author")),
        ))
    return tuple(comments)


def normalize_reviews(values: Any) -> tuple[Review, ...]:
    reviews: list[Review] = []
    for value in _items(values):
        item = _mapping(value)
        commit = _mapping(item.get("commit"))
        comments = _mapping(item.get("comments"))
        reviews.append(Review(
            database_id=_integer(
                item.get("fullDatabaseId")
                or item.get("databaseId")
                or item.get("id")
            ),
            commit_id=_text(item.get("commit_id") or commit.get("oid")),
            finding_count=_integer(
                item.get("finding_count") or comments.get("totalCount")
            ),
            url=_text(item.get("url") or item.get("html_url")),
            actor=normalize_actor(item.get("user") or item.get("author")),
            state=_text(item.get("state")).upper(),
            body=str(item.get("body") or ""),
            submitted_at=_text(
                item.get("submitted_at") or item.get("submittedAt")
            ),
            updated_at=_text(item.get("updated_at") or item.get("updatedAt")),
            content_updated_at=_text(
                item.get("content_updated_at")
                or item.get("lastEditedAt")
                or item.get("submitted_at")
                or item.get("submittedAt")
            ),
        ))
    return tuple(reviews)


def normalize_review_requests(values: Any) -> tuple[ReviewRequest, ...]:
    requests: list[ReviewRequest] = []
    for value in _items(values):
        item = _mapping(value)
        reviewer = _mapping(item.get("requestedReviewer")) or item
        requests.append(ReviewRequest(
            kind=_text(
                reviewer.get("__typename")
                or reviewer.get("type")
                or ("Team" if reviewer.get("slug") else "")
            ),
            login=_text(reviewer.get("login") or reviewer.get("slug")),
        ))
    return tuple(requests)


def normalize_review_threads(values: Any) -> tuple[ReviewThread, ...]:
    threads: list[ReviewThread] = []
    for value in _items(values):
        item = _mapping(value)
        comments: list[ReviewThreadComment] = []
        for raw_comment in _items(item.get("comments")):
            comment = _mapping(raw_comment)
            reaction_groups: list[ReactionGroup] = []
            for raw_group in _items(comment.get("reactionGroups")):
                group = _mapping(raw_group)
                users = _mapping(group.get("users"))
                reaction_groups.append(ReactionGroup(
                    content=_text(group.get("content")),
                    user_logins=tuple(
                        normalize_actor(user).login
                        for user in _items(users)
                        if normalize_actor(user).login
                    ),
                ))
            comments.append(ReviewThreadComment(
                node_id=_text(comment.get("id") or comment.get("node_id")),
                url=_text(comment.get("url") or comment.get("html_url")),
                body=str(comment.get("body") or ""),
                created_at=_text(
                    comment.get("createdAt") or comment.get("created_at")
                ),
                updated_at=_text(
                    comment.get("lastEditedAt")
                    or comment.get("updatedAt")
                    or comment.get("updated_at")
                    or comment.get("createdAt")
                    or comment.get("created_at")
                ),
                actor=normalize_actor(
                    comment.get("author") or comment.get("user")
                ),
                reaction_groups=tuple(reaction_groups),
            ))
        line_value = item.get("line")
        threads.append(ReviewThread(
            node_id=_text(item.get("id") or item.get("node_id")),
            is_resolved=bool(
                item.get("isResolved") or item.get("is_resolved")
            ),
            is_outdated=bool(
                item.get("isOutdated") or item.get("is_outdated")
            ),
            path=_text(item.get("path")),
            line=_integer(line_value) if line_value is not None else None,
            comments=tuple(comments),
        ))
    return tuple(threads)


def normalize_checks(values: Any) -> tuple[Check, ...] | None:
    if values is None:
        return None
    checks: list[Check] = []
    for value in _items(values):
        item = _mapping(value)
        checks.append(Check(
            name=_text(item.get("name")),
            state=_text(item.get("state")),
            bucket=_text(item.get("bucket")),
            workflow=_text(item.get("workflow")),
            workflow_run_id=(
                _integer(item.get("workflow_run_id"))
                if item.get("workflow_run_id") is not None
                else None
            ),
            description=str(item.get("description") or ""),
            link=_text(item.get("link")),
            started_at=_text(item.get("started_at")),
            completed_at=_text(item.get("completed_at")),
            check_run_id=(
                _integer(item.get("check_run_id"))
                if item.get("check_run_id") is not None
                else None
            ),
            integration_id=(
                _integer(item.get("integration_id"))
                if item.get("integration_id") is not None
                else None
            ),
            is_status_context=bool(item.get("status_context")),
        ))
    return tuple(checks)


def normalize_non_blocking_failures(
    values: Any,
) -> tuple[NonBlockingFailure, ...]:
    return tuple(
        NonBlockingFailure(
            name=_text(item.get("name")),
            bucket=_text(item.get("bucket")),
            completed_at=_text(item.get("completed_at")),
        )
        for value in _items(values)
        if (item := _mapping(value))
    )


def normalize_pull_request_source(
    aggregate: Mapping[str, Any],
    *,
    number: int = 0,
) -> PullRequestSource:
    """Normalize transport payloads without exposing them to domain code."""
    return PullRequestSource(
        pull_request=normalize_pull_request(aggregate.get("pr"), number=number),
        commits=normalize_commits(aggregate.get("commits")),
        issue_comments=normalize_issue_comments(aggregate.get("issue_comments")),
        review_comments=normalize_review_comments(
            aggregate.get("review_comments")
        ),
        reviews=normalize_reviews(aggregate.get("reviews")),
        review_requests=normalize_review_requests(
            aggregate.get("review_requests")
        ),
        review_threads=normalize_review_threads(
            aggregate.get("review_threads")
        ),
        checks=normalize_checks(aggregate.get("checks")),
        non_blocking_failures=normalize_non_blocking_failures(
            aggregate.get("non_blocking_check_failures")
        ),
        fingerprint=RoutingFingerprintProjection.from_transport(aggregate),
    )


def fetch_pull_request_source(
    repo: str,
    owner: str,
    repo_name: str,
    number: int,
    non_blocking_check_patterns: Sequence[str] = (),
    *,
    include_commits: bool = True,
) -> PullRequestSource:
    """Fetch one coherent pull request source snapshot."""
    pr = gh_pr_view(repo, number) or {}
    metadata = normalize_pull_request(pr, number=number)
    if metadata.state != "OPEN" or metadata.is_draft:
        return normalize_pull_request_source({"pr": pr}, number=number)

    with ThreadPoolExecutor() as pool:
        issue_comments_future = pool.submit(
            fetch_pr_issue_comments,
            owner,
            repo_name,
            number,
        )
        review_comments_future = pool.submit(
            gh_api,
            f"/repos/{owner}/{repo_name}/pulls/{number}/comments?per_page=100",
            True,
        )
        review_threads_future = pool.submit(
            fetch_review_threads,
            owner,
            repo_name,
            number,
        )
        reviews_future = pool.submit(fetch_pr_reviews, owner, repo_name, number)
        review_requests_future = pool.submit(
            fetch_review_requests,
            owner,
            repo_name,
            number,
        )
        commits_future = (
            pool.submit(
                gh_api,
                f"/repos/{owner}/{repo_name}/pulls/{number}/commits?per_page=100",
                True,
            )
            if include_commits
            else None
        )

        check_rollup_future = pool.submit(
            gh_pr_check_rollup,
            repo,
            pr.get("id") or "",
            list(non_blocking_check_patterns),
        )
        branch_rules_future = pool.submit(
            gh_branch_rules,
            repo,
            pr.get("baseRefName") or "",
        )
        check_rollup = check_rollup_future.result()
        branch_rules = branch_rules_future.result()

        if check_rollup is not None and check_rollup["head_oid"] != (
            pr.get("headRefOid") or ""
        ):
            check_rollup = None
        required_contexts = required_check_contexts(branch_rules)
        settled_app_ids: set[int] = set()
        if check_rollup is not None and required_contexts is not None and any(
            requirement.get("integration_id") is not None
            for requirement in unreported_required_contexts(
                check_rollup["required"],
                required_contexts,
            )
        ):
            settled_app_ids = settled_check_suite_app_ids(
                repo,
                pr.get("headRefOid") or "",
            )
        checks = include_missing_required_checks(
            None if check_rollup is None else check_rollup["required"],
            required_contexts,
            settled_app_ids,
        )
        if checks is not None and check_rollup is not None:
            checks = merge_code_scanning_checks(
                checks,
                required_code_scanning_checks(
                    check_rollup["code_scanning"],
                    code_scanning_tools(branch_rules),
                    bool(check_rollup["pending"]),
                ),
            )
        aggregate = {
            "pr": pr,
            "commits": commits_future.result() if commits_future else [],
            "issue_comments": issue_comments_future.result() or [],
            "review_comments": review_comments_future.result() or [],
            "reviews": reviews_future.result() or [],
            "review_requests": review_requests_future.result() or [],
            "review_threads": review_threads_future.result() or [],
            "checks": checks,
            "non_blocking_check_failures": (
                []
                if check_rollup is None
                else check_rollup["non_blocking_failures"]
            ),
        }
        return normalize_pull_request_source(aggregate, number=number)
