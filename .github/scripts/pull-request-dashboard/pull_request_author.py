"""Resolve the human behind a Copilot-authored pull request."""

from __future__ import annotations

from collections.abc import Sequence

from pull_request_source import Commit, PullRequestMetadata
from utils import is_unattended_author_login, normalize_author_identity


# GitHub reports Copilot PR authors as `app/copilot-swe-agent` or `Copilot`,
# while REST committers can use the bare `copilot` slug.
_COPILOT_COMMITTER_IDENTITIES = {"copilot"}
_COPILOT_PR_AUTHOR_IDENTITIES = {"copilot-swe-agent", "copilot"}


def is_copilot_author(login: str) -> bool:
    return normalize_author_identity(login) in _COPILOT_PR_AUTHOR_IDENTITIES


def _human_author_for_copilot_pr(
    pr: PullRequestMetadata,
    commits: Sequence[Commit],
) -> str:
    for assignee in pr.assignees:
        login = assignee.login
        if (
            login
            and not is_copilot_author(login)
            and not is_unattended_author_login(login)
        ):
            return login

    if not commits:
        return ""
    committer = commits[0].committer
    login = committer.login
    if (
        not login
        or normalize_author_identity(login) in _COPILOT_COMMITTER_IDENTITIES
        or is_unattended_author_login(login)
        or committer.is_bot
        or committer.is_copilot_reviewer
    ):
        return ""
    return login


def effective_author(
    pr: PullRequestMetadata,
    commits: Sequence[Commit] = (),
) -> str:
    author = pr.author.login
    if is_copilot_author(author):
        human_author = _human_author_for_copilot_pr(pr, commits)
        if human_author:
            return human_author
    return author
