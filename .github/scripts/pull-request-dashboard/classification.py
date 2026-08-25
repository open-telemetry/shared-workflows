"""Operational adapter for pull request discussion classification."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any

from classification_policy import (
    AUTHOR_REPLY_PROMPT_TEMPLATE,
    AUTHOR_REPLY_VERDICTS,
    MAX_PROMPT_CHARS,
    PRAISE_PROMPT_TEMPLATE,
    PRAISE_VERDICTS,
    REVIEWER_FEEDBACK_PROMPT_TEMPLATE,
    REVIEWER_FEEDBACK_VERDICTS,
    TOP_LEVEL_AUTHOR_COMMENT_BATCH_PROMPT_TEMPLATE,
    TOP_LEVEL_CLASSIFICATION_BATCH_SIZE,
    ActionDecision,
    AuthorCommentDecision,
    AuthorCommentModelRequest,
    ClassificationDecision,
    ClassificationDeferred,
    ClassificationDiagnostics,
    ClassificationDiscussion,
    ClassificationFailure,
    ClassificationResult,
    ClassificationSuccess,
    DiscussionAction,
    DiscussionClassifications,
    RawModelResponse,
    VerdictContract,
    VerdictModelRequest,
    author_comment_prompt_input,
    cached_classification_record,
    classification_result_from_cache_record,
    combine_author_comment_results,
    discussion_cache_key,
    extract_json_object,
    fallback_author_comment_decision,
    fallback_verdict_decision,
    is_automation_command_comment,
    is_conflict_resolution_comment,
    leading_mentions,
    make_author_comment_request,
    map_verdict_result,
    normalize_discussion_action,
    prepare_author_comment_requests,
    prepare_praise_candidates,
    prepare_verdict_requests,
    praise_prompt_input,
    render_prompt_inputs,
    resolve_author_comment_response,
    resolve_review_thread_policy,
    resolve_verdict_response,
    reviewer_feedback_prompt_input,
    reviewer_feedback_prompt_item,
    with_result_metadata,
)


LLM_DISCUSSION_TIMEOUT_SECONDS = 180
CLASSIFICATION_CACHE_DIR = Path(
    os.environ.get(
        "PR_DASHBOARD_CLASSIFICATION_CACHE_DIR",
        Path(__file__).resolve().parent / ".cache" / "classifications",
    )
)
MAX_TOP_LEVEL_CLASSIFICATIONS_PER_PR = 200
MAX_TOP_LEVEL_AUTHOR_COMMENT_MODEL_CALLS_PER_PR = 20


def print_copilot_otel_file(path: Path) -> None:
    if not path.exists():
        return
    try:
        contents = path.read_text(encoding="utf-8").strip()
    except OSError as error:
        print(
            f"  warning: failed to read Copilot OTel output {path}: {error!r}",
            file=sys.stderr,
        )
        return
    if contents:
        print(
            "--- BEGIN COPILOT OTEL JSONL ---\n"
            f"{contents}\n"
            "--- END COPILOT OTEL JSONL ---",
            file=sys.stderr,
        )


def run_copilot(prompt: str, model: str) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory(prefix="copilot-otel-") as otel_dir:
        otel_path = Path(otel_dir) / "copilot-otel.jsonl"
        env = os.environ.copy()
        env["COPILOT_OTEL_FILE_EXPORTER_PATH"] = str(otel_path)
        env.setdefault("COPILOT_OTEL_EXPORTER_TYPE", "file")
        proc = subprocess.run(
            ["copilot", "-p", prompt, "--model", model, "--silent"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=LLM_DISCUSSION_TIMEOUT_SECONDS,
            env=env,
        )
        print_copilot_otel_file(otel_path)
    return proc


def _policy_discussions(
    discussions: list[dict[str, Any]],
) -> tuple[ClassificationDiscussion, ...]:
    return tuple(
        ClassificationDiscussion.from_record(discussion)
        for discussion in discussions
    )


def _raw_model_response(
    proc: subprocess.CompletedProcess[str],
) -> RawModelResponse:
    return RawModelResponse(
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )


def top_level_reviewer_feedback_prompt_input(
    discussion: dict[str, Any],
) -> dict[str, Any]:
    return reviewer_feedback_prompt_input(
        ClassificationDiscussion.from_record(discussion)
    )


def top_level_author_comment_prompt_input(
    discussion: dict[str, Any],
) -> dict[str, Any]:
    return author_comment_prompt_input(
        ClassificationDiscussion.from_record(discussion)
    )


def render_top_level_batch_prompt(
    discussions: list[dict[str, Any]],
    prompt_template: str,
    prompt_discussions: list[dict[str, Any]],
) -> str:
    if len(discussions) != len(prompt_discussions):
        raise ValueError("prompt inputs must match the discussions")
    return render_prompt_inputs(
        prompt_discussions,
        prompt_template,
        max_prompt_chars=MAX_PROMPT_CHARS,
    )


def top_level_author_comment_batch_prompt(
    discussions: list[dict[str, Any]],
) -> str:
    return make_author_comment_request(
        _policy_discussions(discussions),
        max_prompt_chars=MAX_PROMPT_CHARS,
    ).prompt


def author_comment_prompt_batches(
    discussions: list[dict[str, Any]] | tuple[ClassificationDiscussion, ...],
) -> tuple[AuthorCommentModelRequest, ...]:
    policy_discussions = (
        discussions
        if not discussions or isinstance(discussions[0], ClassificationDiscussion)
        else _policy_discussions(discussions)
    )
    return prepare_author_comment_requests(
        policy_discussions,
        batch_size=TOP_LEVEL_CLASSIFICATION_BATCH_SIZE,
        max_prompt_chars=MAX_PROMPT_CHARS,
    )


def verdict_prompt_batches(
    discussions: tuple[ClassificationDiscussion, ...],
    contract: VerdictContract,
) -> tuple[VerdictModelRequest, ...]:
    return prepare_verdict_requests(
        discussions,
        contract,
        batch_size=TOP_LEVEL_CLASSIFICATION_BATCH_SIZE,
        max_prompt_chars=MAX_PROMPT_CHARS,
    )


def _run_verdict_request(
    request: VerdictModelRequest,
    model: str,
) -> tuple[ClassificationResult, ...]:
    proc = run_copilot(request.prompt, model)
    return resolve_verdict_response(request, _raw_model_response(proc))


def run_llm_for_verdict_batch(
    discussions: list[dict[str, Any]],
    model: str,
    prompt: str,
    verdicts: tuple[str, str],
) -> list[ClassificationResult]:
    contract = {
        REVIEWER_FEEDBACK_VERDICTS: VerdictContract.REVIEWER_FEEDBACK,
        AUTHOR_REPLY_VERDICTS: VerdictContract.AUTHOR_REPLY,
        PRAISE_VERDICTS: VerdictContract.PRAISE,
    }.get(tuple(verdicts))
    if contract is None:
        raise ValueError(f"unknown verdict contract: {verdicts!r}")
    request = VerdictModelRequest(
        _policy_discussions(discussions),
        contract,
        prompt,
    )
    return list(_run_verdict_request(request, model))


def _run_author_comment_request(
    request: AuthorCommentModelRequest,
    model: str,
) -> tuple[ClassificationResult, ...]:
    proc = run_copilot(request.prompt, model)
    return resolve_author_comment_response(request, _raw_model_response(proc))


def run_llm_for_top_level_author_comment_batch(
    discussions: list[dict[str, Any]],
    model: str,
) -> list[ClassificationResult]:
    policy_discussions = _policy_discussions(discussions)
    partial_results: dict[str, list[ClassificationResult]] = {
        discussion.identity.discussion_id: []
        for discussion in policy_discussions
    }
    for request in author_comment_prompt_batches(policy_discussions):
        for result in _run_author_comment_request(request, model):
            partial_results[result.identity.discussion_id].append(result)
    return list(
        combine_author_comment_results(
            policy_discussions,
            partial_results,
        )
    )


def load_classification_cache(
    pr_number: int,
) -> dict[str, dict[str, Any]]:
    path = CLASSIFICATION_CACHE_DIR / f"{pr_number}.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(
            f"  warning: ignoring unreadable classification cache "
            f"{path}: {error!r}",
            file=sys.stderr,
        )
        return {}
    return data if isinstance(data, dict) else {}


def save_classification_cache(
    pr_number: int,
    cache: dict[str, dict[str, Any]],
) -> None:
    CLASSIFICATION_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CLASSIFICATION_CACHE_DIR / f"{pr_number}.json"
    path.write_text(
        json.dumps(cache, sort_keys=True, indent=2),
        encoding="utf-8",
    )


def prune_classification_cache(open_pr_numbers: set[int]) -> None:
    if not CLASSIFICATION_CACHE_DIR.exists():
        return
    for path in CLASSIFICATION_CACHE_DIR.glob("*.json"):
        if not path.stem.isdigit():
            continue
        if int(path.stem) not in open_pr_numbers:
            path.unlink()


def _cached_classification(
    discussion: ClassificationDiscussion,
    model: str,
    cache_in: dict[str, dict[str, Any]],
    cache_out: dict[str, dict[str, Any]],
    *,
    verdict_contract: VerdictContract | None = None,
    author_comment: bool = False,
) -> tuple[str, ClassificationResult | None]:
    key = discussion_cache_key(
        discussion,
        model,
        verdict_contract=verdict_contract,
        author_comment=author_comment,
    )
    cached = cache_in.get(key)
    if not isinstance(cached, dict):
        return key, None
    result = classification_result_from_cache_record(
        cached,
        discussion,
        verdict_contract=verdict_contract,
        author_comment=author_comment,
    )
    cache_out[key] = cached_classification_record(result)
    return key, result


def _fallback_decision(
    reason: str,
    contract: VerdictContract | None,
    *,
    author_comment: bool,
) -> ClassificationDecision:
    if author_comment:
        return fallback_author_comment_decision(reason)
    if contract is None:
        raise ValueError("classification requires a contract")
    return fallback_verdict_decision(reason, contract)


def _run_classification_batch(
    discussions: tuple[ClassificationDiscussion, ...],
    model: str,
    contract: VerdictContract | None,
    *,
    author_comment: bool,
) -> tuple[ClassificationResult, ...]:
    if author_comment:
        partial_results: dict[str, list[ClassificationResult]] = {
            discussion.identity.discussion_id: []
            for discussion in discussions
        }
        for request in prepare_author_comment_requests(
            discussions,
            batch_size=TOP_LEVEL_CLASSIFICATION_BATCH_SIZE,
            max_prompt_chars=MAX_PROMPT_CHARS,
        ):
            for result in _run_author_comment_request(request, model):
                partial_results[result.identity.discussion_id].append(result)
        return combine_author_comment_results(
            discussions,
            partial_results,
        )
    if contract is None:
        raise ValueError("classification requires a verdict contract")
    return tuple(
        result
        for request in verdict_prompt_batches(discussions, contract)
        for result in _run_verdict_request(request, model)
    )


def _classification_limit_result(
    discussion: ClassificationDiscussion,
    contract: VerdictContract | None,
    *,
    author_comment: bool,
    deferrable: bool,
) -> ClassificationResult:
    reason = (
        "Deferred by per-PR classification limit"
        if deferrable
        else "Exceeded per-PR classification limit"
    )
    decision = _fallback_decision(
        reason,
        contract,
        author_comment=author_comment,
    )
    if deferrable:
        return ClassificationDeferred(discussion.identity, decision)
    return ClassificationFailure(
        discussion.identity,
        decision,
        ClassificationDiagnostics(error=reason),
    )


def _author_comment_budget_size(
    uncached: list[tuple[ClassificationDiscussion, str]],
) -> int:
    discussions = tuple(discussion for discussion, _key in uncached)
    try:
        requests = author_comment_prompt_batches(discussions)
    except ValueError:
        return len(uncached)
    if len(requests) <= MAX_TOP_LEVEL_AUTHOR_COMMENT_MODEL_CALLS_PER_PR:
        return len(uncached)
    overflow_ids = {
        discussion.identity.discussion_id
        for request in requests[
            MAX_TOP_LEVEL_AUTHOR_COMMENT_MODEL_CALLS_PER_PR:
        ]
        for discussion in request.discussions
    }
    return next(
        (
            index
            for index, (discussion, _key) in enumerate(uncached)
            if discussion.identity.discussion_id in overflow_ids
        ),
        len(uncached),
    )


def _classify_items(
    number: int,
    discussions: tuple[ClassificationDiscussion, ...],
    model: str,
    cache_in: dict[str, dict[str, Any]],
    cache_out: dict[str, dict[str, Any]],
    *,
    contract: VerdictContract | None = None,
    author_comment: bool = False,
    deferrable: bool = False,
    warning_label: str,
) -> dict[str, ClassificationResult]:
    classifications_by_id: dict[str, ClassificationResult] = {}
    uncached: list[tuple[ClassificationDiscussion, str]] = []
    for discussion in discussions:
        key, cached = _cached_classification(
            discussion,
            model,
            cache_in,
            cache_out,
            verdict_contract=contract,
            author_comment=author_comment,
        )
        if cached is not None:
            classifications_by_id[discussion.identity.discussion_id] = cached
            continue
        if len(uncached) < MAX_TOP_LEVEL_CLASSIFICATIONS_PER_PR:
            uncached.append((discussion, key))
            continue
        classifications_by_id[discussion.identity.discussion_id] = (
            _classification_limit_result(
                discussion,
                contract,
                author_comment=author_comment,
                deferrable=deferrable,
            )
        )

    if author_comment:
        budget_size = _author_comment_budget_size(uncached)
        for discussion, _key in uncached[budget_size:]:
            classifications_by_id[discussion.identity.discussion_id] = (
                _classification_limit_result(
                    discussion,
                    contract,
                    author_comment=True,
                    deferrable=deferrable,
                )
            )
        uncached = uncached[:budget_size]

    for offset in range(
        0,
        len(uncached),
        TOP_LEVEL_CLASSIFICATION_BATCH_SIZE,
    ):
        batch = uncached[
            offset:offset + TOP_LEVEL_CLASSIFICATION_BATCH_SIZE
        ]
        batch_discussions = tuple(
            discussion for discussion, _key in batch
        )
        try:
            results = _run_classification_batch(
                batch_discussions,
                model,
                contract,
                author_comment=author_comment,
            )
        except subprocess.TimeoutExpired as error:
            results = tuple(
                ClassificationFailure(
                    discussion.identity,
                    _fallback_decision(
                        "LLM timeout",
                        contract,
                        author_comment=author_comment,
                    ),
                    ClassificationDiagnostics(
                        error=(
                            "Copilot CLI timed out after "
                            f"{LLM_DISCUSSION_TIMEOUT_SECONDS}s"
                        ),
                        response_text=(
                            error.stdout
                            if isinstance(error.stdout, str)
                            else ""
                        ),
                        stderr=(
                            error.stderr
                            if isinstance(error.stderr, str)
                            else ""
                        ),
                    ),
                    cli_call=(index == 0),
                )
                for index, discussion in enumerate(batch_discussions)
            )
        except Exception as error:
            print(
                f"  warning: {warning_label} batch on PR "
                f"#{number} failed to classify:",
                file=sys.stderr,
            )
            traceback.print_exc()
            results = tuple(
                ClassificationFailure(
                    discussion.identity,
                    _fallback_decision(
                        f"LLM failed: {error!r}",
                        contract,
                        author_comment=author_comment,
                    ),
                    ClassificationDiagnostics(
                        error=f"LLM failed: {error!r}",
                    ),
                    cli_call=(index == 0),
                )
                for index, discussion in enumerate(batch_discussions)
            )
        for result, (_discussion, key) in zip(
            results,
            batch,
            strict=True,
        ):
            classifications_by_id[result.identity.discussion_id] = result
            if isinstance(result, ClassificationSuccess):
                cache_out[key] = cached_classification_record(result)

    if contract is None:
        return classifications_by_id
    return {
        discussion_id: map_verdict_result(result, contract)
        for discussion_id, result in classifications_by_id.items()
    }


def classify_praise(
    number: int,
    discussions: list[dict[str, Any]],
    model: str,
    cache_in: dict[str, dict[str, Any]],
    cache_out: dict[str, dict[str, Any]],
) -> dict[str, ClassificationResult]:
    return _classify_items(
        number,
        _policy_discussions(discussions),
        model,
        cache_in,
        cache_out,
        contract=VerdictContract.PRAISE,
        warning_label="praise",
    )


def classify_review_threads(
    number: int,
    discussions: list[dict[str, Any]],
    model: str,
    cache_in: dict[str, dict[str, Any]],
    cache_out: dict[str, dict[str, Any]],
) -> dict[str, ClassificationResult]:
    policy_discussions = _policy_discussions(discussions)
    praise_candidates = prepare_praise_candidates(policy_discussions)
    praise = _classify_items(
        number,
        praise_candidates,
        model,
        cache_in,
        cache_out,
        contract=VerdictContract.PRAISE,
        warning_label="praise",
    )
    plan = resolve_review_thread_policy(
        policy_discussions,
        praise,
    )
    replies = _classify_items(
        number,
        plan.author_replies,
        model,
        cache_in,
        cache_out,
        contract=VerdictContract.AUTHOR_REPLY,
        warning_label="author_reply",
    )
    ignored = {
        discussion_id
        for discussion_id, result in praise.items()
        if isinstance(result, ClassificationSuccess)
        and isinstance(result.decision, ActionDecision)
        and result.decision.action is DiscussionAction.NONE
    }
    by_id = {
        result.identity.discussion_id: result
        for result in plan.resolved
    }
    for discussion in plan.author_replies:
        discussion_id = discussion.identity.discussion_id
        result = replies[discussion_id]
        by_id[discussion_id] = with_result_metadata(
            result,
            since=(
                discussion.comments[-1].timestamp
                if discussion.comments
                else ""
            ),
            ignored_last_comment=(discussion_id in ignored),
        )
    return by_id


def classify_reviewer_feedback(
    number: int,
    discussions: list[dict[str, Any]],
    model: str,
    cache_in: dict[str, dict[str, Any]],
    cache_out: dict[str, dict[str, Any]],
) -> dict[str, ClassificationResult]:
    return _classify_items(
        number,
        _policy_discussions(discussions),
        model,
        cache_in,
        cache_out,
        contract=VerdictContract.REVIEWER_FEEDBACK,
        warning_label="reviewer_feedback",
    )


def classify_author_replies(
    number: int,
    discussions: list[dict[str, Any]],
    model: str,
    cache_in: dict[str, dict[str, Any]],
    cache_out: dict[str, dict[str, Any]],
) -> dict[str, ClassificationResult]:
    return _classify_items(
        number,
        _policy_discussions(discussions),
        model,
        cache_in,
        cache_out,
        contract=VerdictContract.AUTHOR_REPLY,
        warning_label="author_reply",
    )


def classify_top_level_author_comments(
    number: int,
    discussions: list[dict[str, Any]],
    model: str,
    cache_in: dict[str, dict[str, Any]],
    cache_out: dict[str, dict[str, Any]],
) -> dict[str, ClassificationResult]:
    return _classify_items(
        number,
        _policy_discussions(discussions),
        model,
        cache_in,
        cache_out,
        author_comment=True,
        deferrable=True,
        warning_label="top_level_author_comment",
    )


def classify_discussion_domains(
    number: int,
    review_threads: list[dict[str, Any]],
    top_level_items: list[dict[str, Any]],
    top_level_author_comment_items: list[dict[str, Any]],
    model: str,
) -> DiscussionClassifications:
    cache_in = load_classification_cache(number)
    cache_out: dict[str, dict[str, Any]] = {}
    review_thread_classifications = classify_review_threads(
        number,
        review_threads,
        model,
        cache_in,
        cache_out,
    )
    top_level_classifications = classify_reviewer_feedback(
        number,
        top_level_items,
        model,
        cache_in,
        cache_out,
    )
    top_level_author_comment_classifications = (
        classify_top_level_author_comments(
            number,
            top_level_author_comment_items,
            model,
            cache_in,
            cache_out,
        )
    )
    save_classification_cache(number, cache_out)
    return DiscussionClassifications(
        tuple(
            review_thread_classifications[thread["discussion_id"]]
            for thread in review_threads
        ),
        tuple(
            top_level_classifications[item["discussion_id"]]
            for item in top_level_items
        ),
        tuple(
            top_level_author_comment_classifications[
                item["discussion_id"]
            ]
            for item in top_level_author_comment_items
        ),
    )
