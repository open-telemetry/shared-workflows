from __future__ import annotations

from contextlib import redirect_stderr
from datetime import datetime, timezone
import io
import unittest
from unittest.mock import patch

from author_nudge import copilot_request_fingerprint

from copilot_review import (
    REQUEST_CONFIRMATION_ATTEMPTS,
    copilot_first_review_overdue,
    deliver_copilot_review_requests,
    record_copilot_review_observation,
    set_copilot_first_review_missing_since,
    set_copilot_review_request_needed,
    stale_request_reason,
)
from utils import format_ts


NOW = datetime(2026, 7, 20, 2, tzinfo=timezone.utc)


class CopilotFirstReviewMissingSinceTest(unittest.TestCase):
    def test_starts_clock_when_review_is_missing(self) -> None:
        facts: dict = {}

        set_copilot_first_review_missing_since(
            facts, None, enabled=True, now=NOW
        )

        self.assertEqual(
            "2026-07-20T02:00:00+00:00",
            facts["copilot_first_review_missing_since"],
        )

    def test_carries_clock_forward_across_passes(self) -> None:
        facts: dict = {}
        previous = {
            "facts": {"copilot_first_review_missing_since": "2026-07-20T00:00:00+00:00"}
        }

        set_copilot_first_review_missing_since(
            facts, previous, enabled=True, now=NOW
        )

        self.assertEqual(
            "2026-07-20T00:00:00+00:00",
            facts["copilot_first_review_missing_since"],
        )

    def test_push_does_not_restart_clock(self) -> None:
        # GitHub does not automatically review a PR it has never reviewed, so a
        # push must not reset the wait or an active PR would never recover.
        facts: dict = {"head_sha": "new-head"}
        previous = {
            "facts": {
                "head_sha": "old-head",
                "copilot_first_review_missing_since": "2026-07-20T00:00:00+00:00",
            }
        }

        set_copilot_first_review_missing_since(
            facts, previous, enabled=True, now=NOW
        )

        self.assertEqual(
            "2026-07-20T00:00:00+00:00",
            facts["copilot_first_review_missing_since"],
        )

    def test_draft_clears_clock(self) -> None:
        facts: dict = {"is_draft": True}
        previous = {
            "facts": {"copilot_first_review_missing_since": "2026-07-20T00:00:00+00:00"}
        }

        set_copilot_first_review_missing_since(
            facts, previous, enabled=True, now=NOW
        )

        self.assertNotIn("copilot_first_review_missing_since", facts)

    def test_existing_review_clears_clock(self) -> None:
        facts: dict = {"copilot_review_exists": True}
        previous = {
            "facts": {"copilot_first_review_missing_since": "2026-07-20T00:00:00+00:00"}
        }

        set_copilot_first_review_missing_since(
            facts, previous, enabled=True, now=NOW
        )

        self.assertNotIn("copilot_first_review_missing_since", facts)

    def test_disabled_gate_clears_clock(self) -> None:
        facts: dict = {}
        previous = {
            "facts": {"copilot_first_review_missing_since": "2026-07-20T00:00:00+00:00"}
        }

        set_copilot_first_review_missing_since(
            facts, previous, enabled=False, now=NOW
        )

        self.assertNotIn("copilot_first_review_missing_since", facts)

    def test_overdue_only_after_the_grace_period(self) -> None:
        self.assertFalse(copilot_first_review_overdue({}, NOW))
        self.assertFalse(
            copilot_first_review_overdue(
                {"copilot_first_review_missing_since": "2026-07-20T01:01:00+00:00"},
                NOW,
            )
        )
        self.assertTrue(
            copilot_first_review_overdue(
                {"copilot_first_review_missing_since": "2026-07-20T01:00:00+00:00"},
                NOW,
            )
        )


class CopilotFirstReviewRequestTest(unittest.TestCase):
    def base_facts(self, **overrides) -> dict:
        facts = {
            "copilot_review_exists": False,
            "copilot_review_stale": False,
            "copilot_review_requested": False,
            "ci_pending_count": 0,
        }
        facts.update(overrides)
        return facts

    def test_within_grace_does_not_request(self) -> None:
        facts = self.base_facts(
            copilot_first_review_missing_since="2026-07-20T01:30:00+00:00",
        )

        set_copilot_review_request_needed(
            facts, "approver", enabled=True, now=NOW
        )

        self.assertFalse(facts["copilot_review_request_needed"])

    def test_past_grace_requests_the_first_review(self) -> None:
        facts = self.base_facts(
            copilot_first_review_missing_since="2026-07-20T00:30:00+00:00",
        )

        set_copilot_review_request_needed(
            facts, "approver", enabled=True, now=NOW
        )

        self.assertTrue(facts["copilot_review_request_needed"])

    def test_pending_request_is_not_duplicated(self) -> None:
        facts = self.base_facts(
            copilot_review_requested=True,
            copilot_first_review_missing_since="2026-07-20T00:30:00+00:00",
        )

        set_copilot_review_request_needed(
            facts, "approver", enabled=True, now=NOW
        )

        self.assertFalse(facts["copilot_review_request_needed"])

    def test_unsettled_checks_do_not_hold_the_first_review_request(self) -> None:
        facts = self.base_facts(
            ci_pending_count=1,
            copilot_first_review_missing_since="2026-07-20T00:30:00+00:00",
        )

        set_copilot_review_request_needed(
            facts, "approver", enabled=True, now=NOW
        )

        self.assertTrue(facts["copilot_review_request_needed"])

    def test_author_route_does_not_request(self) -> None:
        facts = self.base_facts(
            copilot_first_review_missing_since="2026-07-20T00:30:00+00:00",
        )

        set_copilot_review_request_needed(
            facts, "author", enabled=True, now=NOW
        )

        self.assertFalse(facts["copilot_review_request_needed"])


class CopilotReviewRequestStateTest(unittest.TestCase):
    @patch("copilot_review.save_copilot_review_requests")
    @patch("copilot_review.load_copilot_review_requests", return_value={})
    def test_records_request_for_current_head(self, _load_requests, save_requests) -> None:
        record_copilot_review_observation(
            7,
            {
                "route": "approver",
                "facts": {
                    "head_sha": "current-head",
                    "copilot_review_request_needed": True,
                    "copilot_request_fingerprint": "accepted-fingerprint",
                },
            },
            NOW,
        )

        save_requests.assert_called_once_with({
            "7": {
                "head_sha": "current-head",
                "observed_at": "2026-07-20T02:00:00+00:00",
                "requested_at": "",
                "copilot_request_fingerprint": "accepted-fingerprint",
            },
        })

    @patch("copilot_review.save_copilot_review_requests")
    @patch(
        "copilot_review.load_copilot_review_requests",
        return_value={
            "7": {
                "head_sha": "old-head",
                "requested_at": "old-request",
            }
        },
    )
    def test_new_head_replaces_previous_request(self, _load_requests, save_requests) -> None:
        record_copilot_review_observation(
            7,
            {
                "route": "approver",
                "facts": {
                    "head_sha": "current-head",
                    "copilot_review_request_needed": True,
                    "copilot_request_fingerprint": "accepted-fingerprint",
                },
            },
            NOW,
        )

        save_requests.assert_called_once_with({
            "7": {
                "head_sha": "current-head",
                "observed_at": "2026-07-20T02:00:00+00:00",
                "requested_at": "",
                "copilot_request_fingerprint": "accepted-fingerprint",
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
                "route": "approver",
                "facts": {
                    "head_sha": "current-head",
                    "copilot_review_request_needed": True,
                    "copilot_request_fingerprint": "accepted-fingerprint",
                },
            },
            NOW,
        )

        save_requests.assert_called_once_with({
            "7": {
                "head_sha": "current-head",
                "observed_at": "2026-07-20T02:00:00+00:00",
                "requested_at": "",
                "copilot_request_fingerprint": "accepted-fingerprint",
            },
        })

    @patch("copilot_review.save_copilot_review_requests")
    @patch(
        "copilot_review.load_copilot_review_requests",
        return_value={
            "7": {
                "head_sha": "current-head",
                "requested_at": "",
            }
        },
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
    def test_missing_first_review_within_grace_does_not_enqueue_request(
        self,
        _load_requests,
        save_requests,
    ) -> None:
        record_copilot_review_observation(
            7,
            {
                "route": "approver",
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
        "copilot_review.fetch_review_requests",
        return_value=[{"__typename": "Bot", "login": "copilot-pull-request-reviewer"}],
    )
    @patch(
        "copilot_review.copilot_request_fingerprint",
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
                "copilot_request_fingerprint": "accepted-fingerprint",
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
        fetch_pending_requests,
    ) -> None:
        pr = {
            "state": "OPEN",
            "isDraft": False,
            "headRefOid": "current-head",
            "id": "PR_node_id",
        }
        fetch_current_state.return_value = (pr, {"checks": []})
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
        fetch_pending_requests.assert_called_once_with("open-telemetry", "example", 7)
        save_requests.assert_called_once_with({
            "7": {
                "head_sha": "current-head",
                "observed_at": "2026-07-20T01:00:00+00:00",
                "requested_at": "2026-07-20T02:00:00+00:00",
                "copilot_request_fingerprint": "accepted-fingerprint",
            },
        })

    @patch(
        "copilot_review.copilot_request_fingerprint",
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
                "copilot_request_fingerprint": "accepted-fingerprint",
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
                "checks": [],
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
                "copilot_request_fingerprint": "accepted-fingerprint",
            },
        })

    @patch(
        "copilot_review.copilot_request_fingerprint",
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
            {"checks": []},
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
                "copilot_request_fingerprint": "accepted-fingerprint",
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
            "conflicts",
            "issue_comments",
            "pr_text",
            "review_comments",
            "review_requests",
            "reviews",
            "review_threads",
        ):
            self.assertIn(component, discarded)
        self.assertNotIn("checks", discarded)

    def test_running_checks_do_not_discard_a_pending_request(self) -> None:
        # The request is recorded while CI is still running, so every check that
        # finishes changes the routing inputs. Discarding on that would put the
        # request back to waiting for CI, one pass at a time.
        pr = {
            "id": "PR_node",
            "state": "OPEN",
            "isDraft": False,
            "headRefOid": "current-head",
            "mergeable": "MERGEABLE",
            "mergeStateStatus": "CLEAN",
        }
        observed_raw = {
            "checks": [{"name": "build", "bucket": "pending"}],
            "pr": pr,
        }
        delivery_raw = {
            "checks": [{"name": "build", "bucket": "pass"}],
            "pr": pr,
        }
        requests = {
            "7": {
                "head_sha": "current-head",
                "observed_at": "2026-07-20T01:00:00+00:00",
                "requested_at": "",
                "copilot_request_fingerprint": copilot_request_fingerprint(observed_raw),
            }
        }
        with (
            patch(
                "copilot_review.load_copilot_review_requests",
                return_value=requests,
            ),
            patch("copilot_review.save_copilot_review_requests") as save_requests,
            patch(
                "copilot_review.fetch_current_pr_routing_inputs",
                return_value=(pr, delivery_raw),
            ),
            patch("copilot_review.fetch_pr_reviews", return_value=[]),
            patch("copilot_review.request_copilot_review") as request_review,
            patch(
                "copilot_review.fetch_review_requests",
                return_value=[{"login": "Copilot"}],
            ),
        ):
            errors = deliver_copilot_review_requests(
                "open-telemetry/example",
                NOW,
            )

        self.assertEqual([], errors)
        request_review.assert_called_once_with("PR_node")
        save_requests.assert_called_once_with(
            {
                "7": {
                    "head_sha": "current-head",
                    "observed_at": "2026-07-20T01:00:00+00:00",
                    "requested_at": format_ts(NOW),
                    "copilot_request_fingerprint": copilot_request_fingerprint(
                        observed_raw
                    ),
                }
            }
        )

    def test_drops_request_when_pr_becomes_conflicted(self) -> None:
        clean_raw = {
            "checks": [],
            "pr": {
                "mergeable": "MERGEABLE",
                "mergeStateStatus": "CLEAN",
            },
        }
        conflicted_pr = {
            "id": "PR_node",
            "state": "OPEN",
            "isDraft": False,
            "headRefOid": "current-head",
            "mergeable": "CONFLICTING",
            "mergeStateStatus": "DIRTY",
        }
        conflicted_raw = {"checks": [], "pr": conflicted_pr}
        requests = {
            "7": {
                "head_sha": "current-head",
                "observed_at": "2026-07-20T01:00:00+00:00",
                "requested_at": "",
                "copilot_request_fingerprint": copilot_request_fingerprint(clean_raw),
            }
        }
        with (
            patch(
                "copilot_review.load_copilot_review_requests",
                return_value=requests,
            ),
            patch("copilot_review.save_copilot_review_requests") as save_requests,
            patch(
                "copilot_review.fetch_current_pr_routing_inputs",
                return_value=(conflicted_pr, conflicted_raw),
            ),
            patch("copilot_review.fetch_pr_reviews") as fetch_reviews,
            patch("copilot_review.request_copilot_review") as request_review,
        ):
            errors = deliver_copilot_review_requests(
                "open-telemetry/example",
                NOW,
            )

        self.assertEqual([], errors)
        fetch_reviews.assert_not_called()
        request_review.assert_not_called()
        save_requests.assert_called_once_with({})

    @patch(
        "copilot_review.copilot_request_fingerprint",
        return_value="accepted-fingerprint",
    )
    @patch("copilot_review.request_copilot_review")
    @patch(
        "copilot_review.fetch_pr_reviews",
        return_value=[
            {"user": {"login": "Copilot"}, "commit_id": "current-head"},
        ],
    )
    @patch(
        "copilot_review.fetch_current_pr_routing_inputs",
        return_value=(
            {
                "state": "OPEN",
                "isDraft": False,
                "headRefOid": "current-head",
            },
            {"checks": []},
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
                "copilot_request_fingerprint": "accepted-fingerprint",
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
            "already covers head current-head",
            stderr.getvalue(),
        )

    @patch(
        "copilot_review.fetch_review_requests",
        return_value=[{"__typename": "Bot", "login": "copilot-pull-request-reviewer"}],
    )
    @patch(
        "copilot_review.copilot_request_fingerprint",
        return_value="accepted-fingerprint",
    )
    @patch("copilot_review.request_copilot_review")
    @patch("copilot_review.fetch_pr_reviews", return_value=[])
    @patch(
        "copilot_review.fetch_current_pr_routing_inputs",
        return_value=(
            {
                "id": "PR_node",
                "state": "OPEN",
                "isDraft": False,
                "headRefOid": "current-head",
            },
            {"checks": []},
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
                "copilot_request_fingerprint": "accepted-fingerprint",
            }
        },
    )
    def test_delivers_request_for_missing_first_review(
        self,
        _load_requests,
        save_requests,
        _fetch_current_state,
        _fetch_reviews,
        request_review,
        _fingerprint,
        _fetch_pending_requests,
    ) -> None:
        errors = deliver_copilot_review_requests("open-telemetry/example", NOW)

        self.assertEqual([], errors)
        request_review.assert_called_once_with("PR_node")
        save_requests.assert_called_once_with({
            "7": {
                "head_sha": "current-head",
                "observed_at": "2026-07-20T01:00:00+00:00",
                "requested_at": "2026-07-20T02:00:00+00:00",
                "copilot_request_fingerprint": "accepted-fingerprint",
            },
        })

    @patch("copilot_review.sleep_for_retry")
    @patch("copilot_review.fetch_review_requests", return_value=[])
    @patch(
        "copilot_review.copilot_request_fingerprint",
        return_value="accepted-fingerprint",
    )
    @patch("copilot_review.request_copilot_review")
    @patch("copilot_review.fetch_pr_reviews", return_value=[])
    @patch(
        "copilot_review.fetch_current_pr_routing_inputs",
        return_value=(
            {
                "id": "PR_node",
                "state": "OPEN",
                "isDraft": False,
                "headRefOid": "current-head",
            },
            {"checks": []},
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
                "copilot_request_fingerprint": "accepted-fingerprint",
            }
        },
    )
    def test_dropped_request_is_not_recorded_as_delivered(
        self,
        _load_requests,
        save_requests,
        _fetch_current_state,
        _fetch_reviews,
        _request_review,
        _fingerprint,
        fetch_pending_requests,
        _sleep,
    ) -> None:
        stderr = io.StringIO()

        with redirect_stderr(stderr):
            errors = deliver_copilot_review_requests("open-telemetry/example", NOW)

        self.assertEqual([], errors)
        self.assertEqual(
            REQUEST_CONFIRMATION_ATTEMPTS,
            fetch_pending_requests.call_count,
        )
        save_requests.assert_called_once_with({
            "7": {
                "head_sha": "current-head",
                "observed_at": "2026-07-20T01:00:00+00:00",
                "requested_at": "",
                "copilot_request_fingerprint": "accepted-fingerprint",
            },
        })
        self.assertIn(
            "GitHub did not record the Copilot review request for PR #7 on "
            "head current-head",
            stderr.getvalue(),
        )

    @patch("copilot_review.sleep_for_retry")
    @patch("copilot_review.fetch_review_requests", return_value=[])
    @patch(
        "copilot_review.copilot_request_fingerprint",
        return_value="accepted-fingerprint",
    )
    @patch("copilot_review.request_copilot_review")
    @patch(
        "copilot_review.fetch_pr_reviews",
        side_effect=[
            [],
            [
                {
                    "id": 20,
                    "commit_id": "current-head",
                    "user": {"login": "copilot-pull-request-reviewer"},
                    "submitted_at": "2026-07-20T02:00:00Z",
                }
            ],
        ],
    )
    @patch(
        "copilot_review.fetch_current_pr_routing_inputs",
        return_value=(
            {
                "id": "PR_node",
                "state": "OPEN",
                "isDraft": False,
                "headRefOid": "current-head",
            },
            {"checks": []},
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
                "copilot_request_fingerprint": "accepted-fingerprint",
            }
        },
    )
    def test_review_that_arrives_before_the_read_counts_as_delivered(
        self,
        _load_requests,
        save_requests,
        _fetch_current_state,
        _fetch_reviews,
        _request_review,
        _fingerprint,
        _fetch_pending_requests,
        _sleep,
    ) -> None:
        errors = deliver_copilot_review_requests("open-telemetry/example", NOW)

        self.assertEqual([], errors)
        save_requests.assert_called_once_with({
            "7": {
                "head_sha": "current-head",
                "observed_at": "2026-07-20T01:00:00+00:00",
                "requested_at": "2026-07-20T02:00:00+00:00",
                "copilot_request_fingerprint": "accepted-fingerprint",
            },
        })


class StaleRequestReasonTest(unittest.TestCase):
    ENTRY = {
        "head_sha": "current-head",
        "copilot_request_fingerprint": "accepted-fingerprint",
    }
    OPEN_PR = {"state": "OPEN", "isDraft": False}

    def reason(
        self,
        entry: dict | None = None,
        pr: dict | None = None,
        current_head: str = "current-head",
        current_routing_fingerprint: str = "accepted-fingerprint",
        raw: dict | None = None,
    ) -> str:
        return stale_request_reason(
            self.ENTRY if entry is None else entry,
            self.OPEN_PR if pr is None else pr,
            current_head,
            current_routing_fingerprint,
            {"checks": []} if raw is None else raw,
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

    def test_reports_unavailable_check_results(self) -> None:
        self.assertEqual(
            "required check results are unavailable",
            self.reason(raw={}),
        )

    def test_reports_failing_required_checks(self) -> None:
        self.assertEqual(
            "required checks are failing: build",
            self.reason(
                raw={
                    "checks": [
                        {"name": "build", "bucket": "fail"},
                        {"name": "lint", "bucket": "pass"},
                    ],
                },
            ),
        )

    def test_pending_required_checks_do_not_make_a_request_stale(self) -> None:
        # The request is sent while the checks are still running, so pending
        # ones are not a reason to discard it.
        self.assertEqual(
            "",
            self.reason(
                raw={
                    "checks": [
                        {"name": "build", "bucket": "pending"},
                        {"name": "lint", "bucket": "pass"},
                    ],
                },
            ),
        )

    def test_summarizes_long_lists_of_failing_checks(self) -> None:
        self.assertEqual(
            "required checks are failing: a, b, c and 2 more",
            self.reason(
                raw={
                    "checks": [
                        {"name": name, "bucket": "fail"}
                        for name in ("e", "d", "c", "b", "a")
                    ],
                },
            ),
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
            "issue_comments",
            "pr_text",
            "review_comments",
            "review_requests",
            "reviews",
            "review_threads",
        ):
            self.assertIn(component, reason)
        self.assertNotIn("checks", reason)


if __name__ == "__main__":
    unittest.main()