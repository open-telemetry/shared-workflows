from __future__ import annotations

from contextlib import redirect_stderr
import io
import unittest
from unittest.mock import patch

from datetime import datetime, timezone

from copilot_review import (
    deliver_copilot_review_requests,
    record_copilot_review_observation,
    stale_request_reason,
)


NOW = datetime(2026, 7, 20, 2, tzinfo=timezone.utc)


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
                    "routing_input_fingerprint": "accepted-fingerprint",
                },
            },
            NOW,
        )

        save_requests.assert_called_once_with({
            "7": {
                "head_sha": "current-head",
                "observed_at": "2026-07-20T02:00:00+00:00",
                "requested_at": "",
                "routing_input_fingerprint": "accepted-fingerprint",
            },
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
                    "routing_input_fingerprint": "accepted-fingerprint",
                },
            },
            NOW,
        )

        save_requests.assert_called_once_with({
            "7": {
                "head_sha": "current-head",
                "observed_at": "2026-07-20T02:00:00+00:00",
                "requested_at": "",
                "routing_input_fingerprint": "accepted-fingerprint",
            },
        })

    @patch("copilot_review.save_copilot_review_requests")
    @patch(
        "copilot_review.load_copilot_review_requests",
        return_value={
            "7": {
                "head_sha": "current-head",
                "requested_at": "2026-07-20T01:00:00Z",
            }
        },
    )
    def test_same_head_request_needed_resets_acknowledgement(
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
                    "copilot_review_request_needed": True,
                    "routing_input_fingerprint": "accepted-fingerprint",
                },
            },
            NOW,
        )

        save_requests.assert_called_once_with({
            "7": {
                "head_sha": "current-head",
                "observed_at": "2026-07-20T02:00:00+00:00",
                "requested_at": "",
                "routing_input_fingerprint": "accepted-fingerprint",
            },
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
            NOW,
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
            NOW,
        )

        save_requests.assert_called_once_with({})

    @patch(
        "copilot_review.routing_input_fingerprint",
        return_value="accepted-fingerprint",
    )
    @patch("copilot_review.request_copilot_review")
    @patch("copilot_review.fetch_pr_reviews")
    @patch("copilot_review.fetch_current_pr_routing_inputs")
    @patch("copilot_review.save_copilot_review_requests")
    @patch(
        "copilot_review.load_copilot_review_requests",
        return_value={
            "7": {
                "head_sha": "current-head",
                "observed_at": "2026-07-20T01:00:00+00:00",
                "requested_at": "",
                "routing_input_fingerprint": "accepted-fingerprint",
            }
        },
    )
    def test_delivers_request_for_current_stale_review(
        self,
        _load_requests,
        save_requests,
        fetch_current_state,
        fetch_reviews,
        request_review,
        _fingerprint,
    ) -> None:
        pr = {
            "state": "OPEN",
            "isDraft": False,
            "headRefOid": "current-head",
            "id": "PR_node_id",
        }
        fetch_current_state.return_value = (pr, {})
        fetch_reviews.return_value = [{
            "id": 20,
            "commit_id": "reviewed-head",
            "finding_count": 0,
            "user": {"login": "copilot"},
            "submitted_at": "2026-07-20T01:00:00Z",
        }]

        errors = deliver_copilot_review_requests(
            "open-telemetry/example",
            NOW,
        )

        self.assertEqual([], errors)
        fetch_current_state.assert_called_once_with("open-telemetry/example", 7)
        fetch_reviews.assert_called_once_with("open-telemetry", "example", 7)
        request_review.assert_called_once_with("PR_node_id")
        save_requests.assert_called_once_with({
            "7": {
                "head_sha": "current-head",
                "observed_at": "2026-07-20T01:00:00+00:00",
                "requested_at": "2026-07-20T02:00:00+00:00",
                "routing_input_fingerprint": "accepted-fingerprint",
            },
        })

    @patch(
        "copilot_review.routing_input_fingerprint",
        return_value="accepted-fingerprint",
    )
    @patch("copilot_review.request_copilot_review")
    @patch("copilot_review.fetch_pr_reviews")
    @patch("copilot_review.fetch_current_pr_routing_inputs")
    @patch("copilot_review.save_copilot_review_requests")
    @patch(
        "copilot_review.load_copilot_review_requests",
        return_value={
            "7": {
                "head_sha": "current-head",
                "observed_at": "2026-07-20T01:00:00+00:00",
                "requested_at": "",
                "routing_input_fingerprint": "accepted-fingerprint",
            }
        },
    )
    def test_pending_request_is_acknowledged_from_pull_response(
        self,
        _load_requests,
        save_requests,
        fetch_current_state,
        fetch_reviews,
        request_review,
        _fingerprint,
    ) -> None:
        pr = {
            "state": "OPEN",
            "isDraft": False,
            "headRefOid": "current-head",
        }
        fetch_current_state.return_value = (
            pr,
            {
                "review_requests": [
                    {
                        "__typename": "Bot",
                        "login": "copilot-pull-request-reviewer",
                    },
                ],
            },
        )

        errors = deliver_copilot_review_requests(
            "open-telemetry/example",
            NOW,
        )

        self.assertEqual([], errors)
        fetch_current_state.assert_called_once_with("open-telemetry/example", 7)
        fetch_reviews.assert_not_called()
        request_review.assert_not_called()
        save_requests.assert_called_once_with({
            "7": {
                "head_sha": "current-head",
                "observed_at": "2026-07-20T01:00:00+00:00",
                "requested_at": "2026-07-20T02:00:00+00:00",
                "routing_input_fingerprint": "accepted-fingerprint",
            },
        })

    @patch(
        "copilot_review.routing_input_fingerprint",
        return_value="new-fingerprint",
    )
    @patch("copilot_review.request_copilot_review")
    @patch("copilot_review.fetch_pr_reviews")
    @patch(
        "copilot_review.fetch_current_pr_routing_inputs",
        return_value=(
            {
                "state": "OPEN",
                "isDraft": False,
                "headRefOid": "current-head",
            },
            {},
        ),
    )
    @patch("copilot_review.save_copilot_review_requests")
    @patch(
        "copilot_review.load_copilot_review_requests",
        return_value={
            "7": {
                "head_sha": "current-head",
                "observed_at": "2026-07-20T01:00:00+00:00",
                "requested_at": "",
                "routing_input_fingerprint": "accepted-fingerprint",
            }
        },
    )
    def test_drops_request_when_live_routing_inputs_changed(
        self,
        _load_requests,
        save_requests,
        _fetch_current_state,
        fetch_reviews,
        request_review,
        _fingerprint,
    ) -> None:
        stderr = io.StringIO()

        with redirect_stderr(stderr):
            errors = deliver_copilot_review_requests(
                "open-telemetry/example",
                NOW,
            )

        self.assertEqual([], errors)
        fetch_reviews.assert_not_called()
        request_review.assert_not_called()
        save_requests.assert_called_once_with({})
        discarded = stderr.getvalue()
        self.assertIn(
            "discarding Copilot review request for PR #7: routing fingerprint is "
            "new-fingerprint but accepted-fingerprint was observed; components ",
            discarded,
        )
        for component in (
            "base_branch",
            "checks",
            "issue_comments",
            "labels",
            "pr_text",
            "review_comments",
            "reviews",
            "review_threads",
        ):
            self.assertIn(component, discarded)

    @patch(
        "copilot_review.routing_input_fingerprint",
        return_value="accepted-fingerprint",
    )
    @patch("copilot_review.request_copilot_review")
    @patch("copilot_review.fetch_pr_reviews", return_value=[])
    @patch(
        "copilot_review.fetch_current_pr_routing_inputs",
        return_value=(
            {
                "state": "OPEN",
                "isDraft": False,
                "headRefOid": "current-head",
            },
            {},
        ),
    )
    @patch("copilot_review.save_copilot_review_requests")
    @patch(
        "copilot_review.load_copilot_review_requests",
        return_value={
            "7": {
                "head_sha": "current-head",
                "observed_at": "2026-07-20T01:00:00+00:00",
                "requested_at": "",
                "routing_input_fingerprint": "accepted-fingerprint",
            }
        },
    )
    def test_drops_request_when_copilot_review_no_longer_needed(
        self,
        _load_requests,
        save_requests,
        _fetch_current_state,
        _fetch_reviews,
        request_review,
        _fingerprint,
    ) -> None:
        stderr = io.StringIO()

        with redirect_stderr(stderr):
            errors = deliver_copilot_review_requests(
                "open-telemetry/example",
                NOW,
            )

        self.assertEqual([], errors)
        request_review.assert_not_called()
        save_requests.assert_called_once_with({})
        self.assertIn(
            "discarding Copilot review request for PR #7: Copilot review "
            "exists=False needed=False for head current-head",
            stderr.getvalue(),
        )


class StaleRequestReasonTest(unittest.TestCase):
    ENTRY = {
        "head_sha": "current-head",
        "routing_input_fingerprint": "accepted-fingerprint",
    }
    OPEN_PR = {"state": "OPEN", "isDraft": False}

    def reason(
        self,
        entry: dict | None = None,
        pr: dict | None = None,
        current_head: str = "current-head",
        current_routing_fingerprint: str = "accepted-fingerprint",
    ) -> str:
        return stale_request_reason(
            self.ENTRY if entry is None else entry,
            self.OPEN_PR if pr is None else pr,
            current_head,
            current_routing_fingerprint,
            {},
        )

    def test_current_request_is_not_stale(self) -> None:
        self.assertEqual("", self.reason())

    def test_reports_closed_pull_request(self) -> None:
        self.assertEqual(
            "pull request state is 'CLOSED'",
            self.reason(pr={"state": "CLOSED"}),
        )

    def test_reports_draft_pull_request(self) -> None:
        self.assertEqual(
            "pull request is a draft",
            self.reason(pr={"state": "OPEN", "isDraft": True}),
        )

    def test_reports_advanced_head(self) -> None:
        self.assertEqual(
            "head is new-head but current-head was observed",
            self.reason(current_head="new-head"),
        )

    def test_reports_missing_observed_fingerprint(self) -> None:
        self.assertEqual(
            "no routing fingerprint was observed",
            self.reason(entry={"head_sha": "current-head"}),
        )

    def test_fingerprint_mismatch_reports_component_digests(self) -> None:
        reason = self.reason(current_routing_fingerprint="new-fingerprint")

        self.assertIn(
            "routing fingerprint is new-fingerprint but accepted-fingerprint "
            "was observed; components ",
            reason,
        )
        for component in (
            "base_branch",
            "checks",
            "issue_comments",
            "labels",
            "pr_text",
            "review_comments",
            "reviews",
            "review_threads",
        ):
            self.assertIn(component, reason)


if __name__ == "__main__":
    unittest.main()