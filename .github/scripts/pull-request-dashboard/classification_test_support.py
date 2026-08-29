"""Test doubles for classification execution."""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable, Iterable

from classification_execution import (
    ClassificationCache,
    ClassificationExecutionRequest,
    ModelRunRequest,
    ReviewerFeedbackClassificationRequest,
)
from classification_policy import (
    ClassificationDiscussion,
    ClassificationResult,
    DiscussionClassifications,
    RawModelResponse,
)


def raw_response(
    *items: dict,
    returncode: int = 0,
    stderr: str = "",
) -> RawModelResponse:
    return RawModelResponse(
        returncode,
        json.dumps({"items": list(items)}),
        stderr,
    )


class FakeModelRunner:
    def __init__(
        self,
        responses: Iterable[RawModelResponse | Exception] = (),
        *,
        responder: Callable[[ModelRunRequest], RawModelResponse] | None = None,
    ) -> None:
        self.responses = list(responses)
        self.responder = responder
        self.requests: list[ModelRunRequest] = []

    def run(self, request: ModelRunRequest) -> RawModelResponse:
        self.requests.append(request)
        if self.responder is not None:
            return self.responder(request)
        if not self.responses:
            raise AssertionError("model runner received an unexpected request")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class MemoryClassificationCacheStore:
    def __init__(
        self,
        entries: dict[int, ClassificationCache] | None = None,
    ) -> None:
        self.entries = copy.deepcopy(entries or {})
        self.loads: list[int] = []
        self.writes: list[tuple[int, ClassificationCache]] = []
        self.prunes: list[set[int]] = []

    def load(self, pr_number: int) -> ClassificationCache:
        self.loads.append(pr_number)
        return copy.deepcopy(self.entries.get(pr_number, {}))

    def write(self, pr_number: int, cache: ClassificationCache) -> None:
        saved = copy.deepcopy(dict(cache))
        self.entries[pr_number] = saved
        self.writes.append((pr_number, saved))

    def prune(self, open_pr_numbers: set[int]) -> None:
        self.prunes.append(set(open_pr_numbers))
        self.entries = {
            pr_number: cache
            for pr_number, cache in self.entries.items()
            if pr_number in open_pr_numbers
        }


class FakeClassificationOperation:
    def __init__(
        self,
        result: DiscussionClassifications | None = None,
        *,
        reviewer_feedback_result: tuple[ClassificationResult, ...] = (),
        error: Exception | None = None,
    ) -> None:
        self.result = result or DiscussionClassifications.empty()
        self.reviewer_feedback_result = tuple(reviewer_feedback_result)
        self.error = error
        self.requests: list[ClassificationExecutionRequest] = []
        self.reviewer_feedback_requests: list[
            ReviewerFeedbackClassificationRequest
        ] = []

    def classify(
        self,
        request: ClassificationExecutionRequest,
    ) -> DiscussionClassifications:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.result

    def classify_reviewer_feedback(
        self,
        request: ReviewerFeedbackClassificationRequest,
    ) -> tuple[ClassificationResult, ...]:
        self.reviewer_feedback_requests.append(request)
        if self.error is not None:
            raise self.error
        return self.reviewer_feedback_result


def prompt_items(request: ModelRunRequest) -> list[dict]:
    match = re.search(
        r"---BEGIN [A-Z -]+---\n(.*?)\n---END [A-Z -]+---",
        request.prompt,
        re.DOTALL,
    )
    if match is None:
        raise AssertionError("model request has no prompt item block")
    items = json.loads(match.group(1))
    if not isinstance(items, list):
        raise AssertionError("model prompt items must be a list")
    return items


def successful_response(
    request: ModelRunRequest,
    *,
    reviewer_feedback: str = "no_author_action",
    praise: str = "not_praise",
    author_reply: str = "complete",
    author_comment_action: str = "none",
) -> RawModelResponse:
    items = prompt_items(request)
    if "---BEGIN AUTHOR FOLLOW-UPS---" in request.prompt:
        return raw_response(*[
            {
                "discussion_id": item["discussion_id"],
                "feedback_outcomes": [
                    {
                        "feedback_key": feedback["feedback_key"],
                        "discussion_action": author_comment_action,
                        "reason": "Test author-comment outcome.",
                    }
                    for feedback in item.get("candidate_feedback", [])
                ],
            }
            for item in items
        ])
    if "---BEGIN REVIEWER FEEDBACK---" in request.prompt:
        verdict = reviewer_feedback
    elif "---BEGIN AUTHOR COMMENTS---" in request.prompt:
        verdict = author_reply
    elif "---BEGIN COMMENTS---" in request.prompt:
        verdict = praise
    else:
        raise AssertionError("unknown model prompt contract")
    return raw_response(*[
        {
            "discussion_id": item["discussion_id"],
            "verdict": verdict,
            "reason": "Test verdict.",
        }
        for item in items
    ])


def typed_discussions(
    records: Iterable[dict],
) -> tuple[ClassificationDiscussion, ...]:
    return tuple(
        ClassificationDiscussion.from_record(record)
        for record in records
    )
