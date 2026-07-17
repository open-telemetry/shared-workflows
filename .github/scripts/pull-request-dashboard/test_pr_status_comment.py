from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import patch

import pr_status_comment


class LoadAcceptedDashboardStateTest(unittest.TestCase):
    @patch.object(pr_status_comment.state_branch, "fetch_state_branch", return_value=True)
    @patch.object(pr_status_comment.state_branch, "run")
    @patch.object(pr_status_comment.state_branch, "remove_existing_state_dir")
    @patch.object(pr_status_comment, "load_dashboard_state_cache", return_value={"version": 3, "prs": {}})
    def test_worktree_checkout_is_quiet(
        self,
        _load_state: object,
        _remove_state_dir: object,
        run: object,
        _fetch_state_branch: object,
    ) -> None:
        checkout_dir = Path("checkout")
        with patch.object(pr_status_comment.state_branch, "temporary_state_dir") as temporary_state_dir:
            temporary_state_dir.return_value.__enter__.return_value = checkout_dir

            state = pr_status_comment.load_accepted_dashboard_state(
                "open-telemetry/example",
                "state-branch",
            )

        self.assertEqual({"version": 3, "prs": {}}, state)
        run.assert_called_once_with([
            "git", "worktree", "add", "--quiet", "--detach", "checkout",
            "refs/remotes/origin/state-branch",
        ])


class RenderStatusCommentTest(unittest.TestCase):
    def pr(self, **overrides: object) -> dict[str, object]:
        pr: dict[str, object] = {
            "state": "open",
            "draft": False,
            "merged": False,
            "user": {"login": "alice"},
        }
        pr.update(overrides)
        return pr

    def test_waiting_on_author_links_discussions(self) -> None:
        body = pr_status_comment.render_status_comment(
            self.pr(),
            {
                "route": "author",
                "facts": {
                    "author": "alice",
                    "author_action_discussion_urls": [
                        "https://github.com/open-telemetry/example/pull/1#discussion_r1",
                    ],
                },
            },
        )

        self.assertIn("**Next action:** @alice", body)
        self.assertIn("[Discussion 1]", body)
        self.assertIn("give every review discussion a clear outcome", body)

    def test_waiting_on_author_caps_discussion_links(self) -> None:
        urls = [
            f"https://github.com/open-telemetry/example/pull/1#discussion_r{index}"
            for index in range(pr_status_comment.AUTHOR_ACTION_DISCUSSION_LINK_LIMIT + 2)
        ]

        body = pr_status_comment.render_status_comment(
            self.pr(),
            {
                "route": "author",
                "facts": {
                    "author": "alice",
                    "author_action_discussion_urls": urls,
                },
            },
        )

        self.assertEqual(pr_status_comment.AUTHOR_ACTION_DISCUSSION_LINK_LIMIT, body.count("- [Discussion "))
        self.assertIn("- 2 more unresolved discussions not shown", body)
        self.assertNotIn(urls[-1], body)

    def test_draft_waits_on_author(self) -> None:
        body = pr_status_comment.render_status_comment(self.pr(draft=True), None)

        self.assertIn("**Next action:** @alice", body)
        self.assertIn("mark this pull request ready for review", body)

    def test_merged_pr_has_no_author_guidance(self) -> None:
        body = pr_status_comment.render_status_comment(
            self.pr(state="closed", merged=True),
            None,
        )

        self.assertIn("**Next action:** No one", body)
        self.assertIn("has been merged", body)
        self.assertNotIn("give every review discussion a clear outcome", body)

    def test_terminal_pr_has_no_author_discussion_links(self) -> None:
        result = {
            "route": "author",
            "facts": {
                "author": "alice",
                "author_action_discussion_urls": [
                    "https://github.com/open-telemetry/example/pull/1#discussion_r1",
                ],
            },
        }

        for overrides in ({"state": "closed"}, {"state": "closed", "merged": True}):
            with self.subTest(overrides=overrides):
                body = pr_status_comment.render_status_comment(self.pr(**overrides), result)

                self.assertNotIn("Unresolved discussions waiting on the author", body)
                self.assertNotIn("[Discussion 1]", body)

    def test_blank_login_falls_back_to_author(self) -> None:
        body = pr_status_comment.render_status_comment(
            self.pr(user={"login": " "}, draft=True),
            None,
        )

        self.assertIn("**Next action:** the author", body)
        self.assertNotIn("**Next action:** @", body)


class UpsertStatusCommentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.commands: list[list[str]] = []
        self.run_gh_patch = patch.object(
            pr_status_comment,
            "run_gh",
            side_effect=lambda command: self.commands.append(command) or "",
        )
        self.run_gh_patch.start()

    def tearDown(self) -> None:
        self.run_gh_patch.stop()

    @patch.object(pr_status_comment, "managed_status_comments", return_value=[])
    def test_creates_first_status_comment(self, _comments: object) -> None:
        pr_status_comment.upsert_status_comment("open-telemetry/example", 1, "body")

        self.assertEqual("POST", self.commands[0][3])

    @patch.object(
        pr_status_comment,
        "managed_status_comments",
        return_value=[{"id": 7, "body": "body"}],
    )
    def test_does_not_update_unchanged_comment(self, _comments: object) -> None:
        pr_status_comment.upsert_status_comment("open-telemetry/example", 1, "body")

        self.assertEqual([], self.commands)

    @patch.object(
        pr_status_comment,
        "managed_status_comments",
        return_value=[
            {"id": 7, "body": "<!-- review-guidance --> old"},
            {"id": 8, "body": "<!-- pull-request-dashboard-status --> duplicate"},
        ],
    )
    def test_migrates_legacy_comment_and_deletes_duplicates(self, _comments: object) -> None:
        pr_status_comment.upsert_status_comment("open-telemetry/example", 1, "body")

        self.assertEqual(["PATCH", "DELETE"], [command[3] for command in self.commands])


class ManagedStatusCommentsTest(unittest.TestCase):
    @patch.object(
        pr_status_comment,
        "gh_api",
        return_value=[
            {"id": 1, "body": "<!-- pull-request-dashboard-status --> spoofed"},
            {
                "id": 2,
                "body": "<!-- pull-request-dashboard-status --> current",
                "performed_via_github_app": {"slug": "opentelemetry-pr-dashboard"},
            },
            {
                "id": 3,
                "body": "<!-- review-guidance --> legacy",
                "performed_via_github_app": {"slug": "opentelemetry-pr-dashboard"},
            },
            {
                "id": 4,
                "body": "<!-- pull-request-dashboard-status --> other app",
                "performed_via_github_app": {"slug": "other-app"},
            },
        ],
    )
    def test_requires_dashboard_app_identity_and_marker(self, _gh_api: object) -> None:
        comments = pr_status_comment.managed_status_comments("open-telemetry/example", 1)

        self.assertEqual([2, 3], [comment["id"] for comment in comments])


if __name__ == "__main__":
    unittest.main()