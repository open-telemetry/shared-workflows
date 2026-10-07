from __future__ import annotations

from contextlib import redirect_stderr
from dataclasses import replace
from datetime import datetime, timezone
import io
import unittest
from unittest.mock import patch

from copilot_review import (
    REQUEST_CONFIRMATION_ATTEMPTS,
    copilot_review_status,
    open_copilot_finding_urls,
    set_copilot_review_request_needed,
    stale_request_reason,
)
from copilot_review_delivery import (
    deliver_copilot_review_requests,
    record_copilot_review_observation,
)
from dashboard_test_support import (
    actor,
    dashboard_facts,
    review_source,
    review_thread,
    review_thread_comment,
    stored_dashboard_result,
)
from routing_snapshot import build_routing_snapshot
from pull_request_source import normalize_pull_request_source
from utils import format_ts


NOW = datetime(2026, 7, 20, 2, tzinfo=timezone.utc)
QUOTA_FAILURE_BODY = (
    "Copilot was unable to review this pull request because the user who "
    "requested the review has reached their quota limit."
)


def routing_snapshot(raw: dict | None = None, **changes):
    if raw is None:
        raw = {
            "checks": [],
            "pr": {
                "id": "PR_node",
                "state": "OPEN",
                "isDraft": False,
                "headRefOid": "current-head",
            },
        }
    return replace(
        build_routing_snapshot(normalize_pull_request_source(raw)),
        **changes,
    )


def review_result(route: str = "approver", **fact_changes):
    return stored_dashboard_result(
        7,
        route,
        facts=dashboard_facts(**fact_changes),
    )


class CopilotFindingLifecycleTest(unittest.TestCase):
    def test_clean_current_review_keeps_open_findings(self) -> None:
        reviews = (review_source(
            actor=actor("copilot"),
            commit_id="current-head",
            finding_count=0,
            submitted_at="2026-07-20T02:00:00Z",
        ),)
        threads = (
            review_thread(
                node_id="open",
                comments=(review_thread_comment(
                    url="https://example.test/open",
                    actor=actor("copilot"),
                    created_at="2026-07-20T01:00:00Z",
                    updated_at="2026-07-20T01:00:00Z",
                ),),
            ),
            review_thread(
                node_id="resolved",
                is_resolved=True,
                comments=(review_thread_comment(
                    url="https://example.test/resolved",
                    actor=actor("copilot"),
                    created_at="2026-07-20T01:00:00Z",
                    updated_at="2026-07-20T01:00:00Z",
                ),),
            ),
            review_thread(
                node_id="outdated",
                is_outdated=True,
                comments=(review_thread_comment(
                    url="https://example.test/outdated",
                    actor=actor("copilot"),
                    created_at="2026-07-20T01:00:00Z",
                    updated_at="2026-07-20T01:00:00Z",
                ),),
            ),
        )

        self.assertEqual(
            (True, False, True),
            copilot_review_status(reviews, "current-head", threads),
        )
        self.assertEqual(
            ("https://example.test/open",),
            open_copilot_finding_urls(threads),
        )


class CopilotReviewStatusTest(unittest.TestCase):
    def test_quota_failures_do_not_count_as_completed_reviews(self) -> None:
        quota_failure = review_source(
            actor=actor("copilot-pull-request-reviewer[bot]"),
            commit_id="current-head",
            state="COMMENTED",
            body=QUOTA_FAILURE_BODY,
        )
        completed_review = review_source(
            actor=actor("copilot"),
            commit_id="current-head",
            body="No issues found.",
        )
        cases = (
            ("failure_only", (quota_failure,), (False, False, False)),
            (
                "whitespace",
                (replace(quota_failure, body=f"\n{QUOTA_FAILURE_BODY}\n"),),
                (False, False, False),
            ),
            (
                "current_review_then_failure",
                (completed_review, quota_failure),
                (True, False, False),
            ),
            (
                "failure_then_current_review",
                (quota_failure, completed_review),
                (True, False, False),
            ),
            (
                "stale_review_then_failure",
                (replace(completed_review, commit_id="old-head"), quota_failure),
                (True, True, False),
            ),
            (
                "review_discussing_quotas",
                (replace(
                    completed_review,
                    body=f"Handle this failure:\n> {QUOTA_FAILURE_BODY}",
                ),),
                (True, False, False),
            ),
        )
        for name, reviews, expected in cases:
            with self.subTest(name=name):
                self.assertEqual(
                    expected,
                    copilot_review_status(reviews, "current-head", ()),
                )


class CopilotFirstReviewRequestTest(unittest.TestCase):
    def base_facts(self, **overrides):
        values = {"ci_pending_count": 0}
        values.update(overrides)
        return dashboard_facts(**values)

    def test_requests_the_first_review_on_the_first_pass(self) -> None:
        for route in ("approver", "maintainer"):
            with self.subTest(route=route):
                facts = set_copilot_review_request_needed(
                    self.base_facts(), route, enabled=True
                )

                self.assertTrue(facts.copilot_review_request_needed)

    def test_pending_request_is_not_duplicated(self) -> None:
        facts = self.base_facts(
            copilot_review_requested=True,
        )

        facts = set_copilot_review_request_needed(
            facts, "approver", enabled=True
        )

        self.assertFalse(facts.copilot_review_request_needed)

    def test_unsettled_checks_do_not_hold_the_first_review_request(self) -> None:
        facts = self.base_facts(
            ci_pending_count=1,
        )

        facts = set_copilot_review_request_needed(
            facts, "approver", enabled=True
        )

        self.assertTrue(facts.copilot_review_request_needed)

    def test_author_route_does_not_request(self) -> None:
        facts = self.base_facts()

        facts = set_copilot_review_request_needed(
            facts, "author", enabled=True
        )

        self.assertFalse(facts.copilot_review_request_needed)


class CopilotReviewRequestStateTest(unittest.TestCase):
    @patch("copilot_review_delivery.save_copilot_review_requests")
    @patch(
        "copilot_review_delivery.load_copilot_review_requests",
        return_value={},
    )
    def test_records_request_for_current_head(self, _load_requests, save_requests) -> None:
        record_copilot_review_observation(
            7,
            review_result(
                head_sha="current-head",
                copilot_review_request_needed=True,
                copilot_request_fingerprint="accepted-fingerprint",
            ),
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

    @patch("copilot_review_delivery.save_copilot_review_requests")
    @patch(
        "copilot_review_delivery.load_copilot_review_requests",
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
            review_result(
                head_sha="current-head",
                copilot_review_request_needed=True,
                copilot_request_fingerprint="accepted-fingerprint",
            ),
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

    @patch("copilot_review_delivery.save_copilot_review_requests")
    @patch(
        "copilot_review_delivery.load_copilot_review_requests",
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
            review_result(
                head_sha="current-head",
                copilot_review_request_needed=True,
                copilot_request_fingerprint="accepted-fingerprint",
            ),
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

    @patch("copilot_review_delivery.save_copilot_review_requests")
    @patch(
        "copilot_review_delivery.load_copilot_review_requests",
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
            review_result(
                "maintainer",
                head_sha="current-head",
                copilot_review_request_needed=False,
            ),
            NOW,
        )

        save_requests.assert_called_once_with({})

    @patch("copilot_review_delivery.save_copilot_review_requests")
    @patch(
        "copilot_review_delivery.load_copilot_review_requests",
        return_value={},
    )
    def test_missing_first_review_without_request_needed_does_not_enqueue(
        self,
        _load_requests,
        save_requests,
    ) -> None:
        record_copilot_review_observation(
            7,
            review_result(
                head_sha="current-head",
                copilot_review_exists=False,
                copilot_review_request_needed=False,
            ),
            NOW,
        )

        save_requests.assert_called_once_with({})

    @patch(
        "copilot_review.fetch_review_requests",
        return_value=[{"__typename": "Bot", "login": "copilot-pull-request-reviewer"}],
    )
    @patch("copilot_review_delivery.request_copilot_review")
    @patch("copilot_review_delivery.fetch_pr_reviews")
    @patch("copilot_review_delivery.fetch_routing_snapshot")
    @patch("copilot_review_delivery.save_copilot_review_requests")
    @patch(
        "copilot_review_delivery.load_copilot_review_requests",
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
        fetch_snapshot,
        fetch_reviews,
        request_review,
        fetch_pending_requests,
    ) -> None:
        pr = {
            "state": "OPEN",
            "isDraft": False,
            "headRefOid": "current-head",
            "id": "PR_node_id",
        }
        fetch_snapshot.return_value = routing_snapshot(
            {"checks": [], "pr": pr},
            copilot_request_fingerprint="accepted-fingerprint",
        )
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
        fetch_snapshot.assert_called_once_with("open-telemetry/example", 7)
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

    def test_requests_review_after_quota_failure_and_confirms_delivery(self) -> None:
        entry = {
            "head_sha": "current-head",
            "observed_at": "2026-07-20T01:00:00+00:00",
            "requested_at": "",
            "copilot_request_fingerprint": "accepted-fingerprint",
        }
        failed_reviews = [{
            "user": {"login": "copilot-pull-request-reviewer[bot]"},
            "commit_id": "current-head",
            "state": "COMMENTED",
            "body": QUOTA_FAILURE_BODY,
        }]
        for pending in (True, False):
            with (
                self.subTest(pending=pending),
                patch(
                    "copilot_review_delivery.load_copilot_review_requests",
                    return_value={"7": entry},
                ),
                patch(
                    "copilot_review_delivery.save_copilot_review_requests",
                ) as save_requests,
                patch(
                    "copilot_review_delivery.fetch_routing_snapshot",
                    return_value=routing_snapshot(
                        copilot_request_fingerprint="accepted-fingerprint",
                    ),
                ),
                patch(
                    "copilot_review_delivery.fetch_pr_reviews",
                    return_value=failed_reviews,
                ),
                patch(
                    "copilot_review_delivery.request_copilot_review",
                ) as request_review,
                patch(
                    "copilot_review.fetch_review_requests",
                    return_value=(
                        [{"__typename": "Bot", "login": "copilot-pull-request-reviewer"}]
                        if pending else []
                    ),
                ),
                patch(
                    "copilot_review.fetch_pr_reviews",
                    return_value=failed_reviews,
                ),
                patch("copilot_review.sleep_for_retry"),
                redirect_stderr(io.StringIO()) as stderr,
            ):
                errors = deliver_copilot_review_requests(
                    "open-telemetry/example", NOW
                )

                self.assertEqual([], errors)
                request_review.assert_called_once_with("PR_node")
                save_requests.assert_called_once_with({
                    "7": {
                        **entry,
                        "requested_at": format_ts(NOW) if pending else "",
                    },
                })
                if not pending:
                    self.assertIn(
                        "GitHub did not record the Copilot review request",
                        stderr.getvalue(),
                    )

    @patch("copilot_review_delivery.request_copilot_review")
    @patch("copilot_review_delivery.fetch_pr_reviews")
    @patch("copilot_review_delivery.fetch_routing_snapshot")
    @patch("copilot_review_delivery.save_copilot_review_requests")
    @patch(
        "copilot_review_delivery.load_copilot_review_requests",
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
        fetch_snapshot,
        fetch_reviews,
        request_review,
    ) -> None:
        pr = {
            "state": "OPEN",
            "isDraft": False,
            "headRefOid": "current-head",
        }
        fetch_snapshot.return_value = routing_snapshot(
            {
                "checks": [],
                "pr": pr,
                "review_requests": [
                    {
                        "__typename": "Bot",
                        "login": "copilot-pull-request-reviewer",
                    },
                ],
            },
            copilot_request_fingerprint="accepted-fingerprint",
        )

        errors = deliver_copilot_review_requests(
            "open-telemetry/example",
            NOW,
        )

        self.assertEqual([], errors)
        fetch_snapshot.assert_called_once_with("open-telemetry/example", 7)
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

    @patch("copilot_review_delivery.request_copilot_review")
    @patch("copilot_review_delivery.fetch_pr_reviews")
    @patch(
        "copilot_review_delivery.fetch_routing_snapshot",
        return_value=routing_snapshot(
            copilot_request_fingerprint="new-fingerprint",
        ),
    )
    @patch("copilot_review_delivery.save_copilot_review_requests")
    @patch(
        "copilot_review_delivery.load_copilot_review_requests",
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
        _fetch_snapshot,
        fetch_reviews,
        request_review,
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
                "copilot_request_fingerprint": build_routing_snapshot(
                    normalize_pull_request_source(observed_raw)
                ).copilot_request_fingerprint,
            }
        }
        with (
            patch(
                "copilot_review_delivery.load_copilot_review_requests",
                return_value=requests,
            ),
            patch(
                "copilot_review_delivery.save_copilot_review_requests"
            ) as save_requests,
            patch(
                "copilot_review_delivery.fetch_routing_snapshot",
                return_value=build_routing_snapshot(
                    normalize_pull_request_source(delivery_raw)
                ),
            ),
            patch("copilot_review_delivery.fetch_pr_reviews", return_value=[]),
            patch(
                "copilot_review_delivery.request_copilot_review"
            ) as request_review,
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
                    "copilot_request_fingerprint": build_routing_snapshot(
                        normalize_pull_request_source(observed_raw)
                    ).copilot_request_fingerprint,
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
                "copilot_request_fingerprint": build_routing_snapshot(
                    normalize_pull_request_source(clean_raw)
                ).copilot_request_fingerprint,
            }
        }
        with (
            patch(
                "copilot_review_delivery.load_copilot_review_requests",
                return_value=requests,
            ),
            patch(
                "copilot_review_delivery.save_copilot_review_requests"
            ) as save_requests,
            patch(
                "copilot_review_delivery.fetch_routing_snapshot",
                return_value=build_routing_snapshot(
                    normalize_pull_request_source(conflicted_raw)
                ),
            ),
            patch("copilot_review_delivery.fetch_pr_reviews") as fetch_reviews,
            patch(
                "copilot_review_delivery.request_copilot_review"
            ) as request_review,
        ):
            errors = deliver_copilot_review_requests(
                "open-telemetry/example",
                NOW,
            )

        self.assertEqual([], errors)
        fetch_reviews.assert_not_called()
        request_review.assert_not_called()
        save_requests.assert_called_once_with({})

    @patch("copilot_review_delivery.request_copilot_review")
    @patch(
        "copilot_review_delivery.fetch_pr_reviews",
        return_value=[
            {"user": {"login": "Copilot"}, "commit_id": "current-head"},
        ],
    )
    @patch(
        "copilot_review_delivery.fetch_routing_snapshot",
        return_value=routing_snapshot(
            copilot_request_fingerprint="accepted-fingerprint",
        ),
    )
    @patch("copilot_review_delivery.save_copilot_review_requests")
    @patch(
        "copilot_review_delivery.load_copilot_review_requests",
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
        _fetch_snapshot,
        _fetch_reviews,
        request_review,
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
    @patch("copilot_review_delivery.request_copilot_review")
    @patch("copilot_review_delivery.fetch_pr_reviews", return_value=[])
    @patch(
        "copilot_review_delivery.fetch_routing_snapshot",
        return_value=routing_snapshot(
            copilot_request_fingerprint="accepted-fingerprint",
        ),
    )
    @patch("copilot_review_delivery.save_copilot_review_requests")
    @patch(
        "copilot_review_delivery.load_copilot_review_requests",
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
        _fetch_snapshot,
        _fetch_reviews,
        request_review,
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
    @patch("copilot_review_delivery.request_copilot_review")
    @patch("copilot_review_delivery.fetch_pr_reviews", return_value=[])
    @patch("copilot_review.fetch_pr_reviews", return_value=[])
    @patch(
        "copilot_review_delivery.fetch_routing_snapshot",
        return_value=routing_snapshot(
            copilot_request_fingerprint="accepted-fingerprint",
        ),
    )
    @patch("copilot_review_delivery.save_copilot_review_requests")
    @patch(
        "copilot_review_delivery.load_copilot_review_requests",
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
        _fetch_snapshot,
        _fetch_confirmation_reviews,
        _fetch_delivery_reviews,
        _request_review,
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
    @patch("copilot_review_delivery.request_copilot_review")
    @patch("copilot_review_delivery.fetch_pr_reviews", return_value=[])
    @patch(
        "copilot_review.fetch_pr_reviews",
        return_value=[
            {
                "id": 20,
                "commit_id": "current-head",
                "user": {"login": "copilot-pull-request-reviewer"},
                "submitted_at": "2026-07-20T02:00:00Z",
            }
        ],
    )
    @patch(
        "copilot_review_delivery.fetch_routing_snapshot",
        return_value=routing_snapshot(
            copilot_request_fingerprint="accepted-fingerprint",
        ),
    )
    @patch("copilot_review_delivery.save_copilot_review_requests")
    @patch(
        "copilot_review_delivery.load_copilot_review_requests",
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
        _fetch_snapshot,
        _fetch_confirmation_reviews,
        _fetch_delivery_reviews,
        _request_review,
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
        raw = dict({"checks": []} if raw is None else raw)
        raw["pr"] = {
            **(raw.get("pr") or {}),
            **(self.OPEN_PR if pr is None else pr),
            "headRefOid": current_head,
        }
        snapshot = routing_snapshot(
            raw,
            copilot_request_fingerprint=current_routing_fingerprint,
        )
        return stale_request_reason(
            self.ENTRY if entry is None else entry,
            snapshot,
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

    def test_request_recorded_while_pending_is_stale_after_action_required(
        self,
    ) -> None:
        # The unchanged fingerprint models a request recorded while this check
        # was pending because check results are not part of that fingerprint.
        self.assertEqual(
            "required checks are failing: build",
            self.reason(
                raw={
                    "checks": [
                        {"name": "build", "bucket": "action_required"},
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