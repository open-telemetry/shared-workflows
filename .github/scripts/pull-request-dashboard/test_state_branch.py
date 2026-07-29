from __future__ import annotations

from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

import state_branch


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

    @patch.object(state_branch, "remote_is_behind_local", return_value=True)
    @patch.object(subprocess, "run")
    def test_keeps_local_ref_when_remote_is_behind(
        self,
        run: object,
        _remote_is_behind_local: object,
    ) -> None:
        run.return_value = self.rejected_fetch()

        self.assertTrue(state_branch.fetch_state_branch("state-branch", required=True))

    @patch.object(state_branch, "remote_is_behind_local", return_value=False)
    @patch.object(subprocess, "run")
    def test_raises_when_remote_diverged(
        self,
        run: object,
        _remote_is_behind_local: object,
    ) -> None:
        run.return_value = self.rejected_fetch()

        with self.assertRaises(RuntimeError):
            state_branch.fetch_state_branch("state-branch", required=True)


class RemoteIsBehindLocalTest(unittest.TestCase):
    @patch.object(state_branch, "has_state_branch", return_value=False)
    @patch.object(subprocess, "run")
    def test_false_without_local_ref(
        self,
        run: object,
        _has_state_branch: object,
    ) -> None:
        self.assertFalse(state_branch.remote_is_behind_local("state-branch"))

        run.assert_not_called()

    @patch.object(state_branch, "has_state_branch", return_value=True)
    @patch.object(subprocess, "run")
    def test_checks_fetched_commit_is_contained_in_local_ref(
        self,
        run: object,
        _has_state_branch: object,
    ) -> None:
        run.return_value = subprocess.CompletedProcess(args=["git"], returncode=0)

        self.assertTrue(state_branch.remote_is_behind_local("state-branch"))

        self.assertEqual(
            ["git", "merge-base", "--is-ancestor", "FETCH_HEAD", "refs/remotes/origin/state-branch"],
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

        self.assertFalse(state_branch.remote_is_behind_local("state-branch"))


if __name__ == "__main__":
    unittest.main()