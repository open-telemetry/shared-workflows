"""Operational execution for pull request discussion classification."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import traceback
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, Mapping, Protocol, Sequence

from copilot import CopilotClient

from classification_policy import (
    CLASSIFIER_SYSTEM_PROMPT,
    MAX_PROMPT_CHARS,
    TOP_LEVEL_CLASSIFICATION_BATCH_SIZE,
    ActionDecision,
    AuthorCommentExecutionBatch,
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
    cached_classification_record,
    classification_result_from_cache_record,
    combine_author_comment_results,
    discussion_cache_key,
    fallback_author_comment_decision,
    fallback_verdict_decision,
    map_verdict_result,
    prepare_author_comment_discussion,
    prepare_author_comment_requests,
    prepare_praise_candidates,
    prepare_verdict_requests,
    resolve_author_comment_response,
    resolve_review_thread_policy,
    resolve_verdict_response,
    select_author_comment_requests,
    with_result_metadata,
)


LLM_DISCUSSION_TIMEOUT_SECONDS = 180
COPILOT_CLIENT_STOP_TIMEOUT_SECONDS = 10
CLASSIFICATION_CACHE_DIR = Path(
    os.environ.get(
        "PR_DASHBOARD_CLASSIFICATION_CACHE_DIR",
        Path(__file__).resolve().parent / ".cache" / "classifications",
    )
)
MAX_TOP_LEVEL_CLASSIFICATIONS_PER_PR = 200
MAX_TOP_LEVEL_AUTHOR_COMMENT_MODEL_CALLS_PER_PR = 20
INVALID_CLASSIFICATION_ATTEMPTS = 2


@dataclass(frozen=True)
class ModelRunRequest:
    prompt: str
    model: str


class ModelRunner(Protocol):
    async def run(self, request: ModelRunRequest) -> RawModelResponse:
        """Execute one rendered model request."""


def _print_copilot_otel_file(path: Path) -> None:
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


class CopilotSdkModelRunner:
    def __init__(
        self,
        timeout_seconds: float = LLM_DISCUSSION_TIMEOUT_SECONDS,
        stop_timeout_seconds: float = COPILOT_CLIENT_STOP_TIMEOUT_SECONDS,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.stop_timeout_seconds = stop_timeout_seconds
        self._directory: tempfile.TemporaryDirectory[str] | None = None
        self._client: CopilotClient | None = None

    async def __aenter__(self) -> CopilotSdkModelRunner:
        if self._directory is not None:
            raise RuntimeError("Copilot runner is already open")
        self._directory = tempfile.TemporaryDirectory(prefix="copilot-classifier-")
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        try:
            if self._client is not None:
                try:
                    try:
                        await asyncio.wait_for(
                            self._client.stop(), timeout=self.stop_timeout_seconds
                        )
                    except TimeoutError:
                        await self._client.force_stop()
                    except BaseException:
                        try:
                            await self._client.force_stop()
                        except BaseException as force_stop_error:
                            print(
                                "  warning: failed to force-stop Copilot client: "
                                f"{force_stop_error!r}",
                                file=sys.stderr,
                            )
                        raise
                except BaseException as cleanup_error:
                    if exc_value is None:
                        raise
                    print(
                        f"  warning: failed to stop Copilot client: {cleanup_error!r}",
                        file=sys.stderr,
                    )
        finally:
            self._client = None
            if self._directory is not None:
                _print_copilot_otel_file(
                    Path(self._directory.name) / "copilot-otel.jsonl"
                )
                self._directory.cleanup()
                self._directory = None

    def _get_client(self) -> CopilotClient:
        if self._directory is None:
            raise RuntimeError("Copilot runner must be used as an async context manager")
        if self._client is not None:
            return self._client
        actions = os.environ.get("GITHUB_ACTIONS") == "true"
        token = (
            os.environ.get("COPILOT_GITHUB_TOKEN")
            or (None if actions else os.environ.get("GH_TOKEN"))
            or os.environ.get("GITHUB_TOKEN")
        )
        if not token:
            raise RuntimeError("Copilot classification requires a GitHub token")
        directory = self._directory.name
        env = {
            key: os.environ[key]
            for key in (
                "COMSPEC", "LD_LIBRARY_PATH", "PATH", "PATHEXT",
                "SSL_CERT_DIR", "SSL_CERT_FILE", "SYSTEMROOT", "TEMP", "TMP", "WINDIR",
            )
            if key in os.environ
        }
        env.update(
            CI="true",
            HOME=directory,
            COPILOT_GITHUB_TOKEN=token,
            COPILOT_OTEL_ENABLED="true",
            COPILOT_OTEL_EXPORTER_TYPE="file",
            COPILOT_OTEL_FILE_EXPORTER_PATH=str(Path(directory) / "copilot-otel.jsonl"),
        )
        if actions:
            env.update(GITHUB_ACTIONS="true", GITHUB_TOKEN=token)
        self._client = CopilotClient(
            mode="empty",
            working_directory=directory,
            base_directory=directory,
            env=env,
            github_token=None if actions else token,
            use_logged_in_user=actions,
        )
        return self._client

    async def run(self, request: ModelRunRequest) -> RawModelResponse:
        session = None
        try:
            async with asyncio.timeout(self.timeout_seconds):
                session = await self._get_client().create_session(
                    model=request.model,
                    available_tools=[],
                    system_message={"mode": "replace", "content": CLASSIFIER_SYSTEM_PROMPT},
                    enable_session_telemetry=True,
                )
                response = await session.send_and_wait(
                    request.prompt,
                    timeout=self.timeout_seconds,
                )
                if (
                    response is None
                    or not response.data.content
                    or not response.data.content.strip()
                ):
                    # Completed calls without output use the invalid-response retry policy.
                    message = "Copilot SDK returned an empty classification"
                    print(f"  warning: {message}", file=sys.stderr)
                    return RawModelResponse(0, "", message)
                return RawModelResponse(0, response.data.content, "")
        except (TimeoutError, asyncio.CancelledError) as error:
            if session is not None:
                try:
                    async with asyncio.timeout(10):
                        await session.abort()
                except Exception as cleanup_error:
                    print(
                        f"  warning: failed to abort Copilot session: {cleanup_error!r}",
                        file=sys.stderr,
                    )
            if isinstance(error, TimeoutError):
                raise TimeoutError(
                    f"Copilot SDK timed out after {self.timeout_seconds}s"
                ) from error
            raise
        finally:
            if session is not None:
                active_exception = sys.exception()
                try:
                    async with asyncio.timeout(10):
                        await session.disconnect()
                except BaseException as cleanup_error:
                    if active_exception is not None:
                        print(
                            "  warning: failed to disconnect Copilot session: "
                            f"{cleanup_error!r}",
                            file=sys.stderr,
                        )
                    elif isinstance(cleanup_error, TimeoutError):
                        raise TimeoutError(
                            "Copilot SDK session cleanup timed out after 10s"
                        ) from cleanup_error
                    else:
                        raise


ClassificationCache = dict[str, Any]


@dataclass
class AuthorCommentCallBudget:
    remaining: int
    pending_initial_attempts: int

    def begin_request(self) -> None:
        self.pending_initial_attempts -= 1
        if self.remaining <= 0:
            raise AssertionError("author comment model call budget exhausted")
        self.remaining -= 1

    def begin_retry(self) -> bool:
        if self.remaining <= self.pending_initial_attempts:
            return False
        self.remaining -= 1
        return True


class ClassificationCacheStore(Protocol):
    def load(self, pr_number: int) -> ClassificationCache:
        """Load and validate one pull request's cache."""

    def write(self, pr_number: int, cache: Mapping[str, Any]) -> None:
        """Replace one pull request's cache."""

    def prune(self, open_pr_numbers: set[int]) -> None:
        """Remove caches for pull requests that are no longer open."""


@dataclass(frozen=True)
class FileClassificationCacheStore:
    directory: Path

    def _path(self, pr_number: int) -> Path:
        return self.directory / f"{pr_number}.json"

    def load(self, pr_number: int) -> ClassificationCache:
        path = self._path(pr_number)
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

    def write(self, pr_number: int, cache: Mapping[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path(pr_number)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.directory,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as output:
                temporary_path = Path(output.name)
                json.dump(dict(cache), output, sort_keys=True, indent=2)
            temporary_path.replace(path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def prune(self, open_pr_numbers: set[int]) -> None:
        if not self.directory.exists():
            return
        for path in self.directory.glob("*.json"):
            if not path.stem.isdigit():
                continue
            if int(path.stem) not in open_pr_numbers:
                path.unlink()


@dataclass(frozen=True)
class ClassificationExecutionRequest:
    pr_number: int
    model: str
    review_threads: tuple[ClassificationDiscussion, ...] = ()
    top_level_items: tuple[ClassificationDiscussion, ...] = ()
    top_level_author_comments: tuple[ClassificationDiscussion, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "review_threads", tuple(self.review_threads))
        object.__setattr__(self, "top_level_items", tuple(self.top_level_items))
        object.__setattr__(
            self,
            "top_level_author_comments",
            tuple(self.top_level_author_comments),
        )


@dataclass(frozen=True)
class ReviewerFeedbackClassificationRequest:
    pr_number: int
    model: str
    discussions: tuple[ClassificationDiscussion, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "discussions", tuple(self.discussions))


class ClassificationOperation(Protocol):
    async def classify(
        self,
        request: ClassificationExecutionRequest,
    ) -> DiscussionClassifications:
        """Classify all prepared discussion domains for one pull request."""

    async def classify_reviewer_feedback(
        self,
        request: ReviewerFeedbackClassificationRequest,
    ) -> tuple[ClassificationResult, ...]:
        """Classify a subset of reviewer feedback without pruning the cache."""


@dataclass(frozen=True)
class ClassificationService:
    runner: ModelRunner
    cache_store: ClassificationCacheStore
    batch_size: int = TOP_LEVEL_CLASSIFICATION_BATCH_SIZE
    max_prompt_chars: int = MAX_PROMPT_CHARS
    max_classifications_per_pr: int = MAX_TOP_LEVEL_CLASSIFICATIONS_PER_PR
    max_author_comment_model_calls_per_pr: int = (
        MAX_TOP_LEVEL_AUTHOR_COMMENT_MODEL_CALLS_PER_PR
    )

    async def classify(
        self,
        request: ClassificationExecutionRequest,
    ) -> DiscussionClassifications:
        cache_in = self.cache_store.load(request.pr_number)
        cache_out: ClassificationCache = {}
        review_threads = await self._classify_review_threads(
            request.pr_number,
            request.review_threads,
            request.model,
            cache_in,
            cache_out,
        )
        top_level_items = await self._classify_items(
            request.pr_number,
            request.top_level_items,
            request.model,
            cache_in,
            cache_out,
            contract=VerdictContract.REVIEWER_FEEDBACK,
            warning_label="reviewer_feedback",
        )
        top_level_author_comments = await self._classify_items(
            request.pr_number,
            request.top_level_author_comments,
            request.model,
            cache_in,
            cache_out,
            author_comment=True,
            deferrable=True,
            warning_label="top_level_author_comment",
        )
        self.cache_store.write(request.pr_number, cache_out)
        return DiscussionClassifications(
            tuple(
                review_threads[discussion.identity.discussion_id]
                for discussion in request.review_threads
            ),
            tuple(
                top_level_items[discussion.identity.discussion_id]
                for discussion in request.top_level_items
            ),
            tuple(
                top_level_author_comments[discussion.identity.discussion_id]
                for discussion in request.top_level_author_comments
            ),
        )

    async def classify_reviewer_feedback(
        self,
        request: ReviewerFeedbackClassificationRequest,
    ) -> tuple[ClassificationResult, ...]:
        cache_in = self.cache_store.load(request.pr_number)
        cache_out: ClassificationCache = {}
        classifications = await self._classify_items(
            request.pr_number,
            request.discussions,
            request.model,
            cache_in,
            cache_out,
            contract=VerdictContract.REVIEWER_FEEDBACK,
            warning_label="reviewer_feedback",
        )
        merged_cache = {**cache_in, **cache_out}
        if merged_cache != cache_in:
            self.cache_store.write(request.pr_number, merged_cache)
        return tuple(
            classifications[discussion.identity.discussion_id]
            for discussion in request.discussions
        )

    def _cached_classification(
        self,
        discussion: ClassificationDiscussion,
        model: str,
        cache_in: Mapping[str, Any],
        cache_out: ClassificationCache,
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
        if result.deferred:
            return key, None
        cache_out[key] = cached_classification_record(result)
        return key, result

    @staticmethod
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

    def _classification_limit_result(
        self,
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
        decision = self._fallback_decision(
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

    def _author_comment_execution_batches(
        self,
        uncached: list[tuple[ClassificationDiscussion, str]],
    ) -> tuple[
        tuple[AuthorCommentExecutionBatch, ...],
        tuple[ClassificationDiscussion, ...],
    ] | None:
        try:
            plans = tuple(
                prepare_author_comment_discussion(
                    discussion,
                    max_prompt_chars=self.max_prompt_chars,
                )
                for discussion, _key in uncached
            )
            selection = select_author_comment_requests(
                plans,
                max_model_calls=(
                    self.max_author_comment_model_calls_per_pr
                ),
                classification_batch_size=self.batch_size,
                request_batch_size=self.batch_size,
                max_prompt_chars=self.max_prompt_chars,
            )
        except ValueError:
            return None
        return selection.batches, selection.deferred

    @staticmethod
    def _cache_classified(
        cache_out: ClassificationCache,
        key: str,
        result: ClassificationResult,
    ) -> None:
        if result.failed or result.deferred:
            return
        cache_out[key] = cached_classification_record(result)

    def _author_comment_requests(
        self,
        discussions: Sequence[ClassificationDiscussion],
    ) -> tuple[AuthorCommentModelRequest, ...]:
        return prepare_author_comment_requests(
            discussions,
            batch_size=self.batch_size,
            max_prompt_chars=self.max_prompt_chars,
        )

    def _verdict_requests(
        self,
        discussions: Sequence[ClassificationDiscussion],
        contract: VerdictContract,
    ) -> tuple[VerdictModelRequest, ...]:
        return prepare_verdict_requests(
            discussions,
            contract,
            batch_size=self.batch_size,
            max_prompt_chars=self.max_prompt_chars,
        )

    async def _run_verdict_request(
        self,
        request: VerdictModelRequest,
        model: str,
    ) -> tuple[ClassificationResult, ...]:
        results: tuple[ClassificationResult, ...] | None = None
        for attempt in range(INVALID_CLASSIFICATION_ATTEMPTS):
            try:
                response = await self.runner.run(ModelRunRequest(request.prompt, model))
            except Exception as error:
                if results is not None:
                    return self._merge_retry_results(
                        results,
                        self._request_failure_results(
                            request.discussions,
                            error,
                            request.contract,
                            author_comment=False,
                        ),
                    )
                raise
            attempt_results = resolve_verdict_response(request, response)
            results = self._merge_retry_results(results, attempt_results)
            if (
                attempt + 1 == INVALID_CLASSIFICATION_ATTEMPTS
                or not self._has_invalid_response(results)
            ):
                return results
        raise AssertionError("classification retry loop did not return")

    async def _run_author_comment_request(
        self,
        request: AuthorCommentModelRequest,
        model: str,
        call_budget: AuthorCommentCallBudget | None = None,
    ) -> tuple[ClassificationResult, ...]:
        results: tuple[ClassificationResult, ...] | None = None
        for attempt in range(INVALID_CLASSIFICATION_ATTEMPTS):
            if call_budget is not None:
                if attempt == 0:
                    call_budget.begin_request()
                elif not call_budget.begin_retry():
                    assert results is not None
                    return results
            try:
                response = await self.runner.run(ModelRunRequest(request.prompt, model))
            except Exception as error:
                if results is not None:
                    return self._merge_retry_results(
                        results,
                        self._request_failure_results(
                            request.discussions,
                            error,
                            None,
                            author_comment=True,
                        ),
                    )
                raise
            attempt_results = resolve_author_comment_response(request, response)
            results = self._merge_retry_results(results, attempt_results)
            if (
                attempt + 1 == INVALID_CLASSIFICATION_ATTEMPTS
                or not self._has_invalid_response(results)
            ):
                return results
        raise AssertionError("classification retry loop did not return")

    @staticmethod
    def _merge_retry_results(
        results: tuple[ClassificationResult, ...] | None,
        retry_results: tuple[ClassificationResult, ...],
    ) -> tuple[ClassificationResult, ...]:
        if results is None:
            return retry_results
        retry_by_id = {
            result.identity.discussion_id: result for result in retry_results
        }
        return tuple(
            retry_by_id[result.identity.discussion_id]
            if isinstance(result, ClassificationFailure)
            and result.diagnostics.invalid_response
            else result
            for result in results
        )

    @staticmethod
    def _has_invalid_response(
        results: Sequence[ClassificationResult],
    ) -> bool:
        return any(
            isinstance(result, ClassificationFailure)
            and result.diagnostics.invalid_response
            for result in results
        )

    async def _run_author_comment_batch(
        self,
        discussions: tuple[ClassificationDiscussion, ...],
        model: str,
        requests: tuple[AuthorCommentModelRequest, ...] | None = None,
        call_budget: AuthorCommentCallBudget | None = None,
    ) -> tuple[ClassificationResult, ...]:
        partial_results: dict[str, list[ClassificationResult]] = {
            discussion.identity.discussion_id: []
            for discussion in discussions
        }
        for request in (
            self._author_comment_requests(discussions)
            if requests is None
            else requests
        ):
            try:
                request_results = await self._run_author_comment_request(
                    request,
                    model,
                    call_budget,
                )
            except Exception as error:
                request_results = self._request_failure_results(
                    request.discussions,
                    error,
                    None,
                    author_comment=True,
                )
            for result in request_results:
                partial_results[result.identity.discussion_id].append(result)
        return combine_author_comment_results(discussions, partial_results)

    def _request_failure_results(
        self,
        discussions: Sequence[ClassificationDiscussion],
        error: Exception,
        contract: VerdictContract | None,
        *,
        author_comment: bool,
    ) -> tuple[ClassificationFailure, ...]:
        if isinstance(error, TimeoutError):
            reason = "LLM timeout"
            diagnostics = ClassificationDiagnostics(
                error=str(error),
            )
        else:
            reason = f"LLM failed: {error!r}"
            diagnostics = ClassificationDiagnostics(error=reason)
        return tuple(
            ClassificationFailure(
                discussion.identity,
                self._fallback_decision(
                    reason,
                    contract,
                    author_comment=author_comment,
                ),
                diagnostics,
                cli_call=(index == 0),
            )
            for index, discussion in enumerate(discussions)
        )

    async def _run_classification_batch(
        self,
        discussions: tuple[ClassificationDiscussion, ...],
        model: str,
        contract: VerdictContract | None,
        *,
        author_comment: bool,
        author_comment_requests: tuple[AuthorCommentModelRequest, ...] | None = None,
        author_comment_call_budget: AuthorCommentCallBudget | None = None,
    ) -> tuple[ClassificationResult, ...]:
        if author_comment:
            return await self._run_author_comment_batch(
                discussions,
                model,
                author_comment_requests,
                author_comment_call_budget,
            )
        if contract is None:
            raise ValueError("classification requires a verdict contract")
        results: list[ClassificationResult] = []
        for request in self._verdict_requests(discussions, contract):
            try:
                results.extend(await self._run_verdict_request(request, model))
            except Exception as error:
                results.extend(
                    self._request_failure_results(
                        request.discussions,
                        error,
                        contract,
                        author_comment=False,
                    )
                )
        return tuple(results)

    async def _classify_items(
        self,
        number: int,
        discussions: tuple[ClassificationDiscussion, ...],
        model: str,
        cache_in: Mapping[str, Any],
        cache_out: ClassificationCache,
        *,
        contract: VerdictContract | None = None,
        author_comment: bool = False,
        deferrable: bool = False,
        warning_label: str,
    ) -> dict[str, ClassificationResult]:
        classifications_by_id: dict[str, ClassificationResult] = {}
        uncached: list[tuple[ClassificationDiscussion, str]] = []
        for discussion in discussions:
            key, cached = self._cached_classification(
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
            if len(uncached) < self.max_classifications_per_pr:
                uncached.append((discussion, key))
                continue
            result = self._classification_limit_result(
                discussion,
                contract,
                author_comment=author_comment,
                deferrable=deferrable,
            )
            classifications_by_id[discussion.identity.discussion_id] = result

        prepared_requests_by_batch: list[
            tuple[
                list[tuple[ClassificationDiscussion, str]],
                tuple[AuthorCommentModelRequest, ...] | None,
            ]
        ] = []
        if author_comment:
            execution_plan = self._author_comment_execution_batches(uncached)
            if execution_plan is None:
                for offset in range(0, len(uncached), self.batch_size):
                    batch = uncached[offset:offset + self.batch_size]
                    try:
                        requests = self._author_comment_requests(
                            tuple(discussion for discussion, _key in batch)
                        )
                    except ValueError:
                        requests = None
                    prepared_requests_by_batch.append((batch, requests))
            else:
                execution_batches, deferred = execution_plan
                for discussion in deferred:
                    result = self._classification_limit_result(
                        discussion,
                        contract,
                        author_comment=True,
                        deferrable=deferrable,
                    )
                    classifications_by_id[
                        discussion.identity.discussion_id
                    ] = result
                key_by_discussion_id = {
                    discussion.identity.discussion_id: key
                    for discussion, key in uncached
                }
                prepared_requests_by_batch = [
                    (
                        [
                            (
                                discussion,
                                key_by_discussion_id[
                                    discussion.identity.discussion_id
                                ],
                            )
                            for discussion in batch.discussions
                        ],
                        batch.requests,
                    )
                    for batch in execution_batches
                ]
        else:
            prepared_requests_by_batch = [
                (
                    uncached[offset:offset + self.batch_size],
                    None,
                )
                for offset in range(0, len(uncached), self.batch_size)
            ]

        author_comment_call_budget = (
            AuthorCommentCallBudget(
                self.max_author_comment_model_calls_per_pr,
                sum(
                    len(requests)
                    for _batch, requests in prepared_requests_by_batch
                    if requests is not None
                ),
            )
            if author_comment
            else None
        )
        for batch, author_comment_requests in prepared_requests_by_batch:
            batch_discussions = tuple(discussion for discussion, _key in batch)
            try:
                results = await self._run_classification_batch(
                    batch_discussions,
                    model,
                    contract,
                    author_comment=author_comment,
                    author_comment_requests=author_comment_requests,
                    author_comment_call_budget=author_comment_call_budget,
                )
            except Exception as error:
                print(
                    f"  warning: {warning_label} batch on PR "
                    f"#{number} failed to classify:",
                    file=sys.stderr,
                )
                traceback.print_exc()
                results = self._request_failure_results(
                    batch_discussions,
                    error,
                    contract,
                    author_comment=author_comment,
                )
            for result, (_discussion, key) in zip(
                results,
                batch,
                strict=True,
            ):
                classifications_by_id[result.identity.discussion_id] = result
                self._cache_classified(cache_out, key, result)

        if contract is None:
            return classifications_by_id
        return {
            discussion_id: map_verdict_result(result, contract)
            for discussion_id, result in classifications_by_id.items()
        }

    async def _classify_review_threads(
        self,
        number: int,
        discussions: tuple[ClassificationDiscussion, ...],
        model: str,
        cache_in: Mapping[str, Any],
        cache_out: ClassificationCache,
    ) -> dict[str, ClassificationResult]:
        praise_candidates = prepare_praise_candidates(discussions)
        praise = await self._classify_items(
            number,
            praise_candidates,
            model,
            cache_in,
            cache_out,
            contract=VerdictContract.PRAISE,
            warning_label="praise",
        )
        plan = resolve_review_thread_policy(discussions, praise)
        replies = await self._classify_items(
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
                    discussion.selected_activity_timestamp
                    or (
                        discussion.comments[-1].timestamp
                        if discussion.comments
                        else ""
                    )
                ),
                ignored_last_comment=(discussion_id in ignored),
                ignored_comment_index=discussion.ignored_comment_index,
            )
        return by_id


DEFAULT_CLASSIFICATION_CACHE_STORE = FileClassificationCacheStore(
    CLASSIFICATION_CACHE_DIR
)
