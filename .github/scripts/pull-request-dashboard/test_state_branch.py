from __future__ import annotations

from pathlib import Path
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


if __name__ == "__main__":
    unittest.main()