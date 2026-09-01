from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from classification_execution import (
    LLM_DISCUSSION_TIMEOUT_SECONDS,
    ClassificationExecutionRequest,
    ClassificationService,
    CopilotCliModelRunner,
    FileClassificationCacheStore,
    ModelRunRequest,
    ReviewerFeedbackClassificationRequest,
)
from classification_policy import (
    ActionDecision,
    AuthorCommentDecision,
    AuthorCommentDiscussionPlan,
    AuthorCommentModelRequest,
    ClassificationDeferred,
    ClassificationDiscussion,
    ClassificationFailure,
    ClassificationSuccess,
    DiscussionAction,
    DiscussionKind,
    RawModelResponse,
    cached_classification_record,
    discussion_cache_key,
    make_author_comment_request,
)
from classification_test_support import (
    FakeModelRunner,
    MemoryClassificationCacheStore,
    prompt_items,
    successful_response,
    typed_discussions,
)


def discussion_record(
    discussion_id: str,
    kind: DiscussionKind = DiscussionKind.TOP_LEVEL_FEEDBACK,
    *,
    body: str = "Please update this.",
    actor_role: str = "reviewer",
    candidate_feedback: tuple[tuple[str, str], ...] = (),
) -> dict:
    return {
        "discussion_id": discussion_id,
        "discussion_kind": kind.value,
        "requester": "reviewer",
        "pr_author": "author",
        "comments": [
            {
                "timestamp": "2026-01-02T03:04:05Z",
                "actor_role": actor_role,
                "body": body,
            }
        ],
        "candidate_feedback": [
            {
                "discussion_id": feedback_id,
                "body": feedback_body,
            }
            for feedback_id, feedback_body in candidate_feedback
        ],
    }


def execution_request(
    *,
    number: int = 123,
    model: str = "model",
    review_threads: tuple[dict, ...] = (),
    top_level_items: tuple[dict, ...] = (),
    author_comments: tuple[dict, ...] = (),
) -> ClassificationExecutionRequest:
    return ClassificationExecutionRequest(
        number,
        model,
        typed_discussions(review_threads),
        typed_discussions(top_level_items),
        typed_discussions(author_comments),
    )


def author_comment_plan(
    discussion: ClassificationDiscussion,
    request_count: int,
) -> AuthorCommentDiscussionPlan:
    return AuthorCommentDiscussionPlan(
        discussion,
        tuple(
            AuthorCommentModelRequest(
                (discussion,),
                f"prompt-{discussion.identity.discussion_id}-{index}",
                (),
            )
            for index in range(request_count)
        ),
    )


def author_comment_result(
    discussion: ClassificationDiscussion,
    *,
    cli_call: bool,
) -> ClassificationSuccess:
    return ClassificationSuccess(
        discussion.identity,
        AuthorCommentDecision(),
        cli_call=cli_call,
    )


class CopilotCliModelRunnerTest(unittest.TestCase):
    @patch("classification_execution.subprocess.run")
    def test_command_environment_telemetry_and_tempfile_lifecycle(
        self,
        run,
    ) -> None:
        observed_path: Path | None = None

        def execute(command, **kwargs):
            nonlocal observed_path
            observed_path = Path(
                kwargs["env"]["COPILOT_OTEL_FILE_EXPORTER_PATH"]
            )
            observed_path.write_text('{"event":"done"}\n', encoding="utf-8")
            return subprocess.CompletedProcess(command, 7, "response", "diagnostic")

        run.side_effect = execute
        stderr = io.StringIO()
        with (
            patch.dict(os.environ, {}, clear=True),
            redirect_stderr(stderr),
        ):
            response = CopilotCliModelRunner().run(
                ModelRunRequest("prompt bytes", "gpt-test")
            )

        self.assertEqual(
            run.call_args.args[0],
            [
                "copilot",
                "-p",
                "prompt bytes",
                "--model",
                "gpt-test",
                "--silent",
            ],
        )
        kwargs = run.call_args.kwargs
        self.assertTrue(kwargs["capture_output"])
        self.assertTrue(kwargs["text"])
        self.assertEqual(kwargs["encoding"], "utf-8")
        self.assertEqual(kwargs["errors"], "replace")
        self.assertEqual(
            kwargs["timeout"],
            LLM_DISCUSSION_TIMEOUT_SECONDS,
        )
        self.assertEqual(
            kwargs["env"]["COPILOT_OTEL_EXPORTER_TYPE"],
            "file",
        )
        self.assertEqual(
            response,
            RawModelResponse(7, "response", "diagnostic"),
        )
        self.assertEqual(
            stderr.getvalue().count("--- BEGIN COPILOT OTEL JSONL ---"),
            1,
        )
        self.assertIn('{"event":"done"}', stderr.getvalue())
        assert observed_path is not None
        self.assertFalse(observed_path.parent.exists())

    @patch("classification_execution.subprocess.run")
    def test_existing_telemetry_exporter_type_is_preserved(self, run) -> None:
        run.return_value = subprocess.CompletedProcess([], 0, "", "")

        with patch.dict(
            os.environ,
            {"COPILOT_OTEL_EXPORTER_TYPE": "configured"},
            clear=True,
        ):
            CopilotCliModelRunner().run(ModelRunRequest("prompt", "model"))

        self.assertEqual(
            run.call_args.kwargs["env"]["COPILOT_OTEL_EXPORTER_TYPE"],
            "configured",
        )

    @patch("classification_execution.subprocess.run")
    def test_timeout_prints_telemetry_and_propagates_after_cleaning_the_tempfile(
        self,
        run,
    ) -> None:
        observed_path: Path | None = None
        expected_error = subprocess.TimeoutExpired(
            "copilot",
            LLM_DISCUSSION_TIMEOUT_SECONDS,
            output="partial",
            stderr="slow",
        )

        def timeout(_command, **kwargs):
            nonlocal observed_path
            observed_path = Path(
                kwargs["env"]["COPILOT_OTEL_FILE_EXPORTER_PATH"]
            )
            observed_path.write_text('{"event":"timeout"}\n', encoding="utf-8")
            raise expected_error

        run.side_effect = timeout
        stderr = io.StringIO()
        with (
            redirect_stderr(stderr),
            self.assertRaises(subprocess.TimeoutExpired) as raised,
        ):
            CopilotCliModelRunner().run(ModelRunRequest("prompt", "model"))

        self.assertIs(raised.exception, expected_error)
        self.assertIn('{"event":"timeout"}', stderr.getvalue())
        assert observed_path is not None
        self.assertFalse(observed_path.parent.exists())

    @patch("classification_execution.subprocess.run")
    def test_subprocess_error_prints_telemetry_without_masking_error(
        self,
        run,
    ) -> None:
        expected_error = OSError("failed to launch Copilot")

        def fail(_command, **kwargs):
            otel_path = Path(kwargs["env"]["COPILOT_OTEL_FILE_EXPORTER_PATH"])
            otel_path.write_text('{"event":"launch-error"}\n', encoding="utf-8")
            raise expected_error

        run.side_effect = fail
        stderr = io.StringIO()
        with (
            redirect_stderr(stderr),
            self.assertRaises(OSError) as raised,
        ):
            CopilotCliModelRunner().run(ModelRunRequest("prompt", "model"))

        self.assertIs(raised.exception, expected_error)
        self.assertIn('{"event":"launch-error"}', stderr.getvalue())


class FileClassificationCacheStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.directory = Path(temporary_directory.name)
        self.store = FileClassificationCacheStore(self.directory)

    def test_write_load_and_prune_preserve_the_existing_disk_shape(self) -> None:
        cache = {"b": {"failed": False}, "a": {"decision": {"verdict": "praise"}}}

        self.store.write(12, cache)

        path = self.directory / "12.json"
        self.assertEqual(
            path.read_text(encoding="utf-8"),
            json.dumps(cache, sort_keys=True, indent=2),
        )
        self.assertEqual(self.store.load(12), cache)
        (self.directory / "13.json").write_text("{}", encoding="utf-8")
        (self.directory / "notes.json").write_text("{}", encoding="utf-8")

        self.store.prune({13})

        self.assertFalse(path.exists())
        self.assertTrue((self.directory / "13.json").exists())
        self.assertTrue((self.directory / "notes.json").exists())

    def test_unreadable_and_non_object_cache_files_are_ignored(self) -> None:
        (self.directory / "1.json").write_text("{", encoding="utf-8")
        (self.directory / "2.json").write_text("[]", encoding="utf-8")
        stderr = io.StringIO()

        with redirect_stderr(stderr):
            self.assertEqual(self.store.load(1), {})

        self.assertIn("ignoring unreadable classification cache", stderr.getvalue())
        self.assertEqual(self.store.load(2), {})

    def test_interrupted_write_preserves_the_existing_cache(self) -> None:
        existing = {"existing": {"failed": False}}
        self.store.write(1, existing)

        def interrupt(_cache, output, **_kwargs) -> None:
            output.write("{")
            raise OSError("interrupted")

        with (
            patch("classification_execution.json.dump", side_effect=interrupt),
            self.assertRaisesRegex(OSError, "interrupted"),
        ):
            self.store.write(1, {"replacement": {"failed": False}})

        self.assertEqual(self.store.load(1), existing)
        self.assertEqual(list(self.directory.glob("*.tmp")), [])


class ClassificationServiceTest(unittest.TestCase):
    def test_partial_reviewer_feedback_uses_the_actionable_feedback_contract(
        self,
    ) -> None:
        runner = FakeModelRunner(responder=successful_response)
        cache = MemoryClassificationCacheStore()
        service = ClassificationService(runner, cache)
        discussion = ClassificationDiscussion.from_record(
            discussion_record(
                "thread",
                DiscussionKind.REVIEW_THREAD,
                body="For context, this API is deprecated.",
            )
        )

        result = service.classify_reviewer_feedback(
            ReviewerFeedbackClassificationRequest(
                123,
                "model",
                (discussion,),
            )
        )

        self.assertEqual(
            result[0].decision,
            ActionDecision(
                DiscussionAction.NONE,
                "Test verdict.",
            ),
        )
        self.assertIn("---BEGIN REVIEWER FEEDBACK---", runner.requests[0].prompt)

    def test_partial_reviewer_feedback_preserves_unrelated_cache_entries(
        self,
    ) -> None:
        cache = MemoryClassificationCacheStore({
            123: {"unrelated-key": {"discussion_id": "other", "failed": False}}
        })
        service = ClassificationService(
            FakeModelRunner(responder=successful_response),
            cache,
        )
        discussion = ClassificationDiscussion.from_record(
            discussion_record("feedback")
        )

        service.classify_reviewer_feedback(
            ReviewerFeedbackClassificationRequest(
                123,
                "model",
                (discussion,),
            )
        )

        self.assertIn("unrelated-key", cache.entries[123])
        self.assertEqual(2, len(cache.entries[123]))

    def test_empty_partial_reviewer_feedback_does_not_rewrite_cache(self) -> None:
        existing = {"existing-key": {"discussion_id": "existing"}}
        cache = MemoryClassificationCacheStore({123: existing})
        service = ClassificationService(FakeModelRunner(), cache)

        result = service.classify_reviewer_feedback(
            ReviewerFeedbackClassificationRequest(123, "model")
        )

        self.assertEqual(result, ())
        self.assertEqual(cache.writes, [])

    def test_cache_miss_hit_batching_order_and_cli_call_attribution(self) -> None:
        records = tuple(
            discussion_record(f"feedback-{index}")
            for index in range(12)
        )
        cache = MemoryClassificationCacheStore()
        first_runner = FakeModelRunner(responder=successful_response)
        first_service = ClassificationService(first_runner, cache)

        first = first_service.classify(
            execution_request(top_level_items=records)
        )

        self.assertEqual(len(first_runner.requests), 2)
        self.assertEqual(
            [len(prompt_items(request)) for request in first_runner.requests],
            [10, 2],
        )
        self.assertEqual(
            [
                result.identity.discussion_id
                for result in first.top_level_items
            ],
            [f"feedback-{index}" for index in range(12)],
        )
        self.assertEqual(
            [result.cli_call for result in first.top_level_items],
            [True] + [False] * 9 + [True, False],
        )
        self.assertEqual(len(cache.entries[123]), 12)

        cached_runner = FakeModelRunner()
        cached = ClassificationService(cached_runner, cache).classify(
            execution_request(top_level_items=records)
        )

        self.assertEqual(cached_runner.requests, [])
        self.assertEqual(
            [result.decision for result in cached.top_level_items],
            [result.decision for result in first.top_level_items],
        )
        self.assertEqual(
            [result.cli_call for result in cached.top_level_items],
            [False] * 12,
        )

    def test_author_comment_model_call_budget_defers_the_remainder(self) -> None:
        records = tuple(
            discussion_record(
                f"reply-{index}",
                DiscussionKind.TOP_LEVEL_AUTHOR_REPLY,
                actor_role="author",
                candidate_feedback=((f"feedback-{index}", "Please fix this."),),
            )
            for index in range(3)
        )
        runner = FakeModelRunner(responder=successful_response)
        cache = MemoryClassificationCacheStore()
        service = ClassificationService(
            runner,
            cache,
            batch_size=1,
            max_author_comment_model_calls_per_pr=2,
        )

        result = service.classify(
            execution_request(author_comments=records)
        ).top_level_author_comments

        self.assertEqual(len(runner.requests), 2)
        self.assertEqual(
            [item.deferred for item in result],
            [False, False, True],
        )
        self.assertIsInstance(result[2], ClassificationDeferred)
        assert isinstance(result[2].decision, AuthorCommentDecision)
        self.assertEqual(
            result[2].decision.reason,
            "Deferred by per-PR classification limit",
        )
        self.assertEqual(len(cache.entries[123]), 2)

    def test_author_comment_budget_skips_overflow_and_retries_it(self) -> None:
        request_counts = {
            "expensive-1": 10,
            "expensive-2": 10,
            "overflow-1": 3,
            "cheap": 2,
            "overflow-2": 2,
        }
        records = tuple(
            discussion_record(
                discussion_id,
                DiscussionKind.TOP_LEVEL_AUTHOR_REPLY,
                actor_role="author",
            )
            for discussion_id in request_counts
        )
        cache = MemoryClassificationCacheStore()
        runner = FakeModelRunner()
        service = ClassificationService(
            runner,
            cache,
            max_author_comment_model_calls_per_pr=20,
        )

        def prepared(
            discussion: ClassificationDiscussion,
            **_kwargs,
        ) -> AuthorCommentDiscussionPlan:
            return author_comment_plan(
                discussion,
                request_counts[discussion.identity.discussion_id],
            )

        def classified(
            request: AuthorCommentModelRequest,
            _model: str,
        ) -> tuple[ClassificationSuccess, ...]:
            return tuple(
                author_comment_result(
                    discussion,
                    cli_call=(index == 0),
                )
                for index, discussion in enumerate(request.discussions)
            )

        with (
            patch(
                "classification_execution.prepare_author_comment_discussion",
                side_effect=prepared,
            ) as prepare_discussion,
            patch.object(
                ClassificationService,
                "_run_author_comment_request",
                side_effect=classified,
            ) as run_author_request,
        ):
            first = service.classify(
                execution_request(author_comments=records)
            ).top_level_author_comments

            self.assertEqual(
                [result.identity.discussion_id for result in first],
                list(request_counts),
            )
            self.assertEqual(
                [
                    result.identity.discussion_id
                    for result in first
                    if not result.deferred
                ],
                ["expensive-1", "expensive-2", "cheap"],
            )
            self.assertEqual(
                [
                    result.identity.discussion_id
                    for result in first
                    if result.deferred
                ],
                ["overflow-1", "overflow-2"],
            )
            self.assertEqual(run_author_request.call_count, 20)
            self.assertEqual(
                [result.cli_call for result in first],
                [True, True, False, True, False],
            )
            self.assertEqual(len(cache.entries[123]), 3)
            self.assertEqual(
                sum(
                    bool(record.get("deferred"))
                    for record in cache.entries[123].values()
                ),
                0,
            )

            run_author_request.reset_mock()
            prepare_discussion.reset_mock()
            second = service.classify(
                execution_request(author_comments=records)
            ).top_level_author_comments

        self.assertEqual(
            [
                call.args[0].identity.discussion_id
                for call in prepare_discussion.call_args_list
            ],
            ["overflow-1", "overflow-2"],
        )
        self.assertEqual(run_author_request.call_count, 4)
        self.assertEqual(
            [result.deferred for result in second],
            [False] * 5,
        )
        self.assertEqual(
            [result.identity.discussion_id for result in second],
            list(request_counts),
        )
        self.assertEqual(len(cache.entries[123]), 5)

    def test_oversized_first_author_comment_does_not_block_later_items(
        self,
    ) -> None:
        request_counts = {
            "oversized": 21,
            "cheap-1": 1,
            "cheap-2": 1,
        }
        records = tuple(
            discussion_record(
                discussion_id,
                DiscussionKind.TOP_LEVEL_AUTHOR_REPLY,
                actor_role="author",
            )
            for discussion_id in request_counts
        )
        cache = MemoryClassificationCacheStore()
        service = ClassificationService(
            FakeModelRunner(),
            cache,
            max_author_comment_model_calls_per_pr=20,
        )

        def prepared(
            discussion: ClassificationDiscussion,
            **_kwargs,
        ) -> AuthorCommentDiscussionPlan:
            return author_comment_plan(
                discussion,
                request_counts[discussion.identity.discussion_id],
            )

        def classified(
            request: AuthorCommentModelRequest,
            _model: str,
        ) -> tuple[ClassificationSuccess, ...]:
            return tuple(
                author_comment_result(
                    discussion,
                    cli_call=(index == 0),
                )
                for index, discussion in enumerate(request.discussions)
            )

        with (
            patch(
                "classification_execution.prepare_author_comment_discussion",
                side_effect=prepared,
            ),
            patch.object(
                ClassificationService,
                "_run_author_comment_request",
                side_effect=classified,
            ) as run_author_request,
        ):
            results = service.classify(
                execution_request(author_comments=records)
            ).top_level_author_comments

        self.assertEqual(run_author_request.call_count, 1)
        self.assertEqual(
            [
                discussion.identity.discussion_id
                for discussion in run_author_request.call_args.args[0].discussions
            ],
            ["cheap-1", "cheap-2"],
        )
        self.assertEqual(
            [result.deferred for result in results],
            [True, False, False],
        )
        self.assertEqual(
            [result.cli_call for result in results],
            [False, True, False],
        )
        self.assertEqual(
            [result.identity.discussion_id for result in results],
            list(request_counts),
        )
        self.assertEqual(len(cache.entries[123]), 2)
        self.assertEqual(
            sum(
                bool(record.get("deferred"))
                for record in cache.entries[123].values()
            ),
            0,
        )

    def test_deferred_author_comments_are_classified_on_the_next_refresh(
        self,
    ) -> None:
        records = tuple(
            discussion_record(
                f"reply-{index}",
                DiscussionKind.TOP_LEVEL_AUTHOR_REPLY,
                actor_role="author",
                candidate_feedback=((f"feedback-{index}", "Please fix this."),),
            )
            for index in range(3)
        )
        cache = MemoryClassificationCacheStore()
        first_runner = FakeModelRunner(responder=successful_response)
        first = ClassificationService(
            first_runner,
            cache,
            max_classifications_per_pr=2,
        ).classify(execution_request(author_comments=records))

        self.assertEqual(
            [result.deferred for result in first.top_level_author_comments],
            [False, False, True],
        )
        self.assertEqual(len(cache.entries[123]), 2)

        second_runner = FakeModelRunner(responder=successful_response)
        second = ClassificationService(
            second_runner,
            cache,
            max_classifications_per_pr=2,
        ).classify(execution_request(author_comments=records))

        self.assertEqual(len(second_runner.requests), 1)
        self.assertEqual(len(prompt_items(second_runner.requests[0])), 1)
        self.assertEqual(
            [result.deferred for result in second.top_level_author_comments],
            [False, False, False],
        )
        self.assertEqual(len(cache.entries[123]), 3)

    def test_cached_deferred_author_comment_is_retried(self) -> None:
        record = discussion_record(
            "reply",
            DiscussionKind.TOP_LEVEL_AUTHOR_REPLY,
            actor_role="author",
            candidate_feedback=(("feedback", "Please fix this."),),
        )
        discussion = ClassificationDiscussion.from_record(record)
        key = discussion_cache_key(
            discussion,
            "model",
            author_comment=True,
        )
        deferred = ClassificationDeferred(
            discussion.identity,
            AuthorCommentDecision(
                reason="Deferred by per-PR classification limit"
            ),
        )
        cache = MemoryClassificationCacheStore({
            123: {key: cached_classification_record(deferred)}
        })
        runner = FakeModelRunner(responder=successful_response)

        result = ClassificationService(runner, cache).classify(
            execution_request(author_comments=(record,))
        ).top_level_author_comments[0]

        self.assertEqual(len(runner.requests), 1)
        self.assertFalse(result.deferred)
        self.assertFalse(cache.entries[123][key].get("deferred"))

    def test_author_comment_sliced_prompt_calls_are_bounded(self) -> None:
        records = tuple(
            discussion_record(
                f"reply-{index}",
                DiscussionKind.TOP_LEVEL_AUTHOR_REPLY,
                actor_role="author",
                candidate_feedback=((f"feedback-{index}", "Please fix this."),),
            )
            for index in range(11)
        )

        def prepared(
            discussion: ClassificationDiscussion,
            **_kwargs,
        ) -> AuthorCommentDiscussionPlan:
            return AuthorCommentDiscussionPlan(
                discussion,
                tuple(
                    make_author_comment_request((discussion,))
                    for _index in range(
                        3
                        if discussion.identity.discussion_id == "reply-0"
                        else 1
                    )
                ),
            )

        def classified(
            request: AuthorCommentModelRequest,
            _model: str,
        ) -> tuple[ClassificationSuccess, ...]:
            return tuple(
                author_comment_result(
                    discussion,
                    cli_call=(index == 0),
                )
                for index, discussion in enumerate(request.discussions)
            )

        runner = FakeModelRunner()
        service = ClassificationService(
            runner,
            MemoryClassificationCacheStore(),
            max_author_comment_model_calls_per_pr=3,
        )
        with (
            patch(
                "classification_execution.prepare_author_comment_discussion",
                side_effect=prepared,
            ),
            patch.object(
                ClassificationService,
                "_run_author_comment_request",
                side_effect=classified,
            ) as run_author_request,
        ):
            results = service.classify(
                execution_request(author_comments=records)
            ).top_level_author_comments

        self.assertEqual(run_author_request.call_count, 3)
        self.assertEqual(
            [
                [
                    discussion.identity.discussion_id
                    for discussion in call.args[0].discussions
                ]
                for call in run_author_request.call_args_list
            ],
            [
                ["reply-0"],
                ["reply-0"],
                [f"reply-{index}" for index in range(10)],
            ],
        )
        self.assertEqual(
            [result.deferred for result in results],
            [False] * 10 + [True],
        )

    def test_author_comment_chunks_count_toward_budget_and_combine_attribution(
        self,
    ) -> None:
        record = discussion_record(
            "reply",
            DiscussionKind.TOP_LEVEL_AUTHOR_REPLY,
            actor_role="author",
            candidate_feedback=tuple(
                (
                    f"feedback-{index}",
                    f"Request {index}: " + "x" * 1000,
                )
                for index in range(30)
            ),
        )
        runner = FakeModelRunner(responder=successful_response)
        result = ClassificationService(
            runner,
            MemoryClassificationCacheStore(),
            max_prompt_chars=5000,
        ).classify(
            execution_request(author_comments=(record,))
        ).top_level_author_comments[0]

        self.assertGreater(len(runner.requests), 1)
        self.assertTrue(result.cli_call)
        assert isinstance(result.decision, AuthorCommentDecision)
        self.assertEqual(
            [
                outcome.feedback_id
                for outcome in result.decision.feedback_outcomes
            ],
            [f"feedback-{index}" for index in range(30)],
        )

        bounded_runner = FakeModelRunner(responder=successful_response)
        bounded = ClassificationService(
            bounded_runner,
            MemoryClassificationCacheStore(),
            max_prompt_chars=5000,
            max_author_comment_model_calls_per_pr=1,
        ).classify(
            execution_request(author_comments=(record,))
        ).top_level_author_comments[0]

        self.assertEqual(bounded_runner.requests, [])
        self.assertIsInstance(bounded, ClassificationDeferred)

    def test_nonzero_malformed_and_timeout_responses_keep_diagnostics(self) -> None:
        record = discussion_record("feedback")
        cases = (
            (
                "nonzero",
                RawModelResponse(
                    9,
                    '{"items":[{"discussion_id":"feedback",'
                    '"verdict":"no_author_action","reason":"done"}]}',
                    "cli stderr",
                ),
                "exited with status 9",
                "cli stderr",
            ),
            (
                "malformed",
                RawModelResponse(0, "not json", "parse stderr"),
                "did not return a valid verdict",
                "parse stderr",
            ),
            (
                "timeout",
                subprocess.TimeoutExpired(
                    "copilot",
                    37,
                    output="partial response",
                    stderr="timeout stderr",
                ),
                "timed out after 37s",
                "timeout stderr",
            ),
        )
        for name, response, error_text, stderr_text in cases:
            with self.subTest(name=name):
                service = ClassificationService(
                    FakeModelRunner([response]),
                    MemoryClassificationCacheStore(),
                )

                result = service.classify(
                    execution_request(top_level_items=(record,))
                ).top_level_items[0]

                self.assertIsInstance(result, ClassificationFailure)
                assert isinstance(result, ClassificationFailure)
                self.assertIn(error_text, result.diagnostics.error)
                self.assertEqual(result.diagnostics.stderr, stderr_text)
                self.assertTrue(result.cli_call)

    def test_failed_items_are_retried_and_limits_apply_only_to_uncached_items(
        self,
    ) -> None:
        records = tuple(
            discussion_record(f"feedback-{index}")
            for index in range(23)
        )
        cache = MemoryClassificationCacheStore()
        first_runner = FakeModelRunner(responder=successful_response)
        first = ClassificationService(
            first_runner,
            cache,
            max_classifications_per_pr=20,
        ).classify(execution_request(top_level_items=records))

        self.assertEqual(len(first_runner.requests), 2)
        self.assertEqual(
            [result.failed for result in first.top_level_items],
            [False] * 20 + [True] * 3,
        )
        self.assertEqual(len(cache.entries[123]), 20)

        second_runner = FakeModelRunner(responder=successful_response)
        second = ClassificationService(
            second_runner,
            cache,
            max_classifications_per_pr=20,
        ).classify(execution_request(top_level_items=records))

        self.assertEqual(len(second_runner.requests), 1)
        self.assertEqual(len(prompt_items(second_runner.requests[0])), 3)
        self.assertEqual(
            [result.failed for result in second.top_level_items],
            [False] * 23,
        )
        self.assertEqual(len(cache.entries[123]), 23)

    def test_over_limit_items_fail_to_the_author_without_a_model_call(
        self,
    ) -> None:
        runner = FakeModelRunner(responder=successful_response)
        service = ClassificationService(
            runner,
            MemoryClassificationCacheStore(),
            max_classifications_per_pr=0,
        )

        result = service.classify(
            execution_request(
                top_level_items=(discussion_record("feedback"),)
            )
        ).top_level_items[0]

        self.assertEqual(runner.requests, [])
        self.assertIsInstance(result, ClassificationFailure)
        assert isinstance(result, ClassificationFailure)
        self.assertTrue(result.failed)
        self.assertEqual(
            result.decision,
            ActionDecision(
                DiscussionAction.AUTHOR,
                "Exceeded per-PR classification limit",
            ),
        )
        self.assertEqual(
            result.diagnostics.error,
            "Exceeded per-PR classification limit",
        )
        self.assertFalse(result.cli_call)

    def test_cache_key_ignores_non_policy_facts_but_includes_comment_body(
        self,
    ) -> None:
        record = discussion_record("feedback")
        record["discussion_facts"] = {"current_conflicts": "no"}
        cache = MemoryClassificationCacheStore()
        first_runner = FakeModelRunner(responder=successful_response)
        ClassificationService(first_runner, cache).classify(
            execution_request(top_level_items=(record,))
        )

        record["discussion_facts"]["current_conflicts"] = "yes"
        cached_runner = FakeModelRunner()
        ClassificationService(cached_runner, cache).classify(
            execution_request(top_level_items=(record,))
        )

        self.assertEqual(cached_runner.requests, [])
        record["comments"][0]["body"] = "Please change the implementation."
        changed_runner = FakeModelRunner(responder=successful_response)
        ClassificationService(changed_runner, cache).classify(
            execution_request(top_level_items=(record,))
        )
        self.assertEqual(len(changed_runner.requests), 1)


class ReviewThreadExecutionTest(unittest.TestCase):
    @staticmethod
    def thread(*comments: tuple[str, str, str]) -> dict:
        return {
            "discussion_id": "thread",
            "discussion_kind": DiscussionKind.REVIEW_THREAD.value,
            "comments": [
                {
                    "timestamp": timestamp,
                    "actor_role": role,
                    "body": body,
                }
                for role, body, timestamp in comments
            ],
        }

    def classify(
        self,
        thread: dict,
        *,
        responder=successful_response,
        responses: tuple[RawModelResponse | Exception, ...] = (),
    ):
        runner = FakeModelRunner(
            responses,
            responder=responder if not responses else None,
        )
        result = ClassificationService(
            runner,
            MemoryClassificationCacheStore(),
        ).classify(execution_request(review_threads=(thread,)))
        return result.review_threads[0], runner

    def test_long_reviewer_request_needs_no_model(self) -> None:
        result, runner = self.classify(self.thread(
            (
                "approver",
                "Could this be deterministic without relying on sleep? "
                "The current approach is flaky on slower machines.",
                "2026-03-12T00:00:00Z",
            )
        ))

        self.assertEqual(runner.requests, [])
        self.assertIsInstance(result, ClassificationSuccess)
        assert isinstance(result.decision, ActionDecision)
        self.assertIs(result.decision.action, DiscussionAction.AUTHOR)

    def test_praise_keeps_the_previous_request_and_wait_age(self) -> None:
        result, runner = self.classify(self.thread(
            ("approver", "Please fix this.", "2026-03-12T00:00:00Z"),
            ("approver", "LGTM", "2026-05-20T00:00:00Z"),
        ), responder=lambda request: successful_response(
            request,
            praise="praise",
        ))

        self.assertEqual(len(runner.requests), 1)
        assert isinstance(result.decision, ActionDecision)
        self.assertIs(result.decision.action, DiscussionAction.AUTHOR)
        self.assertEqual(result.since, "2026-03-12T00:00:00Z")
        self.assertTrue(result.ignored_last_comment)

    def test_praise_after_completed_author_reply_hands_back_to_reviewer(
        self,
    ) -> None:
        result, runner = self.classify(self.thread(
            ("author", "Fixed it.", "2026-03-12T00:00:00Z"),
            ("approver", "LGTM", "2026-05-20T00:00:00Z"),
        ), responder=lambda request: successful_response(
            request,
            praise="praise",
            author_reply="complete",
        ))

        self.assertEqual(len(runner.requests), 2)
        assert isinstance(result.decision, ActionDecision)
        self.assertIs(result.decision.action, DiscussionAction.REVIEWER)
        self.assertEqual(result.since, "2026-03-12T00:00:00Z")
        self.assertTrue(result.ignored_last_comment)

    def test_failed_praise_and_author_reply_calls_fail_safe_to_author(
        self,
    ) -> None:
        praise_thread = self.thread(
            ("approver", "LGTM", "2026-03-12T00:00:00Z")
        )
        author_thread = self.thread(
            (
                "author",
                "I will update this after the next release.",
                "2026-03-12T00:00:00Z",
            )
        )
        for name, thread in (
            ("praise", praise_thread),
            ("author reply", author_thread),
        ):
            with self.subTest(name=name):
                result, _runner = self.classify(
                    thread,
                    responses=(RawModelResponse(1, "", "failed"),),
                )
                self.assertIsInstance(result, ClassificationFailure)
                assert isinstance(result.decision, ActionDecision)
                self.assertIs(
                    result.decision.action,
                    DiscussionAction.AUTHOR,
                )


if __name__ == "__main__":
    unittest.main()
