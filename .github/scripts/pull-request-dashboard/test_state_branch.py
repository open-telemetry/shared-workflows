from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import state_branch


def run_git(directory: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=directory,
        check=True,
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


class DeliveryStateBranchTest(unittest.TestCase):
    def test_maps_accepted_state_branch_to_delivery_branch(self) -> None:
        self.assertEqual(
            "otelbot/pull-request-dashboard-delivery/open-telemetry/example",
            state_branch.delivery_state_branch(
                "otelbot/pull-request-dashboard-state/open-telemetry/example"
            ),
        )

    def test_rejects_unexpected_state_branch(self) -> None:
        with self.assertRaisesRegex(ValueError, "must start with"):
            state_branch.delivery_state_branch("custom/dashboard-state/example")


class TemporaryStateDirTest(unittest.TestCase):
    @patch.object(state_branch, "remove_existing_state_dir")
    def test_removes_registered_worktree_before_temporary_directory(
        self,
        remove_existing_state_dir: object,
    ) -> None:
        with state_branch.temporary_state_dir() as state_dir:
            state_dir.mkdir()

        remove_existing_state_dir.assert_called_once_with(state_dir)


class AcceptedStateDirTest(unittest.TestCase):
    @patch.object(state_branch, "fetch_state_branch", return_value=True)
    @patch.object(state_branch, "run")
    @patch.object(state_branch, "remove_existing_state_dir")
    def test_checks_out_remote_state_quietly(
        self,
        remove_existing_state_dir: object,
        run: object,
        _fetch_state_branch: object,
    ) -> None:
        checkout_dir = Path("checkout")
        with patch.object(state_branch, "temporary_state_dir") as temporary_state_dir:
            temporary_state_dir.return_value.__enter__.return_value = checkout_dir

            with state_branch.accepted_state_dir("state-branch", required=True) as state_dir:
                self.assertEqual(checkout_dir, state_dir)

        run.assert_called_once_with([
            "git", "worktree", "add", "--quiet", "--detach", "checkout",
            "refs/remotes/origin/state-branch",
        ])
        remove_existing_state_dir.assert_called_once_with(checkout_dir)

    @patch.object(state_branch, "fetch_state_branch", return_value=False)
    @patch.object(state_branch, "run")
    def test_returns_none_when_optional_state_is_missing(
        self,
        run: object,
        _fetch_state_branch: object,
    ) -> None:
        with state_branch.accepted_state_dir("state-branch", required=False) as state_dir:
            self.assertIsNone(state_dir)

        run.assert_not_called()


class LegacyPublisherLockCleanupTest(unittest.TestCase):
    @patch.object(state_branch, "checkout_state")
    @patch.object(state_branch, "configure_git")
    def test_worker_update_removes_legacy_lock_without_waiting(
        self,
        _configure_git: object,
        _checkout_state: object,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            lock = state_dir / ".publisher-lock.json"
            lock.write_text('{"owner":"old-publisher"}\n', encoding="utf-8")

            def update_state() -> int:
                self.assertFalse(lock.exists())
                return 1

            status = state_branch.push_state_changes(
                state_dir,
                "Update dashboard state",
                update_state,
                state_branch="state-branch",
            )

        self.assertEqual(1, status)

    @patch.object(state_branch, "push_state")
    @patch.object(state_branch, "checkout_state")
    @patch.object(state_branch, "configure_git")
    def test_narrow_update_commits_legacy_lock_deletion(
        self,
        _configure_git: object,
        _checkout_state: object,
        push_state: object,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            repo_dir = state_dir / "open-telemetry" / "example"
            repo_dir.mkdir(parents=True)
            lock = state_dir / ".publisher-lock.json"
            lock.write_text('{"owner":"old-publisher"}\n', encoding="utf-8")
            state_file = repo_dir / "dashboard-state.json"
            state_file.write_text('{"revision":1}\n', encoding="utf-8")
            run_git(state_dir, "init")
            run_git(state_dir, "config", "user.email", "test@example.com")
            run_git(state_dir, "config", "user.name", "Test")
            run_git(state_dir, "add", ".")
            run_git(state_dir, "commit", "-m", "Initial state")

            def update_state() -> int:
                self.assertFalse(lock.exists())
                state_file.write_text('{"revision":2}\n', encoding="utf-8")
                return 0

            def inspect_pushed_commit(_state_dir: Path, _state_branch: str) -> bool:
                changed_paths = run_git(
                    state_dir,
                    "diff-tree",
                    "--no-commit-id",
                    "--name-status",
                    "-r",
                    "HEAD",
                ).stdout.splitlines()
                self.assertIn("D\t.publisher-lock.json", changed_paths)
                self.assertIn(
                    "M\topen-telemetry/example/dashboard-state.json",
                    changed_paths,
                )
                self.assertFalse(lock.exists())
                return True

            push_state.side_effect = inspect_pushed_commit
            status = state_branch.push_state_changes(
                state_dir,
                "Update dashboard state",
                update_state,
                state_branch="state-branch",
                add_paths=["open-telemetry/example"],
            )

        self.assertEqual(0, status)
        push_state.assert_called_once_with(state_dir, "state-branch")


class FetchStateBranchTest(unittest.TestCase):
    @staticmethod
    def rejected_fetch() -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["git", "fetch"],
            returncode=1,
            stdout="",
            stderr=(
                " ! [rejected] state-branch -> origin/state-branch (non-fast-forward)\n"
            ),
        )

    @patch.object(state_branch, "temporary_fetch_ref", return_value="refs/temp/fetch")
    @patch.object(state_branch, "has_state_branch", return_value=False)
    @patch.object(state_branch, "run")
    @patch.object(state_branch.time, "sleep")
    @patch.object(state_branch, "retry_delay_seconds", return_value=0.25)
    @patch.object(subprocess, "run")
    def test_recovers_from_transient_fetch_error(
        self,
        subprocess_run: object,
        retry_delay_seconds: object,
        sleep: object,
        run: object,
        _has_state_branch: object,
        _temporary_fetch_ref: object,
    ) -> None:
        subprocess_run.side_effect = [
            subprocess.CompletedProcess(
                args=["git", "fetch"],
                returncode=128,
                stdout="",
                stderr="fatal: unable to access repository: The requested URL returned error: 503\n",
            ),
            subprocess.CompletedProcess(
                args=["git", "fetch"],
                returncode=0,
                stdout="",
                stderr="",
            ),
        ]

        self.assertTrue(state_branch.fetch_state_branch("state-branch", required=True))

        self.assertEqual(2, subprocess_run.call_count)
        retry_delay_seconds.assert_called_once_with(1)
        sleep.assert_called_once_with(0.25)
        self.assertEqual(
            [
                (
                    [
                        "git",
                        "update-ref",
                        "refs/remotes/origin/state-branch",
                        "refs/temp/fetch",
                    ],
                ),
                (
                    ["git", "update-ref", "-d", "refs/temp/fetch"],
                ),
            ],
            [call.args for call in run.call_args_list],
        )

    @patch.object(state_branch, "temporary_fetch_ref", return_value="refs/temp/fetch")
    @patch.object(state_branch, "run")
    @patch.object(state_branch.time, "sleep")
    @patch.object(state_branch, "retry_delay_seconds", return_value=0.25)
    @patch.object(subprocess, "run")
    def test_raises_after_transient_fetch_retries_are_exhausted(
        self,
        subprocess_run: object,
        retry_delay_seconds: object,
        sleep: object,
        run: object,
        _temporary_fetch_ref: object,
    ) -> None:
        subprocess_run.return_value = subprocess.CompletedProcess(
            args=["git", "fetch"],
            returncode=128,
            stdout="",
            stderr="fatal: unable to access repository: Recv failure: Connection was reset\n",
        )

        with self.assertRaisesRegex(RuntimeError, "failed to fetch required"):
            state_branch.fetch_state_branch("state-branch", required=True)

        self.assertEqual(4, subprocess_run.call_count)
        self.assertEqual(
            [1, 2, 3],
            [call.args[0] for call in retry_delay_seconds.call_args_list],
        )
        self.assertEqual(3, sleep.call_count)
        run.assert_called_once_with(
            ["git", "update-ref", "-d", "refs/temp/fetch"],
            check=False,
        )

    @patch.object(state_branch, "temporary_fetch_ref", return_value="refs/temp/fetch")
    @patch.object(state_branch, "run")
    @patch.object(state_branch.time, "sleep")
    @patch.object(subprocess, "run")
    def test_returns_false_for_missing_optional_branch_without_retry(
        self,
        subprocess_run: object,
        sleep: object,
        run: object,
        _temporary_fetch_ref: object,
    ) -> None:
        subprocess_run.return_value = subprocess.CompletedProcess(
            args=["git", "fetch"],
            returncode=128,
            stdout="",
            stderr="fatal: couldn't find remote ref state-branch\n",
        )

        self.assertFalse(state_branch.fetch_state_branch("state-branch", required=False))

        subprocess_run.assert_called_once()
        sleep.assert_not_called()
        run.assert_called_once_with(
            ["git", "update-ref", "-d", "refs/temp/fetch"],
            check=False,
        )

    @patch.object(state_branch, "temporary_fetch_ref", return_value="refs/temp/fetch")
    @patch.object(state_branch, "run")
    @patch.object(state_branch.time, "sleep")
    @patch.object(subprocess, "run")
    def test_raises_permanent_fetch_error_without_retry(
        self,
        subprocess_run: object,
        sleep: object,
        run: object,
        _temporary_fetch_ref: object,
    ) -> None:
        subprocess_run.return_value = subprocess.CompletedProcess(
            args=["git", "fetch"],
            returncode=128,
            stdout="",
            stderr="remote: Permission to repository denied.\nfatal: HTTP 403\n",
        )

        with self.assertRaisesRegex(RuntimeError, "Permission to repository denied"):
            state_branch.fetch_state_branch("state-branch", required=True)

        subprocess_run.assert_called_once()
        sleep.assert_not_called()
        run.assert_called_once_with(
            ["git", "update-ref", "-d", "refs/temp/fetch"],
            check=False,
        )

    @patch.object(state_branch, "temporary_fetch_ref", return_value="refs/temp/fetch")
    @patch.object(state_branch, "remote_is_behind_local", return_value=True)
    @patch.object(state_branch, "ref_is_ancestor", return_value=False)
    @patch.object(state_branch, "has_state_branch", return_value=True)
    @patch.object(state_branch, "run")
    @patch.object(subprocess, "run")
    def test_keeps_local_ref_when_remote_is_behind(
        self,
        subprocess_run: object,
        run: object,
        _has_state_branch: object,
        _ref_is_ancestor: object,
        _remote_is_behind_local: object,
        _temporary_fetch_ref: object,
    ) -> None:
        subprocess_run.return_value = subprocess.CompletedProcess(
            args=["git", "fetch"],
            returncode=0,
            stdout="",
            stderr="",
        )

        self.assertTrue(state_branch.fetch_state_branch("state-branch", required=True))

        self.assertEqual(
            [
                "git",
                "fetch",
                "--no-write-fetch-head",
                "origin",
                "state-branch:refs/temp/fetch",
            ],
            subprocess_run.call_args.args[0],
        )
        run.assert_called_once_with(
            ["git", "update-ref", "-d", "refs/temp/fetch"],
            check=False,
        )

    @patch.object(state_branch, "temporary_fetch_ref", return_value="refs/temp/fetch")
    @patch.object(state_branch, "remote_is_behind_local", return_value=False)
    @patch.object(state_branch, "ref_is_ancestor", return_value=False)
    @patch.object(state_branch, "has_state_branch", return_value=True)
    @patch.object(state_branch, "run")
    @patch.object(subprocess, "run")
    def test_raises_when_remote_diverged(
        self,
        subprocess_run: object,
        _run: object,
        _has_state_branch: object,
        _ref_is_ancestor: object,
        _remote_is_behind_local: object,
        _temporary_fetch_ref: object,
    ) -> None:
        subprocess_run.return_value = subprocess.CompletedProcess(
            args=["git", "fetch"],
            returncode=0,
            stdout="",
            stderr="",
        )

        with self.assertRaisesRegex(RuntimeError, "diverged"):
            state_branch.fetch_state_branch("state-branch", required=True)


class RemoteIsBehindLocalTest(unittest.TestCase):
    @patch.object(state_branch, "has_state_branch", return_value=False)
    @patch.object(subprocess, "run")
    def test_false_without_local_ref(
        self,
        run: object,
        _has_state_branch: object,
    ) -> None:
        self.assertFalse(state_branch.remote_is_behind_local("state-branch", "refs/temp/fetch"))

        run.assert_not_called()

    @patch.object(state_branch, "has_state_branch", return_value=True)
    @patch.object(subprocess, "run")
    def test_checks_fetched_commit_is_contained_in_local_ref(
        self,
        run: object,
        _has_state_branch: object,
    ) -> None:
        run.return_value = subprocess.CompletedProcess(args=["git"], returncode=0)

        self.assertTrue(state_branch.remote_is_behind_local("state-branch", "refs/temp/fetch"))

        self.assertEqual(
            [
                "git",
                "merge-base",
                "--is-ancestor",
                "refs/temp/fetch",
                "refs/remotes/origin/state-branch",
            ],
            run.call_args.args[0],
        )

    @patch.object(state_branch, "has_state_branch", return_value=True)
    @patch.object(subprocess, "run")
    def test_false_when_fetched_commit_diverged(
        self,
        run: object,
        _has_state_branch: object,
    ) -> None:
        run.return_value = subprocess.CompletedProcess(args=["git"], returncode=1)

        self.assertFalse(state_branch.remote_is_behind_local("state-branch", "refs/temp/fetch"))


class SetGitConfigTest(unittest.TestCase):
    @patch.object(state_branch.time, "sleep")
    @patch.object(state_branch, "retry_delay_seconds", return_value=0.1)
    @patch.object(subprocess, "run")
    def test_retries_config_lock_contention(
        self,
        run: object,
        _retry_delay_seconds: object,
        sleep: object,
    ) -> None:
        run.side_effect = [
            subprocess.CompletedProcess(
                args=["git", "config"],
                returncode=255,
                stdout="",
                stderr="error: could not lock config file .git/config: File exists\n",
            ),
            subprocess.CompletedProcess(args=["git", "config"], returncode=0),
        ]

        state_branch.set_git_config("user.name", "otelbot")

        self.assertEqual(2, run.call_count)
        sleep.assert_called_once_with(0.1)

    @patch.object(state_branch.time, "sleep")
    @patch.object(subprocess, "run")
    def test_surfaces_non_lock_error_without_retry(
        self,
        run: object,
        sleep: object,
    ) -> None:
        run.return_value = subprocess.CompletedProcess(
            args=["git", "config"],
            returncode=1,
            stdout="details",
            stderr="error: invalid key: bad key\n",
        )

        with self.assertRaises(subprocess.CalledProcessError) as raised:
            state_branch.set_git_config("bad key", "value")

        self.assertEqual("details", raised.exception.stdout)
        self.assertEqual("error: invalid key: bad key\n", raised.exception.stderr)
        run.assert_called_once()
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()