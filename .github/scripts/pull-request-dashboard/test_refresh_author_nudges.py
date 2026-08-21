from __future__ import annotations

from contextlib import redirect_stderr
from datetime import datetime, timezone
from io import StringIO
import unittest
from unittest.mock import patch

import author_nudge
import refresh_author_nudges
from route_presentation import status_headline


NOW = datetime(2026, 8, 21, tzinfo=timezone.utc)
LIVE_EPISODE = "0f1e2d3c4b5a60718293a4b5c6d7e8f9"
STALE_EPISODE = "9f8e7d6c5b4a30291817263544536271"
UNMATCHED_EPISODE = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


def dashboard_comment(comment_id: int, body: str) -> dict:
    return {
        "id": comment_id,
        "node_id": f"node-{comment_id}",
        "body": body,
        "html_url": f"https://github.com/open-telemetry/repo/pull/1#issuecomment-{comment_id}",
        "performed_via_github_app": {"slug": "opentelemetry-pr-dashboard"},
        "user": {"login": "opentelemetry-pr-dashboard[bot]"},
    }


def status_comment(
    comment_id: int = 10,
    episode_id: str = "",
    route: str = "approver",
) -> dict:
    lines = ["<!-- pull-request-dashboard-status -->"]
    if episode_id:
        lines.append(f"<!-- pull-request-dashboard-author-nudge-episode:{episode_id} -->")
    lines.append(f"**{status_headline(route)}** \u00b7 refreshed 2026-08-21 00:00 UTC")
    return dashboard_comment(comment_id, "\n".join(lines))


def nudge_comment(comment_id: int, episode_id: str, created_at: str = "") -> dict:
    comment = dashboard_comment(
        comment_id,
        author_nudge.render_nudge("alice", "https://status", episode_id),
    )
    comment["created_at"] = created_at or f"2026-08-{comment_id:02d}T00:00:00Z"
    return comment


class RefreshAuthorNudgesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.pull = {"number": 7, "author": {"login": "alice"}}

    def sweep(
        self,
        comments: list[dict],
        *,
        minimization_reason: str = "",
        dry_run: bool = False,
    ) -> tuple[list[str], list]:
        with (
            patch.object(
                refresh_author_nudges, "pull_request_comments", return_value=comments
            ),
            patch.object(
                refresh_author_nudges,
                "comment_minimization_reason",
                return_value=minimization_reason,
            ),
            patch.object(refresh_author_nudges, "patch_comment_body") as patched,
            patch.object(refresh_author_nudges, "minimize_comment") as minimized,
            patch.object(refresh_author_nudges, "unminimize_comment") as unminimized,
        ):
            actions = refresh_author_nudges.sweep_pull_request(
                "open-telemetry/repo", self.pull, NOW, dry_run
            )
        return actions, [patched, minimized, unminimized]

    def test_reminder_left_behind_by_lost_state_is_collapsed(self) -> None:
        comments = [status_comment(), nudge_comment(11, STALE_EPISODE)]

        actions, (patched, minimized, unminimized) = self.sweep(comments)

        self.assertEqual(len(actions), 1)
        self.assertIn("stale reminder 11", actions[0])
        minimized.assert_called_once_with("node-11")
        unminimized.assert_not_called()
        body = patched.call_args.args[2]
        self.assertIn(author_nudge.completed_nudge_marker(STALE_EPISODE), body)
        self.assertIn("no longer waiting on you", body)

    def test_superseded_reminder_is_collapsed_as_routing_changed(self) -> None:
        status = status_comment(episode_id=LIVE_EPISODE, route="author")
        live = dashboard_comment(
            12,
            author_nudge.render_nudge("alice", status["html_url"], LIVE_EPISODE),
        )
        live["created_at"] = "2026-08-12T00:00:00Z"
        comments = [status, nudge_comment(11, STALE_EPISODE), live]

        actions, (patched, _, _) = self.sweep(comments)

        self.assertEqual(len(actions), 1)
        self.assertIn("stale reminder 11", actions[0])
        body = patched.call_args.args[2]
        self.assertIn("no longer reflects the current dashboard state", body)

    def test_newest_reminder_is_live_when_the_status_comment_lost_its_episode(
        self,
    ) -> None:
        status = status_comment(route="author")
        comments = [
            status,
            nudge_comment(12, LIVE_EPISODE, "2026-08-12T00:00:00Z"),
            nudge_comment(11, STALE_EPISODE, "2026-08-11T00:00:00Z"),
        ]

        actions, (patched, minimized, _) = self.sweep(comments)

        self.assertEqual(len(actions), 2)
        self.assertIn("stale reminder 11", actions[0])
        self.assertIn("live reminder 12 rewritten", actions[1])
        minimized.assert_called_once_with("node-11")
        self.assertEqual(
            patched.call_args.args[2],
            author_nudge.render_nudge("alice", status["html_url"], LIVE_EPISODE),
        )

    def test_reminder_is_stale_when_status_episode_has_no_comment(self) -> None:
        status = status_comment(episode_id=UNMATCHED_EPISODE, route="author")
        comments = [status, nudge_comment(11, STALE_EPISODE)]

        actions, (patched, minimized, _) = self.sweep(comments)

        self.assertEqual(len(actions), 1)
        self.assertIn("stale reminder 11", actions[0])
        minimized.assert_called_once_with("node-11")
        body = patched.call_args.args[2]
        self.assertIn(author_nudge.completed_nudge_marker(STALE_EPISODE), body)

    def test_ambiguous_status_leaves_reminders_alone(self) -> None:
        comments = [
            status_comment(route="unknown"),
            nudge_comment(11, STALE_EPISODE),
        ]
        stderr = StringIO()

        with redirect_stderr(stderr):
            actions, (patched, minimized, _) = self.sweep(comments)

        self.assertEqual(actions, [])
        self.assertIn("dashboard routing is not definitive", stderr.getvalue())
        patched.assert_not_called()
        minimized.assert_not_called()

    def test_draft_author_status_does_not_keep_a_reminder_live(self) -> None:
        self.pull["isDraft"] = True
        comments = [
            status_comment(route="author"),
            nudge_comment(11, STALE_EPISODE),
        ]

        actions, (patched, minimized, _) = self.sweep(comments)

        self.assertEqual(len(actions), 1)
        self.assertIn("stale reminder 11", actions[0])
        minimized.assert_called_once_with("node-11")
        body = patched.call_args.args[2]
        self.assertIn("no longer reflects the current dashboard state", body)

    def test_live_reminder_is_rewritten_when_the_wording_changed(self) -> None:
        stale_wording = dashboard_comment(
            11,
            f"{author_nudge.nudge_marker(LIVE_EPISODE)}\nOld wording.\n",
        )
        comments = [
            status_comment(episode_id=LIVE_EPISODE, route="author"),
            stale_wording,
        ]

        actions, (patched, minimized, _) = self.sweep(comments)

        self.assertEqual(len(actions), 1)
        self.assertIn("live reminder 11 rewritten", actions[0])
        minimized.assert_not_called()
        self.assertEqual(
            patched.call_args.args[2],
            author_nudge.render_nudge(
                "alice", comments[0]["html_url"], LIVE_EPISODE
            ),
        )

    def test_current_reminders_are_left_alone(self) -> None:
        status = status_comment(episode_id=LIVE_EPISODE, route="author")
        live = dashboard_comment(
            11,
            author_nudge.render_nudge("alice", status["html_url"], LIVE_EPISODE),
        )
        collapsed = dashboard_comment(
            12,
            author_nudge.render_completed_nudge(
                author_nudge.render_nudge("alice", status["html_url"], STALE_EPISODE),
                status["html_url"],
                STALE_EPISODE,
                NOW,
            ),
        )
        live["created_at"] = "2026-08-12T00:00:00Z"
        collapsed["created_at"] = "2026-08-11T00:00:00Z"

        actions, (patched, minimized, unminimized) = self.sweep(
            [status, live, collapsed], minimization_reason="OUTDATED"
        )

        self.assertEqual(actions, [])
        patched.assert_not_called()
        minimized.assert_not_called()
        unminimized.assert_not_called()

    def test_finished_reminders_are_never_taken_for_the_live_one(self) -> None:
        status = status_comment(route="author")
        finished = dashboard_comment(
            11,
            author_nudge.render_completed_nudge(
                author_nudge.render_nudge("alice", status["html_url"], LIVE_EPISODE),
                status["html_url"],
                LIVE_EPISODE,
                NOW,
            ),
        )
        finished["created_at"] = "2026-08-12T00:00:00Z"
        stale = nudge_comment(12, STALE_EPISODE, "2026-08-11T00:00:00Z")

        actions, (patched, minimized, _) = self.sweep([status, finished, stale])

        self.assertEqual(len(actions), 2)
        self.assertIn("stale reminder 12", actions[0])
        self.assertIn("stale reminder 11 collapsed", actions[1])
        self.assertEqual(minimized.call_count, 2)
        body = patched.call_args.args[2]
        self.assertIn(author_nudge.completed_nudge_marker(STALE_EPISODE), body)
        self.assertIn("no longer reflects the current dashboard state", body)

    def test_reminder_hidden_for_another_reason_is_reclassified(self) -> None:
        comments = [status_comment(), nudge_comment(11, STALE_EPISODE)]

        actions, (_, minimized, unminimized) = self.sweep(
            comments, minimization_reason="OFF_TOPIC"
        )

        self.assertEqual(len(actions), 1)
        unminimized.assert_called_once_with("node-11")
        minimized.assert_called_once_with("node-11")

    def test_dry_run_reports_without_writing(self) -> None:
        comments = [status_comment(), nudge_comment(11, STALE_EPISODE)]

        actions, (patched, minimized, unminimized) = self.sweep(
            comments, dry_run=True
        )

        self.assertEqual(len(actions), 1)
        patched.assert_not_called()
        minimized.assert_not_called()
        unminimized.assert_not_called()

    def test_pull_request_without_a_status_comment_is_reported_and_skipped(self) -> None:
        comments = [
            nudge_comment(11, STALE_EPISODE),
            nudge_comment(12, LIVE_EPISODE),
        ]
        stderr = StringIO()

        with redirect_stderr(stderr):
            actions, (patched, minimized, _) = self.sweep(comments)

        self.assertEqual(actions, [])
        self.assertIn(
            "no dashboard status comment; left 2 reminder(s) alone",
            stderr.getvalue(),
        )
        patched.assert_not_called()
        minimized.assert_not_called()

    def test_comments_from_other_authors_are_ignored(self) -> None:
        impostor = {
            "id": 99,
            "node_id": "node-99",
            "body": author_nudge.render_nudge("alice", "https://status", STALE_EPISODE),
            "user": {"login": "mallory"},
        }
        comments = [status_comment(), impostor]

        actions, (patched, minimized, _) = self.sweep(comments)

        self.assertEqual(actions, [])
        patched.assert_not_called()
        minimized.assert_not_called()

    def test_pull_request_failures_do_not_stop_the_sweep(self) -> None:
        pulls = [{"number": 7, "author": {"login": "alice"}}, {"number": 8}]

        def sweep(repo, pull, now, dry_run):
            if pull["number"] == 7:
                raise RuntimeError("boom")
            return ["PR #8: stale reminder 11 collapsed"]

        with (
            patch.object(refresh_author_nudges, "list_open_prs", return_value=pulls),
            patch.object(refresh_author_nudges, "sweep_pull_request", sweep),
        ):
            status = refresh_author_nudges.refresh_author_nudges(
                "open-telemetry/repo", False
            )

        self.assertEqual(status, 1)


if __name__ == "__main__":
    unittest.main()
