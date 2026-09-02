from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import state_branch


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


class PublisherLockTest(unittest.TestCase):
    @patch.object(state_branch, "commit_publisher_lock", return_value=True)
    @patch.object(state_branch, "checkout_state")
    @patch.object(state_branch, "configure_git")
    def test_acquires_and_releases_publisher_lock(
        self,
        _configure_git: object,
        _checkout_state: object,
        commit_publisher_lock: object,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            with patch.object(
                state_branch,
                "temporary_state_dir",
                return_value=nullcontext(state_dir),
            ):
                state_branch.acquire_publisher_lock(
                    "state-branch",
                    "worker",
                    now=lambda: 100,
                )
                lock = state_branch.load_publisher_lock(state_dir)
                self.assertEqual(lock, {"owner": "worker", "expiresAt": 3700})

                state_branch.release_publisher_lock("state-branch", "worker")
                self.assertFalse(
                    (state_dir / state_branch.PUBLISHER_LOCK_PATH).exists()
                )

        self.assertEqual(commit_publisher_lock.call_count, 2)

    @patch.object(state_branch, "checkout_state")
    @patch.object(state_branch, "configure_git")
    def test_active_publisher_lock_times_out(
        self,
        _configure_git: object,
        _checkout_state: object,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            (state_dir / state_branch.PUBLISHER_LOCK_PATH).write_text(
                '{"expiresAt": 200, "owner": "other"}\n',
                encoding="utf-8",
            )
            with (
                patch.object(
                    state_branch,
                    "temporary_state_dir",
                    return_value=nullcontext(state_dir),
                ),
                self.assertRaisesRegex(TimeoutError, "timed out waiting"),
            ):
                state_branch.acquire_publisher_lock(
                    "state-branch",
                    "worker",
                    wait_seconds=0,
                    now=lambda: 100,
                )


class PublisherWriteBarrierTest(unittest.TestCase):
    @patch.object(state_branch, "reset_state", return_value=True)
    @patch.object(
        state_branch,
        "load_publisher_lock",
        side_effect=[
            {"owner": "publisher", "expiresAt": 200},
            None,
        ],
    )
    def test_waits_for_active_publisher_lock(
        self,
        load_publisher_lock: object,
        reset_state: object,
    ) -> None:
        sleeps: list[float] = []

        state_branch.wait_for_publisher_unlock(
            Path("state"),
            "state-branch",
            now=lambda: 100,
            sleep=sleeps.append,
        )

        self.assertEqual([5], sleeps)
        self.assertEqual(2, load_publisher_lock.call_count)
        reset_state.assert_called_once_with(Path("state"), "state-branch")

    @patch.object(state_branch, "reset_state")
    @patch.object(
        state_branch,
        "load_publisher_lock",
        return_value={"owner": "publisher", "expiresAt": 100},
    )
    def test_expired_publisher_lock_does_not_wait(
        self,
        _load_publisher_lock: object,
        reset_state: object,
    ) -> None:
        sleeps: list[float] = []

        state_branch.wait_for_publisher_unlock(
            Path("state"),
            "state-branch",
            now=lambda: 100,
            sleep=sleeps.append,
        )

        self.assertEqual([], sleeps)
        reset_state.assert_not_called()

    @patch.object(
        state_branch,
        "load_publisher_lock",
        return_value={"owner": "publisher", "expiresAt": 200},
    )
    def test_active_publisher_lock_times_out(
        self,
        _load_publisher_lock: object,
    ) -> None:
        with self.assertRaisesRegex(
            TimeoutError,
            "state-branch held by publisher",
        ):
            state_branch.wait_for_publisher_unlock(
                Path("state"),
                "state-branch",
                wait_seconds=0,
                now=lambda: 100,
            )

    def test_checks_barrier_before_each_cas_attempt(self) -> None:
        lifecycle: list[str] = []

        def run(
            command: list[str],
            **_kwargs: object,
        ) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                command,
                1 if command[:4] == ["git", "diff", "--cached", "--quiet"] else 0,
            )

        def update_state() -> int:
            lifecycle.append("update")
            return 0

        with (
            patch.object(state_branch, "configure_git"),
            patch.object(state_branch, "checkout_state"),
            patch.object(state_branch, "run", side_effect=run),
            patch.object(state_branch, "push_state", side_effect=[False, True]),
            patch.object(state_branch, "reset_state", return_value=True),
            patch.object(state_branch, "retry_delay_seconds", return_value=0),
            patch.object(state_branch.time, "sleep"),
            patch.object(
                state_branch,
                "wait_for_publisher_unlock",
                side_effect=lambda *_args, **_kwargs: lifecycle.append("wait"),
            ) as wait_for_publisher_unlock,
        ):
            status = state_branch.push_state_changes(
                Path("state"),
                "Update dashboard state",
                update_state,
                state_branch="state-branch",
                respect_publisher_lock=True,
            )

        self.assertEqual(0, status)
        self.assertEqual(["wait", "update", "wait", "update"], lifecycle)
        self.assertEqual(2, wait_for_publisher_unlock.call_count)


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