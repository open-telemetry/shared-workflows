from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import state_branch
from test_state_branch import run_git


@contextmanager
def working_directory(directory: Path) -> Iterator[None]:
    previous = Path.cwd()
    os.chdir(directory)
    try:
        yield
    finally:
        os.chdir(previous)


class DataSnapshotTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.remote = self.root / "remote.git"
        self.worker = self.root / "worker"
        self.worker.mkdir()
        run_git(self.root, "init", "--bare", str(self.remote))
        run_git(self.worker, "init", "-b", "data")
        run_git(self.worker, "config", "user.email", "test@example.com")
        run_git(self.worker, "config", "user.name", "Test")
        run_git(self.worker, "remote", "add", "origin", str(self.remote))
        (self.worker / "jobs.json").write_text('{"jobs":[1]}\n', encoding="utf-8")
        (self.worker / "ledger.json").write_text('{"sent":[1]}\n', encoding="utf-8")
        run_git(self.worker, "add", ".")
        run_git(self.worker, "commit", "-m", "Initial data")
        run_git(self.worker, "push", "-u", "origin", "data")
        self.legacy = run_git(self.worker, "rev-parse", "HEAD").stdout.strip()
        run_git(self.worker, "switch", "--detach")
        self.environment = patch.dict(
            os.environ, {"DATA_BRANCH_SNAPSHOTS": "true", "GITHUB_TOKEN": ""},
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def remote_git(self, *args: str) -> str:
        return run_git(self.root, f"--git-dir={self.remote}", *args).stdout

    def snapshot(self, directory: Path, expected_sha: str) -> str:
        run_git(directory, "add", "--all")
        self.assertTrue(state_branch.commit_staged_state(directory, "Update data", expected_sha))
        return run_git(directory, "rev-parse", "HEAD").stdout.strip()

    def test_compacts_unchanged_legacy_tree_and_preserves_all_files(self) -> None:
        before = run_git(self.worker, "rev-parse", "HEAD^{tree}").stdout
        snapshot = self.snapshot(self.worker, self.legacy)
        self.assertEqual(before, run_git(self.worker, "rev-parse", "HEAD^{tree}").stdout)
        self.assertEqual("1\n", run_git(self.worker, "rev-list", "--count", "HEAD").stdout)
        self.assertEqual(1, state_branch.snapshot_generation(snapshot, self.worker))
        self.assertTrue(state_branch.push_state(self.worker, "data", self.legacy))
        clone = self.root / "fresh-clone"
        run_git(self.root, "clone", "--no-local", "--branch", "data", str(self.remote), str(clone))
        self.assertEqual("1\n", run_git(clone, "rev-list", "--count", "HEAD").stdout)
        legacy_object = subprocess.run(
            ["git", "cat-file", "-e", self.legacy], cwd=clone,
            check=False, capture_output=True, text=True,
            creationflags=state_branch.SUBPROCESS_FLAGS,
        )
        self.assertNotEqual(0, legacy_object.returncode)
        self.assertEqual('{"jobs":[1]}\n', (clone / "jobs.json").read_text(encoding="utf-8"))
        self.assertEqual('{"sent":[1]}\n', (clone / "ledger.json").read_text(encoding="utf-8"))
        self.assertFalse(state_branch.commit_staged_state(self.worker, "No changes", snapshot))
        self.assertEqual(snapshot, run_git(self.worker, "rev-parse", "HEAD").stdout.strip())

    def test_repeated_updates_keep_one_commit_and_increment_generation(self) -> None:
        expected = self.legacy
        for generation in range(1, 4):
            (self.worker / "jobs.json").write_text(f'{{"jobs":[1,{generation}]}}\n', encoding="utf-8")
            snapshot = self.snapshot(self.worker, expected)
            self.assertTrue(state_branch.push_state(self.worker, "data", expected))
            self.assertEqual("1\n", self.remote_git("rev-list", "--count", "data"))
            self.assertEqual(generation, state_branch.snapshot_generation(snapshot, self.worker))
            self.assertEqual('{"sent":[1]}\n', (self.worker / "ledger.json").read_text(encoding="utf-8"))
            expected = snapshot

    def test_explicit_lease_rejects_stale_writer_even_after_background_fetch(self) -> None:
        other = self.root / "other"
        run_git(self.root, "clone", "--branch", "data", str(self.remote), str(other))
        run_git(other, "config", "user.email", "test@example.com")
        run_git(other, "config", "user.name", "Test")
        (self.worker / "jobs.json").write_text('{"jobs":[1,2]}\n', encoding="utf-8")
        accepted = self.snapshot(self.worker, self.legacy)
        self.assertTrue(state_branch.push_state(self.worker, "data", self.legacy))
        (other / "ledger.json").write_text('{"sent":[1,2]}\n', encoding="utf-8")
        self.snapshot(other, self.legacy)
        run_git(other, "fetch", "origin")
        self.assertFalse(state_branch.push_state(other, "data", self.legacy))
        self.assertEqual(accepted, self.remote_git("rev-parse", "data").strip())

    def test_dashboard_retry_reapplies_update_to_winning_snapshot(self) -> None:
        worktree = self.root / "dashboard"
        real_push = state_branch.push_state
        attempts = 0

        def competing_push(directory: Path, branch: str, expected: str) -> bool:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                (self.worker / "ledger.json").write_text('{"sent":[1,2]}\n', encoding="utf-8")
                self.snapshot(self.worker, self.legacy)
                self.assertTrue(real_push(self.worker, branch, self.legacy))
            return real_push(directory, branch, expected)

        def update() -> int:
            (worktree / "jobs.json").write_text('{"jobs":[1,2]}\n', encoding="utf-8")
            return 0

        with (
            working_directory(self.worker),
            patch.object(state_branch, "push_state", side_effect=competing_push),
            patch.object(state_branch.time, "sleep"),
        ):
            status = state_branch.push_state_changes(
                worktree, "Update state", update, state_branch="data",
            )
        self.assertEqual(0, status)
        self.assertEqual(2, attempts)
        self.assertEqual('{"sent":[1,2]}\n', (worktree / "ledger.json").read_text(encoding="utf-8"))
        self.assertEqual('{"jobs":[1,2]}\n', (worktree / "jobs.json").read_text(encoding="utf-8"))
        self.assertEqual("1\n", self.remote_git("rev-list", "--count", "data"))
        self.assertEqual(2, state_branch.snapshot_generation("HEAD", worktree))

    def test_fetch_ignores_stale_snapshots_and_legacy_heads(self) -> None:
        first = self.snapshot(self.worker, self.legacy)
        self.assertTrue(state_branch.push_state(self.worker, "data", self.legacy))
        (self.worker / "jobs.json").write_text('{"jobs":[1,2]}\n', encoding="utf-8")
        second = self.snapshot(self.worker, first)
        self.assertTrue(state_branch.push_state(self.worker, "data", first))
        with working_directory(self.worker):
            state_branch.update_snapshot_ref("data", first)
            state_branch.update_snapshot_ref("data", self.legacy)
            self.assertEqual(second, state_branch.ref_oid(state_branch.remote_ref("data")))
            self.assertTrue(state_branch.fetch_state_branch("data", required=True))
        self.assertEqual("", run_git(self.worker, "for-each-ref", "--format=%(refname)", "refs/pull-request-dashboard-fetch").stdout)

    def test_fetch_replaces_legacy_ref_with_snapshot_and_accepts_newer_generation(self) -> None:
        first = self.snapshot(self.worker, self.legacy)
        (self.worker / "jobs.json").write_text('{"jobs":[1,2]}\n', encoding="utf-8")
        second = self.snapshot(self.worker, first)
        with working_directory(self.worker):
            state_branch.update_snapshot_ref("data", first)
            self.assertEqual(first, state_branch.ref_oid(state_branch.remote_ref("data")))
            state_branch.update_snapshot_ref("data", second)
            self.assertEqual(second, state_branch.ref_oid(state_branch.remote_ref("data")))

    def test_same_generation_with_different_contents_is_an_error(self) -> None:
        first = self.snapshot(self.worker, self.legacy)
        self.assertTrue(state_branch.push_state(self.worker, "data", self.legacy))
        run_git(self.worker, "update-ref", "HEAD", self.legacy)
        (self.worker / "jobs.json").write_text('{"jobs":[2]}\n', encoding="utf-8")
        run_git(self.worker, "add", "--all")
        conflicting = self.snapshot(self.worker, self.legacy)
        with working_directory(self.worker), self.assertRaisesRegex(RuntimeError, "conflicting"):
            state_branch.update_snapshot_ref("data", conflicting)
        self.assertEqual(first, run_git(self.worker, "rev-parse", "origin/data").stdout.strip())

    def test_concurrent_fetch_does_not_regress_local_ref(self) -> None:
        first = self.snapshot(self.worker, self.legacy)
        (self.worker / "jobs.json").write_text('{"jobs":[2]}\n', encoding="utf-8")
        second = self.snapshot(self.worker, first)
        real_run = state_branch.run

        def concurrent_update(
            cmd: list[str], *, check: bool = True, cwd: Path | None = None,
        ) -> subprocess.CompletedProcess[str]:
            if cmd[:3] == ["git", "update-ref", state_branch.remote_ref("data")]:
                run_git(self.worker, "update-ref", state_branch.remote_ref("data"), second)
            return real_run(cmd, check=check, cwd=cwd)

        with working_directory(self.worker), patch.object(state_branch, "run", side_effect=concurrent_update):
            state_branch.update_snapshot_ref("data", first)
        self.assertEqual(second, run_git(self.worker, "rev-parse", "origin/data").stdout.strip())

    def test_history_bearing_write_after_snapshot_is_rejected(self) -> None:
        first = self.snapshot(self.worker, self.legacy)
        self.assertTrue(state_branch.push_state(self.worker, "data", self.legacy))
        (self.worker / "jobs.json").write_text('{"jobs":[2]}\n', encoding="utf-8")
        run_git(self.worker, "add", ".")
        run_git(self.worker, "commit", "-m", "History-bearing update")
        with working_directory(self.worker), self.assertRaisesRegex(RuntimeError, "history restored"):
            state_branch.update_snapshot_ref("data", "HEAD")

    def test_fresh_checkout_rejects_history_bearing_head_after_snapshot(self) -> None:
        self.snapshot(self.worker, self.legacy)
        (self.worker / "jobs.json").write_text('{"jobs":[2]}\n', encoding="utf-8")
        run_git(self.worker, "add", ".")
        run_git(self.worker, "commit", "-m", "History-bearing update")
        run_git(self.worker, "update-ref", "-d", state_branch.remote_ref("data"))
        with working_directory(self.worker), self.assertRaisesRegex(RuntimeError, "history restored"):
            state_branch.update_snapshot_ref("data", "HEAD")

    def test_disabled_mode_preserves_legacy_behavior_but_cannot_restore_history(self) -> None:
        with patch.dict(os.environ, {"DATA_BRANCH_SNAPSHOTS": "false"}):
            self.assertFalse(state_branch.commit_staged_state(self.worker, "No changes", self.legacy))
            (self.worker / "jobs.json").write_text('{"jobs":[2]}\n', encoding="utf-8")
            run_git(self.worker, "add", ".")
            self.assertTrue(state_branch.commit_staged_state(self.worker, "Update", self.legacy))
            self.assertEqual("2\n", run_git(self.worker, "rev-list", "--count", "HEAD").stdout)
        first = self.snapshot(self.worker, run_git(self.worker, "rev-parse", "HEAD").stdout.strip())
        with patch.dict(os.environ, {"DATA_BRANCH_SNAPSHOTS": ""}), self.assertRaisesRegex(RuntimeError, "must remain true"):
            state_branch.commit_staged_state(self.worker, "No changes", first)

    def test_invalid_mode_is_an_error(self) -> None:
        with patch.dict(os.environ, {"DATA_BRANCH_SNAPSHOTS": "yes"}), self.assertRaisesRegex(ValueError, "must be"):
            state_branch.commit_staged_state(self.worker, "Update", self.legacy)

    def test_interrupted_update_does_not_modify_remote(self) -> None:
        self.snapshot(self.worker, self.legacy)
        self.assertEqual(self.legacy, self.remote_git("rev-parse", "data").strip())

    def test_invalid_snapshot_metadata_is_rejected_even_in_shallow_checkout(self) -> None:
        first = self.snapshot(self.worker, self.legacy)
        self.assertTrue(state_branch.push_state(self.worker, "data", self.legacy))
        (self.worker / "jobs.json").write_text('{"jobs":[2]}\n', encoding="utf-8")
        run_git(self.worker, "add", ".")
        run_git(self.worker, "commit", "-m", "Invalid snapshot\n\nData-snapshot-generation: 2")
        invalid = run_git(self.worker, "rev-parse", "HEAD").stdout.strip()
        run_git(self.worker, "push", "origin", "HEAD:refs/heads/data")
        shallow = self.root / "shallow"
        run_git(self.root, "clone", "--depth=1", "--branch", "data", self.remote.as_uri(), str(shallow))
        with self.assertRaisesRegex(RuntimeError, "invalid data snapshot"):
            state_branch.snapshot_generation(invalid, shallow)

    def test_git_helpers_supply_no_window_flag(self) -> None:
        with patch.object(subprocess, "run", wraps=subprocess.run) as launch:
            self.snapshot(self.worker, self.legacy)
            self.assertTrue(state_branch.push_state(self.worker, "data", self.legacy))
            with working_directory(self.worker):
                self.assertTrue(state_branch.fetch_state_branch("data", required=True))
        self.assertTrue(launch.call_args_list)
        for invocation in launch.call_args_list:
            self.assertEqual(
                getattr(subprocess, "CREATE_NO_WINDOW", 0),
                invocation.kwargs["creationflags"],
            )

    def test_collector_command_preserves_measurements_and_checkpoints(self) -> None:
        (self.worker / "state.json").write_text('{"cursor":"2026-10-01T17:00:00Z"}\n', encoding="utf-8")
        command = [
            sys.executable, str(Path(state_branch.__file__).resolve()), "commit-data",
            "--state-dir", str(self.worker), "--state-branch", "data",
            "--expected-sha", self.legacy, "--message", "Collect queue data",
        ]
        result = subprocess.run(
            command, cwd=self.worker, check=True, capture_output=True, text=True,
            creationflags=state_branch.SUBPROCESS_FLAGS,
        )
        self.assertEqual(0, result.returncode)
        self.assertEqual("1\n", self.remote_git("rev-list", "--count", "data"))
        self.assertEqual('{"jobs":[1]}\n', self.remote_git("show", "data:jobs.json"))
        self.assertEqual('{"cursor":"2026-10-01T17:00:00Z"}\n', self.remote_git("show", "data:state.json"))
        accepted = run_git(self.worker, "rev-parse", "HEAD").stdout.strip()
        resumed = self.root / "resumed"
        run_git(self.root, "clone", "--branch", "data", str(self.remote), str(resumed))
        run_git(resumed, "config", "user.email", "test@example.com")
        run_git(resumed, "config", "user.name", "Test")
        self.assertEqual('{"cursor":"2026-10-01T17:00:00Z"}\n', (resumed / "state.json").read_text(encoding="utf-8"))
        (resumed / "state.json").write_text('{"cursor":"2026-10-01T18:00:00Z"}\n', encoding="utf-8")
        (resumed / "new-jobs.json").write_text('{"jobs":[2]}\n', encoding="utf-8")
        subprocess.run(
            [
                sys.executable, str(Path(state_branch.__file__).resolve()), "commit-data",
                "--state-dir", str(resumed), "--state-branch", "data",
                "--expected-sha", accepted, "--message", "Resume collection",
            ],
            cwd=resumed, check=True, capture_output=True, text=True,
            creationflags=state_branch.SUBPROCESS_FLAGS,
        )
        self.assertEqual("1\n", self.remote_git("rev-list", "--count", "data"))
        self.assertEqual('{"jobs":[1]}\n', self.remote_git("show", "data:jobs.json"))
        self.assertEqual('{"jobs":[2]}\n', self.remote_git("show", "data:new-jobs.json"))
        self.assertEqual('{"cursor":"2026-10-01T18:00:00Z"}\n', self.remote_git("show", "data:state.json"))

    def test_first_snapshot_creates_missing_branch_with_empty_lease(self) -> None:
        new_branch = "new-data"
        run_git(self.worker, "switch", "--orphan", new_branch)
        (self.worker / "jobs.json").write_text('{"jobs":[1]}\n', encoding="utf-8")
        first = self.snapshot(self.worker, "")
        self.assertTrue(state_branch.push_state(self.worker, new_branch, ""))
        (self.worker / "jobs.json").write_text('{"jobs":[2]}\n', encoding="utf-8")
        self.snapshot(self.worker, first)
        self.assertFalse(state_branch.push_state(self.worker, new_branch, ""))
        self.assertEqual("1\n", self.remote_git("rev-list", "--count", new_branch))


if __name__ == "__main__":
    unittest.main()
