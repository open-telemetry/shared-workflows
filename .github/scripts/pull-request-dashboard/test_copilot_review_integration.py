"""Exercise Copilot delivery with local Git remotes and isolated worker processes.

GitHub responses, the clock, and unrelated notifications are controlled. Barrier
hooks coordinate real Git pushes and process termination at dispatch boundaries.
"""

from __future__ import annotations

from dataclasses import asdict, replace
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
from copilot_review import refresh_copilot_review_cycle
from copilot_review_delivery import record_copilot_review_observation
from dashboard_contracts import CopilotReviewCycle, CopilotReviewCycleUpdate
from dashboard_test_support import (
    copilot_review_cycle,
    dashboard_facts,
    dashboard_state,
    pull_request_source,
    stored_dashboard_result,
)
from github_cli import repo_state_key
from routing_snapshot import RoutingSnapshot, build_routing_snapshot


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

    def fetch_snapshot(*_args: object) -> RoutingSnapshot:
        current_head = control / "current-head"
        return (
            replace(snapshot, head_sha=current_head.read_text(encoding="utf-8"))
            if current_head.exists() else snapshot
        )

    def push(directory: Path, branch: str, expected: str) -> bool:
        nonlocal paused
        cycles = state.load_copilot_review_cycles()
        cycle = cycles.get("7")
        if not paused and (
            mode == "migrate-conflict"
            or (mode in ("contend", "head-change") and cycle is not None and cycle.phase == "sending")
        ):
            paused = True
            (control / f"candidate-{name}.json").write_text(json.dumps({
                "expected_sha": expected,
                "cycle": json.loads(state.copilot_review_dispatch_state_path().read_text(encoding="utf-8")),
            }), encoding="utf-8")
            pause()
        return real_push(directory, branch, expected)

    def request(_node_id: str, **_kwargs: object) -> str:
        result = subprocess.run(
            ["git", f"--git-dir={control / 'remote.git'}", "show",
             f"{DELIVERY_BRANCH}:{REPO_KEY}/{state.COPILOT_REVIEW_DISPATCH_STATE_FILE}"],
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
        return "next-head" if mode == "head-race" else durable["head_sha"]

    with (
        patch("copilot_review_delivery.fetch_routing_snapshot", side_effect=fetch_snapshot),
        patch("copilot_review_delivery.fetch_pr_reviews", return_value=[]),
        patch("copilot_review_delivery.request_copilot_review", side_effect=request),
        patch("copilot_review_delivery.utc_now", return_value=NOW),
        patch.object(delivery, "deliver_from_state", return_value=[]),
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
        self.save_intent()
        delivery.initialize_delivery_state(self.accepted)
        state.save_delivery_versions(state.current_delivery_versions())
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

    def save_intent(self) -> None:
        with (
            patch.object(state, "_state_dir", self.accepted),
            patch.object(state, "_using_delivery_state", False),
        ):
            record_copilot_review_observation(
                7, stored_dashboard_result(7, facts=dashboard_facts(
                    head_sha=self.snapshot.head_sha, copilot_review_request_needed=True,
                    copilot_request_fingerprint=self.snapshot.copilot_request_fingerprint,
                )), NOW,
            )

    def remote_json(self, filename: str) -> dict:
        return json.loads(self.git(
            self.root, f"--git-dir={self.remote}", "show", f"{DELIVERY_BRANCH}:{REPO_KEY}/{filename}",
        ))

    def cycle(self) -> dict:
        return self.remote_json(state.COPILOT_REVIEW_DISPATCH_STATE_FILE)["prs"]["7"]

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

    def save_remote_cycle(self, cycle: CopilotReviewCycle) -> None:
        writer = self.root / "cycle-writer"
        self.git(self.root, "clone", "--branch", DELIVERY_BRANCH, str(self.remote), str(writer))
        expected_sha = self.git(writer, "rev-parse", "HEAD")
        (writer / REPO_KEY / state.COPILOT_REVIEW_DISPATCH_STATE_FILE).write_text(json.dumps({
            "version": state.COPILOT_REVIEW_FALLBACK_STATE_VERSION,
            "prs": {"7": asdict(cycle)},
        }), encoding="utf-8")
        self.git(writer, "add", ".")
        with patch.dict(os.environ, self.environment):
            self.assertTrue(state_branch.commit_staged_state(writer, "Advance review cycle", expected_sha))
            self.assertTrue(state_branch.push_state(writer, DELIVERY_BRANCH, expected_sha))

    def test_legacy_migration_reloads_the_latest_receipt_after_a_real_cas_retry(self) -> None:
        fallback = {
            "version": 1, "prs": {"7": {
                "quota_failure_ids": [10], "attempted_at": (NOW - timedelta(hours=1)).isoformat(),
                "exhausted": True,
            }},
        }
        (self.seed / REPO_KEY / state.COPILOT_REVIEW_FALLBACK_STATE_FILE).write_text(
            json.dumps(fallback), encoding="utf-8",
        )
        receipt = self.seed / REPO_KEY / state.COPILOT_REVIEW_REQUEST_STATE_FILE
        receipt.write_text(json.dumps({
            "version": 8, "prs": {"7": {
                "head_sha": "old-head", "requested_at": (NOW - timedelta(minutes=20)).isoformat(),
            }},
        }), encoding="utf-8")
        self.publish_seed()
        worker = self.start_worker("migrator", "migrate-conflict")
        self.wait_ready(worker, "migrator")
        legacy = {
            "version": 8, "prs": {"7": {
                "head_sha": "old-head", "requested_at": (NOW - timedelta(minutes=5)).isoformat(),
            }},
        }
        receipt.write_text(json.dumps(legacy), encoding="utf-8")
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
        self.assertEqual(legacy, self.remote_json(state.COPILOT_REVIEW_REQUEST_STATE_FILE))
        self.assertEqual(fallback, self.remote_json(state.COPILOT_REVIEW_FALLBACK_STATE_FILE))
        self.assertEqual(
            state.COPILOT_REVIEW_FALLBACK_STATE_VERSION,
            self.remote_json(state.COPILOT_REVIEW_DISPATCH_STATE_FILE)["version"],
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

    def test_newer_head_budget_survives_a_stale_reservation_cas_retry(self) -> None:
        worker = self.start_worker("stale", "head-change")
        self.wait_ready(worker, "stale")
        newer = copilot_review_cycle(
            "next-head", phase="limited", request_count=3, limit_reason="requests",
        )
        self.save_remote_cycle(newer)
        saved = self.cycle()
        (self.root / "current-head").write_text("next-head", encoding="utf-8")
        (self.root / "go").touch()
        log = self.finish_worker(worker)
        self.assertIn("push rejected (attempt 1)", log)
        self.assertEqual(saved, self.cycle())
        self.assertEqual([], self.requests())

        self.snapshot = replace(self.snapshot, head_sha="next-head")
        self.save_observation(copilot_review_request_needed=True)
        self.save_intent()
        self.finish_worker(self.start_worker("later"))
        self.assertEqual(saved, self.cycle())
        self.assertEqual([], self.requests())

    def test_checkpointed_head_change_preserves_the_preceding_budget_after_cancellation(self) -> None:
        prior = copilot_review_cycle(
            "next-head", phase="limited", request_count=3, limit_reason="requests",
        )
        self.save_remote_cycle(prior)
        saved = self.cycle()
        after = refresh_copilot_review_cycle(
            prior, (), self.snapshot.head_sha, NOW, fallback_available=False,
        )
        self.save_observation(
            copilot_review_request_needed=True,
            copilot_review_cycle_update=CopilotReviewCycleUpdate(prior, after),
        )
        worker = self.start_worker("cancelled", "head-change")
        self.wait_ready(worker, "cancelled")
        (self.root / "current-head").write_text("next-head", encoding="utf-8")
        (self.root / "go").touch()
        self.finish_worker(worker)
        self.assertEqual(saved, self.cycle())
        self.assertEqual([], self.requests())
        self.finish_worker(self.start_worker("restarted"))
        self.assertEqual(saved, self.cycle())
        self.assertEqual([], self.requests())

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

    def test_old_head_observation_cannot_release_a_send_with_a_mismatched_head(self) -> None:
        worker = self.start_worker("sender", "head-race")
        self.wait_ready(worker, "sender")
        reserved = self.cycle()
        observed = state.load_copilot_review_cycles_from(
            self.root / "sender" / "state" / REPO_KEY,
        )["7"]
        self.save_observation(
            copilot_review_exists=True,
            copilot_review_cycle_update=CopilotReviewCycleUpdate(
                observed, replace(observed, phase="complete"),
            ),
        )
        state.save_state_file(
            self.accepted / state.COPILOT_REVIEW_INTENTS_FILE, {"prs": {}},
            state.COPILOT_REVIEW_REQUEST_STATE_VERSION,
        )
        self.finish_worker(self.start_worker("observer"))
        self.assertEqual(reserved, self.cycle())
        self.assertEqual(1, len(self.requests()))
        (self.root / "go").touch()
        self.finish_worker(worker)
        uncertain = self.cycle()
        self.assertEqual("unknown", uncertain["phase"])
        self.assertEqual(self.snapshot.head_sha, uncertain["head_sha"])
        self.assertEqual(1, uncertain["request_count"])
        self.finish_worker(self.start_worker("late-observer"))
        self.assertEqual(uncertain, self.cycle())
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
