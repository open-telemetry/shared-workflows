"""Exercise Copilot delivery with local Git remotes and isolated worker processes.

GitHub responses, the clock, and unrelated notifications are controlled. Barrier
hooks coordinate real Git pushes and process termination at dispatch boundaries.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import delivery
import state
import state_branch
from copilot_review_delivery import record_copilot_review_observation
from dashboard_test_support import (
    dashboard_facts,
    dashboard_state,
    pull_request_source,
    stored_dashboard_result,
)
from github_cli import repo_state_key
from routing_snapshot import build_routing_snapshot


REPO = "open-telemetry/example"
REPO_KEY = repo_state_key(REPO)
ACCEPTED_BRANCH = f"{state_branch.STATE_BRANCH_PREFIX}/{REPO}"
DELIVERY_BRANCH = state_branch.delivery_state_branch(ACCEPTED_BRANCH)
NOW = datetime(2026, 8, 16, 8, tzinfo=timezone.utc)
SUBPROCESS_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def wait_for_file(path: Path) -> None:
    deadline = time.monotonic() + 30
    while not path.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Timed out waiting for {path}")
        time.sleep(0.01)


def run_worker(checkout: Path, control: Path, name: str, mode: str) -> int:
    os.chdir(checkout)
    target = checkout / "state"
    state.set_delivery_state_dirs(target / REPO_KEY, control / "accepted" / REPO_KEY)
    snapshot = build_routing_snapshot(pull_request_source())
    real_push = state_branch.push_state
    paused = False

    def pause() -> None:
        (control / f"ready-{name}").write_text("ready", encoding="utf-8")
        wait_for_file(control / "go")

    def push(directory: Path, branch: str, expected: str) -> bool:
        nonlocal paused
        cycles = state.load_copilot_review_cycles()
        cycle = cycles.get("7")
        if not paused and (
            mode == "migrate-conflict"
            or (mode == "contend" and cycle is not None and cycle.phase == "sending")
        ):
            paused = True
            (control / f"candidate-{name}.json").write_text(json.dumps({
                "expected_sha": expected,
                "cycle": json.loads(state.copilot_review_fallback_state_path().read_text(encoding="utf-8")),
            }), encoding="utf-8")
            pause()
        return real_push(directory, branch, expected)

    def request(_node_id: str, **_kwargs: object) -> str:
        result = subprocess.run(
            ["git", f"--git-dir={control / 'remote.git'}", "show",
             f"{DELIVERY_BRANCH}:{REPO_KEY}/{state.COPILOT_REVIEW_FALLBACK_STATE_FILE}"],
            check=True, capture_output=True, text=True, encoding="utf-8",
            creationflags=SUBPROCESS_FLAGS,
        )
        durable = json.loads(result.stdout)["prs"]["7"]
        local = state.load_copilot_review_cycles()["7"]
        if durable["phase"] != "sending" or durable["dispatch_id"] != local.dispatch_id:
            raise AssertionError("Mutation reached without an owned durable reservation")
        if mode == "crash-before":
            pause()
        (control / f"request-{name}.json").write_text(json.dumps(durable), encoding="utf-8")
        if mode in ("crash-after", "head-race"):
            pause()
        return "next-head" if mode == "head-race" else snapshot.head_sha

    def other_delivery(*_args: object, **_kwargs: object) -> list[str]:
        if mode == "migrate-conflict":
            state.save_copilot_review_requests({})
        return []

    with (
        patch("copilot_review_delivery.fetch_routing_snapshot", return_value=snapshot),
        patch("copilot_review_delivery.fetch_pr_reviews", return_value=[]),
        patch("copilot_review_delivery.request_copilot_review", side_effect=request),
        patch("copilot_review_delivery.utc_now", return_value=NOW),
        patch.object(delivery, "deliver_from_state", side_effect=other_delivery),
        patch.object(state_branch, "push_state", side_effect=push),
    ):
        return delivery.deliver_with_state(
            REPO, ACCEPTED_BRANCH, target, accepted_repo_dir=control / "accepted" / REPO_KEY,
        )


class CopilotReviewGitIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.remote = self.root / "remote.git"
        self.seed = self.root / "seed"
        self.seed.mkdir()
        self.workers: list[subprocess.Popen[str]] = []
        self.addCleanup(self.stop_workers)
        self.environment = {
            **os.environ,
            "GITHUB_TOKEN": "",
            "COPILOT_REVIEW_FALLBACK_TOKEN": "",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_COUNT": "2",
            "GIT_CONFIG_KEY_0": "user.name",
            "GIT_CONFIG_VALUE_0": "Test",
            "GIT_CONFIG_KEY_1": "user.email",
            "GIT_CONFIG_VALUE_1": "test@example.com",
            "PYTHONIOENCODING": "utf-8",
        }
        for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR"):
            self.environment.pop(key, None)
        self.git(self.root, "init", "--bare", str(self.remote))
        self.git(self.seed, "init", "--initial-branch", DELIVERY_BRANCH)
        self.git(self.seed, "remote", "add", "origin", str(self.remote))
        self.enterContext(patch.multiple(
            state, _state_dir=None, _accepted_state_dir=None, _using_delivery_state=False,
        ))
        self.accepted = self.root / "accepted" / REPO_KEY
        state.set_delivery_state_dirs(self.seed / REPO_KEY, self.accepted)
        self.snapshot = build_routing_snapshot(pull_request_source())
        self.save_observation()
        delivery.initialize_delivery_state(self.accepted)
        state.save_delivery_versions(state.current_delivery_versions())
        state.save_copilot_review_cycles({})
        record_copilot_review_observation(
            7, stored_dashboard_result(7, facts=dashboard_facts(
                head_sha=self.snapshot.head_sha, copilot_review_request_needed=True,
                copilot_request_fingerprint=self.snapshot.copilot_request_fingerprint,
            )), NOW,
        )
        (self.accepted / state.COPILOT_REVIEW_REQUEST_STATE_FILE).write_bytes(
            state.copilot_review_request_state_path().read_bytes(),
        )
        self.publish_seed()

    def git(self, directory: Path, *arguments: str) -> str:
        return subprocess.run(
            ["git", *arguments], cwd=directory, env=self.environment, check=True,
            capture_output=True, text=True, encoding="utf-8", creationflags=SUBPROCESS_FLAGS,
        ).stdout.strip()

    def publish_seed(self) -> None:
        self.git(self.seed, "add", ".")
        self.git(self.seed, "commit", "-m", "Seed integration fixture")
        self.git(self.seed, "push", "origin", DELIVERY_BRANCH)

    def save_observation(self, **changes: object) -> None:
        state.save_dashboard_state_cache(dashboard_state(stored_dashboard_result(
            7, facts=dashboard_facts(head_sha=self.snapshot.head_sha, **changes),
        )))

    def remote_json(self, filename: str) -> dict:
        return json.loads(self.git(
            self.root, f"--git-dir={self.remote}", "show", f"{DELIVERY_BRANCH}:{REPO_KEY}/{filename}",
        ))

    def cycle(self) -> dict:
        return self.remote_json(state.COPILOT_REVIEW_FALLBACK_STATE_FILE)["prs"]["7"]

    def requests(self) -> list[Path]:
        return list(self.root.glob("request-*.json"))

    def start_worker(self, name: str, mode: str = "normal") -> subprocess.Popen[str]:
        checkout = self.root / name
        self.git(self.root, "clone", "--branch", DELIVERY_BRANCH, str(self.remote), str(checkout))
        self.git(checkout, "switch", "--detach")
        worker = subprocess.Popen(
            [sys.executable, "-B", str(Path(__file__).resolve()), "--worker",
             str(checkout), str(self.root), name, mode],
            cwd=checkout, env=self.environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", creationflags=SUBPROCESS_FLAGS,
        )
        self.workers.append(worker)
        return worker

    def wait_ready(self, worker: subprocess.Popen[str], name: str) -> None:
        deadline = time.monotonic() + 30
        while not (self.root / f"ready-{name}").exists():
            if worker.poll() is not None:
                stdout, stderr = worker.communicate()
                self.fail(f"Worker exited before reaching its barrier: {stdout}\n{stderr}")
            if time.monotonic() >= deadline:
                self.fail(f"Worker {name} did not reach its barrier")
            time.sleep(0.01)

    def finish_worker(self, worker: subprocess.Popen[str]) -> str:
        stdout, stderr = worker.communicate(timeout=60)
        self.assertEqual(0, worker.returncode, f"{stdout}\n{stderr}")
        return stderr

    def stop_workers(self) -> None:
        for worker in self.workers:
            if worker.poll() is None:
                worker.kill()
            worker.communicate(timeout=10)

    def test_legacy_migration_survives_a_real_cas_retry_and_receipt_rewrite(self) -> None:
        (self.seed / REPO_KEY / state.COPILOT_REVIEW_FALLBACK_STATE_FILE).write_text(json.dumps({
            "version": 1, "prs": {"7": {
                "quota_failure_ids": [10], "attempted_at": (NOW - timedelta(hours=1)).isoformat(),
                "exhausted": True,
            }},
        }), encoding="utf-8")
        receipt = self.seed / REPO_KEY / state.COPILOT_REVIEW_REQUEST_STATE_FILE
        receipt.write_text(json.dumps({
            "version": 8, "prs": {"7": {
                "head_sha": "old-head", "requested_at": (NOW - timedelta(minutes=20)).isoformat(),
            }},
        }), encoding="utf-8")
        self.publish_seed()
        worker = self.start_worker("migrator", "migrate-conflict")
        self.wait_ready(worker, "migrator")
        receipt.write_text(json.dumps({
            "version": 8, "prs": {"7": {
                "head_sha": "old-head", "requested_at": (NOW - timedelta(minutes=5)).isoformat(),
            }},
        }), encoding="utf-8")
        (self.seed / REPO_KEY / "other-worker.json").write_text('{"kept":true}', encoding="utf-8")
        self.publish_seed()
        (self.root / "go").touch()
        log = self.finish_worker(worker)
        self.assertIn("push rejected (attempt 1)", log)
        self.assertEqual("unknown", self.cycle()["phase"])
        self.assertEqual("", self.cycle()["head_sha"])
        self.assertEqual(1, self.cycle()["request_count"])
        self.assertEqual("2026-08-16T07:55:00+00:00", self.cycle()["started_at"])
        self.assertEqual({"kept": True}, self.remote_json("other-worker.json"))
        self.assertEqual({}, self.remote_json(state.COPILOT_REVIEW_REQUEST_STATE_FILE)["prs"])
        self.assertEqual(
            state.COPILOT_REVIEW_FALLBACK_STATE_VERSION,
            self.remote_json(state.COPILOT_REVIEW_FALLBACK_STATE_FILE)["version"],
        )
        self.assertEqual([], self.requests())

    def test_competing_workers_reserve_and_send_only_once(self) -> None:
        first = self.start_worker("first", "contend")
        second = self.start_worker("second", "contend")
        self.wait_ready(first, "first")
        self.wait_ready(second, "second")
        candidates = [
            json.loads((self.root / f"candidate-{name}.json").read_text(encoding="utf-8"))
            for name in ("first", "second")
        ]
        self.assertEqual(candidates[0]["expected_sha"], candidates[1]["expected_sha"])
        self.assertNotEqual(
            candidates[0]["cycle"]["prs"]["7"]["dispatch_id"],
            candidates[1]["cycle"]["prs"]["7"]["dispatch_id"],
        )
        (self.root / "go").touch()
        logs = self.finish_worker(first) + self.finish_worker(second)
        self.assertIn("push rejected (attempt 1)", logs)
        self.assertEqual(1, len(self.requests()))
        self.assertEqual("accepted", self.cycle()["phase"])
        self.assertEqual(1, self.cycle()["request_count"])
        sent = json.loads(self.requests()[0].read_text(encoding="utf-8"))
        self.assertEqual(sent["dispatch_id"], self.cycle()["dispatch_id"])

    def assert_crash_recovery(self, mode: str, expected_requests: int) -> None:
        worker = self.start_worker("crashed", mode)
        self.wait_ready(worker, "crashed")
        reserved = self.cycle()
        self.assertEqual("sending", reserved["phase"])
        self.assertEqual(1, reserved["request_count"])
        worker.kill()
        worker.communicate(timeout=10)
        self.assertNotEqual(0, worker.returncode)
        self.finish_worker(self.start_worker("restarted"))
        self.assertEqual(reserved, self.cycle())
        self.assertEqual(expected_requests, len(self.requests()))

    def test_crash_before_mutation_keeps_the_reservation_on_restart(self) -> None:
        self.assert_crash_recovery("crash-before", 0)

    def test_crash_after_mutation_does_not_resend_on_restart(self) -> None:
        self.assert_crash_recovery("crash-after", 1)

    def test_old_head_observation_cannot_release_a_dispatch_returning_a_new_head(self) -> None:
        worker = self.start_worker("sender", "head-race")
        self.wait_ready(worker, "sender")
        reserved = self.cycle()
        observed = state.load_copilot_review_cycles_file(
            self.root / "sender" / "state" / REPO_KEY / state.COPILOT_REVIEW_FALLBACK_STATE_FILE,
        )["7"]
        self.save_observation(copilot_review_exists=True, copilot_review_observed_cycle=observed)
        (self.accepted / state.COPILOT_REVIEW_REQUEST_STATE_FILE).write_text(json.dumps({
            "version": state.COPILOT_REVIEW_REQUEST_STATE_VERSION, "prs": {},
        }), encoding="utf-8")
        self.finish_worker(self.start_worker("observer"))
        self.assertEqual(reserved, self.cycle())
        self.assertEqual(1, len(self.requests()))
        (self.root / "go").touch()
        self.finish_worker(worker)
        accepted = self.cycle()
        self.assertEqual("accepted", accepted["phase"])
        self.assertEqual("next-head", accepted["head_sha"])
        self.assertEqual(1, accepted["request_count"])
        self.finish_worker(self.start_worker("late-observer"))
        self.assertEqual(accepted, self.cycle())
        self.assertEqual(1, len(self.requests()))

    def test_newer_delivery_versions_stop_a_worker_before_mutation(self) -> None:
        versions = state.current_delivery_versions()
        versions["COPILOT_REVIEW_FALLBACK_STATE_VERSION"] += 1
        state.save_delivery_versions(versions)
        self.publish_seed()
        before = self.git(self.root, f"--git-dir={self.remote}", "rev-parse", f"{DELIVERY_BRANCH}^{{tree}}")
        self.finish_worker(self.start_worker("older"))
        self.assertEqual(
            before, self.git(self.root, f"--git-dir={self.remote}", "rev-parse", f"{DELIVERY_BRANCH}^{{tree}}"),
        )
        self.assertEqual([], self.requests())


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        sys.exit(run_worker(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4], sys.argv[5]))
    unittest.main()
