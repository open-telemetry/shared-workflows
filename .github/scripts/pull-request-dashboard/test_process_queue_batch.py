from __future__ import annotations

from contextlib import contextmanager, nullcontext
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import process_queue_batch
from process_queue_batch import (
    Claim,
    LeaseMonitor,
    WorkItem,
    group_by_repository,
    load_claims,
    process_batch,
    resolve_work_items,
)


def claim(
    item_key: str,
    repository: str,
    *,
    pr_number: int | None = None,
    head_sha: str = "",
) -> Claim:
    return Claim(item_key, 1, repository, pr_number, head_sha, 0)


class QueueBatchTest(unittest.TestCase):
    def test_lease_monitor_reports_heartbeat_loss(self) -> None:
        class Client:
            calls = 0

            def call(self, _action: str, **_payload: object) -> dict[str, bool]:
                self.calls += 1
                if self.calls > 1:
                    raise RuntimeError("heartbeat unavailable")
                return {"dispatcher": True}

        monitor = LeaseMonitor(Client(), 1, "worker", interval_seconds=0.01)
        monitor.start()
        try:
            self.assertTrue(monitor.lost_event.wait(timeout=1))
            with self.assertRaisesRegex(RuntimeError, "heartbeat unavailable"):
                monitor.assert_valid()
        finally:
            monitor.close()

    def test_load_claims_validates_the_shape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "claims.json"
            path.write_text(
                json.dumps(
                    [
                        {
                            "itemKey": "example#pr:1",
                            "claimGeneration": 1,
                            "repository": "example",
                            "prNumber": 1,
                            "headSha": "",
                            "attempts": 0,
                        }
                    ]
                ),
                encoding="utf-8",
            )
            self.assertEqual(load_claims(path), [claim("example#pr:1", "example", pr_number=1)])

    def test_head_and_pr_claims_collapse_to_one_work_item(self) -> None:
        claims = [
            claim("example#pr:1", "example", pr_number=1),
            claim("example#head:abc", "example", head_sha="a" * 40),
        ]
        work, completed = resolve_work_items(claims, lambda _repo, _sha: 1)
        self.assertEqual(completed, [])
        self.assertEqual(len(work), 1)
        self.assertEqual(len(work[0].claims), 2)

    def test_unresolved_heads_are_acknowledged_without_work(self) -> None:
        head = claim("example#head:abc", "example", head_sha="a" * 40)
        work, completed = resolve_work_items([head], lambda _repo, _sha: None)
        self.assertEqual(work, [])
        self.assertEqual(completed[0]["outcome"], "success")

    def test_head_resolution_failure_retries_only_that_claim(self) -> None:
        direct = claim("example#pr:1", "example", pr_number=1)
        head = claim("example#head:abc", "example", head_sha="a" * 40)

        def fail_resolution(_repository: str, _head_sha: str) -> int | None:
            raise RuntimeError("GitHub API unavailable")

        work, completed = resolve_work_items([direct, head], fail_resolution)
        self.assertEqual([item.pr_number for item in work], [1])
        self.assertEqual(completed[0]["itemKey"], head.item_key)
        self.assertEqual(completed[0]["outcome"], "retry")

    def test_head_resolution_defers_publisher_lock_contention(self) -> None:
        head = Claim(
            "example#head:abc",
            1,
            "example",
            None,
            "a" * 40,
            process_queue_batch.MAX_ATTEMPTS - 1,
        )

        def locked(_repository: str, _head_sha: str) -> int | None:
            raise process_queue_batch.state_branch.PublisherLockTimeoutError(
                "publisher lock is busy"
            )

        work, completed = resolve_work_items([head], locked)

        self.assertEqual(work, [])
        self.assertEqual(completed[0]["outcome"], "retry")
        self.assertEqual(
            completed[0]["retryAfterMs"],
            process_queue_batch.PUBLISHER_LOCK_RETRY_AFTER_MS,
        )

    def test_head_resolution_checks_publisher_lock_before_github(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "repositories.json"
            config_path.write_text("[]", encoding="utf-8")
            run = mock.Mock()
            processor = process_queue_batch.DashboardBatchProcessor(
                config_path,
                run=run,
            )
            with mock.patch.object(
                processor,
                "_assert_publisher_unlocked",
                side_effect=process_queue_batch.state_branch.PublisherLockTimeoutError(
                    "publisher lock is busy"
                ),
            ):
                with self.assertRaises(
                    process_queue_batch.state_branch.PublisherLockTimeoutError
                ):
                    processor.resolve_head("example", "a" * 40)

        run.assert_not_called()

    def test_prs_are_grouped_sequentially_by_repository(self) -> None:
        items = [
            WorkItem("b", 2, (claim("b#pr:2", "b", pr_number=2),)),
            WorkItem("a", 3, (claim("a#pr:3", "a", pr_number=3),)),
            WorkItem("a", 1, (claim("a#pr:1", "a", pr_number=1),)),
        ]
        grouped = group_by_repository(items)
        self.assertEqual(list(grouped), ["a", "b"])
        self.assertEqual([item.pr_number for item in grouped["a"]], [1, 3])

    def test_repository_concurrency_is_bounded(self) -> None:
        active = 0
        maximum = 0
        lock = threading.Lock()

        def process(repository: str, items: list[WorkItem]) -> list[dict[str, object]]:
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.03)
            with lock:
                active -= 1
            return [
                {
                    "itemKey": items[0].claims[0].item_key,
                    "claimGeneration": 1,
                    "outcome": "success",
                }
            ]

        items = [
            WorkItem(
                f"repo-{index}",
                1,
                (claim(f"repo-{index}#pr:1", f"repo-{index}", pr_number=1),),
            )
            for index in range(8)
        ]
        process_batch(items, process, max_repositories=4)
        self.assertEqual(maximum, 4)

    def test_repository_workers_use_private_runner_temp_directories(self) -> None:
        runner_temps: dict[str, str] = {}

        def initial_backfill(
            repository: str,
            _state_branch: str,
            env: dict[str, str],
        ) -> bool:
            runner_temps[repository] = env["RUNNER_TEMP"]
            return False

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "repositories.json"
            config_path.write_text(
                json.dumps([{"name": "a"}, {"name": "b"}]),
                encoding="utf-8",
            )
            processor = process_queue_batch.DashboardBatchProcessor(
                config_path,
                script_dir=root / "scripts",
            )
            with mock.patch.object(
                processor,
                "_initial_backfill_complete",
                side_effect=initial_backfill,
            ):
                processor.process_repository(
                    "a",
                    [WorkItem("a", 1, (claim("a#pr:1", "a", pr_number=1),))],
                )
                processor.process_repository(
                    "b",
                    [WorkItem("b", 1, (claim("b#pr:1", "b", pr_number=1),))],
                )

        self.assertNotEqual(runner_temps["a"], runner_temps["b"])

    def test_initial_backfill_is_checked_once_for_all_repository_items(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "repositories.json"
            config_path.write_text(
                json.dumps([{"name": "example"}]),
                encoding="utf-8",
            )
            processor = process_queue_batch.DashboardBatchProcessor(config_path)
            items = [
                WorkItem(
                    "example",
                    number,
                    (claim(f"example#pr:{number}", "example", pr_number=number),),
                )
                for number in (1, 2)
            ]
            with (
                mock.patch.object(
                    processor,
                    "_initial_backfill_complete",
                    return_value=False,
                ) as initial_backfill_complete,
                mock.patch.object(processor, "_update_dashboard") as update_dashboard,
            ):
                results = processor.process_repository("example", items)

        initial_backfill_complete.assert_called_once()
        update_dashboard.assert_not_called()
        self.assertEqual([result["outcome"] for result in results], ["success", "success"])

    def test_repository_failure_does_not_suppress_other_results(self) -> None:
        items = [
            WorkItem("bad", 1, (claim("bad#pr:1", "bad", pr_number=1),)),
            WorkItem("good", 1, (claim("good#pr:1", "good", pr_number=1),)),
        ]

        def process(repository: str, work: list[WorkItem]) -> list[dict[str, object]]:
            if repository == "bad":
                raise RuntimeError("failed")
            return [
                {
                    "itemKey": work[0].claims[0].item_key,
                    "claimGeneration": 1,
                    "outcome": "success",
                }
            ]

        results = process_batch(items, process)
        outcomes = {result["itemKey"]: result["outcome"] for result in results}
        self.assertEqual(outcomes, {"bad#pr:1": "retry", "good#pr:1": "success"})

    def test_completed_repository_results_are_reported_before_slow_repositories_finish(
        self,
    ) -> None:
        good_reported = threading.Event()
        slow_observed_report = False
        items = [
            WorkItem("good", 1, (claim("good#pr:1", "good", pr_number=1),)),
            WorkItem("slow", 1, (claim("slow#pr:1", "slow", pr_number=1),)),
        ]

        def process(repository: str, work: list[WorkItem]) -> list[dict[str, object]]:
            nonlocal slow_observed_report
            if repository == "slow":
                slow_observed_report = good_reported.wait(timeout=1)
            return [
                {
                    "itemKey": work[0].claims[0].item_key,
                    "claimGeneration": 1,
                    "outcome": "success",
                }
            ]

        def report(results: list[dict[str, object]]) -> None:
            if results[0]["itemKey"] == "good#pr:1":
                good_reported.set()

        process_batch(items, process, max_repositories=2, on_results=report)

        self.assertTrue(slow_observed_report)

    def test_publisher_lock_busy_defers_the_repository_batch(self) -> None:
        lifecycle: list[str] = []
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "repositories.json"
            config_path.write_text(
                json.dumps([{"name": "example"}]),
                encoding="utf-8",
            )
            processor = process_queue_batch.DashboardBatchProcessor(config_path)
            items = [
                WorkItem(
                    "example",
                    number,
                    (claim(f"example#pr:{number}", "example", pr_number=number),),
                )
                for number in (1, 2, 3)
            ]

            def update(_repo, number, *_args) -> None:
                lifecycle.append(f"update-{number}")
                if number == 2:
                    raise process_queue_batch.CommandFailedError(
                        ["dashboard.py"],
                        process_queue_batch.state_branch.PUBLISHER_LOCK_BUSY_STATUS,
                    )

            with (
                mock.patch.object(
                    processor,
                    "_initial_backfill_complete",
                    return_value=True,
                ),
                mock.patch.object(processor, "_update_dashboard", side_effect=update),
                mock.patch.object(
                    processor,
                    "_deliver",
                    side_effect=lambda *_args: lifecycle.append("deliver"),
                ),
            ):
                results = processor.process_repository("example", items)

        self.assertEqual(["update-1", "update-2"], lifecycle)
        self.assertEqual(
            [result["itemKey"] for result in results],
            ["example#pr:1", "example#pr:2", "example#pr:3"],
        )
        self.assertTrue(all(result["outcome"] == "retry" for result in results))
        self.assertTrue(
            all(
                result["retryAfterMs"]
                == process_queue_batch.PUBLISHER_LOCK_RETRY_AFTER_MS
                for result in results
            )
        )

    def test_publisher_lock_deferral_does_not_dead_letter_at_attempt_limit(self) -> None:
        claim = Claim("example#pr:1", 1, "example", 1, "", 2)

        [result] = process_queue_batch.publisher_lock_acknowledgments(
            (claim,),
            process_queue_batch.state_branch.PublisherLockTimeoutError("busy"),
        )

        self.assertEqual("retry", result["outcome"])
        self.assertEqual(
            process_queue_batch.PUBLISHER_LOCK_RETRY_AFTER_MS,
            result["retryAfterMs"],
        )

    def test_queue_publisher_lock_does_not_wait(self) -> None:
        with mock.patch.object(
            process_queue_batch.state_branch,
            "publisher_lock",
            return_value=nullcontext(),
        ) as publisher_lock:
            with process_queue_batch.queue_publisher_lock("state", "worker"):
                pass

        publisher_lock.assert_called_once_with("state", "worker", wait_seconds=0)

    def test_busy_publisher_defers_updates_that_are_ready_to_deliver(self) -> None:
        @contextmanager
        def busy_publisher_lock(_branch: str, _owner: str):
            raise process_queue_batch.state_branch.PublisherLockTimeoutError("busy")
            yield

        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "repositories.json"
            config_path.write_text(
                json.dumps([{"name": "example"}]),
                encoding="utf-8",
            )
            processor = process_queue_batch.DashboardBatchProcessor(
                config_path,
                publisher_lock=busy_publisher_lock,
            )
            items = [
                WorkItem(
                    "example",
                    number,
                    (claim(f"example#pr:{number}", "example", pr_number=number),),
                )
                for number in (1, 2)
            ]
            with (
                mock.patch.object(
                    processor,
                    "_initial_backfill_complete",
                    return_value=True,
                ),
                mock.patch.object(processor, "_update_dashboard"),
            ):
                results = processor.process_repository("example", items)

        self.assertTrue(all(result["outcome"] == "retry" for result in results))
        self.assertTrue(
            all(
                result["retryAfterMs"]
                == process_queue_batch.PUBLISHER_LOCK_RETRY_AFTER_MS
                for result in results
            )
        )

    def test_delivery_error_still_publishes_committed_active_state(self) -> None:
        commands: list[str] = []

        def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            script = Path(command[1]).name
            commands.append(script)
            if script == "state.py":
                return subprocess.CompletedProcess(command, 0, stdout="true\n", stderr="")
            if script == "delivery.py":
                output_path = Path(command[command.index("--github-output") + 1])
                output_path.write_text("active=true\n", encoding="utf-8")
                return subprocess.CompletedProcess(
                    command,
                    1,
                    stdout="",
                    stderr="status comments failed\n",
                )
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "repositories.json"
            config_path.write_text(
                json.dumps([{"name": "example"}]),
                encoding="utf-8",
            )
            processor = process_queue_batch.DashboardBatchProcessor(
                config_path,
                run=run,
                publisher_lock=lambda _branch, _owner: nullcontext(),
            )
            item = WorkItem(
                "example",
                1,
                (claim("example#pr:1", "example", pr_number=1),),
            )

            results = processor.process_repository("example", [item])

        self.assertEqual(results[0]["outcome"], "retry")
        self.assertIn("publish_dashboard.py", commands)

    def test_repository_delivery_and_publication_share_one_publisher_lock(self) -> None:
        lifecycle: list[str] = []

        @contextmanager
        def publisher_lock(_branch: str, _owner: str):
            lifecycle.append("acquire")
            try:
                yield
            finally:
                lifecycle.append("release")

        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "repositories.json"
            config_path.write_text(
                json.dumps([{"name": "example"}]),
                encoding="utf-8",
            )
            processor = process_queue_batch.DashboardBatchProcessor(
                config_path,
                publisher_lock=publisher_lock,
                publisher_lock_owner="worker",
            )
            items = [
                WorkItem(
                    "example",
                    number,
                    (claim(f"example#pr:{number}", "example", pr_number=number),),
                )
                for number in (1, 2)
            ]
            with (
                mock.patch.object(
                    processor,
                    "_initial_backfill_complete",
                    return_value=True,
                ),
                mock.patch.object(
                    processor,
                    "_update_dashboard",
                    side_effect=lambda _repo, number, *_args: lifecycle.append(
                        f"update-{number}"
                    ),
                ),
                mock.patch.object(
                    processor,
                    "_deliver",
                    side_effect=lambda _repo, number, *_args: (
                        lifecycle.append(f"deliver-{number}") or (True, None)
                    ),
                ),
                mock.patch.object(
                    processor,
                    "_publish",
                    side_effect=lambda *_args: lifecycle.append("publish"),
                ),
            ):
                results = processor.process_repository("example", items)

        self.assertEqual(
            lifecycle,
            [
                "update-1",
                "update-2",
                "acquire",
                "deliver-1",
                "deliver-2",
                "publish",
                "release",
            ],
        )
        self.assertEqual([result["outcome"] for result in results], ["success", "success"])

    def test_an_aborted_batch_still_records_decided_acknowledgments(self) -> None:
        lifecycle: list[str] = []

        class Client:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def call(self, _action: str, **_payload: object) -> dict[str, bool]:
                return {"dispatcher": True}

        class Processor:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def resolve_head(self, _repository: str, _head_sha: str) -> int | None:
                return None

            def process_repository(
                self,
                _repository: str,
                _items: list[WorkItem],
            ) -> list[dict[str, object]]:
                raise AssertionError("unreachable")

        def acknowledge(*_args: object, **_kwargs: object) -> dict[str, int]:
            lifecycle.append("acknowledge")
            return {"acknowledged": 1}

        original_close = LeaseMonitor.close

        def close(monitor: LeaseMonitor) -> None:
            lifecycle.append("close")
            original_close(monitor)

        with tempfile.TemporaryDirectory() as directory:
            claims_path = Path(directory) / "claims.json"
            results_path = Path(directory) / "results.json"
            claims_path.write_text(
                json.dumps(
                    [
                        {
                            "itemKey": "example#head:abc",
                            "claimGeneration": 1,
                            "repository": "example",
                            "headSha": "a" * 40,
                            "attempts": 0,
                        }
                    ]
                ),
                encoding="utf-8",
            )
            argv = [
                "process_queue_batch.py",
                "--claims",
                str(claims_path),
                "--results",
                str(results_path),
                # An invalid bound aborts the batch after head resolution has
                # already decided an acknowledgment.
                "--max-repositories",
                "0",
                "--queue-endpoint",
                "https://example.test/worker",
                "--dispatcher-generation",
                "1",
                "--worker-id",
                "worker",
            ]
            with (
                mock.patch.object(process_queue_batch, "QueueWorkerClient", Client),
                mock.patch.object(process_queue_batch, "DashboardBatchProcessor", Processor),
                mock.patch.object(process_queue_batch, "acknowledge_all", side_effect=acknowledge),
                mock.patch.object(LeaseMonitor, "close", new=close),
                mock.patch.object(sys, "argv", argv),
            ):
                with self.assertRaises(ValueError):
                    process_queue_batch.main()

            results = json.loads(results_path.read_text(encoding="utf-8"))
        self.assertEqual(
            [(result["itemKey"], result["outcome"]) for result in results],
            [("example#head:abc", "success")],
        )
        self.assertEqual(lifecycle, ["acknowledge", "close"])


if __name__ == "__main__":
    unittest.main()
