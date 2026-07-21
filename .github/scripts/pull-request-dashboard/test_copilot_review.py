from __future__ import annotations

import unittest
from unittest.mock import patch

from datetime import datetime, timezone

from copilot_review import deliver_copilot_review_requests, record_copilot_review_observation


class CopilotReviewRequestStateTest(unittest.TestCase):
    @patch("copilot_review.save_copilot_review_requests")
    @patch("copilot_review.load_copilot_review_requests", return_value={})
    def test_records_request_for_current_head(self, _load_requests, save_requests) -> None:
        record_copilot_review_observation(
            7,
            {
                "route": "copilot",
                "facts": {
                    "head_sha": "current-head",
                    "copilot_review_request_needed": True,
                },
            },
        )

        save_requests.assert_called_once_with({
            "7": {"head_sha": "current-head", "requested_at": ""},
        })

    @patch("copilot_review.save_copilot_review_requests")
    @patch(
        "copilot_review.load_copilot_review_requests",
        return_value={"7": {"head_sha": "old-head", "requested_at": "old-request"}},
    )
    def test_new_head_replaces_previous_request(self, _load_requests, save_requests) -> None:
        record_copilot_review_observation(
            7,
            {
                "route": "copilot",
                "facts": {
                    "head_sha": "current-head",
                    "copilot_review_request_needed": True,
                },
            },
        )

        save_requests.assert_called_once_with({
            "7": {"head_sha": "current-head", "requested_at": ""},
        })

    @patch("copilot_review.save_copilot_review_requests")
    @patch(
        "copilot_review.load_copilot_review_requests",
        return_value={"7": {"head_sha": "current-head", "requested_at": ""}},
    )
    def test_clears_request_when_no_longer_needed(self, _load_requests, save_requests) -> None:
        record_copilot_review_observation(
            7,
            {
                "route": "maintainer",
                "facts": {
                    "head_sha": "current-head",
                    "copilot_review_request_needed": False,
                },
            },
        )

        save_requests.assert_called_once_with({})

    @patch("copilot_review.save_copilot_review_requests")
    @patch("copilot_review.load_copilot_review_requests", return_value={})
    def test_initial_automatic_review_does_not_enqueue_request(
        self,
        _load_requests,
        save_requests,
    ) -> None:
        record_copilot_review_observation(
            7,
            {
                "route": "copilot",
                "facts": {
                    "head_sha": "current-head",
                    "copilot_review_exists": False,
                    "copilot_review_request_needed": False,
                },
            },
        )

        save_requests.assert_called_once_with({})

    @patch("copilot_review.request_copilot_review")
    @patch("copilot_review.fetch_pr_review_data")
    @patch("copilot_review.gh_pr_view", return_value={"reviewRequests": []})
    @patch("copilot_review.gh_api")
    @patch("copilot_review.save_copilot_review_requests")
    @patch(
        "copilot_review.load_copilot_review_requests",
        return_value={"7": {"head_sha": "current-head", "requested_at": ""}},
    )
    def test_delivers_request_for_current_stale_review(
        self,
        _load_requests,
        save_requests,
        gh_api,
        _gh_pr_view,
        fetch_review_data,
        request_review,
    ) -> None:
        gh_api.side_effect = [
            {"state": "open", "draft": False, "head": {"sha": "current-head"}},
            [{"sha": "reviewed-head"}, {"sha": "current-head"}],
            [],
        ]
        fetch_review_data.return_value = {
            "reviews": [{
                "id": 20,
                "commit_id": "reviewed-head",
                "user": {"login": "copilot"},
                "submitted_at": "2026-07-20T01:00:00Z",
            }],
        }

        errors = deliver_copilot_review_requests(
            "open-telemetry/example",
            datetime(2026, 7, 20, 2, tzinfo=timezone.utc),
        )

        self.assertEqual([], errors)
        request_review.assert_called_once_with("open-telemetry/example", 7)
        save_requests.assert_called_once_with({
            "7": {
                "head_sha": "current-head",
                "requested_at": "2026-07-20T02:00:00+00:00",
            },
        })


if __name__ == "__main__":
    unittest.main()