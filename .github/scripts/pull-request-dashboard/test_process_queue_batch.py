from __future__ import annotations

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
            )
            item = WorkItem(
                "example",
                1,
                (claim("example#pr:1", "example", pr_number=1),),
            )

            results = processor.process_repository("example", [item])

        self.assertEqual(results[0]["outcome"], "retry")
        self.assertIn("publish_dashboard.py", commands)

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
