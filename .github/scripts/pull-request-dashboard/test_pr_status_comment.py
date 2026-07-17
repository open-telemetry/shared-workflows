from __future__ import annotations

import unittest
from unittest.mock import patch

import pr_status_comment


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

    def test_waiting_on_author_splits_review_feedback_links(self) -> None:
        body = pr_status_comment.render_status_comment(
            self.pr(),
            {
                "route": "author",
                "facts": {
                    "author": "alice",
                    "author_action_review_thread_urls": [
                        "https://github.com/open-telemetry/example/pull/1#discussion_r1",
                    ],
                    "author_action_top_level_feedback_urls": [
                        "https://github.com/open-telemetry/example/pull/1#pullrequestreview-2",
                    ],
                },
            },
        )

        self.assertIn(
            "**Status:** Waiting on @alice to address or respond to review feedback.",
            body,
        )
        self.assertIn("Unresolved inline review threads waiting on the author:", body)
        self.assertIn("[Thread 1]", body)
        self.assertIn("Top-level feedback waiting on the author:", body)
        self.assertIn("[Feedback 1]", body)
        self.assertIn("give each review feedback item a clear outcome", body)

    def test_waiting_on_author_caps_feedback_links_across_sections(self) -> None:
        review_thread_urls = [
            f"https://github.com/open-telemetry/example/pull/1#discussion_r{index}"
            for index in range(pr_status_comment.AUTHOR_ACTION_FEEDBACK_LINK_LIMIT - 1)
        ]
        top_level_feedback_urls = [
            f"https://github.com/open-telemetry/example/pull/1#pullrequestreview-{index}"
            for index in range(3)
        ]

        body = pr_status_comment.render_status_comment(
            self.pr(),
            {
                "route": "author",
                "facts": {
                    "author": "alice",
                    "author_action_review_thread_urls": review_thread_urls,
                    "author_action_top_level_feedback_urls": top_level_feedback_urls,
                },
            },
        )

        self.assertEqual(len(review_thread_urls), body.count("- [Thread "))
        self.assertEqual(1, body.count("- [Feedback "))
        self.assertIn("- 2 more top-level feedback items not shown", body)
        self.assertNotIn(top_level_feedback_urls[-1], body)

    def test_draft_waits_on_author(self) -> None:
        body = pr_status_comment.render_status_comment(self.pr(draft=True), None)

        self.assertIn(
            "**Status:** Waiting on @alice to mark this pull request ready for review.",
            body,
        )

    def test_merged_pr_has_no_author_guidance(self) -> None:
        body = pr_status_comment.render_status_comment(
            self.pr(state="closed", merged=True),
            None,
        )

        self.assertIn("**Status:** This pull request has been merged.", body)
        self.assertNotIn("give each review feedback item a clear outcome", body)

    def test_terminal_pr_has_no_author_feedback_links(self) -> None:
        result = {
            "route": "author",
            "facts": {
                "author": "alice",
                "author_action_review_thread_urls": [
                    "https://github.com/open-telemetry/example/pull/1#discussion_r1",
                ],
                "author_action_top_level_feedback_urls": [
                    "https://github.com/open-telemetry/example/pull/1#pullrequestreview-2",
                ],
            },
        }

        for overrides in ({"state": "closed"}, {"state": "closed", "merged": True}):
            with self.subTest(overrides=overrides):
                body = pr_status_comment.render_status_comment(self.pr(**overrides), result)

                self.assertNotIn("Unresolved inline review threads waiting on the author", body)
                self.assertNotIn("Top-level feedback waiting on the author", body)
                self.assertNotIn("[Thread 1]", body)
                self.assertNotIn("[Feedback 1]", body)

    def test_blank_login_falls_back_to_author(self) -> None:
        body = pr_status_comment.render_status_comment(
            self.pr(user={"login": " "}, draft=True),
            None,
        )

        self.assertIn(
            "**Status:** Waiting on the author to mark this pull request ready for review.",
            body,
        )
        self.assertNotIn("**Status:** Waiting on @", body)

    def test_routes_render_one_status_sentence(self) -> None:
        expected_statuses = {
            "approver": "Waiting on reviewers to review the latest changes.",
            "maintainer": "Waiting on maintainers to merge the pull request.",
            "external": "Waiting on an external dependency or decision.",
            "transient-failure": "Waiting on dashboard maintainers to determine the next action.",
            "unknown": "Waiting on dashboard maintainers to determine the next action.",
        }

        for route, expected in expected_statuses.items():
            with self.subTest(route=route):
                body = pr_status_comment.render_status_comment(
                    self.pr(),
                    {"route": route, "facts": {}},
                )

                self.assertIn(f"**Status:** {expected}", body)
                self.assertEqual(1, body.count("**Status:**"))
                self.assertNotIn("**Next action:**", body)
                self.assertNotIn("**Waiting on:**", body)


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


class EnsureStatusCommentTest(unittest.TestCase):
    @patch.object(
        pr_status_comment,
        "managed_status_comments",
        return_value=[{"html_url": "https://github.com/open-telemetry/example/pull/1#issuecomment-1"}],
    )
    @patch.object(pr_status_comment, "upsert_status_comment")
    @patch.object(
        pr_status_comment,
        "gh_api",
        return_value={
            "state": "open",
            "draft": False,
            "merged": False,
            "user": {"login": "alice"},
        },
    )
    def test_returns_managed_status_comment_url(
        self,
        _gh_api: object,
        upsert_status_comment: object,
        _managed_status_comments: object,
    ) -> None:
        url = pr_status_comment.ensure_status_comment(
            "open-telemetry/example",
            1,
            {"route": "author", "facts": {"author": "alice"}},
        )

        self.assertEqual(
            url,
            "https://github.com/open-telemetry/example/pull/1#issuecomment-1",
        )
        upsert_status_comment.assert_called_once()


if __name__ == "__main__":
    unittest.main()