from __future__ import annotations

import concurrent.futures
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import drain_queue
from drain_queue import (
    DrainResult,
    GitHubAppTokenClient,
    JobResult,
    drain_queue as run_drain,
    process_repository_claims,
    requeue_acknowledgments,
    unresolved_acknowledgments,
)
from process_queue_batch import Claim
from execution_code import ExecutionCode


def claim(item_key: str, *, attempts: int = 0) -> Claim:
    repository = item_key.split("#", 1)[0]
    return Claim(item_key, 1, repository, 1, "", attempts)


# Hang guard only; scheduling assertions never depend on elapsed time.
WAIT_SECONDS = 30


class FakeQueue:
    """Claims the oldest eligible items the way the queue's optional filters do."""

    def __init__(self, item_keys: list[str], *, limit: int = 16) -> None:
        self.queued = [claim(item_key) for item_key in item_keys]
        self.limit = limit
        self.calls: list[tuple[int, list[str], list[str]]] = []

    def claim(
        self, free_slots: int, excluded: list[str], active_repositories: list[str]
    ) -> list[Claim]:
        self.calls.append((free_slots, excluded, active_repositories))
        selected: list[Claim] = []
        repositories: list[str] = []
        for item in self.queued:
            if len(selected) >= self.limit:
                break
            if item.item_key in excluded or item.repository in active_repositories:
                continue
            if item.repository not in repositories:
                if len(repositories) >= free_slots:
                    continue
                repositories.append(item.repository)
            selected.append(item)
        for item in selected:
            self.queued.remove(item)
        return selected


class ActiveTracker:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.active: set[str] = set()
        self.maximum = 0
        self.violations: list[str] = []

    def enter(self, claims: list[Claim]) -> None:
        repository = claims[0].repository
        with self.lock:
            if {item.repository for item in claims} != {repository}:
                self.violations.append(f"mixed job for {repository}")
            if repository in self.active:
                self.violations.append(f"{repository} overlapped itself")
            self.active.add(repository)
            self.maximum = max(self.maximum, len(self.active))
            if len(self.active) > 4:
                self.violations.append(f"{len(self.active)} active repositories")

    def leave(self, claims: list[Claim]) -> None:
        with self.lock:
            self.active.remove(claims[0].repository)


class ControlledExecutor:
    def __init__(self, *, on_submit=None) -> None:
        self.futures: list[concurrent.futures.Future[JobResult]] = []
        self.on_submit = on_submit

    def __enter__(self) -> ControlledExecutor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def submit(self, _process_job, claims: list[Claim], _job: int):
        future: concurrent.futures.Future[JobResult] = concurrent.futures.Future()
        self.futures.append(future)
        if self.on_submit is not None:
            self.on_submit(future, claims)
        return future


class DrainQueueTest(unittest.TestCase):
    def test_code_preparation_environment_omits_credentials(self) -> None:
        secrets = {
            "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "oidc",
            "ACTIONS_ID_TOKEN_REQUEST_URL": "https://oidc.example",
            "COPILOT_GITHUB_TOKEN": "copilot",
            "COPILOT_REVIEW_FALLBACK_TOKEN": "request-token",
            "GITHUB_TOKEN": "github",
            "PR_DASHBOARD_PRIVATE_KEY": "key",
            "SLACK_WEBHOOK_URL": "https://slack.example",
        }
        with mock.patch.dict(os.environ, {**secrets, "PATH": "/bin"}):
            environment = drain_queue.code_preparation_environment()
        self.assertEqual("/bin", environment["PATH"])
        self.assertTrue(secrets.keys().isdisjoint(environment))

    def test_main_reports_invalid_canary_repositories_json(self) -> None:
        argv = [
            "drain_queue.py",
            "--canary-code-ref",
            "a" * 40,
            "--deadline",
            "1",
            "--generation",
            "1",
            "--worker",
            "worker",
            "--endpoint",
            "https://example.test/queue",
            "--canary-repositories-json",
            "{",
        ]
        stderr = io.StringIO()

        with (
            mock.patch.object(sys, "argv", argv),
            contextlib.redirect_stderr(stderr),
            self.assertRaises(SystemExit) as exit_context,
        ):
            drain_queue.main()

        self.assertEqual(exit_context.exception.code, 2)
        self.assertIn(
            "argument --canary-repositories-json: expected a JSON array of "
            'repository names, for example ["opentelemetry-java-instrumentation"]',
            stderr.getvalue(),
        )
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_stops_without_processing_when_queue_is_empty(self) -> None:
        queue = FakeQueue([])
        process_job = mock.Mock()

        result = run_drain(100_000, queue.claim, process_job, now=lambda: 0)

        self.assertEqual(result, DrainResult(0, 0, "queue_empty", 0))
        self.assertEqual(queue.calls, [(4, [], [])])
        process_job.assert_not_called()

    def test_blocked_repository_does_not_delay_work_beyond_the_first_claim(self) -> None:
        first_batch = [
            "slow#pr:1",
            *[f"{repository}#pr:{number}" for repository in ("a", "b", "c") for number in range(1, 6)],
        ]
        later = ["d#pr:1", "e#pr:1", "f#pr:1"]
        queue = FakeQueue(first_batch + later)
        tracker = ActiveTracker()
        later_started = threading.Event()
        released: list[bool] = []
        slow_active_for_later: list[bool] = []

        def process_job(claims: list[Claim], _job: int) -> JobResult:
            tracker.enter(claims)
            try:
                if claims[0].repository == "slow":
                    released.append(later_started.wait(WAIT_SECONDS))
                elif claims[0].item_key in later:
                    with tracker.lock:
                        slow_active_for_later.append("slow" in tracker.active)
                    later_started.set()
                return JobResult(0, ())
            finally:
                tracker.leave(claims)

        result = run_drain(100_000, queue.claim, process_job, now=lambda: 0)

        # The blocked job is released only by work claimed after the first
        # 16-item claim, so it cannot finish first and pass by timing alone.
        self.assertEqual(released, [True])
        self.assertTrue(slow_active_for_later[0])
        self.assertEqual(tracker.violations, [])
        self.assertEqual(queue.calls[0], (4, [], []))
        self.assertEqual(result, DrainResult(19, 0, "queue_empty", 7))
        self.assertEqual(queue.queued, [])

    def test_keeps_four_repositories_active_without_self_overlap(self) -> None:
        # Small item limits split each repository into several claims, so
        # replenishment must keep skipping repositories that are still active.
        item_keys = [
            f"repo-{repository}#pr:{number}"
            for number in range(1, 4)
            for repository in range(6)
        ]
        queue = FakeQueue(item_keys, limit=2)
        tracker = ActiveTracker()
        barrier = threading.Barrier(4, timeout=WAIT_SECONDS)
        processed: list[str] = []

        def process_job(claims: list[Claim], job: int) -> JobResult:
            tracker.enter(claims)
            try:
                if job <= 4:
                    barrier.wait()
                processed.extend(item.item_key for item in claims)
                return JobResult(0, ())
            finally:
                tracker.leave(claims)

        result = run_drain(100_000, queue.claim, process_job, now=lambda: 0)

        self.assertEqual(tracker.violations, [])
        self.assertEqual(tracker.maximum, 4)
        self.assertEqual(sorted(processed), sorted(item_keys))
        self.assertEqual(result.reason, "queue_empty")
        self.assertEqual(result.claims, len(item_keys))
        for free_slots, _excluded, active in queue.calls:
            self.assertLessEqual(len(active) + free_slots, 4)
            self.assertGreaterEqual(free_slots, 1)

    def test_retries_empty_claim_when_repository_finishes_during_request(self) -> None:
        executor = ControlledExecutor()
        calls: list[tuple[int, list[str], list[str]]] = []

        def claim_work(
            free_slots: int, excluded: list[str], active_repositories: list[str]
        ) -> list[Claim]:
            calls.append((free_slots, excluded, active_repositories))
            if len(calls) == 1:
                return [claim("a#pr:1"), claim("b#pr:1")]
            if len(calls) == 2:
                executor.futures[0].set_result(JobResult(0, ()))
                return []
            if len(calls) == 3:
                self.assertEqual(active_repositories, ["b"])
                self.assertFalse(executor.futures[1].done())
                return [claim("a#pr:2")]
            if len(calls) == 4:
                for future in executor.futures:
                    if not future.done():
                        future.set_result(JobResult(0, ()))
                return []
            return []

        with mock.patch.object(
            drain_queue.concurrent.futures,
            "ThreadPoolExecutor",
            return_value=executor,
        ):
            result = run_drain(100_000, claim_work, mock.Mock(), now=lambda: 0, maximum_repositories=3)

        self.assertEqual(result, DrainResult(3, 0, "queue_empty", 3))
        self.assertEqual(len(calls), 5)

    def test_rejects_claims_that_overlap_an_active_repository(self) -> None:
        executor = ControlledExecutor()
        calls = iter([[claim("a#pr:1")], [claim("a#pr:2")]])

        def claim_work(*_args: object) -> list[Claim]:
            return next(calls)

        with mock.patch.object(
            drain_queue.concurrent.futures,
            "ThreadPoolExecutor",
            return_value=executor,
        ):
            with self.assertRaisesRegex(RuntimeError, "active repositories: a"):
                run_drain(100_000, claim_work, mock.Mock(), now=lambda: 0)

        self.assertEqual(len(executor.futures), 1)
        self.assertFalse(executor.futures[0].done())

    def test_lease_loss_while_replenishing_waits_for_running_jobs(self) -> None:
        release = threading.Event()
        finished: list[str] = []
        calls = iter([[claim("a#pr:1")]])

        def claim_work(*_args: object) -> list[Claim]:
            claims = next(calls, None)
            if claims is None:
                release.set()
                raise RuntimeError("queue lease heartbeat failed: rejected")
            return claims

        def process_job(claims: list[Claim], _job: int) -> JobResult:
            self.assertTrue(release.wait(WAIT_SECONDS))
            finished.append(claims[0].item_key)
            return JobResult(0, ())

        with self.assertRaisesRegex(RuntimeError, "queue lease heartbeat failed"):
            run_drain(100_000, claim_work, process_job, now=lambda: 0)

        self.assertEqual(finished, ["a#pr:1"])

    def test_stops_admitting_before_the_safe_deadline(self) -> None:
        queue = FakeQueue(["example#pr:1"])
        process_job = mock.Mock()

        result = run_drain(
            1_000, queue.claim, process_job, now=lambda: 400,
            admission_margin_seconds=600,
        )

        self.assertEqual(result, DrainResult(0, 0, "deadline", 0))
        self.assertEqual(queue.calls, [])
        process_job.assert_not_called()

    def test_running_jobs_finish_after_admission_stops_at_the_deadline(self) -> None:
        queue = FakeQueue(["a#pr:1", "b#pr:1"])
        clock = [0.0]
        processed: list[str] = []

        def process_job(claims: list[Claim], _job: int) -> JobResult:
            processed.append(claims[0].item_key)
            clock[0] = 500
            return JobResult(0, ())

        result = run_drain(
            1_000, queue.claim, process_job, now=lambda: clock[0],
            admission_margin_seconds=600, maximum_repositories=1,
        )

        self.assertEqual(result, DrainResult(1, 0, "deadline", 1))
        self.assertEqual(processed, ["a#pr:1"])
        self.assertEqual([item.item_key for item in queue.queued], ["b#pr:1"])

    def test_returns_claims_acquired_after_deadline_admission_stops(self) -> None:
        clock = [0]
        returned: list[tuple[str, int, str]] = []

        def claim_work(*_args: object) -> list[Claim]:
            clock[0] = 500
            return [claim("a#pr:1", attempts=2)]

        def return_work(claims: list[Claim]) -> None:
            acknowledgments = requeue_acknowledgments(claims)
            returned.extend(
                (item.item_key, item.attempts, acknowledgment["outcome"])
                for item, acknowledgment in zip(claims, acknowledgments)
            )

        result = run_drain(
            1_000,
            claim_work,
            mock.Mock(),
            now=lambda: clock[0],
            admission_margin_seconds=600,
            maximum_repositories=1,
            return_unstarted=return_work,
        )

        self.assertEqual(result, DrainResult(1, 0, "deadline", 0))
        self.assertEqual(returned, [("a#pr:1", 2, "requeue")])

    def test_continues_after_dead_letters_and_excludes_retries(self) -> None:
        queue = FakeQueue(["example#pr:1", "example#pr:2", "example#pr:3"], limit=2)
        release_initial = threading.Event()
        release_followup = threading.Event()

        def process_job(claims: list[Claim], _job: int) -> JobResult:
            keys = [item.item_key for item in claims]
            if keys == ["example#pr:1", "example#pr:2"]:
                self.assertTrue(release_initial.wait(WAIT_SECONDS))
                queue.queued.append(claim("example#pr:2"))
                return JobResult(1, ("example#pr:2",))
            self.assertTrue(release_followup.wait(WAIT_SECONDS))
            return JobResult(0, ())

        def claim_work(
            free_slots: int, excluded: list[str], active_repositories: list[str]
        ) -> list[Claim]:
            claims = queue.claim(free_slots, excluded, active_repositories)
            if len(queue.calls) == 2:
                release_initial.set()
            elif len(queue.calls) == 4:
                release_followup.set()
            return claims

        result = run_drain(100_000, claim_work, process_job, now=lambda: 0)

        self.assertEqual(result, DrainResult(3, 1, "queue_empty", 2))
        self.assertEqual(
            queue.calls,
            [
                (4, [], []),
                (3, [], ["example"]),
                (4, ["example#pr:2"], []),
                (3, ["example#pr:2"], ["example"]),
                (4, ["example#pr:2"], []),
            ],
        )
        self.assertEqual([item.item_key for item in queue.queued], ["example#pr:2"])

    def test_reports_failed_repository_job_after_other_jobs_finish(self) -> None:
        queue = FakeQueue(["removed#pr:1", "valid#pr:1"])
        processed: list[str] = []
        failed = threading.Event()
        valid_started = threading.Event()

        def process_job(claims: list[Claim], _job: int) -> JobResult:
            if claims[0].repository == "removed":
                self.assertTrue(valid_started.wait(WAIT_SECONDS))
                failed.set()
                raise RuntimeError("repository is not accessible")
            valid_started.set()
            self.assertTrue(failed.wait(WAIT_SECONDS))
            processed.append(claims[0].item_key)
            return JobResult(0, ())

        with self.assertRaisesRegex(
            RuntimeError, "1 repository job\\(s\\) failed: removed: repository is not accessible"
        ):
            run_drain(100_000, queue.claim, process_job, now=lambda: 0)

        self.assertEqual(processed, ["valid#pr:1"])

    def test_failed_repository_job_stops_new_admissions(self) -> None:
        queue = FakeQueue(["removed#pr:1", "later#pr:1"])

        def process_job(_claims: list[Claim], _job: int) -> JobResult:
            raise RuntimeError("repository is not accessible")

        with self.assertRaisesRegex(RuntimeError, "removed: repository is not accessible"):
            run_drain(
                100_000, queue.claim, process_job, now=lambda: 0, maximum_repositories=1,
            )

        self.assertEqual(queue.calls, [(1, [], [])])
        self.assertEqual([item.item_key for item in queue.queued], ["later#pr:1"])

    def test_failure_during_claim_returns_all_new_claims_without_starting_them(self) -> None:
        executor = ControlledExecutor()
        returned: list[tuple[str, int, str]] = []
        calls = 0

        def claim_work(*_args: object) -> list[Claim]:
            nonlocal calls
            calls += 1
            if calls == 1:
                return [claim("removed#pr:1")]
            executor.futures[0].set_exception(RuntimeError("repository unavailable"))
            return [
                claim("next-a#pr:1", attempts=2),
                claim("next-b#pr:1", attempts=2),
            ]

        def return_work(claims: list[Claim]) -> None:
            acknowledgments = requeue_acknowledgments(claims)
            returned.extend(
                (item.item_key, item.attempts, acknowledgment["outcome"])
                for item, acknowledgment in zip(claims, acknowledgments)
            )
            raise RuntimeError("acknowledgment rejected")

        with mock.patch.object(
            drain_queue.concurrent.futures,
            "ThreadPoolExecutor",
            return_value=executor,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "removed: repository unavailable; unstarted claims could not be returned: "
                "acknowledgment rejected",
            ):
                run_drain(
                    100_000,
                    claim_work,
                    mock.Mock(),
                    now=lambda: 0,
                    maximum_repositories=2,
                    return_unstarted=return_work,
                )

        self.assertEqual(
            returned,
            [("next-a#pr:1", 2, "requeue"), ("next-b#pr:1", 2, "requeue")],
        )
        self.assertEqual(len(executor.futures), 1)

    def test_failure_between_submissions_returns_remaining_claims(self) -> None:
        returned: list[str] = []
        calls = 0

        def on_submit(
            future: concurrent.futures.Future[JobResult],
            _claims: list[Claim],
        ) -> None:
            if len(executor.futures) == 1:
                return
            if len(executor.futures) == 2:
                executor.futures[0].set_exception(RuntimeError("repository unavailable"))
            future.set_result(JobResult(0, ()))

        executor = ControlledExecutor(on_submit=on_submit)

        def claim_work(*_args: object) -> list[Claim]:
            nonlocal calls
            calls += 1
            if calls == 1:
                return [claim("removed#pr:1")]
            return [claim("next-a#pr:1"), claim("next-b#pr:1")]

        with mock.patch.object(
            drain_queue.concurrent.futures,
            "ThreadPoolExecutor",
            return_value=executor,
        ):
            with self.assertRaisesRegex(RuntimeError, "removed: repository unavailable"):
                run_drain(
                    100_000,
                    claim_work,
                    mock.Mock(),
                    now=lambda: 0,
                    maximum_repositories=3,
                    return_unstarted=lambda claims: returned.extend(
                        item.item_key for item in claims
                    ),
                )

        self.assertEqual(returned, ["next-b#pr:1"])
        self.assertEqual(len(executor.futures), 2)

    def test_stops_before_retry_exclusions_become_too_large(self) -> None:
        queue = FakeQueue(["example#pr:1", "example#pr:2", "example#pr:3"], limit=2)

        result = run_drain(
            100_000,
            queue.claim,
            lambda claims, _job: JobResult(0, tuple(item.item_key for item in claims)),
            now=lambda: 0,
            maximum_exclusions=2,
        )

        self.assertEqual(result, DrainResult(2, 0, "exclusion_limit", 1))
        self.assertLessEqual(len(queue.calls), 2)

    def test_stops_before_retry_exclusions_exceed_the_byte_budget(self) -> None:
        long_keys = ["a" * 500 + "#pr:1", "a" * 500 + "#pr:2"]
        queue = FakeQueue([*long_keys, "b#pr:1"], limit=2)

        result = run_drain(
            100_000,
            queue.claim,
            lambda claims, _job: JobResult(0, tuple(item.item_key for item in claims)),
            now=lambda: 0,
            maximum_repositories=1,
            maximum_exclusion_bytes=800,
        )

        self.assertEqual(result, DrainResult(2, 0, "exclusion_limit", 1))
        self.assertEqual([item.item_key for item in queue.queued], ["b#pr:1"])


class TokenClientTest(unittest.TestCase):
    def test_sends_credentials_only_through_helper_stdin(self) -> None:
        calls: list[dict[str, object]] = []

        def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append({"command": command, **kwargs})
            payload = json.loads(str(kwargs["input"]))
            stdout = (
                '{"token":"wave-token"}'
                if payload["operation"] == "mint"
                else '{"revoked":true}'
            )
            return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

        with mock.patch.dict(
            os.environ,
            {
                "PR_DASHBOARD_CLIENT_ID": "environment-client",
                "PR_DASHBOARD_PRIVATE_KEY": "environment-key",
            },
        ):
            client = GitHubAppTokenClient("client", "private-key", run=run)
            self.assertEqual(client.mint(["repo-a"]), "wave-token")
            client.revoke("wave-token")

        mint_payload = json.loads(str(calls[0]["input"]))
        self.assertEqual(mint_payload["privateKey"], "private-key")
        for call in calls:
            environment = call["env"]
            self.assertNotIn("PR_DASHBOARD_CLIENT_ID", environment)
            self.assertNotIn("PR_DASHBOARD_PRIVATE_KEY", environment)


class ProcessRepositoryClaimsTest(unittest.TestCase):
    def test_dead_letters_exhausted_claim_when_token_creation_fails(self) -> None:
        class TokenClient:
            def mint(self, _repositories: list[str]) -> str:
                raise RuntimeError("token failed")

        class Client:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            def call(self, action: str, **payload: object) -> dict[str, object]:
                self.calls.append({"action": action, **payload})
                return {"status": "retry"}

        client = Client()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "token failed"):
                process_repository_claims(
                    [claim("example#pr:1", attempts=2)],
                    Path(directory) / "results.json",
                    client,
                    1,
                    "worker",
                    TokenClient(),
                    lease_monitor=mock.Mock(spec=drain_queue.LeaseMonitor),
                    execution_code=ExecutionCode("a" * 40, Path("code"), "python"),
                    repository_root=Path("isolated"),
                )

        self.assertEqual(client.calls[0]["action"], "acknowledge")
        self.assertEqual(client.calls[0]["outcome"], "dead")

    def test_processes_with_scoped_token_and_reports_before_revocation(self) -> None:
        lifecycle: list[str] = []

        class TokenClient:
            def mint(self, repositories: list[str]) -> str:
                self.repositories = repositories
                return "wave-token"

            def revoke(self, token: str) -> None:
                lifecycle.append(f"revoke:{token}")

        token_client = TokenClient()
        results = [
            {
                "itemKey": "example#pr:1",
                "claimGeneration": 1,
                "outcome": "success",
            }
        ]

        def process(
            *_args: object,
            processor_env: dict[str, str],
            **_kwargs: object,
        ) -> tuple[dict[str, int], list[dict[str, object]]]:
            self.assertEqual(processor_env["GH_TOKEN"], "wave-token")
            self.assertNotIn("PR_DASHBOARD_PRIVATE_KEY", processor_env)
            return {"dead_letters": 0}, results

        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.object(drain_queue, "process_claims", side_effect=process),
        ):
            result = process_repository_claims(
                [claim("example#pr:1")],
                Path(directory) / "results.json",
                mock.Mock(),
                1,
                "worker",
                token_client,
                lease_monitor=mock.Mock(spec=drain_queue.LeaseMonitor),
                execution_code=ExecutionCode("a" * 40, Path("code"), "python"),
                repository_root=Path("isolated"),
                report_limits=lambda threshold, token: lifecycle.append(
                    f"report:{threshold}:{token}"
                ),
            )

        self.assertEqual(token_client.repositories, ["example"])
        self.assertEqual(result, results)
        self.assertEqual(lifecycle, ["report:20:wave-token", "revoke:wave-token"])

    def test_only_unresolved_claims_receive_failure_acknowledgments(self) -> None:
        claims = [
            claim("example#pr:1"),
            claim("example#pr:2"),
            claim("example#pr:3", attempts=2),
        ]
        results = [{"itemKey": "example#pr:1", "outcome": "success"}]

        acknowledgments = unresolved_acknowledgments(
            claims,
            results,
            RuntimeError("processor stopped"),
        )

        self.assertEqual(
            [(item["itemKey"], item["outcome"]) for item in acknowledgments],
            [("example#pr:2", "retry"), ("example#pr:3", "dead")],
        )


if __name__ == "__main__":
    unittest.main()
