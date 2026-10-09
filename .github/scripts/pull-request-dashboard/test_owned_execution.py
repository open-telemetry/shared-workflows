from __future__ import annotations

import argparse
import concurrent.futures
import json
import io
import os
import subprocess
import sys
import tempfile
import tarfile
import textwrap
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import drain_queue
import enqueue_dashboard
import execution_code
import execution_process
from enqueue_dashboard import requests
from execution_code import ExecutionCode, stable_code_ref
from process_queue_batch import Claim, DashboardBatchProcessor, LeaseMonitor, WorkItem, parse_claims
from promote_rollout import promoted_text


class OwnedExecutionTest(unittest.TestCase):
    def test_immutable_stable_pin_follows_existing_promotion_tool(self) -> None:
        workflow = execution_code.SCRIPT_DIR.parents[1] / "workflows" / "pull-request-dashboard.yml"
        current = stable_code_ref(workflow)
        self.assertRegex(current, r"^[0-9a-f]{40}$")
        with tempfile.TemporaryDirectory() as directory:
            promoted = Path(directory) / "workflow.yml"
            promoted.write_text(
                promoted_text(workflow.read_text(encoding="utf-8"), "v99.0.0", "a" * 40),
                encoding="utf-8",
            )
            self.assertEqual(stable_code_ref(promoted), "a" * 40)
        loader = execution_code.ExecutionCodeLoader(Path("."), mock.Mock(), {})
        for ref in ("main", "v0.18.0", "../main"):
            with self.subTest(ref=ref), self.assertRaisesRegex(ValueError, "immutable"):
                loader.load(ref)

    def test_rejected_activation_claims_nothing_and_does_not_mint_credentials(self) -> None:
        client = mock.Mock()
        client.call.return_value = {"activated": False}
        args = argparse.Namespace(endpoint="https://example.test", lane="live", generation=1, worker="old")
        with (
            mock.patch.object(drain_queue, "QueueWorkerClient", return_value=client),
            mock.patch.object(drain_queue, "take_github_app_credentials") as credentials,
        ):
            self.assertEqual(drain_queue.run_owned_drain(args), 0)
        client.call.assert_called_once_with("activate", generation=1, workerId="old")
        credentials.assert_not_called()

    def test_code_loading_does_not_overwrite_worker_and_installs_pinned_dependencies(self) -> None:
        commands = []
        ref = "a" * 40
        with tempfile.TemporaryDirectory() as directory:
            loader = execution_code.ExecutionCodeLoader(Path(directory), mock.Mock(spec=LeaseMonitor), {})

            def run(command) -> None:
                commands.append(command)
                if command[1] == "archive":
                    archive = Path(command[3].removeprefix("--output="))
                    with tarfile.open(archive, "w") as output:
                        for name in ("dashboard.py", "requirements.txt", "package.json"):
                            data = b"pinned code"
                            info = tarfile.TarInfo(f"{execution_code.CODE_PATH}/{name}")
                            info.size = len(data)
                            output.addfile(info, io.BytesIO(data))

            with mock.patch.object(loader, "_run", side_effect=run):
                code = loader.load(ref)
                self.assertIs(loader.load(ref), code)
            self.assertEqual(code.ref, ref)
            self.assertEqual((code.script_dir / "dashboard.py").read_text(), "pinned code")
            self.assertEqual(commands[0], ["git", "fetch", "--quiet", "--no-tags", "origin", ref])
            self.assertIn(str(code.script_dir / "requirements.txt"), commands[3])
            self.assertEqual(commands[-1], [code.python, "-m", "copilot", "download-runtime"])
            self.assertNotIn("checkout", [part for command in commands for part in command])
            self.assertNotEqual(code.script_dir, execution_code.SCRIPT_DIR)

    def test_failed_code_preparation_can_be_retried(self) -> None:
        ref = "b" * 40
        with tempfile.TemporaryDirectory() as directory:
            loader = execution_code.ExecutionCodeLoader(Path(directory), mock.Mock(spec=LeaseMonitor), {})
            with mock.patch.object(loader, "_run", side_effect=RuntimeError("fetch failed")):
                with self.assertRaises(RuntimeError):
                    loader.load(ref)
            (Path(directory) / ref / "leftover").write_text("partial")

            def run(command) -> None:
                if command[1] == "archive":
                    with tarfile.open(command[3].removeprefix("--output="), "w") as output:
                        info = tarfile.TarInfo(f"{execution_code.CODE_PATH}/dashboard.py")
                        info.size = 1
                        output.addfile(info, io.BytesIO(b"x"))

            with mock.patch.object(loader, "_run", side_effect=run):
                code = loader.load(ref)
            self.assertFalse((Path(directory) / ref / "leftover").exists())
            self.assertEqual(code.ref, ref)

    def test_monotonic_deadline_expires_even_when_heartbeat_is_stuck(self) -> None:
        clock = [10.0]
        client = mock.Mock()
        client.call.return_value = {"dispatcher": True, "leaseDurationMs": 900_000}
        monitor = LeaseMonitor(client, 1, "worker", now=lambda: clock[0])
        monitor._send_heartbeat()
        clock[0] = 849.9
        monitor.assert_valid()
        clock[0] = 850.0
        with self.assertRaisesRegex(RuntimeError, "safety deadline"):
            monitor.assert_valid()

    def test_lease_loss_kills_subprocess_descendants_before_later_effects(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            effect = Path(directory) / "effect"
            started = Path(directory) / "started"
            child_code = (
                "import time; from pathlib import Path; "
                f"Path({str(started)!r}).write_text('started'); "
                f"time.sleep(1); Path({str(effect)!r}).write_text('effect')"
            )
            parent_code = (
                "import os, subprocess, sys, time; "
                "flags = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}; "
                f"subprocess.Popen([sys.executable, '-c', {child_code!r}], **flags); "
                "time.sleep(20)"
            )
            deadline = time.monotonic() + 5

            def lease_check() -> None:
                if started.exists() or time.monotonic() >= deadline:
                    raise RuntimeError("lease lost")

            with self.assertRaisesRegex(RuntimeError, "lease lost"):
                execution_process.run_monitored([sys.executable, "-c", parent_code], lease_check)
            self.assertTrue(started.exists())
            time.sleep(1.5)
            self.assertFalse(effect.exists())

    @unittest.skipUnless(os.name == "nt", "Windows console launch flags")
    def test_windows_launches_hide_console_windows(self) -> None:
        with mock.patch.object(execution_process.os, "name", "nt"):
            options = execution_process.subprocess_options()
        self.assertTrue(options["creationflags"] & subprocess.CREATE_NO_WINDOW)

    def test_controls_and_existing_refresh_claims_share_the_processor(self) -> None:
        claims = parse_claims([
            {"itemKey": "example#backfill", "repository": "example", "claimGeneration": 1, "kind": "backfill"},
            {"itemKey": "example#reminders", "repository": "example", "claimGeneration": 1, "kind": "reminders"},
            {"itemKey": "example#pr:1", "repository": "example", "claimGeneration": 1, "prNumber": 1},
        ])
        self.assertEqual([item.kind for item in claims], ["backfill", "reminders", "refresh"])

    def test_legacy_waves_reject_controls_before_starting_writers(self) -> None:
        client = mock.Mock()
        tokens = mock.Mock()
        with self.assertRaisesRegex(RuntimeError, "require owned execution"):
            drain_queue.process_claim_wave(
                [Claim("example#backfill", 1, "example", None, "", 0, (), "backfill")],
                Path("unused.json"), client, 1, "legacy", tokens,
                canary_repositories=frozenset({"example"}),
                configured_repositories=frozenset({"example"}),
                resolve_stable_head=mock.Mock(), dispatch_stable=mock.Mock(),
            )
        client.call.assert_not_called()
        tokens.mint.assert_not_called()

    def test_partial_progress_inputs_cannot_enqueue_work(self) -> None:
        fields = [
            ["--status-item-key", "example#pr:1"],
            ["--status-generation", "1"],
            ["--status-request-id", "request"],
        ]
        for flags in [*fields, fields[0] + fields[1], fields[0] + fields[2], fields[1] + fields[2]]:
            with (
                self.subTest(flags=flags),
                mock.patch.dict(os.environ, {}, clear=True),
                mock.patch.object(sys, "argv", ["enqueue_dashboard.py", *flags]),
                mock.patch.object(sys, "stderr", io.StringIO()),
                mock.patch.object(enqueue_dashboard, "QueueWorkerClient") as client,
                self.assertRaises(SystemExit),
            ):
                enqueue_dashboard.main()
            client.assert_not_called()

    def test_unknown_progress_is_reported_with_request_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary = Path(directory) / "summary"
            result = {
                "itemKey": "example#pr:1", "generation": 1,
                "requestId": "request", "status": "unknown",
            }
            client = mock.Mock()
            client.call.return_value = result
            with (
                mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary)}, clear=True),
                mock.patch.object(sys, "argv", [
                    "enqueue_dashboard.py", "--status-item-key", "example#pr:1",
                    "--status-generation", "1", "--status-request-id", "request",
                ]),
                mock.patch.object(sys, "stdout", io.StringIO()),
                mock.patch.object(enqueue_dashboard, "QueueWorkerClient", return_value=client),
            ):
                enqueue_dashboard.main()
            client.call.assert_called_once_with(
                "status", itemKey="example#pr:1", generation=1, requestId="request",
            )
            self.assertIn("| `request` | unknown |", summary.read_text(encoding="utf-8"))

    def test_enqueue_and_progress_use_the_work_lane(self) -> None:
        for flags, expected_lane in [
            (["--repository", "shared-workflows", "--trigger-event", "schedule"], "maintenance"),
            (["--repository", "shared-workflows", "--reminders"], "maintenance"),
            (["--repository", "shared-workflows", "--pr-number", "1"], "live"),
            ([
                "--status-item-key", "shared-workflows#backfill",
                "--status-generation", "1", "--status-request-id", "request",
            ], "maintenance"),
        ]:
            with self.subTest(lane=expected_lane, flags=flags):
                client = mock.Mock()
                client.call.return_value = {
                    "accepted": True, "completed": False, "requestId": "request",
                }
                with (
                    mock.patch.dict(os.environ, {}, clear=True),
                    mock.patch.object(sys, "argv", ["enqueue_dashboard.py", *flags]),
                    mock.patch.object(sys, "stdout", io.StringIO()),
                    mock.patch.object(enqueue_dashboard, "QueueWorkerClient", return_value=client) as factory,
                ):
                    enqueue_dashboard.main()
                factory.assert_called_once_with("", lane=expected_lane)

    def test_enqueue_requires_a_trackable_acceptance(self) -> None:
        client = mock.Mock()
        client.call.return_value = {"accepted": True, "completed": False}
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch.object(sys, "argv", [
                "enqueue_dashboard.py", "--repository", "shared-workflows", "--pr-number", "1",
            ]),
            mock.patch.object(enqueue_dashboard, "QueueWorkerClient", return_value=client),
            self.assertRaisesRegex(RuntimeError, "did not confirm acceptance"),
        ):
            enqueue_dashboard.main()

    def test_entry_inputs_preserve_target_and_head_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.json"
            config.write_text('[{"name":"a"},{"name":"b","large_repo":true}]', encoding="utf-8")
            self.assertEqual(
                [(item["repository"], item["kind"], item["triggerEvent"]) for item in requests(config, "", "", "", "schedule")],
                [("a", "backfill", "schedule"), ("b", "backfill", "schedule")],
            )
            self.assertEqual(requests(config, "b", "", "a" * 40, "status")[0]["headSha"], "a" * 40)
            self.assertEqual(requests(config, "a", "1", "", "pull_request")[0]["prNumber"], 1)
            self.assertEqual(requests(config, "a", "", "", "workflow_dispatch", reminders=True)[0]["kind"], "reminders")
            for repository, pr_number, head_sha in [
                ("", "1", ""), ("open-telemetry/a", "", ""), ("a", "01", ""),
                ("a", "1", "a" * 40), ("a", "", "not-a-sha"),
            ]:
                with self.subTest(repository=repository, pr=pr_number), self.assertRaises(ValueError):
                    requests(config, repository, pr_number, head_sha, "workflow_dispatch")

    def test_stable_claims_are_processed_not_dispatched_and_use_current_config(self) -> None:
        claim = Claim("example#pr:1", 1, "example", 1, "", 0)
        code = ExecutionCode("a" * 40, Path("promoted-code"), "promoted-python")
        client = mock.Mock()
        token_client = mock.Mock()
        token_client.mint.return_value = "token"
        monitor = mock.Mock(spec=LeaseMonitor)
        with tempfile.TemporaryDirectory() as directory:
            result_path = Path(directory) / "results.json"
            with (
                mock.patch.object(drain_queue, "process_claims", return_value=({}, [
                    {"itemKey": claim.item_key, "outcome": "success"},
                ])) as process,
                mock.patch.object(drain_queue, "acknowledge_results"),
            ):
                dispatch = mock.Mock()
                drain_queue.process_claim_wave(
                    [claim], result_path, client, 1, "worker", token_client,
                    canary_repositories=frozenset(), configured_repositories=frozenset({"example"}),
                    resolve_stable_head=mock.Mock(), dispatch_stable=dispatch,
                    execution_code={"example": code},
                    repository_roots={"example": Path("isolated-checkout")},
                    lease_monitor=monitor,
                    report_limits=mock.Mock(),
                )
            dispatch.assert_not_called()
            self.assertEqual(process.call_args.kwargs["script_dir"], Path("promoted-code"))
            self.assertEqual(process.call_args.kwargs["python_executable"], "promoted-python")
            self.assertEqual(process.call_args.kwargs["repository_root"], Path("isolated-checkout"))
            # Omitting config_path deliberately uses the current worker's configuration.
            self.assertNotIn("config_path", process.call_args.kwargs)
            self.assertNotIn("PR_DASHBOARD_PRIVATE_KEY", process.call_args.kwargs["processor_env"])

    def test_owned_wave_requires_an_isolated_checkout_before_processing(self) -> None:
        claim = Claim("example#pr:1", 1, "example", 1, "", 0)
        code = ExecutionCode("a" * 40, Path("promoted-code"), "promoted-python")
        for codes, roots in (
            ({"example": code}, None),
            ({"example": code}, {}),
            ({}, {"example": Path("isolated-checkout")}),
        ):
            with (
                self.subTest(codes=codes, roots=roots),
                mock.patch.object(drain_queue, "process_repository_claims") as process,
                self.assertRaisesRegex(RuntimeError, "requires isolated Git checkouts"),
            ):
                drain_queue.process_claim_wave(
                    [claim], Path("unused.json"), mock.Mock(), 1, "worker", mock.Mock(),
                    canary_repositories=frozenset(), configured_repositories=frozenset({"example"}),
                    resolve_stable_head=mock.Mock(), dispatch_stable=mock.Mock(),
                    execution_code=codes, repository_roots=roots,
                )
            process.assert_not_called()

    def test_owned_waves_preserve_code_selection_and_clean_up_checkouts(self) -> None:
        client = mock.Mock()
        claims = [
            {"itemKey": f"{repository}#pr:1", "repository": repository, "claimGeneration": 1, "prNumber": 1}
            for repository in ("canary", "stable")
        ]
        waves = iter([claims, claims, []])
        client.call.side_effect = lambda action, **_kwargs: (
            {"activated": True} if action == "activate"
            else {"claims": next(waves)} if action == "claim"
            else {}
        )
        monitor = mock.Mock(spec=LeaseMonitor)
        monitor.lost_event = mock.Mock()
        monitor.lost_event.is_set.return_value = False
        monitor.now = mock.Mock(return_value=0)
        monitor.valid_until = 1000
        loader = mock.Mock()
        loader.load.side_effect = lambda ref: ExecutionCode(ref, Path(ref), "python")
        checkout_paths = {}

        def make_loader(root, *_args):
            def checkout(repository):
                path = root / "checkouts" / repository
                path.mkdir(parents=True, exist_ok=True)
                checkout_paths[repository] = path
                return path

            loader.checkout.side_effect = checkout
            return loader

        args = argparse.Namespace(
            endpoint="https://example.test", generation=1, worker="worker", lane="live",
            deadline=int(time.time()) + 3600, canary_code_ref="a" * 40,
            canary_repositories=frozenset({"canary"}),
        )
        with (
            mock.patch.object(drain_queue, "QueueWorkerClient", return_value=client),
            mock.patch.object(drain_queue, "LeaseMonitor", return_value=monitor),
            mock.patch.object(drain_queue, "take_github_app_credentials", return_value=("client", "key")),
            mock.patch.object(drain_queue, "GitHubAppTokenClient"),
            mock.patch.object(drain_queue, "ExecutionCodeLoader", side_effect=make_loader),
            mock.patch.object(drain_queue, "stable_code_ref", return_value="b" * 40),
            mock.patch.object(drain_queue, "load_configured_repositories", return_value=frozenset({"canary", "stable"})),
            mock.patch.object(drain_queue, "process_claim_wave", return_value=drain_queue.WaveResult(0, ())) as process,
        ):
            self.assertEqual(drain_queue.run_owned_drain(args), 0)
        self.assertEqual(process.call_count, 2)
        for call in process.call_args_list:
            self.assertEqual(call.kwargs["execution_code"]["canary"].ref, "a" * 40)
            self.assertEqual(call.kwargs["execution_code"]["stable"].ref, "b" * 40)
            self.assertEqual(call.kwargs["repository_roots"], checkout_paths)
        self.assertTrue(all(not path.exists() for path in checkout_paths.values()))
        monitor.close.assert_called_once()

    def test_failed_checkout_preparation_retries_without_starting_processors(self) -> None:
        client = mock.Mock()
        waves = iter([[
            {"itemKey": "stable#backfill", "repository": "stable", "claimGeneration": 1, "kind": "backfill"},
        ], []])
        client.call.side_effect = lambda action, **_kwargs: (
            {"activated": True} if action == "activate"
            else {"claims": next(waves)} if action == "claim"
            else {}
        )
        monitor = mock.Mock(spec=LeaseMonitor)
        monitor.lost_event = mock.Mock()
        monitor.lost_event.is_set.return_value = False
        monitor.now = mock.Mock(return_value=0)
        monitor.valid_until = 1000
        loader = mock.Mock()
        loader.checkout.side_effect = RuntimeError("clone failed")
        args = argparse.Namespace(
            endpoint="https://example.test", generation=1, worker="worker", lane="maintenance",
            deadline=int(time.time()) + 3600, canary_code_ref="a" * 40,
            canary_repositories=frozenset(),
        )
        with (
            mock.patch.object(drain_queue, "QueueWorkerClient", return_value=client),
            mock.patch.object(drain_queue, "LeaseMonitor", return_value=monitor),
            mock.patch.object(drain_queue, "take_github_app_credentials", return_value=("client", "key")),
            mock.patch.object(drain_queue, "GitHubAppTokenClient"),
            mock.patch.object(drain_queue, "ExecutionCodeLoader", return_value=loader),
            mock.patch.object(drain_queue, "stable_code_ref", return_value="b" * 40),
            mock.patch.object(drain_queue, "load_configured_repositories", return_value=frozenset({"stable"})),
            mock.patch.object(drain_queue, "acknowledge_results") as acknowledge,
            mock.patch.object(drain_queue, "process_claim_wave") as process,
        ):
            self.assertEqual(drain_queue.run_owned_drain(args), 0)
        process.assert_not_called()
        result = acknowledge.call_args.args[1][0]
        self.assertEqual(result["itemKey"], "stable#backfill")
        self.assertEqual(result["outcome"], "retry")
        self.assertEqual(result["error"], "clone failed")

    def test_publication_failure_repeats_calculation_and_delivery_before_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.json"
            config.write_text('[{"name":"example"}]', encoding="utf-8")
            processor = DashboardBatchProcessor(config)
            item = WorkItem("example", 1, (Claim("example#pr:1", 1, "example", 1, "", 0),))
            with (
                mock.patch.object(processor, "_initial_backfill_complete", return_value=True),
                mock.patch.object(processor, "_update_dashboard") as update,
                mock.patch.object(processor, "_deliver", return_value=(True, None, 0)) as delivery,
                mock.patch.object(processor, "_publish", side_effect=[RuntimeError("publish failed"), None]),
            ):
                failed = processor.process_repository("example", [item])
                succeeded = processor.process_repository("example", [item])
            self.assertEqual(failed[0]["outcome"], "retry")
            self.assertEqual(succeeded[0]["outcome"], "success")
            self.assertEqual(update.call_count, 2)
            self.assertEqual(delivery.call_count, 2)
            self.assertGreater(failed[0]["retryAfterMs"], 0)

    def test_bounded_initial_backfill_continues_until_full_publication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.json"
            config.write_text('[{"name":"example","large_repo":true}]', encoding="utf-8")
            processor = DashboardBatchProcessor(config)
            item = WorkItem("example", None, (
                Claim("example#backfill", 1, "example", None, "", 0, ("schedule",), "backfill"),
            ), "backfill")
            with (
                mock.patch.object(processor, "_initial_backfill_complete", side_effect=[False, False, False, True]),
                mock.patch.object(processor, "_update_dashboard") as update,
                mock.patch.object(processor, "_deliver", return_value=(True, None, 7)) as delivery,
                mock.patch.object(processor, "_publish") as publish,
                mock.patch.object(processor, "_complete_full_publish") as receipt,
            ):
                first = processor.process_repository("example", [item])
                self.assertEqual(first[0]["outcome"], "continue")
                delivery.assert_not_called()
                second = processor.process_repository("example", [item])
            self.assertEqual(second[0]["outcome"], "success")
            self.assertEqual(update.call_args.args[1], None)
            self.assertEqual(update.call_args.args[4]["PREPARE_AUTHOR_NUDGES"], "true")
            self.assertTrue(publish.call_args.args[2]["large_repo"])
            receipt.assert_called_once()

    def test_backfill_failure_can_publish_accepted_partial_state_but_never_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.json"
            config.write_text('[{"name":"example"}]', encoding="utf-8")
            processor = DashboardBatchProcessor(config)
            item = WorkItem("example", None, (
                Claim("example#backfill", 1, "example", None, "", 0, (), "backfill"),
            ), "backfill")
            with (
                mock.patch.object(processor, "_initial_backfill_complete", side_effect=[False, True]),
                mock.patch.object(processor, "_update_dashboard", side_effect=RuntimeError("one PR failed")),
                mock.patch.object(processor, "_deliver", return_value=(True, None, 7)),
                mock.patch.object(processor, "_publish") as publish,
                mock.patch.object(processor, "_complete_full_publish") as receipt,
            ):
                results = processor.process_repository("example", [item])
            self.assertEqual([result["outcome"] for result in results], ["retry"])
            publish.assert_called_once()
            receipt.assert_called_once()

    def test_control_cli_uses_backfill_semantics_and_reminders_are_serial(self) -> None:
        commands = []

        def run(command, **_kwargs):
            commands.append(command)
            if Path(command[1]).name == "state.py":
                return subprocess.CompletedProcess(command, 0, "true\n", "")
            return subprocess.CompletedProcess(command, 0, "", "")

        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.json"
            config.write_text('[{"name":"example","approver_teams":["team"]}]', encoding="utf-8")
            processor = DashboardBatchProcessor(config, run=run)
            processor._update_dashboard("example", None, "state", processor.config["example"], {"PREPARE_AUTHOR_NUDGES": "true"})
            item = WorkItem("example", None, (
                Claim("example#reminders", 1, "example", None, "", 0, (), "reminders"),
            ), "reminders")
            results = processor.process_repository("example", [item])
        self.assertNotIn("--pr-number", commands[0])
        self.assertIn("--prepare-author-nudges", commands[0])
        self.assertIn("--approver-team", commands[0])
        self.assertEqual(Path(commands[-1][1]).name, "refresh_author_nudges.py")
        self.assertNotIn("--dry-run", commands[-1])
        self.assertEqual(results[0]["outcome"], "success")


class ExecutionCheckoutTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env = {
            **os.environ,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_COUNT": "2",
            "GIT_CONFIG_KEY_0": "user.name",
            "GIT_CONFIG_VALUE_0": "Test",
            "GIT_CONFIG_KEY_1": "user.email",
            "GIT_CONFIG_VALUE_1": "test@example.com",
        }
        for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GITHUB_TOKEN"):
            self.env.pop(name, None)
        self.source = self.root / "source"
        self.source.mkdir()
        self.remote = self.root / "remote"
        self.git(self.root, "init", "--quiet", "--bare", str(self.remote))
        self.git(self.source, "init", "--quiet", "--initial-branch=main")
        (self.source / "seed").write_text("seed", encoding="utf-8")
        self.git(self.source, "add", "seed")
        for index in range(3):
            self.git(self.source, "commit", "--quiet", "--allow-empty", "-m", f"Seed {index}")
        self.git(self.source, "remote", "add", "origin", self.remote.as_uri())
        self.git(self.source, "push", "--quiet", "origin", "main")
        self.monitor = mock.Mock(spec=LeaseMonitor)
        self.loader = execution_code.ExecutionCodeLoader(
            self.root / "execution", self.monitor, self.env, repository_root=self.source,
        )

    def git(self, directory: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args], cwd=directory, env=self.env, check=check,
            capture_output=True, text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def test_checkout_is_independent_shallow_and_reused_across_waves(self) -> None:
        first = self.loader.checkout("first")
        second = self.loader.checkout("second")
        self.assertEqual(self.loader.checkout("first"), first)
        self.assertNotEqual(first, second)
        self.assertTrue((first / ".git").is_dir())
        self.assertTrue((second / ".git").is_dir())
        self.assertFalse((first / ".git" / "objects" / "info" / "alternates").exists())
        self.assertEqual(self.git(first, "rev-list", "--count", "HEAD").stdout.strip(), "1")
        self.assertEqual(
            self.git(first, "rev-parse", "HEAD").stdout,
            self.git(self.source, "rev-parse", "HEAD").stdout,
        )
        self.assertEqual(self.git(first, "remote", "get-url", "origin").stdout.strip(), self.remote.as_uri())
        self.assertEqual(
            self.git(first, "config", "--get-all", "remote.origin.fetch").stdout.strip(),
            "+refs/heads/*:refs/remotes/origin/*",
        )
        self.assertFalse((first / "seed").exists())
        self.git(first, "config", "isolation.marker", "first")
        self.assertEqual(self.git(second, "config", "--get", "isolation.marker", check=False).returncode, 1)
        self.assertEqual(self.git(self.source, "config", "--get", "isolation.marker", check=False).returncode, 1)

    def test_incomplete_worktree_registration_cannot_break_another_processor(self) -> None:
        broken = self.loader.checkout("first")
        healthy = self.loader.checkout("second")
        metadata = broken / ".git" / "worktrees" / "state"
        metadata.mkdir(parents=True)
        pending = self.root / "pending" / "state"
        pending.mkdir(parents=True)
        (pending / ".git").write_text(f"gitdir: {metadata.as_posix()}\n", encoding="utf-8")
        (metadata / "gitdir").write_text(f"{pending.as_posix()}/.git\n", encoding="utf-8")
        (metadata / "locked").write_text("initializing\n", encoding="utf-8")
        (metadata / "HEAD").write_text(self.git(broken, "rev-parse", "HEAD").stdout, encoding="utf-8")
        (metadata / "commondir").write_bytes(b"")
        target = self.root / "other" / "state"
        baseline = self.git(
            broken, "worktree", "add", "-B", "other", str(target), "HEAD", check=False,
        )
        self.assertEqual(baseline.returncode, 128)
        self.assertIn("failed to read", baseline.stderr)
        self.assertIn("commondir", baseline.stderr)
        self.git(healthy, "worktree", "add", "-B", "other", str(target), "HEAD")
        self.git(healthy, "worktree", "remove", "--force", str(target))
        self.assertEqual((metadata / "commondir").read_bytes(), b"")

    def test_four_processors_fetch_and_push_state_without_shared_registrations(self) -> None:
        repositories = [f"repo-{index}" for index in range(4)]
        checkouts = {repository: self.loader.checkout(repository) for repository in repositories}
        oid = self.git(self.source, "rev-parse", "HEAD").stdout.strip()
        for repository in repositories[:-1]:
            self.git(
                self.remote, "update-ref",
                f"refs/heads/otelbot/pull-request-dashboard-state/{repository}", oid,
            )
        script = textwrap.dedent("""
            import os
            import subprocess
            import sys
            if os.name == "nt":
                run = subprocess.run
                def hidden_run(*args, **kwargs):
                    kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
                    forward = not any(
                        name in kwargs for name in ("capture_output", "stdout", "stderr")
                    )
                    if forward:
                        kwargs["capture_output"] = True
                    result = run(*args, **kwargs)
                    if forward:
                        print(result.stdout, end="")
                        print(result.stderr, end="", file=sys.stderr)
                    return result
                subprocess.run = hidden_run
            sys.path.insert(0, sys.argv[1])
            import state_branch
            accepted_branch = sys.argv[2]
            for iteration in range(3):
                for branch in (
                    accepted_branch, state_branch.delivery_state_branch(accepted_branch),
                ):
                    def update():
                        (target / "value").write_text(str(iteration), encoding="utf-8")
                        return 0
                    with state_branch.temporary_state_dir() as target:
                        status = state_branch.push_state_changes(
                            target, "Update fixture", update, state_branch=branch,
                        )
                        if status != 0:
                            raise RuntimeError("State update failed")
                        with state_branch.accepted_state_dir(branch, required=True) as accepted:
                            if (accepted / "value").read_text(encoding="utf-8") != str(iteration):
                                raise AssertionError("Accepted state did not match")
        """)
        barrier = threading.Barrier(4)

        def process(repository):
            barrier.wait(timeout=30)
            result = execution_process.run_monitored(
                [
                    sys.executable, "-c", script, str(execution_code.SCRIPT_DIR),
                    f"otelbot/pull-request-dashboard-state/{repository}",
                ],
                self.monitor.assert_valid, cwd=checkouts[repository], env=self.env,
            )
            self.assertEqual(result.returncode, 0, f"{result.stderr}\n{result.stdout}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(process, repositories))
        for repository, checkout in checkouts.items():
            for prefix in ("state", "delivery"):
                self.assertEqual(
                    self.git(
                        self.remote, "show",
                        f"otelbot/pull-request-dashboard-{prefix}/{repository}:value",
                    ).stdout,
                    "2",
                )
            registrations = checkout / ".git" / "worktrees"
            self.assertFalse(registrations.exists() and any(registrations.iterdir()))
        self.assertFalse((self.source / ".git" / "worktrees").exists())

    def test_failed_checkout_is_not_cached_and_can_be_retried(self) -> None:
        run = execution_code.run_monitored
        failed = False

        def fail_first_clone(command, *args, **kwargs):
            nonlocal failed
            if command[1] == "clone" and not failed:
                failed = True
                return subprocess.CompletedProcess(command, 1, "", "clone failed")
            return run(command, *args, **kwargs)

        with mock.patch.object(execution_code, "run_monitored", side_effect=fail_first_clone):
            with self.assertRaisesRegex(RuntimeError, "clone failed"):
                self.loader.checkout("example")
            self.assertNotIn("example", self.loader.checkouts)
            checkout = self.loader.checkout("example")
        self.assertEqual(self.loader.checkout("example"), checkout)
        self.assertTrue((checkout / ".git").is_dir())

    def test_lease_loss_prevents_preparation_or_reuse(self) -> None:
        checkout = self.loader.checkout("example")
        self.monitor.assert_valid.side_effect = RuntimeError("lease lost")
        with mock.patch.object(self.loader, "_run") as run:
            for repository in ("example", "another"):
                with self.subTest(repository=repository), self.assertRaisesRegex(RuntimeError, "lease lost"):
                    self.loader.checkout(repository)
            run.assert_not_called()
        self.assertEqual(self.loader.checkouts, {"example": checkout})

    def test_checkout_rejects_path_components(self) -> None:
        for repository in ("", ".", "..", "../other", "owner/repository", r"owner\repository"):
            with self.subTest(repository=repository), self.assertRaisesRegex(ValueError, "repository name"):
                self.loader.checkout(repository)


if __name__ == "__main__":
    unittest.main()
