from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
import unittest

from pull_request_activity import (
    ActivityInput,
    build_activity_timeline,
    is_substantive_activity,
)
from dashboard_test_support import pull_request_source
from pull_request_source import (
    normalize_commits,
    normalize_issue_comments,
    normalize_review_comments,
    normalize_reviews,
)


def activity(
    *,
    commits: list[dict] | None = None,
    issue_comments: list[dict] | None = None,
    review_comments: list[dict] | None = None,
    reviews: list[dict] | None = None,
):
    return build_activity_timeline(
        ActivityInput(
            pull_request_source(
                commits=normalize_commits(commits),
                issue_comments=normalize_issue_comments(issue_comments),
                review_comments=normalize_review_comments(review_comments),
                reviews=normalize_reviews(reviews),
            ),
            "author",
            frozenset({"reviewer"}),
        )
    )


def commit(
    *,
    author_login: str = "author",
    committer_login: str = "author",
    author_name: str = "Author",
    author_date: str = "2026-07-14T01:00:00Z",
    committer_date: str = "2026-07-14T02:00:00Z",
    parents: int = 1,
) -> dict:
    return {
        "sha": "abcdef123456",
        "author": {"login": author_login} if author_login else {},
        "committer": {"login": committer_login} if committer_login else {},
        "commit": {
            "author": {"name": author_name, "date": author_date},
            "committer": {"date": committer_date} if committer_date else {},
            "message": "Address feedback",
        },
        "parents": [{} for _ in range(parents)],
    }


class CommitActivityTest(unittest.TestCase):
    def test_author_commit_prefers_committer_identity_and_date(self) -> None:
        event = activity(
            commits=[commit(author_login="other", committer_login="author")]
        ).events[0]

        self.assertEqual(
            {
                "kind": "commit",
                "timestamp": "2026-07-14T02:00:00Z",
                "actor": "author",
                "actor_role": "author",
                "body": "Address feedback",
                "state": None,
                "path": None,
                "sha": "abcdef1",
                "is_merge_from_base_by_non_author": False,
            },
            event,
        )

    def test_author_commit_falls_back_to_author_date(self) -> None:
        event = activity(
            commits=[commit(committer_date="")]
        ).events[0]

        self.assertEqual("author", event["actor"])
        self.assertEqual("2026-07-14T01:00:00Z", event["timestamp"])

    def test_maintainer_cherry_pick_uses_original_author_and_date(self) -> None:
        event = activity(
            commits=[commit(committer_login="maintainer")]
        ).events[0]

        self.assertEqual("author", event["actor"])
        self.assertEqual("2026-07-14T01:00:00Z", event["timestamp"])

    def test_non_author_commit_uses_committer_identity_and_date(self) -> None:
        event = activity(
            commits=[
                commit(
                    author_login="contributor",
                    committer_login="maintainer",
                )
            ]
        ).events[0]

        self.assertEqual("maintainer", event["actor"])
        self.assertEqual("2026-07-14T02:00:00Z", event["timestamp"])

    def test_missing_github_identities_fall_back_to_author_name_and_date(self) -> None:
        event = activity(
            commits=[
                commit(
                    author_login="",
                    committer_login="",
                    author_name="Local Author",
                )
            ]
        ).events[0]

        self.assertEqual("Local Author", event["actor"])
        self.assertEqual("2026-07-14T01:00:00Z", event["timestamp"])

    def test_missing_committer_identity_uses_commit_author_login(self) -> None:
        event = activity(
            commits=[
                commit(
                    author_login="contributor",
                    committer_login="",
                )
            ]
        ).events[0]

        self.assertEqual("contributor", event["actor"])
        self.assertEqual("2026-07-14T01:00:00Z", event["timestamp"])

    def test_non_author_merge_commit_is_marked_and_not_substantive(self) -> None:
        event = activity(
            commits=[
                commit(
                    author_login="contributor",
                    committer_login="maintainer",
                    parents=2,
                )
            ]
        ).events[0]

        self.assertTrue(event["is_merge_from_base_by_non_author"])
        self.assertFalse(is_substantive_activity(event))


class CommentActivityTest(unittest.TestCase):
    def test_minimized_and_command_only_issue_comments_are_dropped(self) -> None:
        timeline = activity(
            issue_comments=[
                {
                    "id": 1,
                    "minimized": {"reason": "off-topic"},
                    "created_at": "2026-07-14T01:00:00Z",
                    "user": {"login": "author"},
                    "body": "Hidden",
                },
                {
                    "id": 2,
                    "created_at": "2026-07-14T02:00:00Z",
                    "user": {"login": "author"},
                    "body": "/dashboard route:reviewers",
                },
            ]
        )

        self.assertEqual((), timeline.events)

    def test_dashboard_command_explanation_is_retained(self) -> None:
        event = activity(
            issue_comments=[
                {
                    "id": 1,
                    "created_at": "2026-07-14T01:00:00Z",
                    "user": {"login": "author"},
                    "body": (
                        "/dashboard route:reviewers\n\n"
                        "I addressed the feedback."
                    ),
                }
            ]
        ).events[0]

        self.assertEqual("I addressed the feedback.", event["body"])

    def test_edited_comments_use_activity_time_but_creation_order(self) -> None:
        timeline = activity(
            issue_comments=[
                {
                    "id": 1,
                    "created_at": "2026-07-14T01:00:00Z",
                    "updated_at": "2026-07-14T07:00:00Z",
                    "content_updated_at": "2026-07-14T06:00:00Z",
                    "user": {"login": "author"},
                    "body": "Edited issue comment",
                }
            ],
            review_comments=[
                {
                    "id": 2,
                    "created_at": "2026-07-14T02:00:00Z",
                    "updated_at": "2026-07-14T05:00:00Z",
                    "user": {"login": "reviewer"},
                    "body": "Edited review comment",
                    "path": "file.py",
                },
                {
                    "id": 3,
                    "created_at": "2026-07-14T03:00:00Z",
                    "updated_at": "2026-07-14T03:00:00Z",
                    "user": {"login": "reviewer"},
                    "body": "Newer review comment",
                },
            ],
        )

        self.assertEqual([1, 2, 3], [event["source_id"] for event in timeline.events])
        self.assertEqual(
            ["2026-07-14T06:00:00Z", "2026-07-14T05:00:00Z"],
            [timeline.events[0]["timestamp"], timeline.events[1]["timestamp"]],
        )
        self.assertEqual(
            ["2026-07-14T01:00:00Z", "2026-07-14T02:00:00Z"],
            [
                timeline.events[0]["created_timestamp"],
                timeline.events[1]["created_timestamp"],
            ],
        )

    def test_edited_review_keeps_submission_time_for_state_ordering(self) -> None:
        event = activity(
            reviews=[
                {
                    "database_id": 1,
                    "submitted_at": "2026-07-14T01:00:00Z",
                    "updated_at": "2026-07-14T03:00:00Z",
                    "content_updated_at": "2026-07-14T03:00:00Z",
                    "user": {"login": "reviewer"},
                    "state": "CHANGES_REQUESTED",
                    "body": "Please update this.",
                }
            ],
        ).events[0]

        self.assertEqual("2026-07-14T01:00:00Z", event["timestamp"])
        self.assertEqual("2026-07-14T03:00:00Z", event["content_timestamp"])

    def test_copilot_identity_shapes_are_normalized(self) -> None:
        timeline = activity(
            issue_comments=[
                {
                    "id": 1,
                    "created_at": "2026-07-14T01:00:00Z",
                    "user": {"login": "copilot"},
                    "body": "Automated comment",
                }
            ],
            review_comments=[
                {
                    "id": 2,
                    "created_at": "2026-07-14T02:00:00Z",
                    "user": {"login": "copilot-pull-request-reviewer"},
                    "body": "Automated review comment",
                }
            ],
            reviews=[
                {
                    "id": 3,
                    "submitted_at": "2026-07-14T03:00:00Z",
                    "user": {"login": "copilot-pull-request-reviewer[bot]"},
                    "state": "COMMENTED",
                }
            ],
        )

        self.assertEqual(
            ["copilot-pull-request-reviewer[bot]"] * 3,
            [event["actor"] for event in timeline.events],
        )
        self.assertEqual(["bot"] * 3, [event["actor_role"] for event in timeline.events])

    def test_events_with_empty_timestamps_are_dropped(self) -> None:
        timeline = activity(
            commits=[commit(author_date="", committer_date="")],
            issue_comments=[{"id": 1, "body": "No timestamp"}],
            review_comments=[{"id": 2, "body": "No timestamp"}],
            reviews=[{"id": 3, "state": "APPROVED"}],
        )

        self.assertEqual((), timeline.events)


class ActivityClockTest(unittest.TestCase):
    def test_review_states_and_body_text_determine_substantive_activity(self) -> None:
        timeline = activity(
            issue_comments=[
                {
                    "id": 1,
                    "created_at": "2026-07-14T01:00:00Z",
                    "user": {"login": "author"},
                    "body": "   ",
                },
                {
                    "id": 2,
                    "created_at": "2026-07-14T02:00:00Z",
                    "user": {"login": "outsider"},
                    "body": "Participant update",
                },
            ],
            reviews=[
                {
                    "id": 3,
                    "submitted_at": "2026-07-14T03:00:00Z",
                    "user": {"login": "reviewer"},
                    "state": "COMMENTED",
                    "body": "",
                },
                {
                    "id": 4,
                    "submitted_at": "2026-07-14T04:00:00Z",
                    "user": {"login": "reviewer"},
                    "state": "APPROVED",
                    "body": "",
                },
            ],
        )

        self.assertFalse(is_substantive_activity(timeline.events[0]))
        self.assertFalse(is_substantive_activity(timeline.events[2]))
        self.assertTrue(is_substantive_activity(timeline.events[3]))
        self.assertEqual(
            datetime(2026, 7, 14, 4, tzinfo=timezone.utc),
            timeline.latest_participant_activity_at,
        )
        self.assertIsNone(timeline.latest_author_activity_at)
        self.assertEqual(
            datetime(2026, 7, 14, 4, tzinfo=timezone.utc),
            timeline.latest_approver_activity_at,
        )

    def test_review_edit_updates_content_activity_clock(self) -> None:
        timeline = activity(
            reviews=[
                {
                    "id": 1,
                    "submitted_at": "2026-07-14T01:00:00Z",
                    "content_updated_at": "2026-07-14T03:00:00Z",
                    "user": {"login": "reviewer"},
                    "state": "CHANGES_REQUESTED",
                    "body": "Please also update the tests.",
                }
            ],
        )

        self.assertEqual(
            datetime(2026, 7, 14, 3, tzinfo=timezone.utc),
            timeline.latest_approver_activity_at,
        )

    def test_all_activity_clocks_are_independent(self) -> None:
        timeline = activity(
            issue_comments=[
                {
                    "id": 1,
                    "created_at": "2026-07-14T01:00:00Z",
                    "user": {"login": "author"},
                    "body": "Author update",
                },
                {
                    "id": 2,
                    "created_at": "2026-07-14T02:00:00Z",
                    "user": {"login": "reviewer"},
                    "body": "Approver update",
                },
                {
                    "id": 3,
                    "created_at": "2026-07-14T03:00:00Z",
                    "user": {"login": "outsider"},
                    "body": "Outsider update",
                },
                {
                    "id": 4,
                    "created_at": "2026-07-14T04:00:00Z",
                    "user": {"login": "status[bot]"},
                    "body": "Bot update",
                },
            ]
        )

        self.assertEqual(
            datetime(2026, 7, 14, 3, tzinfo=timezone.utc),
            timeline.latest_participant_activity_at,
        )
        self.assertEqual(
            datetime(2026, 7, 14, 1, tzinfo=timezone.utc),
            timeline.latest_author_activity_at,
        )
        self.assertEqual(
            datetime(2026, 7, 14, 2, tzinfo=timezone.utc),
            timeline.latest_approver_activity_at,
        )

    def test_activity_timeline_is_frozen(self) -> None:
        timeline = activity()

        with self.assertRaises(FrozenInstanceError):
            timeline.events = ()  # type: ignore[misc]

    def test_activity_timeline_events_are_frozen(self) -> None:
        timeline = activity(
            issue_comments=[
                {
                    "id": 1,
                    "created_at": "2026-07-14T01:00:00Z",
                    "user": {"login": "author"},
                    "body": "Author update",
                }
            ]
        )

        with self.assertRaises(TypeError):
            timeline.events[0]["body"] = "Changed"  # type: ignore[index]


if __name__ == "__main__":
    unittest.main()
