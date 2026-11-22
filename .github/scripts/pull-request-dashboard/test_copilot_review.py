from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from copilot_review import (
    copilot_quota_issue,
    copilot_review_error_count,
    copilot_review_in_flight,
    copilot_review_outstanding,
    copilot_review_unreported,
    copilot_review_status,
    is_copilot_error,
    is_copilot_quota_failure,
    refresh_copilot_review_cycle,
    set_copilot_review_request_needed,
    stale_request_reason,
)
from copilot_review_delivery import (
    deliver_copilot_review_requests,
    record_copilot_review_observation,
)
from dashboard_test_support import (
    actor,
    copilot_review_cycle,
    dashboard_facts,
    review_source,
    stored_dashboard_result,
)
from github_cli import CopilotReviewRequestRejected, TransientGhError
from state import (
    copilot_review_fallback_state_path,
    copilot_review_request_state_path,
    load_copilot_review_cycles,
    load_copilot_review_requests,
    save_copilot_review_cycles,
)
from routing_snapshot import build_routing_snapshot
from pull_request_source import normalize_pull_request_source
from utils import format_ts


NOW = datetime(2026, 7, 20, 2, tzinfo=timezone.utc)
QUOTA_FAILURE_BODY = (
    "Copilot was unable to review this pull request because the user who "
    "requested the review has reached their quota limit."
)
INSUFFICIENT_QUOTA_FAILURE_BODY = (
    "Copilot was unable to review this pull request because the user who "
    "requested the review has insufficient remaining quota."
)
INSUFFICIENT_QUOTA_FAILURE_WITH_GUIDANCE_BODY = (
    f"{INSUFFICIENT_QUOTA_FAILURE_BODY} "
    "Try requesting a review at a lower effort level."
)
ERROR_BODY = (
    "Copilot encountered an error and was unable to review this pull request. "
    "You can try again by re-requesting a review."
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


class CopilotReviewStatusTest(unittest.TestCase):
    def test_current_review_is_reported_regardless_of_finding_count(self) -> None:
        for finding_count in (0, 1):
            with self.subTest(finding_count=finding_count):
                self.assertEqual(
                    (True, False),
                    copilot_review_status(
                        (review_source(
                            actor=actor("copilot"),
                            commit_id="current-head",
                            finding_count=finding_count,
                        ),),
                        "current-head",
                    ),
                )

    def test_error_count_requires_current_head_copilot_notices(self) -> None:
        failure = review_source(
            actor=actor("copilot-pull-request-reviewer[bot]"),
            commit_id="current-head",
            body=ERROR_BODY,
        )
        cases = (
            ("exact", failure, True),
            ("whitespace", replace(failure, body=f"\n{ERROR_BODY}\n"), True),
            ("human", replace(failure, actor=actor("reviewer")), False),
            ("quota", replace(failure, body=QUOTA_FAILURE_BODY), False),
            ("quoted", replace(failure, body=f"Handle this:\n> {ERROR_BODY}"), False),
        )
        for name, review, expected in cases:
            with self.subTest(name=name):
                self.assertEqual(expected, is_copilot_error(review))
                self.assertEqual(
                    1 if expected else 0,
                    copilot_review_error_count((review,), "current-head"),
                )
                self.assertEqual(0, copilot_review_error_count((review,), "next-head"))
                self.assertEqual(0, copilot_review_error_count((review,), ""))

        self.assertEqual(
            2,
            copilot_review_error_count(
                (failure, replace(failure, database_id=4)),
                "current-head",
            ),
        )

    def test_error_notices_do_not_count_as_completed_reviews(self) -> None:
        failure = review_source(
            actor=actor("copilot-pull-request-reviewer[bot]"),
            commit_id="current-head",
            state="COMMENTED",
            body=ERROR_BODY,
        )
        completed = replace(failure, body="No issues found.")
        self.assertFalse(is_copilot_quota_failure(failure))
        cases = (
            ("failure_only", (failure,), (False, False)),
            (
                "whitespace",
                (replace(failure, body=f"\n{ERROR_BODY}\n"),),
                (False, False),
            ),
            (
                "current_review_then_failure",
                (completed, failure),
                (True, False),
            ),
            (
                "failure_then_current_review",
                (failure, completed),
                (True, False),
            ),
            (
                "stale_review_then_failure",
                (replace(completed, commit_id="old-head"), failure),
                (True, True),
            ),
            (
                "review_discussing_errors",
                (replace(completed, body=f"Handle this failure:\n> {ERROR_BODY}"),),
                (True, False),
            ),
        )
        for name, reviews, expected in cases:
            with self.subTest(name=name):
                self.assertEqual(
                    expected,
                    copilot_review_status(reviews, "current-head"),
                )

    def test_quota_failure_requires_the_copilot_actor_and_notice_prefix(self) -> None:
        for body in (QUOTA_FAILURE_BODY, INSUFFICIENT_QUOTA_FAILURE_BODY):
            for suffix in (
                "",
                " Try requesting a review at a lower effort level.",
                "\n\nTry again later.",
            ):
                with self.subTest(body=body, suffix=suffix):
                    notice = f"{body}{suffix}"
                    failure = review_source(
                        actor=actor("copilot"),
                        body=f"\n{notice}\n",
                    )
                    self.assertTrue(is_copilot_quota_failure(failure))
                    self.assertFalse(is_copilot_quota_failure(
                        replace(failure, actor=actor("reviewer"))
                    ))
                    self.assertFalse(is_copilot_quota_failure(
                        replace(failure, body=f"> {notice}")
                    ))
                    self.assertFalse(is_copilot_quota_failure(
                        replace(failure, body=f"Handle this failure:\n> {notice}")
                    ))

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
            ("failure_only", (quota_failure,), (False, False)),
            (
                "whitespace",
                (replace(quota_failure, body=f"\n{QUOTA_FAILURE_BODY}\n"),),
                (False, False),
            ),
            (
                "insufficient_quota",
                (replace(quota_failure, body=INSUFFICIENT_QUOTA_FAILURE_BODY),),
                (False, False),
            ),
            (
                "insufficient_quota_with_guidance",
                (replace(quota_failure, body=INSUFFICIENT_QUOTA_FAILURE_WITH_GUIDANCE_BODY),),
                (False, False),
            ),
            (
                "current_review_then_failure",
                (completed_review, quota_failure),
                (True, False),
            ),
            (
                "failure_then_current_review",
                (quota_failure, completed_review),
                (True, False),
            ),
            (
                "stale_review_then_failure",
                (replace(completed_review, commit_id="old-head"), quota_failure),
                (True, True),
            ),
            (
                "pending_review",
                (replace(completed_review, state="PENDING"),),
                (False, False),
            ),
            (
                "stale_review_then_insufficient_quota_with_guidance",
                (
                    replace(completed_review, commit_id="old-head"),
                    replace(quota_failure, body=INSUFFICIENT_QUOTA_FAILURE_WITH_GUIDANCE_BODY),
                ),
                (True, True),
            ),
            (
                "review_discussing_quotas",
                (replace(
                    completed_review,
                    body=f"Handle this failure:\n> {QUOTA_FAILURE_BODY}",
                ),),
                (True, False),
            ),
        )
        for name, reviews, expected in cases:
            with self.subTest(name=name):
                self.assertEqual(
                    expected,
                    copilot_review_status(reviews, "current-head"),
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

    def test_quota_failure_disables_initial_requests_and_re_reviews(self) -> None:
        for exists in (False, True):
            with self.subTest(review_exists=exists):
                facts = set_copilot_review_request_needed(
                    self.base_facts(
                        copilot_review_quota_exhausted=True,
                        copilot_review_exists=exists,
                        copilot_review_stale=exists,
                        copilot_review_needed=exists,
                    ),
                    "approver",
                    enabled=True,
                )

                self.assertFalse(facts.copilot_review_request_needed)

    def test_available_fallback_keeps_gate_and_requests_enabled(self) -> None:
        for in_flight in (False, True):
            for exists in (False, True):
                with self.subTest(in_flight=in_flight, exists=exists):
                    facts = self.base_facts(
                        copilot_review_quota_exhausted=True,
                        copilot_review_fallback_available=True,
                        copilot_review_request_in_flight=in_flight,
                        copilot_review_exists=exists,
                        copilot_review_stale=exists,
                        copilot_review_needed=exists,
                    )
                    self.assertEqual(
                        not in_flight,
                        set_copilot_review_request_needed(
                            facts, "approver", enabled=True
                        ).copilot_review_request_needed,
                    )
                    self.assertEqual(
                        True, copilot_review_outstanding(facts, enabled=True)
                    )
                    self.assertEqual(
                        True, copilot_review_unreported(facts, enabled=True)
                    )

    def test_two_errors_stop_requests_and_release_only_the_copilot_gate(self) -> None:
        for error_count in (0, 1, 2, 3):
            for exists in (False, True):
                for fallback_available in (False, True):
                    with self.subTest(
                        error_count=error_count,
                        exists=exists,
                        fallback_available=fallback_available,
                    ):
                        facts = self.base_facts(
                            copilot_review_error_count=error_count,
                            copilot_review_exists=exists,
                            copilot_review_stale=exists,
                            copilot_review_needed=exists,
                            copilot_review_fallback_available=fallback_available,
                        )
                        self.assertEqual(
                            error_count < 2,
                            set_copilot_review_request_needed(
                                facts, "approver", enabled=True
                            ).copilot_review_request_needed,
                        )
                        self.assertEqual(
                            error_count < 2,
                            copilot_review_outstanding(facts, enabled=True),
                        )
                        self.assertEqual(
                            error_count < 2,
                            copilot_review_unreported(facts, enabled=True),
                        )

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


class CopilotReviewCycleTest(unittest.TestCase):
    NOW = datetime(2026, 8, 16, 8, tzinfo=timezone.utc)

    def refresh(self, cycle, reviews=(), *, head="current-head", now=None, fallback=True):
        return refresh_copilot_review_cycle(
            cycle, reviews, head, now or self.NOW, fallback_available=fallback,
        )

    def notice(self, review_id=11, *, body=ERROR_BODY, **changes):
        return review_source(
            database_id=review_id,
            actor=actor("copilot"),
            commit_id="current-head",
            body=body,
            submitted_at="2026-08-16T08:01:00Z",
            **changes,
        )

    def test_current_head_coverage_finishes_any_cycle_without_request_attribution(self) -> None:
        completed = replace(self.notice(10), body="One finding.", finding_count=1)
        for phase, head, reason, sent_at in (
            ("accepted", "current-head", "", "2026-08-16T08:00:00Z"),
            ("sending", "current-head", "", ""),
            ("unknown", "", "", ""),
            ("limited", "current-head", "quota", "2026-08-16T08:00:00Z"),
        ):
            with self.subTest(phase=phase):
                cycle = copilot_review_cycle(
                    head, phase=phase, limit_reason=reason,
                    sent_at=sent_at, baseline_review_ids=(10,),
                )
                result = self.refresh(cycle, (completed,))
                self.assertEqual("complete", result.phase)
                self.assertEqual("current-head", result.head_sha)
                self.assertEqual("", result.limit_reason)

    def test_uncertain_requests_ignore_failure_notices_even_after_a_push(self) -> None:
        for phase in ("sending", "unknown"):
            for head in ("current-head", "old-head", ""):
                with self.subTest(phase=phase, head=head):
                    cycle = copilot_review_cycle(head, phase=phase, sent_at="")
                    result = self.refresh(
                        cycle,
                        (
                            self.notice(body=QUOTA_FAILURE_BODY),
                            self.notice(12),
                            replace(self.notice(13), commit_id="other-head", body="No issues."),
                        ),
                        now=self.NOW + timedelta(hours=1),
                    )
                    self.assertEqual(cycle, result)
                    self.assertTrue(copilot_review_in_flight(result))

    def test_only_fresh_known_head_notices_choose_fallback(self) -> None:
        cycle = copilot_review_cycle("current-head", baseline_review_ids=(10,))
        fresh = self.notice(body=QUOTA_FAILURE_BODY)
        for notice in (
            replace(fresh, database_id=10),
            replace(fresh, submitted_at="2026-08-16T07:59:59Z"),
            replace(fresh, commit_id="other-head"),
            replace(fresh, commit_id=""),
            replace(fresh, actor=actor("reviewer")),
        ):
            with self.subTest(notice=notice):
                self.assertEqual(cycle, self.refresh(cycle, (notice,)))
        ready = self.refresh(cycle, (fresh,))
        self.assertEqual("ready", ready.phase)
        self.assertEqual("fallback", ready.requester)
        self.assertEqual(1, ready.request_count)
        self.assertEqual(cycle.started_at, ready.started_at)

    def test_fallback_quota_does_not_end_a_request_or_trigger_another(self) -> None:
        cycle = copilot_review_cycle(
            "current-head", requester="fallback", request_count=2,
        )
        self.assertEqual(
            cycle, self.refresh(cycle, (self.notice(body=QUOTA_FAILURE_BODY),)),
        )
        limited = self.refresh(
            cycle, (self.notice(body=QUOTA_FAILURE_BODY),),
            now=self.NOW + timedelta(hours=4),
        )
        self.assertEqual("timeout", limited.limit_reason)

    def test_one_generic_error_retries_and_two_release_the_gate(self) -> None:
        cycle = copilot_review_cycle("current-head")
        first = self.notice()
        ready = self.refresh(cycle, (first,))
        self.assertEqual("ready", ready.phase)
        self.assertEqual("app", ready.requester)
        retry = replace(
            ready, phase="accepted", request_count=2,
            sent_at="2026-08-16T08:01:00Z", baseline_review_ids=(11,),
        )
        limited = self.refresh(retry, (first, self.notice(12)))
        self.assertEqual("limited", limited.phase)
        self.assertEqual("errors", limited.limit_reason)
        fresh = self.refresh(retry, (first, self.notice(12)), head="next-head")
        self.assertEqual("ready", fresh.phase)
        self.assertEqual(0, fresh.request_count)

    def test_request_cap_survives_incomplete_error_history(self) -> None:
        cycle = copilot_review_cycle(
            "current-head", requester="fallback", request_count=3,
        )
        result = self.refresh(cycle, (self.notice(),))
        self.assertEqual("limited", result.phase)
        self.assertEqual("requests", result.limit_reason)
        facts = dashboard_facts(
            copilot_review_request_limit_reason=result.limit_reason,
            copilot_review_fallback_available=True,
        )
        self.assertFalse(set_copilot_review_request_needed(
            facts, "approver", enabled=True,
        ).copilot_review_request_needed)
        self.assertTrue(copilot_review_unreported(facts, enabled=True))

    def test_exact_four_hour_deadline_limits_ready_and_in_flight_cycles(self) -> None:
        for phase in ("ready", "sending", "accepted", "unknown"):
            with self.subTest(phase=phase):
                cycle = copilot_review_cycle(
                    "current-head", phase=phase,
                    sent_at="" if phase == "sending" else "2026-08-16T08:00:00Z",
                )
                before = self.NOW + timedelta(hours=4, microseconds=-1)
                self.assertEqual(cycle, self.refresh(cycle, now=before))
                limited = self.refresh(cycle, now=self.NOW + timedelta(hours=4))
                self.assertEqual("limited", limited.phase)
                self.assertEqual("timeout", limited.limit_reason)
                fresh = self.refresh(
                    cycle, head="next-head", now=self.NOW + timedelta(hours=4),
                )
                self.assertEqual("ready", fresh.phase)
                self.assertEqual(0, fresh.request_count)

    def test_old_head_coverage_closes_accepted_but_not_unknown_requests(self) -> None:
        completed = replace(self.notice(10), body="No issues.", submitted_at="2026-08-16T07:00:00Z")
        accepted = copilot_review_cycle("current-head", baseline_review_ids=(10,))
        ready = self.refresh(accepted, (completed,), head="next-head")
        self.assertEqual("next-head", ready.head_sha)
        self.assertEqual("ready", ready.phase)
        self.assertEqual(0, ready.request_count)
        unknown = replace(accepted, phase="unknown")
        self.assertEqual(unknown, self.refresh(unknown, (completed,), head="next-head"))

    def test_enabling_fallback_renews_a_quota_limited_window_only_once(self) -> None:
        limited = copilot_review_cycle(
            "current-head", phase="limited", limit_reason="quota",
        )
        later = self.NOW + timedelta(hours=5)
        ready = self.refresh(limited, now=later)
        self.assertEqual("ready", ready.phase)
        self.assertEqual("fallback", ready.requester)
        self.assertEqual("2026-08-16T13:00:00+00:00", ready.started_at)
        self.assertEqual(ready, self.refresh(ready, now=later + timedelta(hours=1)))

    def test_unknown_timeout_preserves_normal_gate_expiry_reporting(self) -> None:
        facts = dashboard_facts(
            copilot_review_quota_exhausted=True,
            copilot_review_request_limit_reason="timeout",
        )
        self.assertTrue(copilot_review_outstanding(facts, enabled=True))
        self.assertTrue(copilot_review_unreported(facts, enabled=True))
        self.assertFalse(set_copilot_review_request_needed(
            facts, "approver", enabled=True,
        ).copilot_review_request_needed)

    def test_quota_scope_is_current_head_only(self) -> None:
        notice = self.notice(body=QUOTA_FAILURE_BODY)
        self.assertTrue(copilot_quota_issue((notice,), "current-head"))
        self.assertFalse(copilot_quota_issue((notice,), "next-head"))
        self.assertFalse(copilot_quota_issue((replace(notice, state="PENDING"),), "current-head"))

    def test_invalid_clocks_and_notice_times_fail_explicitly(self) -> None:
        cycle = copilot_review_cycle("current-head")
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            self.refresh(cycle, now=self.NOW.replace(tzinfo=None))
        with self.assertRaisesRegex(RuntimeError, "valid timestamp"):
            self.refresh(cycle, (replace(self.notice(), submitted_at="invalid"),))


class CopilotReviewDeliveryTest(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.enterContext(patch("state._state_dir", Path(directory.name)))
        self.enterContext(patch("state._using_delivery_state", False))
        self.enterContext(patch.dict(
            os.environ, {"COPILOT_REVIEW_FALLBACK_TOKEN": "request-token"},
        ))
        self.now = datetime(2026, 8, 16, 8, tzinfo=timezone.utc)
        self.enterContext(patch("copilot_review_delivery.utc_now", side_effect=lambda: self.now))
        self.snapshot = routing_snapshot(copilot_request_fingerprint="accepted-fingerprint")
        self.reviews = []
        self.fetch_snapshot = self.enterContext(patch(
            "copilot_review_delivery.fetch_routing_snapshot", side_effect=lambda *_: self.snapshot,
        ))
        self.fetch_reviews = self.enterContext(patch(
            "copilot_review_delivery.fetch_pr_reviews", side_effect=lambda *_: self.reviews,
        ))
        self.in_commit = False
        self.durable = {}
        self.request_error = None
        self.requested_head = None
        self.request = self.enterContext(patch(
            "copilot_review_delivery.request_copilot_review", side_effect=self.request_review,
        ))
        self.enqueue()
        self.commit(lambda: 0)

    def enqueue(self, *, quota_exhausted=False) -> None:
        record_copilot_review_observation(
            7,
            review_result(
                head_sha=self.snapshot.head_sha,
                copilot_review_request_needed=True,
                copilot_review_quota_exhausted=quota_exhausted,
                copilot_request_fingerprint="accepted-fingerprint",
            ),
            self.now,
        )

    def commit(self, action) -> None:
        self.in_commit = True
        try:
            action()
            self.durable = {
                path: path.read_bytes()
                for path in (copilot_review_request_state_path(), copilot_review_fallback_state_path())
                if path.exists()
            }
        finally:
            self.in_commit = False

    def restore(self) -> None:
        for path in (copilot_review_request_state_path(), copilot_review_fallback_state_path()):
            if path in self.durable:
                path.write_bytes(self.durable[path])
            else:
                path.unlink(missing_ok=True)

    def request_review(self, _pull_request_id, **_kwargs) -> str:
        self.assertFalse(self.in_commit)
        durable = json.loads(self.durable[copilot_review_fallback_state_path()])
        self.assertEqual("sending", durable["prs"]["7"]["phase"])
        if self.request_error is not None:
            raise self.request_error
        return self.requested_head or self.snapshot.head_sha

    def notice(self, review_id=10, *, body=QUOTA_FAILURE_BODY, head=None, submitted_at=None):
        return {
            "id": review_id,
            "user": {"login": "copilot"},
            "commit_id": head or self.snapshot.head_sha,
            "state": "COMMENTED",
            "body": body,
            "submitted_at": submitted_at or format_ts(self.now),
        }

    def deliver(self, commit=None) -> list[str]:
        return deliver_copilot_review_requests("open-telemetry/example", commit or self.commit)

    def test_first_request_is_durable_and_not_repeated_without_confirmation(self) -> None:
        self.assertEqual([], self.deliver())
        self.request.assert_called_once_with("PR_node")
        cycle = load_copilot_review_cycles()["7"]
        self.assertEqual("accepted", cycle.phase)
        self.assertEqual(1, cycle.request_count)
        self.assertEqual("2026-08-16T08:00:00+00:00", load_copilot_review_requests()["7"]["requested_at"])
        for _ in range(3):
            self.enqueue()
            self.assertEqual([], self.deliver())
        self.request.assert_called_once()

    def test_app_quota_switches_once_and_fallback_quota_does_not_loop(self) -> None:
        self.assertEqual([], self.deliver())
        self.now += timedelta(minutes=1)
        self.reviews = [self.notice(10)]
        self.enqueue(quota_exhausted=True)
        self.assertEqual([], self.deliver())
        self.request.assert_called_with("PR_node", token="request-token")
        self.now += timedelta(minutes=1)
        self.reviews.append(self.notice(11))
        self.enqueue(quota_exhausted=True)
        self.assertEqual([], self.deliver())
        self.assertEqual(2, self.request.call_count)
        cycle = load_copilot_review_cycles()["7"]
        self.assertEqual("accepted", cycle.phase)
        self.assertEqual(2, cycle.request_count)
        self.assertEqual("2026-08-16T08:00:00+00:00", cycle.started_at)

    def test_later_head_gets_a_fresh_bounded_request_after_fallback_quota(self) -> None:
        self.reviews = [self.notice()]
        self.assertEqual([], self.deliver())
        self.now += timedelta(minutes=1)
        self.reviews.append(self.notice(11))
        self.snapshot = replace(self.snapshot, head_sha="next-head")
        self.enqueue()
        self.assertEqual([], self.deliver())
        self.request.assert_called_once()
        self.now = datetime(2026, 8, 16, 12, tzinfo=timezone.utc)
        self.enqueue()
        self.assertEqual([], self.deliver())
        self.assertEqual(2, self.request.call_count)
        cycle = load_copilot_review_cycles()["7"]
        self.assertEqual("next-head", cycle.head_sha)
        self.assertEqual(1, cycle.request_count)
        self.assertEqual("fallback", cycle.requester)

    def test_three_dispatch_cap_survives_a_rebound_error_history(self) -> None:
        self.assertEqual([], self.deliver())
        self.now += timedelta(minutes=1)
        self.reviews = [self.notice()]
        self.enqueue()
        self.assertEqual([], self.deliver())
        self.now += timedelta(minutes=1)
        self.reviews.append(self.notice(11, body=ERROR_BODY))
        self.enqueue()
        self.assertEqual([], self.deliver())
        self.now += timedelta(minutes=1)
        self.reviews = [self.notice(12, body=ERROR_BODY)]
        self.enqueue()
        self.assertEqual([], self.deliver())
        self.assertEqual(3, self.request.call_count)
        self.assertEqual("requests", load_copilot_review_cycles()["7"].limit_reason)

    def test_two_generic_errors_stop_retries_but_a_push_resets_the_budget(self) -> None:
        self.assertEqual([], self.deliver())
        self.now += timedelta(minutes=1)
        self.reviews = [self.notice(body=ERROR_BODY)]
        self.enqueue()
        self.assertEqual([], self.deliver())
        self.request.assert_called_with("PR_node")
        self.now += timedelta(minutes=1)
        self.reviews.append(self.notice(11, body=ERROR_BODY))
        self.enqueue()
        self.assertEqual([], self.deliver())
        self.assertEqual(2, self.request.call_count)
        self.snapshot = replace(self.snapshot, head_sha="next-head")
        self.enqueue()
        self.assertEqual([], self.deliver())
        self.assertEqual(3, self.request.call_count)
        self.assertEqual(1, load_copilot_review_cycles()["7"].request_count)

    def test_ambiguous_error_is_durable_and_ignores_later_quota_notices(self) -> None:
        self.request_error = TransientGhError("HTTP 503")
        self.assertIn("HTTP 503", self.deliver()[0])
        self.assertEqual("unknown", load_copilot_review_cycles()["7"].phase)
        self.restore()
        self.request_error = None
        self.now += timedelta(minutes=1)
        self.reviews = [self.notice()]
        self.snapshot = replace(self.snapshot, head_sha="next-head")
        self.enqueue(quota_exhausted=True)
        self.assertEqual([], self.deliver())
        self.request.assert_called_once()
        self.assertEqual("current-head", load_copilot_review_cycles()["7"].head_sha)

    def test_failed_reservation_prevents_rpc(self) -> None:
        def fail(action):
            action()
            raise RuntimeError("reservation push failed")

        self.assertIn("reservation push failed", self.deliver(fail)[0])
        self.request.assert_not_called()
        self.restore()
        self.assertEqual({}, load_copilot_review_cycles())

    def test_failed_receipt_leaves_sending_and_does_not_resend(self) -> None:
        calls = 0

        def persist(action):
            nonlocal calls
            calls += 1
            if calls == 3:
                action()
                raise RuntimeError("receipt push failed")
            self.commit(action)

        self.assertIn("receipt push failed", self.deliver(persist)[0])
        self.restore()
        self.assertEqual("sending", load_copilot_review_cycles()["7"].phase)
        self.enqueue()
        self.assertEqual([], self.deliver())
        self.request.assert_called_once()

    def test_rpc_and_receipt_failures_both_report_and_keep_the_durable_barrier(self) -> None:
        calls = 0
        self.request_error = TransientGhError("HTTP 503")

        def persist(action):
            nonlocal calls
            calls += 1
            if calls == 3:
                action()
                raise RuntimeError("receipt push failed")
            self.commit(action)

        error = self.deliver(persist)[0]
        self.assertIn("HTTP 503", error)
        self.assertIn("receipt push failed", error)
        self.restore()
        self.assertEqual("sending", load_copilot_review_cycles()["7"].phase)
        self.request_error = None
        self.enqueue()
        self.assertEqual([], self.deliver())
        self.request.assert_called_once()

    def test_receipt_cannot_overwrite_terminal_progress_from_another_worker(self) -> None:
        calls = 0

        def persist(action):
            nonlocal calls
            calls += 1
            if calls == 3:
                cycles = load_copilot_review_cycles()
                cycles["7"] = replace(cycles["7"], phase="complete")
                self.commit(lambda: save_copilot_review_cycles(cycles))
            self.commit(action)

        self.assertIn("refusing to overwrite", self.deliver(persist)[0])
        self.restore()
        self.assertEqual("complete", load_copilot_review_cycles()["7"].phase)
        self.request.assert_called_once()

    def test_retried_state_callbacks_do_not_repeat_the_rpc_or_consume_a_slot(self) -> None:
        def retry(action):
            self.in_commit = True
            try:
                action()
                self.restore()
                action()
            finally:
                self.in_commit = False
            self.commit(lambda: 0)

        self.assertEqual([], self.deliver(retry))
        self.request.assert_called_once()
        self.assertEqual(1, load_copilot_review_cycles()["7"].request_count)

    def test_retry_adopts_an_already_durable_reservation(self) -> None:
        def retry(action):
            self.commit(action)
            self.restore()
            self.commit(action)

        self.assertEqual([], self.deliver(retry))
        self.request.assert_called_once()
        self.assertEqual(1, load_copilot_review_cycles()["7"].request_count)

    def test_reservation_lost_to_another_worker_does_not_send(self) -> None:
        def competing(action):
            action()
            self.restore()
            save_copilot_review_cycles({
                "7": copilot_review_cycle(
                    "current-head", phase="sending", sent_at="", dispatch_id="another-worker",
                ),
            })
            self.commit(action)

        self.assertEqual([], self.deliver(competing))
        self.request.assert_not_called()
        self.assertEqual("another-worker", load_copilot_review_cycles()["7"].dispatch_id)

    def test_reservation_changed_during_preflight_prevents_rpc(self) -> None:
        calls = 0

        def persist(action):
            nonlocal calls
            calls += 1
            if calls == 2:
                save_copilot_review_cycles({
                    "7": copilot_review_cycle("current-head", dispatch_id="newer"),
                })
            self.commit(action)

        self.assertIn("reservation changed", self.deliver(persist)[0])
        self.request.assert_not_called()
        self.assertEqual("newer", load_copilot_review_cycles()["7"].dispatch_id)

    def test_definite_rejection_removes_first_reservation(self) -> None:
        self.request_error = CopilotReviewRequestRejected("permission denied")
        self.assertIn("permission denied", self.deliver()[0])
        self.assertEqual({}, load_copilot_review_cycles())
        self.assertEqual("", load_copilot_review_requests()["7"]["requested_at"])

    def test_definite_rejection_restores_a_previous_retry_budget(self) -> None:
        prior = copilot_review_cycle("current-head", phase="ready", requester="fallback")
        save_copilot_review_cycles({"7": prior})
        self.commit(lambda: 0)
        self.request_error = CopilotReviewRequestRejected("permission denied")
        self.assertIn("permission denied", self.deliver()[0])
        self.assertEqual(prior, load_copilot_review_cycles()["7"])

    def test_head_change_after_reservation_cancels_without_consuming_a_slot(self) -> None:
        self.fetch_snapshot.side_effect = [
            self.snapshot, replace(self.snapshot, head_sha="next-head"),
        ]
        self.assertEqual([], self.deliver())
        self.request.assert_not_called()
        self.assertEqual({}, load_copilot_review_cycles())
        self.assertEqual({}, load_copilot_review_requests())

    def test_mutation_head_race_does_not_acknowledge_the_old_intent(self) -> None:
        self.requested_head = "next-head"
        self.assertEqual([], self.deliver())
        self.assertEqual("next-head", load_copilot_review_cycles()["7"].head_sha)
        self.assertEqual({}, load_copilot_review_requests())

    def test_per_request_send_time_follows_final_baseline_fetch(self) -> None:
        def fetch(*_args):
            self.now += timedelta(minutes=1)
            return []

        self.fetch_reviews.side_effect = fetch
        self.assertEqual([], self.deliver())
        cycle = load_copilot_review_cycles()["7"]
        self.assertEqual("2026-08-16T08:01:00+00:00", cycle.started_at)
        self.assertEqual("2026-08-16T08:02:00+00:00", cycle.sent_at)
        self.fetch_reviews.side_effect = lambda *_: self.reviews
        self.reviews = [self.notice(submitted_at="2026-08-16T08:01:30Z")]
        self.enqueue(quota_exhausted=True)
        self.assertEqual([], self.deliver())
        self.request.assert_called_once()

    def test_expiry_during_reservation_prevents_rpc(self) -> None:
        def delayed(action):
            self.commit(action)
            self.now += timedelta(hours=4)

        self.assertEqual([], self.deliver(delayed))
        self.request.assert_not_called()
        self.assertEqual("timeout", load_copilot_review_cycles()["7"].limit_reason)

    def test_pending_request_and_current_coverage_do_not_duplicate_requests(self) -> None:
        self.reviews = [self.notice(body="No issues found.")]
        self.assertEqual([], self.deliver())
        self.request.assert_not_called()
        self.assertEqual({}, load_copilot_review_requests())
        self.reviews = []
        self.snapshot = routing_snapshot(
            {"checks": [], "review_requests": [{
                "requestedReviewer": {"__typename": "Bot", "login": "copilot-pull-request-reviewer"},
            }], "pr": {
                "id": "PR_node", "state": "OPEN", "isDraft": False, "headRefOid": "current-head",
            }},
            copilot_request_fingerprint="accepted-fingerprint",
        )
        self.enqueue()
        self.assertEqual([], self.deliver())
        self.request.assert_not_called()
        self.assertEqual("2026-08-16T08:00:00+00:00", load_copilot_review_requests()["7"]["requested_at"])

    def test_stale_failing_and_draft_requests_are_discarded(self) -> None:
        snapshots = (
            replace(self.snapshot, copilot_request_fingerprint="new-fingerprint"),
            replace(self.snapshot, is_draft=True),
            replace(self.snapshot, has_conflicts=True),
            replace(self.snapshot, state="CLOSED"),
            replace(self.snapshot, checks=None),
            routing_snapshot(
                {"checks": [{"name": "build", "bucket": "fail"}], "pr": {
                    "id": "PR_node", "state": "OPEN", "isDraft": False, "headRefOid": "current-head",
                }},
                copilot_request_fingerprint="accepted-fingerprint",
            ),
        )
        for snapshot in snapshots:
            with self.subTest(snapshot=snapshot):
                self.snapshot = snapshot
                self.enqueue()
                self.assertEqual([], self.deliver())
                self.assertEqual({}, load_copilot_review_requests())
        self.request.assert_not_called()

    def test_pending_checks_and_stale_reviews_allow_dispatch(self) -> None:
        self.snapshot = routing_snapshot(
            {"checks": [{"name": "build", "bucket": "pending"}], "pr": {
                "id": "PR_node", "state": "OPEN", "isDraft": False, "headRefOid": "current-head",
            }},
            copilot_request_fingerprint="accepted-fingerprint",
        )
        self.reviews = [self.notice(body="No issues found.", head="old-head")]
        self.enqueue()
        self.assertEqual([], self.deliver())
        self.request.assert_called_once()

    def test_missing_fallback_secret_releases_quota_head_but_not_later_heads(self) -> None:
        with patch.dict(os.environ, {"COPILOT_REVIEW_FALLBACK_TOKEN": ""}):
            self.reviews = [self.notice()]
            self.assertEqual([], self.deliver())
            self.request.assert_not_called()
            self.snapshot = replace(self.snapshot, head_sha="next-head")
            self.enqueue()
            self.assertEqual([], self.deliver())
        self.request.assert_called_once_with("PR_node")

    def test_new_quota_notice_during_final_preflight_cancels_primary_dispatch(self) -> None:
        self.fetch_reviews.side_effect = [[], [self.notice()]]
        self.assertEqual([], self.deliver())
        self.request.assert_not_called()
        self.assertEqual({}, load_copilot_review_cycles())

    def test_invalid_review_baseline_fails_without_rpc(self) -> None:
        self.reviews = [self.notice(0)]
        self.assertIn("without a review ID", self.deliver()[0])
        self.request.assert_not_called()


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