"""Compatibility entrypoint for discussion classification."""

from __future__ import annotations

from typing import Any

from classification_execution import (
    DEFAULT_CLASSIFICATION_CACHE_STORE,
    DEFAULT_CLASSIFICATION_SERVICE,
    ClassificationExecutionRequest,
)
from classification_policy import ClassificationDiscussion, DiscussionClassifications


def _policy_discussions(
    discussions: list[dict[str, Any]],
) -> tuple[ClassificationDiscussion, ...]:
    return tuple(
        ClassificationDiscussion.from_record(discussion)
        for discussion in discussions
    )


def classify_discussion_domains(
    number: int,
    review_threads: list[dict[str, Any]],
    top_level_items: list[dict[str, Any]],
    top_level_author_comment_items: list[dict[str, Any]],
    model: str,
) -> DiscussionClassifications:
    return DEFAULT_CLASSIFICATION_SERVICE.classify(
        ClassificationExecutionRequest(
            pr_number=number,
            model=model,
            review_threads=_policy_discussions(review_threads),
            top_level_items=_policy_discussions(top_level_items),
            top_level_author_comments=_policy_discussions(
                top_level_author_comment_items
            ),
        )
    )


def prune_classification_cache(open_pr_numbers: set[int]) -> None:
    DEFAULT_CLASSIFICATION_CACHE_STORE.prune(open_pr_numbers)
