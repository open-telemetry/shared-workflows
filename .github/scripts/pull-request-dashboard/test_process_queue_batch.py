from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import process_queue_batch
from execution_process import subprocess_options
from process_queue_batch import (
    Claim,
    LeaseMonitor,
    WorkItem,
    group_by_repository,
    load_claims,
    process_batch,
    process_claims,
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
                            "triggerEvents": ["pull_request", "status"],
                            "attempts": 0,
                        }
                    ]
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                load_claims(path),
                [
                    Claim(
                        "example#pr:1",
                        1,
                        "example",
                        1,
                        "",
                        0,
                        ("pull_request", "status"),
                    )
                ],
            )

    def test_head_and_pr_claims_collapse_to_one_work_item(self) -> None:
        claims = [
            claim("example#pr:1", "example", pr_number=1),
            claim("example#head:abc", "example", head_sha="a" * 40),
        ]
        work, completed = resolve_work_items(claims, lambda _repo, _sha: (1,))
        self.assertEqual(completed, [])
        self.assertEqual(len(work), 1)
        self.assertEqual(len(work[0].claims), 2)

    def test_unresolved_heads_are_acknowledged_without_work(self) -> None:
        head = claim("example#head:abc", "example", head_sha="a" * 40)
        work, completed = resolve_work_items([head], lambda _repo, _sha: ())
        self.assertEqual(work, [])
        self.assertEqual(completed[0]["outcome"], "success")

    def test_head_resolution_failure_retries_only_that_claim(self) -> None:
        direct = claim("example#pr:1", "example", pr_number=1)
        head = claim("example#head:abc", "example", head_sha="a" * 40)

        def fail_resolution(_repository: str, _head_sha: str) -> tuple[int, ...]:
            raise RuntimeError("GitHub API unavailable")

        work, completed = resolve_work_items([direct, head], fail_resolution)
        self.assertEqual([item.pr_number for item in work], [1])
        self.assertEqual(completed[0]["itemKey"], head.item_key)
        self.assertEqual(completed[0]["outcome"], "retry")

    def test_one_head_claim_refreshes_every_matching_pr_and_waits_for_all(self) -> None:
        head = claim("example#head:abc", "example", head_sha="a" * 40)
        direct = claim("example#pr:7", "example", pr_number=7)
        work, completed = resolve_work_items(
            [head, direct],
            lambda _repo, _sha: (7, 9),
        )
        self.assertEqual(completed, [])
        self.assertEqual([item.pr_number for item in work], [7, 9])
        self.assertEqual(work[0].claims, (head, direct))
        self.assertEqual(work[1].claims, (head,))

        processed: list[int] = []

        def process(_repository: str, items: list[WorkItem]) -> list[dict[str, object]]:
            results = []
            for item in items:
                processed.append(item.pr_number)
                for item_claim in item.claims:
                    results.append(
                        process_queue_batch.acknowledgment(
                            item_claim,
                            "retry" if item.pr_number == 9 else "success",
                        )
                    )
            return results

        results = process_batch(work, process)
        self.assertEqual(processed, [7, 9])
        self.assertEqual(
            [(result["itemKey"], result["outcome"]) for result in results],
            [("example#head:abc", "retry"), ("example#pr:7", "success")],
        )

    def test_canary_head_lookup_finds_fork_prs_across_pages(self) -> None:
        sha = "a" * 40
        paths: list[str] = []

        def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            paths.append(command[-1])
            self.assertEqual(_kwargs["encoding"], "utf-8")
            self.assertEqual(command[2:4], ["--paginate", "--slurp"])
            data = [
                [{"number": 3, "state": "open", "head": {"sha": "other"}}],
                [
                    {"number": 9, "state": "open", "head": {"sha": sha}},
                    {"number": 7, "state": "open", "head": {"sha": sha}},
                    {"number": 5, "state": "closed", "head": {"sha": sha}},
                ],
            ]
            return subprocess.CompletedProcess(
                command, 0, stdout=json.dumps(data), stderr=""
            )

        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "repositories.json"
            config_path.write_text("[]", encoding="utf-8")
            processor = process_queue_batch.DashboardBatchProcessor(config_path, run=run)
            self.assertEqual(processor.resolve_head("example", sha), (7, 9))
        self.assertEqual(
            paths,
            [
                "repos/open-telemetry/example/pulls?state=open&per_page=100",
            ],
        )

    def test_canary_head_lookup_preserves_genuine_no_match(self) -> None:
        paths: list[str] = []

        def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            paths.append(command[-1])
            return subprocess.CompletedProcess(
                command,
                0,
                stdout='[[{"number":7,"state":"open","head":{"sha":"other"}}]]',
                stderr="",
            )

        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "repositories.json"
            config_path.write_text("[]", encoding="utf-8")
            processor = process_queue_batch.DashboardBatchProcessor(config_path, run=run)
            self.assertEqual(processor.resolve_head("example", "a" * 40), ())
        self.assertEqual(len(paths), 1)

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

    def test_delivery_error_still_publishes_committed_active_state(self) -> None:
        commands: list[str] = []
        delivery_commands: list[list[str]] = []

        def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            script = Path(command[1]).name
            commands.append(script)
            if script == "state.py":
                return subprocess.CompletedProcess(command, 0, stdout="true\n", stderr="")
            if script == "delivery.py":
                delivery_commands.append(command)
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
        self.assertIn("--delivery-state-branch", delivery_commands[0])
        self.assertIn(
            "otelbot/pull-request-dashboard-delivery/example",
            delivery_commands[0],
        )

    def test_repository_delivery_and_publication_do_not_use_a_shared_lock(self) -> None:
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
                        lifecycle.append(f"deliver-{number}") or (True, None, 0)
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
                "deliver-1",
                "deliver-2",
                "publish",
            ],
        )
        self.assertEqual([result["outcome"] for result in results], ["success", "success"])

    def test_queue_skips_issue_publication_already_completed_by_full_delivery(self) -> None:
        for deliveries, expected_generations in (
            ([(True, None, 5)], [5]),
            ([(True, None, 0), (True, None, 5)], [5]),
            ([(True, None, 5), (True, None, 6)], [5, 6]),
        ):
            with self.subTest(deliveries=deliveries), tempfile.TemporaryDirectory() as directory:
                config_path = Path(directory) / "repositories.json"
                config_path.write_text(json.dumps([{"name": "example"}]), encoding="utf-8")
                processor = process_queue_batch.DashboardBatchProcessor(
                    config_path, env={"REPO_NAME": "example"},
                )
                items = [
                    WorkItem("example", number, (claim(f"example#pr:{number}", "example", pr_number=number),))
                    for number in range(1, len(deliveries) + 1)
                ]
                with (
                    mock.patch.object(processor, "_initial_backfill_complete", return_value=True),
                    mock.patch.object(processor, "_update_dashboard"),
                    mock.patch.object(processor, "_deliver", side_effect=deliveries),
                    mock.patch.object(
                        processor, "_publish",
                        side_effect=[None] * len(expected_generations) + [
                            RuntimeError("redundant publication failed"),
                        ],
                    ) as publish,
                    mock.patch.object(processor, "_complete_full_publish") as acknowledge,
                ):
                    results = processor.process_repository("example", items)
                self.assertEqual(["success"] * len(items), [result["outcome"] for result in results])
                self.assertEqual(len(expected_generations), publish.call_count)
                self.assertEqual(
                    expected_generations,
                    [call.args[2] for call in acknowledge.call_args_list],
                )

    def test_inactive_delivery_is_not_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "repositories.json"
            config.write_text('[{"name":"example"}]', encoding="utf-8")
            processor = process_queue_batch.DashboardBatchProcessor(config)
            item = WorkItem("example", 1, (claim("example#pr:1", "example", pr_number=1),))
            with (
                mock.patch.object(processor, "_initial_backfill_complete", return_value=True),
                mock.patch.object(processor, "_update_dashboard"),
                mock.patch.object(processor, "_deliver", return_value=(False, None, 0)),
            ):
                results = processor.process_repository("example", [item])
        self.assertEqual(results[0]["outcome"], "retry")
        self.assertIn("versions", results[0]["error"])

    def test_queue_drains_full_obligation_once_before_next_targeted_item(self) -> None:
        lifecycle: list[str] = []
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "repositories.json"
            config_path.write_text(json.dumps([{"name": "example"}]), encoding="utf-8")
            processor = process_queue_batch.DashboardBatchProcessor(config_path)
            items = [
                WorkItem("example", number, (claim(f"example#pr:{number}", "example", pr_number=number),))
                for number in (1, 2)
            ]
            with (
                mock.patch.object(processor, "_initial_backfill_complete", return_value=True),
                mock.patch.object(processor, "_update_dashboard"),
                mock.patch.object(
                    processor, "_deliver",
                    side_effect=lambda _repo, number, *_args: (
                        lifecycle.append(f"deliver-{number}") or (True, None, 5 if number == 1 else 0)
                    ),
                ),
                mock.patch.object(processor, "_publish", side_effect=lambda *_args: lifecycle.append("publish")),
                mock.patch.object(
                    processor, "_complete_full_publish",
                    side_effect=lambda *_args: lifecycle.append("acknowledge"),
                ) as acknowledge,
            ):
                results = processor.process_repository("example", items)
        self.assertEqual(
            ["deliver-1", "publish", "acknowledge", "deliver-2", "publish"],
            lifecycle,
        )
        acknowledge.assert_called_once()
        self.assertEqual(["success", "success"], [result["outcome"] for result in results])

    def test_queue_publishes_later_active_state_even_when_delivery_fails(self) -> None:
        lifecycle: list[str] = []
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "repositories.json"
            config_path.write_text(json.dumps([{"name": "example"}]), encoding="utf-8")
            processor = process_queue_batch.DashboardBatchProcessor(
                config_path, env={"REPO_NAME": "example"},
            )
            items = [
                WorkItem("example", number, (claim(f"example#pr:{number}", "example", pr_number=number),))
                for number in (1, 2)
            ]
            with (
                mock.patch.object(processor, "_initial_backfill_complete", return_value=True),
                mock.patch.object(processor, "_update_dashboard"),
                mock.patch.object(
                    processor, "_deliver",
                    side_effect=[(True, None, 5), (True, RuntimeError("status comments failed"), 0)],
                ),
                mock.patch.object(processor, "_publish", side_effect=lambda *_args: lifecycle.append("publish")),
                mock.patch.object(
                    processor, "_complete_full_publish",
                    side_effect=lambda *_args: lifecycle.append("acknowledge"),
                ),
            ):
                results = processor.process_repository("example", items)
        self.assertEqual(["publish", "acknowledge", "publish"], lifecycle)
        self.assertEqual(
            {"example#pr:1": "success", "example#pr:2": "retry"},
            {result["itemKey"]: result["outcome"] for result in results},
        )

    def test_queue_receipt_failure_retries_without_republishing_the_issue(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "repositories.json"
            config_path.write_text(json.dumps([{"name": "example"}]), encoding="utf-8")
            processor = process_queue_batch.DashboardBatchProcessor(
                config_path, env={"REPO_NAME": "example"},
            )
            item = WorkItem("example", 1, (claim("example#pr:1", "example", pr_number=1),))
            with (
                mock.patch.object(processor, "_initial_backfill_complete", return_value=True),
                mock.patch.object(processor, "_update_dashboard"),
                mock.patch.object(processor, "_deliver", return_value=(True, None, 5)),
                mock.patch.object(processor, "_publish") as publish,
                mock.patch.object(
                    processor, "_complete_full_publish", side_effect=RuntimeError("receipt push failed"),
                ) as acknowledge,
            ):
                results = processor.process_repository("example", [item])
        self.assertEqual("retry", results[0]["outcome"])
        self.assertEqual("receipt push failed", results[0]["error"])
        publish.assert_called_once()
        acknowledge.assert_called_once()

    def test_queue_does_not_acknowledge_failed_full_publication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "repositories.json"
            config_path.write_text(json.dumps([{"name": "example"}]), encoding="utf-8")
            processor = process_queue_batch.DashboardBatchProcessor(config_path)
            item = WorkItem("example", 1, (claim("example#pr:1", "example", pr_number=1),))
            with (
                mock.patch.object(processor, "_initial_backfill_complete", return_value=True),
                mock.patch.object(processor, "_update_dashboard"),
                mock.patch.object(processor, "_deliver", return_value=(True, None, 5)),
                mock.patch.object(processor, "_publish", side_effect=RuntimeError("issue unavailable")),
                mock.patch.object(processor, "_complete_full_publish") as acknowledge,
            ):
                results = processor.process_repository("example", [item])
        self.assertEqual("retry", results[0]["outcome"])
        acknowledge.assert_not_called()

    def test_an_aborted_batch_still_records_decided_acknowledgments(self) -> None:
        lifecycle: list[str] = []

        class Client:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def call(self, action: str, **_payload: object) -> dict[str, bool]:
                if action == "acknowledge":
                    lifecycle.append("acknowledge")
                return {"dispatcher": True}

        class Processor:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def resolve_head(self, _repository: str, _head_sha: str) -> tuple[int, ...]:
                return ()

            def process_repository(
                self,
                _repository: str,
                _items: list[WorkItem],
            ) -> list[dict[str, object]]:
                raise AssertionError("unreachable")

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

    def test_results_file_contains_only_accepted_acknowledgments(self) -> None:
        class Client:
            def call(self, action: str, **payload: object) -> dict[str, bool]:
                if action == "heartbeat":
                    return {"dispatcher": True}
                if payload["itemKey"] == "example#pr:1":
                    raise RuntimeError("acknowledgment unavailable")
                return {"dispatcher": True}

        class Processor:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def resolve_head(self, _repository: str, _head_sha: str) -> tuple[int, ...]:
                raise AssertionError("unreachable")

            def process_repository(
                self,
                _repository: str,
                items: list[WorkItem],
            ) -> list[dict[str, object]]:
                return [
                    process_queue_batch.acknowledgment(item_claim, "success")
                    for item in items
                    for item_claim in item.claims
                ]

        claims = [
            claim("example#pr:1", "example", pr_number=1),
            claim("example#pr:2", "example", pr_number=2),
        ]
        with tempfile.TemporaryDirectory() as directory:
            results_path = Path(directory) / "results.json"
            with (
                mock.patch.object(
                    process_queue_batch,
                    "DashboardBatchProcessor",
                    Processor,
                ),
                self.assertRaisesRegex(
                    RuntimeError,
                    "1 incremental acknowledgment",
                ),
            ):
                process_claims(
                    claims,
                    results_path,
                    Client(),
                    1,
                    "worker",
                    config_path=Path(directory) / "repositories.json",
                )

            results = json.loads(results_path.read_text(encoding="utf-8"))

        self.assertEqual(
            [result["itemKey"] for result in results],
            ["example#pr:2"],
        )


class QueueBatchGitTest(unittest.TestCase):
    def test_subprocess_config_preserves_credentials_and_overrides_auto_tracking(self) -> None:
        env = {
            "GH_TOKEN": "repository-token",
            "GIT_CONFIG_COUNT": "2",
            "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
            "GIT_CONFIG_VALUE_0": "AUTHORIZATION: basic test-credential",
            "GIT_CONFIG_KEY_1": "branch.autoSetupMerge",
            "GIT_CONFIG_VALUE_1": "true",
        }
        original_env = dict(env)
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "repositories.json"
            config.write_text("[]", encoding="utf-8")
            processor = process_queue_batch.DashboardBatchProcessor(config, env=env)

        self.assertEqual(env, original_env)
        for name, value in original_env.items():
            if name != "GIT_CONFIG_COUNT":
                self.assertEqual(processor.base_env[name], value)
        self.assertEqual(processor.base_env["GIT_CONFIG_COUNT"], "3")
        self.assertEqual(processor.base_env["GIT_CONFIG_KEY_2"], "branch.autoSetupMerge")
        self.assertEqual(processor.base_env["GIT_CONFIG_VALUE_2"], "false")

    def test_invalid_subprocess_config_count_is_not_silently_replaced(self) -> None:
        for value in ("invalid", "-1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                process_queue_batch.DashboardBatchProcessor(
                    Path("unused.json"), env={"GIT_CONFIG_COUNT": value},
                )

    def test_four_workers_can_check_out_pinned_state_with_shared_config_locked(self) -> None:
        checkout = textwrap.dedent("""
            import os
            import subprocess
            import sys
            from pathlib import Path

            sys.path.insert(0, sys.argv[1])
            import state_branch

            original_run = subprocess.run
            def run(*args, **kwargs):
                if os.name == "nt":
                    kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
                check = kwargs.pop("check", False)
                capture = kwargs.get("capture_output", False)
                kwargs["capture_output"] = True
                result = original_run(*args, check=False, **kwargs)
                if not capture:
                    sys.stdout.write(result.stdout)
                    sys.stderr.write(result.stderr)
                if check:
                    result.check_returncode()
                return result
            subprocess.run = run

            state_branch.checkout_state(Path(sys.argv[2]), sys.argv[3], False)
        """)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {
                name: value for name, value in os.environ.items()
                if not name.startswith("GIT_CONFIG")
            }
            env.update({
                "GIT_CONFIG_GLOBAL": str(root / "empty-global-config"),
                "GIT_CONFIG_NOSYSTEM": "1",
            })

            def git(*args: str, cwd: Path = root, check: bool = True) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    ["git", *args], cwd=cwd, env=env, check=check,
                    text=True, capture_output=True, **subprocess_options(),
                )

            git("init", "--quiet")
            git("config", "user.name", "Test")
            git("config", "user.email", "test@example.com")
            git("config", "branch.autoSetupMerge", "true")
            git("commit", "--quiet", "--allow-empty", "-m", "Initial state")
            git("remote", "add", "origin", str(root))
            initial_oid = git("rev-parse", "HEAD").stdout.strip()
            repositories = [f"repo-{index}" for index in range(4)]
            branches = [
                f"{prefix}/{repository}"
                for repository in repositories
                for prefix in (
                    process_queue_batch.state_branch_git.STATE_BRANCH_PREFIX,
                    process_queue_batch.state_branch_git.DELIVERY_STATE_BRANCH_PREFIX,
                )
            ]
            for branch in ["baseline", *branches]:
                git("update-ref", f"refs/heads/{branch}", initial_oid)

            pinned_scripts = root / "immutable-code"
            pinned_scripts.mkdir()
            shutil.copyfile(
                process_queue_batch.SCRIPT_DIR / "state_branch.py",
                pinned_scripts / "state_branch.py",
            )
            config = root / "repositories.json"
            config.write_text(
                json.dumps([{"name": name} for name in repositories]), encoding="utf-8",
            )
            processor = process_queue_batch.DashboardBatchProcessor(
                config, script_dir=pinned_scripts, repository_root=root,
                env=env, lease_check=lambda: None,
            )
            config_before = (root / ".git" / "config").read_bytes()
            config_lock = root / ".git" / "config.lock"
            config_lock.write_text("another worker holds the lock", encoding="utf-8")
            baseline = subprocess.run(
                [
                    sys.executable, "-c", checkout, str(pinned_scripts),
                    str(root / "baseline"), "baseline",
                ],
                cwd=root, env=env, text=True, capture_output=True,
                **subprocess_options(),
            )
            self.assertNotEqual(baseline.returncode, 0)
            self.assertIn("could not lock config file", baseline.stderr)
            self.assertIn("unable to write upstream branch configuration", baseline.stderr)

            barrier = threading.Barrier(4)

            def update(
                repository: str, _number: int | None, state_branch: str,
                _config: dict[str, object], worker_env: dict[str, str],
            ) -> None:
                barrier.wait(timeout=10)
                for branch in (
                    state_branch,
                    process_queue_batch.state_branch_git.delivery_state_branch(state_branch),
                ):
                    processor._run(
                        [
                            sys.executable, "-c", checkout, str(pinned_scripts),
                            str(root / "checkouts" / branch), branch,
                        ],
                        env=worker_env,
                    )

            items = [
                WorkItem(name, 1, (claim(f"{name}#pr:1", name, pr_number=1),))
                for name in repositories
            ]
            with (
                mock.patch.object(processor, "_initial_backfill_complete", return_value=True),
                mock.patch.object(processor, "_update_dashboard", side_effect=update),
                mock.patch.object(processor, "_deliver", return_value=(True, None, 0)),
                mock.patch.object(processor, "_publish"),
            ):
                results = process_batch(items, processor.process_repository, max_repositories=4)

            self.assertEqual(
                [(result["itemKey"], result["outcome"]) for result in results],
                [(f"{name}#pr:1", "success") for name in repositories],
                results,
            )
            self.assertTrue(config_lock.exists())
            self.assertEqual((root / ".git" / "config").read_bytes(), config_before)
            for branch in branches:
                state_dir = root / "checkouts" / branch
                self.assertEqual(git("rev-parse", "HEAD", cwd=state_dir).stdout.strip(), initial_oid)
                self.assertEqual(
                    git("symbolic-ref", "HEAD", cwd=state_dir).stdout.strip(),
                    f"refs/heads/{branch}",
                )
                self.assertEqual(
                    git("config", "--local", "--get", f"branch.{branch}.remote", check=False).returncode,
                    1,
                )
            config_lock.unlink()


if __name__ == "__main__":
    unittest.main()
