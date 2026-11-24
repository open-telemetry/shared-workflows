from __future__ import annotations

from contextlib import nullcontext, redirect_stderr, redirect_stdout
from dataclasses import asdict, replace
from datetime import datetime, timezone
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import ANY, patch

from copilot_review import COPILOT_ERROR_BODY, refresh_copilot_review_cycle
from dashboard_contracts import (
    CopilotReviewCycle,
    CopilotReviewCycleUpdate,
    RoutingHandoff,
    DashboardCommandReply,
    DashboardRoute,
    DashboardState,
)
from dashboard_test_support import (
    actor,
    copilot_review_cycle,
    dashboard_facts,
    dashboard_state,
    evaluation_success,
    review_source,
    stored_dashboard_result,
)
from state import (
    AUTHOR_NUDGE_STATE_VERSION,
    BACKFILL_STATE_VERSION,
    COPILOT_REVIEW_FALLBACK_STATE_FILE,
    COPILOT_REVIEW_FALLBACK_STATE_VERSION,
    COPILOT_REVIEW_REQUEST_STATE_FILE,
    COPILOT_REVIEW_REQUEST_STATE_VERSION,
    DASHBOARD_STATE_COMPATIBLE_VERSIONS,
    DASHBOARD_STATE_VERSION,
    NOTIFICATION_STATE_VERSION,
    FULL_PUBLISH_STATE_VERSION,
    STATUS_COMMENT_ROLLOUT_STATE_VERSION,
    STATUS_COMMENT_REVISION,
    author_nudge_state_path,
    backfill_state_path,
    copilot_review_dispatch_state_path,
    claim_delivery_versions,
    current_delivery_versions,
    full_publish_needed_path,
    full_publish_delivered_path,
    mark_full_publish_needed,
    read_full_publish_generation,
    record_full_publish_delivered,
    record_copilot_review_cycle_observations,
    dashboard_state_path,
    decode_dashboard_facts,
    decode_dashboard_state,
    decode_stored_result,
    empty_state,
    encode_dashboard_facts,
    encode_dashboard_state,
    encode_stored_result,
    enqueue_status_comment_update,
    load_accepted_dashboard_state,
    load_author_nudges,
    load_backfill_state,
    load_copilot_review_cycles,
    load_copilot_review_cycles_from,
    load_dashboard_state_cache,
    load_delivery_versions,
    load_state_file,
    load_status_comment_rollout_state,
    load_notifications,
    main,
    notification_state_path,
    save_state_file,
    save_author_nudges,
    save_copilot_review_cycles,
    save_dashboard_state_cache,
    save_notifications,
    save_status_comment_rollout_state,
    reconcile_author_nudges,
    stored_result,
    union_merge_author_nudges,
    update_dashboard_state_for_pr,
)


class StateTest(unittest.TestCase):
    def test_prior_journal_versions_seed_the_dispatch_journal(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("state._state_dir", Path(directory)),
            patch("state._using_delivery_state", False),
        ):
            cycles = {"7": copilot_review_cycle("current-head", baseline_review_ids=(10, 11))}
            save_copilot_review_cycles(cycles)
            path = copilot_review_dispatch_state_path()
            legacy = path.with_name(COPILOT_REVIEW_FALLBACK_STATE_FILE)
            data = json.loads(path.read_text(encoding="utf-8"))
            for version in range(4, 11):
                with self.subTest(version=version):
                    path.unlink()
                    original = json.dumps({**data, "version": version})
                    legacy.write_text(original, encoding="utf-8")
                    self.assertEqual(cycles, load_copilot_review_cycles())
                    save_copilot_review_cycles(cycles)
                    self.assertEqual(cycles, load_copilot_review_cycles_from(path.parent))
                    self.assertEqual(original, legacy.read_text(encoding="utf-8"))

    def test_empty_dispatch_journal_ignores_legacy_records(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("state._state_dir", Path(directory)),
        ):
            receipt = Path(directory) / COPILOT_REVIEW_REQUEST_STATE_FILE
            receipt.write_text(json.dumps({
                "version": 8, "prs": {"7": {"requested_at": "2026-08-16T12:00:00Z"}},
            }), encoding="utf-8")
            self.assertIn("7", load_copilot_review_cycles())
            save_copilot_review_cycles({})
            self.assertEqual({}, load_copilot_review_cycles())

    def test_legacy_attempts_coalesce_into_one_unknown_without_exhaustion(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("state._state_dir", Path(directory)),
        ):
            for version in (1, 2, 3):
                for exhausted in (False, True):
                    with self.subTest(version=version, exhausted=exhausted):
                        attempt = {
                            "attempt_id": "latest",
                            "quota_failure_ids": [10],
                            "attempted_at": "2026-08-16T11:59:00Z",
                            "head_sha": "current-head",
                            "review_ids": [10, 11],
                        }
                        entry = (
                            {"attempts": [
                                {**attempt, "attempt_id": "earlier", "attempted_at": "2026-08-16T10:00:00Z"},
                                attempt,
                            ]}
                            if version == 3 else attempt
                        )
                        copilot_review_dispatch_state_path().unlink(missing_ok=True)
                        legacy = Path(directory) / COPILOT_REVIEW_FALLBACK_STATE_FILE
                        original = json.dumps({
                            "version": version, "prs": {"7": {**entry, "exhausted": exhausted}},
                        })
                        legacy.write_text(original, encoding="utf-8")
                        cycles = load_copilot_review_cycles()
                        self.assertEqual({
                            "7": copilot_review_cycle(
                                "", requester="fallback", phase="unknown",
                                dispatch_id="legacy-2026-08-16T11:59:00+00:00",
                                started_at="2026-08-16T11:59:00+00:00", sent_at="",
                            ),
                        }, cycles)
                        save_copilot_review_cycles(cycles)
                        self.assertEqual(cycles, load_copilot_review_cycles())
                        self.assertEqual(original, legacy.read_text(encoding="utf-8"))

    def test_primary_receipts_migrate_when_no_cycle_was_recorded(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("state._state_dir", Path(directory)),
        ):
            receipt = Path(directory) / COPILOT_REVIEW_REQUEST_STATE_FILE
            for version in (6, 7, 8, 9, 10, 11):
                with self.subTest(version=version):
                    receipt.write_text(json.dumps({
                        "version": version,
                        "prs": {"7": {"head_sha": "current-head", "requested_at": "2026-08-16T12:00:00Z"}},
                    }), encoding="utf-8")
                    self.assertEqual({
                        "7": copilot_review_cycle(
                            "", phase="unknown", dispatch_id="legacy-2026-08-16T12:00:00+00:00",
                            started_at="2026-08-16T12:00:00+00:00", sent_at="",
                        ),
                    }, load_copilot_review_cycles())
            receipt.write_text(json.dumps({
                "version": 12,
                "prs": {"7": {"head_sha": "current-head", "requested_at": "2026-08-16T12:00:00Z"}},
            }), encoding="utf-8")
            self.assertEqual({}, load_copilot_review_cycles())

    def test_malformed_legacy_receipt_timestamps_do_not_discard_unknown_sends(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("state._state_dir", Path(directory)),
        ):
            for value in (0, False, [], {}, "2026-08-16T08:00:00"):
                with self.subTest(value=value):
                    (Path(directory) / COPILOT_REVIEW_REQUEST_STATE_FILE).write_text(json.dumps({
                        "version": 8, "prs": {"7": {"requested_at": value}},
                    }), encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, "timestamp"):
                        load_copilot_review_cycles()

    def test_recent_primary_receipt_extends_legacy_fallback_uncertainty(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("state._state_dir", Path(directory)),
        ):
            (Path(directory) / COPILOT_REVIEW_FALLBACK_STATE_FILE).write_text(json.dumps({
                "version": 1, "prs": {"7": {
                    "quota_failure_ids": [], "attempted_at": "2026-08-16T08:00:00Z",
                }},
            }), encoding="utf-8")
            (Path(directory) / COPILOT_REVIEW_REQUEST_STATE_FILE).write_text(json.dumps({
                "version": 11, "prs": {"7": {"requested_at": "2026-08-16T12:00:00Z"}},
            }), encoding="utf-8")
            self.assertEqual({
                "7": copilot_review_cycle(
                    "", requester="fallback", phase="unknown",
                    dispatch_id="legacy-2026-08-16T12:00:00+00:00",
                    started_at="2026-08-16T12:00:00+00:00", sent_at="",
                ),
            }, load_copilot_review_cycles())

    def test_empty_legacy_history_retains_preference_without_a_fake_dispatch(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("state._state_dir", Path(directory)),
        ):
            (Path(directory) / COPILOT_REVIEW_FALLBACK_STATE_FILE).write_text(json.dumps({
                "version": 3, "prs": {"7": {"attempts": [], "exhausted": True}},
            }), encoding="utf-8")
            cycle = load_copilot_review_cycles()["7"]
            self.assertEqual("ready", cycle.phase)
            self.assertEqual("fallback", cycle.requester)
            self.assertEqual(0, cycle.request_count)
            self.assertEqual("", cycle.limit_reason)

    def test_corrupt_active_journals_fail_instead_of_resetting_request_budgets(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("state._state_dir", Path(directory)),
        ):
            path = copilot_review_dispatch_state_path()
            path.with_name(COPILOT_REVIEW_FALLBACK_STATE_FILE).write_text(
                json.dumps({"version": 1, "prs": {}}), encoding="utf-8",
            )
            entry = asdict(copilot_review_cycle("current-head"))
            for changes in (
                {"request_count": -1},
                {"phase": "unknown", "dispatch_id": ""},
                {"started_at": "2026-08-16T08:00:00"},
                {"baseline_review_ids": [True]},
                {"phase": "limited", "limit_reason": ""},
            ):
                with self.subTest(changes=changes):
                    path.write_text(json.dumps({
                        "version": COPILOT_REVIEW_FALLBACK_STATE_VERSION,
                        "prs": {"7": {**entry, **changes}},
                    }), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        load_copilot_review_cycles()
            for data in (None, {"version": 99, "prs": {}}):
                with self.subTest(data=data):
                    path.write_text(json.dumps(data), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        load_copilot_review_cycles()

    def test_cycle_facts_round_trip_with_explicit_limits(self) -> None:
        cycle = copilot_review_cycle(
            "current-head", dispatch_id="observed-cycle", baseline_review_ids=(11,),
        )
        for reason in ("", "quota", "errors", "requests", "timeout"):
            with self.subTest(reason=reason):
                facts = dashboard_facts(
                    copilot_review_quota_exhausted=True,
                    copilot_review_fallback_available=True,
                    copilot_review_request_in_flight=not reason,
                    copilot_review_request_limit_reason=reason,
                    copilot_review_cycle_update=CopilotReviewCycleUpdate(
                        cycle, replace(
                            cycle, phase="limited" if reason else "accepted", limit_reason=reason,
                        ),
                    ),
                )
                self.assertEqual(facts, decode_dashboard_facts(encode_dashboard_facts(facts)))
        self.assertFalse(decode_dashboard_facts({}).copilot_review_request_in_flight)
        self.assertEqual("", decode_dashboard_facts({}).copilot_review_request_limit_reason)
        self.assertIsNone(decode_dashboard_facts({}).copilot_review_cycle_update)
        for before, after in ((None, cycle), (cycle, None), (None, None)):
            with self.subTest(before=before, after=after):
                facts = dashboard_facts(copilot_review_cycle_update=CopilotReviewCycleUpdate(before, after))
                self.assertEqual(facts, decode_dashboard_facts(encode_dashboard_facts(facts)))
        with self.assertRaisesRegex(ValueError, "invalid Copilot"):
            decode_dashboard_facts({"copilot_review_request_limit_reason": "exhausted"})
        for update in (
            0, [], {"before": None}, {"after": None},
            {"before": {"dispatch_id": "incomplete"}, "after": None},
            {"before": None, "after": {"dispatch_id": "incomplete"}},
        ):
            with self.subTest(update=update):
                with self.assertRaisesRegex(ValueError, "invalid Copilot review cycle"):
                    decode_dashboard_facts({"copilot_review_cycle_update": update})

    def test_coverage_checkpoint_completes_a_cycle_without_a_request_intent(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("state._state_dir", Path(directory)),
            patch("state._using_delivery_state", False),
        ):
            cycle = copilot_review_cycle("old-head", phase="unknown", dispatch_id="active")
            save_copilot_review_cycles({"7": cycle})
            save_dashboard_state_cache(dashboard_state(stored_dashboard_result(
                7, facts=dashboard_facts(
                    head_sha="covered-head", copilot_review_exists=True,
                    copilot_review_cycle_update=CopilotReviewCycleUpdate(
                        cycle, CopilotReviewCycle("covered-head", phase="complete"),
                    ),
                ),
            )))
            record_copilot_review_cycle_observations()
            completed = load_copilot_review_cycles()["7"]
            self.assertEqual("complete", completed.phase)
            self.assertEqual("covered-head", completed.head_sha)
            self.assertEqual(0, completed.request_count)

    def test_coverage_checkpoint_does_not_complete_a_newer_dispatch(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("state._state_dir", Path(directory)),
            patch("state._using_delivery_state", False),
        ):
            cycle = copilot_review_cycle("current-head", dispatch_id="newer")
            observed = replace(cycle, dispatch_id="older")
            save_copilot_review_cycles({"7": cycle})
            save_dashboard_state_cache(dashboard_state(stored_dashboard_result(
                7, facts=dashboard_facts(
                    head_sha="current-head", copilot_review_exists=True,
                    copilot_review_cycle_update=CopilotReviewCycleUpdate(
                        observed, replace(observed, phase="complete"),
                    ),
                ),
            )))
            record_copilot_review_cycle_observations()
            self.assertEqual(cycle, load_copilot_review_cycles()["7"])

    def test_coverage_checkpoint_does_not_complete_a_changed_cycle_with_the_same_dispatch_id(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("state._state_dir", Path(directory)),
            patch("state._using_delivery_state", False),
        ):
            observed = copilot_review_cycle("old-head", phase="sending", sent_at="", dispatch_id="active")
            save_dashboard_state_cache(dashboard_state(stored_dashboard_result(
                7, facts=dashboard_facts(
                    head_sha="old-head", copilot_review_exists=True,
                    copilot_review_cycle_update=CopilotReviewCycleUpdate(
                        observed, replace(observed, phase="complete"),
                    ),
                ),
            )))
            for cycle in (
                replace(observed, phase="accepted", sent_at="2026-08-16T08:00:01Z"),
                replace(
                    observed, head_sha="new-head", phase="accepted",
                    started_at="2026-08-16T08:00:01Z", sent_at="2026-08-16T08:00:01Z",
                ),
                replace(observed, phase="unknown", sent_at="2026-08-16T08:00:01Z"),
            ):
                with self.subTest(cycle=cycle):
                    save_copilot_review_cycles({"7": cycle})
                    record_copilot_review_cycle_observations()
                    self.assertEqual(cycle, load_copilot_review_cycles()["7"])

    def test_quota_checkpoint_without_an_intent_allows_late_fallback_enablement(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("state._state_dir", Path(directory)),
            patch("state._using_delivery_state", False),
        ):
            cycle = copilot_review_cycle("current-head", dispatch_id="active")
            save_copilot_review_cycles({"7": cycle})
            save_dashboard_state_cache(dashboard_state(stored_dashboard_result(
                7, facts=dashboard_facts(
                    head_sha="current-head",
                    copilot_review_quota_exhausted=True,
                    copilot_review_request_limit_reason="quota",
                    copilot_review_cycle_update=CopilotReviewCycleUpdate(
                        cycle, replace(cycle, phase="limited", limit_reason="quota"),
                    ),
                ),
            )))
            record_copilot_review_cycle_observations()
            settled = load_copilot_review_cycles()["7"]
            self.assertEqual("limited", settled.phase)
            self.assertEqual("quota", settled.limit_reason)
            ready = refresh_copilot_review_cycle(
                settled, (), "current-head",
                datetime(2026, 8, 16, 13, tzinfo=timezone.utc),
                fallback_available=True,
            )
            self.assertEqual("ready", ready.phase)
            self.assertEqual("fallback", ready.requester)
            self.assertEqual(1, ready.request_count)
            self.assertEqual("2026-08-16T13:00:00+00:00", ready.started_at)

    def test_idle_quota_checkpoint_without_an_intent_preserves_fallback_recovery(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("state._state_dir", Path(directory)),
            patch("state._using_delivery_state", False),
        ):
            cycle = copilot_review_cycle(
                "current-head", phase="ready", requester="fallback", request_count=2,
            )
            save_copilot_review_cycles({"7": cycle})
            save_dashboard_state_cache(dashboard_state(stored_dashboard_result(
                7, facts=dashboard_facts(
                    head_sha="current-head",
                    copilot_review_quota_exhausted=True,
                    copilot_review_request_limit_reason="quota",
                    copilot_review_cycle_update=CopilotReviewCycleUpdate(
                        cycle, replace(cycle, phase="limited", limit_reason="quota"),
                    ),
                ),
            )))
            record_copilot_review_cycle_observations()
            paused = load_copilot_review_cycles()["7"]
            self.assertEqual(replace(cycle, phase="limited", limit_reason="quota"), paused)
            ready = refresh_copilot_review_cycle(
                paused, (), "current-head",
                datetime(2026, 8, 16, 13, tzinfo=timezone.utc),
                fallback_available=True,
            )
            self.assertEqual("ready", ready.phase)
            self.assertEqual(2, ready.request_count)
            self.assertEqual("2026-08-16T13:00:00+00:00", ready.started_at)

    def test_settled_fallback_quota_checkpoint_without_an_intent_preserves_recovery(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("state._state_dir", Path(directory)),
            patch("state._using_delivery_state", False),
        ):
            cycle = copilot_review_cycle(
                "current-head", requester="fallback", request_count=2, baseline_review_ids=(10,),
            )
            save_copilot_review_cycles({"7": cycle})
            save_dashboard_state_cache(dashboard_state(stored_dashboard_result(
                7, facts=dashboard_facts(
                    head_sha="current-head",
                    copilot_review_quota_exhausted=True,
                    copilot_review_request_in_flight=False,
                    copilot_review_request_limit_reason="quota",
                    copilot_review_cycle_update=CopilotReviewCycleUpdate(
                        cycle, replace(cycle, phase="limited", limit_reason="quota"),
                    ),
                ),
            )))
            record_copilot_review_cycle_observations()
            paused = load_copilot_review_cycles()["7"]
            self.assertEqual(replace(cycle, phase="limited", limit_reason="quota"), paused)
            ready = refresh_copilot_review_cycle(
                paused, (), "current-head",
                datetime(2026, 8, 16, 13, tzinfo=timezone.utc),
                fallback_available=True,
            )
            self.assertEqual("ready", ready.phase)
            self.assertEqual(2, ready.request_count)
            self.assertEqual("2026-08-16T13:00:00+00:00", ready.started_at)

    def test_quota_checkpoint_keeps_matching_in_flight_cycles(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("state._state_dir", Path(directory)),
            patch("state._using_delivery_state", False),
        ):
            for phase, in_flight in (
                ("sending", False), ("unknown", True), ("accepted", True),
            ):
                with self.subTest(phase=phase):
                    cycle = copilot_review_cycle(
                        "current-head", requester="fallback", phase=phase, request_count=2,
                        sent_at="" if phase == "sending" else "2026-08-16T08:00:00Z",
                    )
                    save_copilot_review_cycles({"7": cycle})
                    save_dashboard_state_cache(dashboard_state(stored_dashboard_result(
                        7, facts=dashboard_facts(
                            head_sha="current-head",
                            copilot_review_quota_exhausted=True,
                            copilot_review_request_in_flight=in_flight,
                            copilot_review_request_limit_reason="timeout" if phase == "sending" else "",
                            copilot_review_cycle_update=CopilotReviewCycleUpdate(
                                cycle, replace(cycle, phase="limited", limit_reason="timeout")
                                if phase == "sending" else cycle,
                            ),
                        ),
                    )))
                    record_copilot_review_cycle_observations()
                    self.assertEqual(cycle, load_copilot_review_cycles()["7"])

    def test_quota_checkpoint_does_not_rewind_other_heads_dispatches_or_terminal_progress(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("state._state_dir", Path(directory)),
            patch("state._using_delivery_state", False),
        ):
            observed = copilot_review_cycle("current-head", dispatch_id="observed")
            save_dashboard_state_cache(dashboard_state(stored_dashboard_result(
                7, facts=dashboard_facts(
                    head_sha="current-head",
                    copilot_review_quota_exhausted=True,
                    copilot_review_request_limit_reason="quota",
                    copilot_review_cycle_update=CopilotReviewCycleUpdate(
                        observed, replace(observed, phase="limited", limit_reason="quota"),
                    ),
                ),
            )))
            for cycle in (
                copilot_review_cycle("current-head", dispatch_id="newer"),
                copilot_review_cycle("other-head", dispatch_id="observed"),
                copilot_review_cycle("current-head", dispatch_id="observed", phase="complete"),
                copilot_review_cycle("current-head", dispatch_id="observed", phase="unknown"),
            ):
                with self.subTest(cycle=cycle):
                    save_copilot_review_cycles({"7": cycle})
                    record_copilot_review_cycle_observations()
                    self.assertEqual(cycle, load_copilot_review_cycles()["7"])

    def test_checkpointing_does_not_clear_or_renew_a_paused_budget(self) -> None:
        cycle = copilot_review_cycle(
            "current-head", requester="fallback", phase="limited",
            request_count=2, limit_reason="quota",
        )
        resumed = refresh_copilot_review_cycle(
            cycle, (), "current-head", datetime(2026, 8, 16, 13, tzinfo=timezone.utc),
            fallback_available=True,
        )
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("state._state_dir", Path(directory)),
            patch("state._using_delivery_state", False),
        ):
            for after in (None, resumed):
                with self.subTest(after=after):
                    save_copilot_review_cycles({"7": cycle})
                    save_dashboard_state_cache(dashboard_state(stored_dashboard_result(
                        7, facts=dashboard_facts(
                            head_sha="current-head", copilot_review_fallback_available=True,
                            copilot_review_cycle_update=CopilotReviewCycleUpdate(cycle, after),
                        ),
                    )))
                    record_copilot_review_cycle_observations()
                    self.assertEqual({"7": cycle}, load_copilot_review_cycles())

    def test_terminal_policy_decisions_are_checkpointed_without_a_request_intent(self) -> None:
        errors = tuple(review_source(
            database_id=review_id, actor=actor("copilot"), commit_id="current-head",
            body=COPILOT_ERROR_BODY, submitted_at="2026-08-16T08:01:00Z",
        ) for review_id in (11, 12))
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("state._state_dir", Path(directory)),
            patch("state._using_delivery_state", False),
        ):
            for reason, reviews, now in (
                ("errors", errors, datetime(2026, 8, 16, 9, tzinfo=timezone.utc)),
                ("timeout", (), datetime(2026, 8, 16, 12, tzinfo=timezone.utc)),
            ):
                with self.subTest(reason=reason):
                    cycle = copilot_review_cycle("current-head")
                    save_copilot_review_cycles({"7": cycle})
                    after = refresh_copilot_review_cycle(
                        cycle, reviews, "current-head", now, fallback_available=False,
                    )
                    save_dashboard_state_cache(dashboard_state(stored_dashboard_result(
                        7, facts=dashboard_facts(
                            head_sha="current-head",
                            copilot_review_request_limit_reason=reason,
                            copilot_review_error_count=2 if reason == "errors" else 0,
                            copilot_review_cycle_update=CopilotReviewCycleUpdate(cycle, after),
                        ),
                    )))
                    record_copilot_review_cycle_observations()
                    record_copilot_review_cycle_observations()
                    saved = load_copilot_review_cycles()["7"]
                    self.assertEqual("limited", saved.phase)
                    self.assertEqual(reason, saved.limit_reason)
                    self.assertEqual(1, saved.request_count)

    def test_uncovered_legacy_receipts_are_checkpointed_in_the_journal(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("state._state_dir", Path(directory)),
            patch("state._using_delivery_state", False),
        ):
            (Path(directory) / COPILOT_REVIEW_REQUEST_STATE_FILE).write_text(json.dumps({
                "version": 8,
                "prs": {"7": {"head_sha": "old-head", "requested_at": "2026-08-16T12:00:00Z"}},
            }), encoding="utf-8")
            save_dashboard_state_cache(dashboard_state(stored_dashboard_result(
                7, facts=dashboard_facts(head_sha="next-head"),
            )))
            record_copilot_review_cycle_observations()
            self.assertTrue(copilot_review_dispatch_state_path().is_file())
            cycle = load_copilot_review_cycles()["7"]
            self.assertEqual("unknown", cycle.phase)
            self.assertEqual("legacy-2026-08-16T12:00:00+00:00", cycle.dispatch_id)
            self.assertEqual("2026-08-16T12:00:00+00:00", cycle.started_at)

    def test_copilot_error_count_round_trips_and_defaults_to_zero(self) -> None:
        for count in (0, 1, 2, 3):
            with self.subTest(count=count):
                facts = dashboard_facts(copilot_review_error_count=count)
                self.assertEqual(facts, decode_dashboard_facts(encode_dashboard_facts(facts)))
        self.assertEqual(0, decode_dashboard_facts({}).copilot_review_error_count)
        for invalid_count in (True, "2", 1.5, None):
            with self.subTest(invalid_count=invalid_count):
                with self.assertRaisesRegex(ValueError, "copilot_review_error_count"):
                    decode_dashboard_facts({"copilot_review_error_count": invalid_count})

    def test_backfill_preserves_generations_with_extra_marker_metadata(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch("state._state_dir", Path(temp_dir)),
            patch("state._using_delivery_state", False),
        ):
            full_publish_needed_path().write_text(
                json.dumps({
                    "version": FULL_PUBLISH_STATE_VERSION,
                    "generation": 6,
                    "initial_backfill_complete": True,
                }),
                encoding="utf-8",
            )
            record_full_publish_delivered(5)
            mark_full_publish_needed()
            self.assertEqual(
                {"version": FULL_PUBLISH_STATE_VERSION, "generation": 7},
                json.loads(full_publish_needed_path().read_text(encoding="utf-8")),
            )
            self.assertEqual(5, read_full_publish_generation(full_publish_delivered_path()))

    def test_full_publish_generations_are_strict_and_monotonic(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch("state._state_dir", Path(temp_dir)),
            patch("state._using_delivery_state", False),
        ):
            self.assertEqual(0, read_full_publish_generation(full_publish_needed_path()))
            mark_full_publish_needed()
            mark_full_publish_needed()
            record_full_publish_delivered(2)
            record_full_publish_delivered(1)
            self.assertEqual(2, read_full_publish_generation(full_publish_needed_path()))
            self.assertEqual(2, read_full_publish_generation(full_publish_delivered_path()))
            self.assertEqual(FULL_PUBLISH_STATE_VERSION, current_delivery_versions()["FULL_PUBLISH_STATE_VERSION"])
            full_publish_delivered_path().write_text(
                '{"version":999,"generation":2}', encoding="utf-8"
            )
            with self.assertRaisesRegex(RuntimeError, "incompatible full publish state"):
                read_full_publish_generation(full_publish_delivered_path())
            with self.assertRaisesRegex(RuntimeError, "incompatible full publish state"):
                record_full_publish_delivered(3)

    @patch(
        "state.load_accepted_dashboard_state",
        return_value=DashboardState(initial_backfill_complete=True),
    )
    def test_cli_prints_initial_backfill_readiness(self, load_state: object) -> None:
        output = StringIO()
        with (
            patch("sys.argv", [
                "state.py",
                "--repo", "example",
                "--state-branch", "state-branch",
            ]),
            redirect_stdout(output),
        ):
            status = main()

        self.assertEqual(0, status)
        self.assertEqual("true\n", output.getvalue())
        load_state.assert_called_once_with("open-telemetry/example", "state-branch")

    def test_loads_accepted_v21_dashboard_state_from_state_branch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            checkout_dir = Path(temp_dir)
            state_path = checkout_dir / "example" / "dashboard-state.json"
            state_path.parent.mkdir()
            state_path.write_text(
                json.dumps({
                    "version": 21,
                    "initial_backfill_complete": True,
                    "prs": {"123": {"route": "author"}},
                }),
                encoding="utf-8",
            )
            with patch(
                "state.state_branch.accepted_state_dir",
                return_value=nullcontext(checkout_dir),
            ) as accepted_state_dir:
                dashboard_state = load_accepted_dashboard_state(
                    "open-telemetry/example",
                    "state-branch",
                )

        self.assertEqual(
            dashboard_state,
            DashboardState(
                initial_backfill_complete=True,
                results=(
                    decode_stored_result(
                        {"route": "author"},
                        pr_number_hint=123,
                    ),
                ),
            ),
        )
        accepted_state_dir.assert_called_once_with("state-branch", required=False)

    def test_versioned_state_helpers_preserve_arbitrary_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "state.json"

            save_state_file(
                path,
                {"cursor": {"last_pr_number": 78}, "_runtime_only": True},
                9,
            )

            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")),
                {"cursor": {"last_pr_number": 78}, "version": 9},
            )
            self.assertEqual(
                load_state_file(path, 9),
                {"cursor": {"last_pr_number": 78}, "version": 9},
            )

    def test_state_specific_loaders_own_payload_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("state._state_dir", Path(temp_dir)):
            dashboard_state_path().write_text(
                json.dumps({"version": DASHBOARD_STATE_VERSION, "unknown": "discard me"}),
                encoding="utf-8",
            )
            notification_state_path().write_text(
                json.dumps({"version": NOTIFICATION_STATE_VERSION}),
                encoding="utf-8",
            )
            backfill_state_path().write_text(
                json.dumps({"version": BACKFILL_STATE_VERSION}),
                encoding="utf-8",
            )

            self.assertEqual(
                load_dashboard_state_cache(),
                DashboardState(),
            )
            self.assertEqual(load_notifications(), {})
            self.assertEqual(
                load_backfill_state(),
                {"version": BACKFILL_STATE_VERSION, "cursor": {}},
            )

    def test_dashboard_state_save_writes_explicit_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("state._state_dir", Path(temp_dir)):
            save_dashboard_state_cache(DashboardState())

            self.assertEqual(
                json.loads(dashboard_state_path().read_text(encoding="utf-8")),
                {
                    "version": DASHBOARD_STATE_VERSION,
                    "initial_backfill_complete": False,
                    "draft_pr_numbers": [],
                    "prs": {},
                },
            )

    def test_predecessor_writes_cannot_replace_migrated_accepted_state(self) -> None:
        for dashboard_version in (20, 21, 23):
            with self.subTest(version=dashboard_version), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                legacy_snapshot = root / "dashboard-state.json"
                hold_started = "2026-08-16T08:00:00Z"
                save_state_file(legacy_snapshot, {
                    "initial_backfill_complete": True,
                    "prs": {"7": {"route": "approver", "facts": {
                        "route_held_since": hold_started,
                        "head_sha": "current-head",
                        "copilot_request_fingerprint": "fingerprint",
                        "copilot_review_request_needed": True,
                    }}},
                }, dashboard_version)

                with patch("state._state_dir", root), patch("state._using_delivery_state", False):
                    migrated = load_dashboard_state_cache()
                    self.assertTrue(migrated.initial_backfill_complete)
                    self.assertEqual(hold_started, migrated.result_for(7).facts.route_held_since)
                    self.assertTrue(migrated.result_for(7).facts.copilot_review_request_needed)
                    save_dashboard_state_cache(migrated)

                    save_state_file(legacy_snapshot, {"prs": {"9": {"route": "author"}}}, dashboard_version)
                    self.assertEqual(migrated, load_dashboard_state_cache())

    def test_dashboard_state_rejects_incompatible_versions(self) -> None:
        for version in (*range(11, 21), 22):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as temp_dir:
                with (
                    patch("state._state_dir", Path(temp_dir)),
                    redirect_stderr(StringIO()),
                ):
                    dashboard_state_path().write_text(
                        json.dumps({
                            "version": version,
                            "initial_backfill_complete": True,
                            "prs": {},
                        }),
                        encoding="utf-8",
                    )

                    self.assertIsNone(load_dashboard_state_cache())

    def test_loads_previous_routing_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("state._state_dir", Path(temp_dir)):
            dashboard_state_path().write_text(
                json.dumps({
                    "version": 27,
                    "initial_backfill_complete": True,
                    "prs": {"7": {"route": "author", "facts": {
                        "dashboard_override_bound_command_id": 42,
                        "dashboard_override_head_sha": "head",
                    }}},
                }),
                encoding="utf-8",
            )
            result = load_dashboard_state_cache()
            self.assertTrue(result.initial_backfill_complete)
            self.assertEqual(42, result.result_for(7).facts.handoff.command_id)
            self.assertEqual("head", result.result_for(7).facts.handoff.head_sha)

    def test_version_thirteen_codec_does_not_invent_durable_cutoff(self) -> None:
        legacy_facts = dashboard_facts(
            dashboard_command_replies=(
                DashboardCommandReply(
                    91,
                    "routed",
                    "alice",
                    head_sha="current-head",
                    route=DashboardRoute.APPROVER,
                    since="2026-08-16T08:00:00Z",
                ),
            ),
        )
        stored = encode_dashboard_state(dashboard_state(
            stored_dashboard_result(facts=legacy_facts)
        ))
        stored["version"] = 13
        stored["prs"]["1"]["facts"]["dashboard_override_since"] = "2026-08-16T08:00:00Z"

        loaded = decode_dashboard_state(stored)
        facts = loaded.results[0].facts

        self.assertEqual("2026-08-16T08:00:00Z", facts.handoff.since)
        self.assertEqual("", facts.dashboard_top_level_feedback_cutoff)
        self.assertFalse(facts.handoff.persistent)
        self.assertEqual(
            "",
            facts.dashboard_command_replies[0].top_level_feedback_cutoff,
        )
        self.assertFalse(
            facts.dashboard_command_replies[0].persistent_handoff
        )

    def test_version_sixteen_codec_does_not_invent_persistent_handoff(
        self,
    ) -> None:
        legacy_facts = dashboard_facts(
            dashboard_top_level_feedback_cutoff="2026-08-16T08:00:00Z",
            dashboard_command_replies=(
                DashboardCommandReply(
                    91,
                    "routed",
                    "alice",
                    head_sha="bound-head",
                    route=DashboardRoute.APPROVER,
                    top_level_feedback_cutoff="2026-08-16T08:00:00Z",
                ),
            ),
        )
        stored = encode_dashboard_state(dashboard_state(
            stored_dashboard_result(facts=legacy_facts)
        ))
        stored["version"] = 16
        stored["prs"]["1"]["facts"]["dashboard_override_head_sha"] = "bound-head"

        loaded = decode_dashboard_state(stored)
        facts = loaded.results[0].facts

        self.assertEqual(
            "2026-08-16T08:00:00Z",
            facts.dashboard_top_level_feedback_cutoff,
        )
        self.assertFalse(facts.handoff.persistent)
        self.assertFalse(
            facts.dashboard_command_replies[0].persistent_handoff
        )

    def test_dashboard_facts_codec_round_trip(self) -> None:
        facts = dashboard_facts(
            author="alice",
            assignees=("alice", "reviewer"),
            head_sha="current-head",
            routing_input_fingerprint="routing-fingerprint",
            copilot_request_fingerprint="copilot-fingerprint",
            dashboard_override_command_id=91,
            dashboard_override_command_user="alice",
            handoff=RoutingHandoff(
                command_id=91,
                head_sha='current-head',
                since='2026-08-16T08:00:00Z',
                persistent=True,
                released=True,
            ),
            dashboard_top_level_feedback_cutoff="2026-08-16T08:00:00Z",
            dashboard_command_replies=(
                DashboardCommandReply(
                    91,
                    "routed",
                    "alice",
                    head_sha="current-head",
                    route=DashboardRoute.APPROVER,
                    held_gates="the required checks",
                    since="2026-08-16T08:00:00Z",
                    top_level_feedback_cutoff="2026-08-16T08:00:00Z",
                    persistent_handoff=True,
                ),
                DashboardCommandReply(
                    91,
                    "cleared_by_feedback",
                    "alice",
                    head_sha="current-head",
                    since="2026-08-16T08:00:00Z",
                ),
                DashboardCommandReply(
                    92,
                    "unauthorized",
                    "outsider",
                    "route:reviewers",
                ),
            ),
            copilot_review_requested=True,
            copilot_review_quota_exhausted=True,
            copilot_review_exists=True,
            copilot_review_stale=True,
            copilot_review_needed=True,
            is_maintenance_bot=False,
            author_can_act=True,
            is_draft=False,
            approval_count=2,
            conflicts="no",
            created_at="2026-08-16T08:00:00Z",
            last_activity_at="2026-08-16T12:00:00Z",
            last_author_activity_at="2026-08-16T11:00:00Z",
            last_approver_activity_at="2026-08-16T10:00:00Z",
            ci_failing_count=1,
            ci_failing_since="2026-08-16T09:00:00Z",
            ci_maintainer_action_required_count=2,
            ci_pending_count=2,
            non_blocking_check_failures=("CodeQL",),
            copilot_review_outstanding=True,
            copilot_review_unreported=True,
            copilot_review_request_needed=True,
            required_checks_settled=False,
            route_held_since="2026-08-16T09:30:00Z",
            route_hold_expired=True,
            route_held_for_gates=True,
            waiting_since="2026-08-16T08:00:00Z",
            waiting_age_basis="oldest_pending_action",
            author_nudge_episode_id="episode-1",
            author_action_review_thread_urls=("https://example.test/thread/1",),
            author_action_top_level_feedback_urls=(
                "https://example.test/comment/2",
            ),
            reviewers=(
                {
                    "login": "reviewer",
                    "approved": True,
                    "open_thread": True,
                    "unresolved_thread": True,
                },
            ),
        )

        self.assertEqual(
            facts,
            decode_dashboard_facts(encode_dashboard_facts(facts)),
        )

    def test_legacy_facts_infer_whether_the_author_can_act(self) -> None:
        cases = (
            ("alice", True),
            ("app/dependabot", False),
            ("renovate[bot]", False),
            ("opentelemetrybot", False),
        )
        for author, expected in cases:
            with self.subTest(author=author):
                self.assertEqual(
                    expected,
                    decode_dashboard_facts({"author": author}).author_can_act,
                )

        self.assertTrue(
            decode_dashboard_facts({
                "author": "app/dependabot",
                "author_can_act": True,
            }).author_can_act
        )
        self.assertEqual(
            DashboardRoute.AUTHOR,
            decode_stored_result(
                {
                    "route": "author",
                    "facts": {
                        "author": "app/dependabot",
                        "author_can_act": True,
                    },
                },
                pr_number_hint=123,
            ).route,
        )

    def test_legacy_facts_do_not_invent_copilot_quota_exhaustion(self) -> None:
        self.assertFalse(
            decode_dashboard_facts({}).copilot_review_quota_exhausted
        )

    def test_first_review_clock_in_stored_facts_is_ignored(self) -> None:
        for value in (None, "2026-08-16T08:30:00Z"):
            with self.subTest(value=value):
                facts = decode_dashboard_facts({
                    "head_sha": "current-head",
                    "copilot_first_review_missing_since": value,
                })

                self.assertEqual(dashboard_facts(head_sha="current-head"), facts)
                self.assertNotIn(
                    "copilot_first_review_missing_since",
                    encode_dashboard_facts(facts),
                )

    def test_legacy_state_infers_author_capability(self) -> None:
        persisted = {
            "version": 13,
            "initial_backfill_complete": True,
            "prs": {
                "123": {
                    "pr_number": 123,
                    "failed": False,
                    "route": "author",
                    "facts": {"author": "app/dependabot"},
                },
                "124": {
                    "pr_number": 124,
                    "failed": False,
                    "route": "approver",
                    "facts": {"author": "app/dependabot"},
                },
                "125": {
                    "pr_number": 125,
                    "failed": False,
                    "route": "author",
                    "facts": {"author": "alice"},
                },
                "126": {
                    "pr_number": 126,
                    "failed": False,
                    "route": "author",
                    "facts": {
                        "author": "app/dependabot",
                        "author_can_act": True,
                    },
                },
            },
        }
        warnings = StringIO()
        with redirect_stderr(warnings):
            decoded = decode_dashboard_state(persisted)

        self.assertEqual(frozenset({124, 125, 126}), decoded.pr_numbers)
        decoded_by_number = {
            result.pr_number: result
            for result in decoded.results
        }
        self.assertFalse(decoded_by_number[124].facts.author_can_act)
        self.assertEqual(
            DashboardRoute.APPROVER,
            decoded_by_number[124].route,
        )
        self.assertTrue(decoded_by_number[125].facts.author_can_act)
        self.assertEqual(DashboardRoute.AUTHOR, decoded_by_number[125].route)
        self.assertTrue(decoded_by_number[126].facts.author_can_act)
        self.assertEqual(DashboardRoute.AUTHOR, decoded_by_number[126].route)
        self.assertIn(
            "dashboard result author route requires an actionable author",
            warnings.getvalue(),
        )
        self.assertEqual(
            DASHBOARD_STATE_VERSION,
            encode_dashboard_state(decoded)["version"],
        )

    def test_version_fourteen_state_is_regenerated(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch("state._state_dir", Path(temp_dir)),
        ):
            dashboard_state_path().write_text(
                json.dumps({
                    "version": 14,
                    "initial_backfill_complete": True,
                    "prs": {},
                }),
                encoding="utf-8",
            )

            self.assertIsNone(load_dashboard_state_cache())

    def test_stored_result_and_dashboard_state_codecs_round_trip(self) -> None:
        first = stored_dashboard_result(
            7,
            route=DashboardRoute.APPROVER,
            facts=dashboard_facts(author="alice", head_sha="first-head"),
            top_level_history={
                "feedback": {
                    "kind": "commit",
                    "timestamp": "2026-08-16T08:00:00Z",
                }
            },
        )
        second = stored_dashboard_result(
            8,
            route=DashboardRoute.MAINTAINER,
            facts=dashboard_facts(author="bob", head_sha="second-head"),
        )
        state = dashboard_state(
            second,
            first,
            initial_backfill_complete=True,
            draft_pr_numbers=frozenset({9}),
        )

        self.assertEqual(first, decode_stored_result(encode_stored_result(first)))
        self.assertEqual(state, decode_dashboard_state(encode_dashboard_state(state)))

    def test_stored_result_rejects_explicit_non_object_facts(self) -> None:
        stored = {
            "pr_number": 7,
            "pr_url": "https://github.com/open-telemetry/example/pull/7",
            "failed": False,
            "route": "approver",
        }

        self.assertEqual(dashboard_facts(), decode_stored_result(stored).facts)
        for facts in ([], "", 0, False, None):
            with self.subTest(facts=facts):
                with self.assertRaisesRegex(
                    ValueError,
                    "dashboard result facts must be an object",
                ):
                    decode_stored_result({**stored, "facts": facts})

    def test_dashboard_facts_rejects_null_non_optional_fields(self) -> None:
        cases = (
            ({"author": None}, "facts.author must be a string"),
            ({"assignees": None}, "facts.assignees must be an array of strings"),
            ({"is_draft": None}, "facts.is_draft must be a boolean"),
            (
                {"dashboard_override_persistent": None},
                "facts.dashboard_override_persistent must be a boolean",
            ),
            ({"approval_count": None}, "facts.approval_count must be an integer"),
            (
                {"dashboard_command_replies": None},
                "facts.dashboard_command_replies must be an array",
            ),
            ({"reviewers": None}, "facts.reviewers must be an array"),
            (
                {"reviewers": [{"login": None}]},
                "facts.reviewers.login must be a string",
            ),
            (
                {
                    "dashboard_command_replies": [{
                        "comment_id": None,
                        "kind": "unauthorized",
                    }]
                },
                "facts.dashboard_command_replies.comment_id must be an integer",
            ),
            (
                {
                    "dashboard_command_replies": [{
                        "comment_id": 1,
                        "kind": "routed",
                        "route": "approver",
                        "persistent_handoff": None,
                    }]
                },
                "facts.dashboard_command_replies.persistent_handoff "
                "must be a boolean",
            ),
        )

        for facts, message in cases:
            with self.subTest(facts=facts):
                with self.assertRaises(ValueError) as raised:
                    decode_dashboard_facts(facts)
                self.assertEqual(message, str(raised.exception))

    def test_dashboard_facts_accepts_null_optional_fields(self) -> None:
        self.assertEqual(
            dashboard_facts(),
            decode_dashboard_facts({
                "ci_failing_count": None,
                "ci_failing_since": None,
                "ci_maintainer_action_required_count": None,
                "ci_pending_count": None,
                "route_held_since": None,
                "author_nudge_episode_id": None,
            }),
        )

    def test_stored_result_rejects_null_non_optional_fields(self) -> None:
        stored = {
            "pr_number": 7,
            "pr_url": "https://github.com/open-telemetry/example/pull/7",
            "failed": False,
            "route": "approver",
        }
        cases = (
            ("pr_number", "dashboard result pr_number must be an integer"),
            ("pr_url", "dashboard result pr_url must be a string"),
            ("failed", "dashboard result failed must be a boolean"),
            ("route", "dashboard result route must be a string"),
            (
                "top_level_history",
                "dashboard result top_level_history must be an object",
            ),
        )

        for field, message in cases:
            with self.subTest(field=field):
                with self.assertRaises(ValueError) as raised:
                    decode_stored_result({**stored, field: None})
                self.assertEqual(message, str(raised.exception))

    def test_malformed_persisted_results_are_rejected_individually(self) -> None:
        persisted = {
            "version": DASHBOARD_STATE_VERSION,
            "initial_backfill_complete": True,
            "prs": {
                "1": encode_stored_result(
                    stored_dashboard_result(
                        1,
                        facts=dashboard_facts(author="canonical"),
                    )
                ),
                "01": encode_stored_result(
                    stored_dashboard_result(
                        1,
                        facts=dashboard_facts(author="alias"),
                    )
                ),
                "not-a-number": {},
                "0": {},
                "2": [],
                "3": {"pr_number": 3, "failed": True, "route": "unknown"},
                "4": {"pr_number": 40, "failed": False, "route": "author"},
                "5": {"pr_number": 5, "failed": False, "route": "unknown"},
                "6": {
                    "pr_number": 6,
                    "failed": False,
                    "route": "author",
                    "facts": {"reviewers": "not-an-array"},
                },
            },
        }
        sorted_persisted = json.loads(json.dumps(persisted, sort_keys=True))
        warnings = StringIO()

        with redirect_stderr(warnings):
            decoded = decode_dashboard_state(sorted_persisted)

        self.assertEqual(frozenset({1}), decoded.pr_numbers)
        self.assertEqual("canonical", decoded.results[0].facts.author)
        self.assertTrue(decoded.initial_backfill_complete)
        self.assertEqual(8, warnings.getvalue().count(
            "warning: ignoring malformed dashboard result"
        ))

    def test_legacy_dashboard_state_payload_reencodes_to_current_shape(self) -> None:
        persisted = {
            "version": 11,
            "initial_backfill_complete": True,
            "prs": {
                "123": {
                    "pr_number": 123,
                    "pr_url": "https://github.com/open-telemetry/example/pull/123",
                    "failed": False,
                    "route": "approver",
                    "facts": {
                        "author": "alice",
                        "assignees": ["alice"],
                        "head_sha": "current-head",
                        "routing_input_fingerprint": "routing-fingerprint",
                        "copilot_request_fingerprint": "copilot-fingerprint",
                        "dashboard_override_command_id": 91,
                        "dashboard_override_command_user": "alice",
                        "dashboard_override_bound_command_id": 91,
                        "dashboard_override_head_sha": "current-head",
                        "dashboard_override_since": "2026-08-16T08:00:00Z",
                        "dashboard_override_cleared_by_feedback": True,
                        "dashboard_command_replies": [
                            {
                                "comment_id": 91,
                                "kind": "routed",
                                "head_sha": "current-head",
                                "user": "alice",
                                "since": "2026-08-16T08:00:00Z",
                                "route": "approver",
                                "held_gates": "",
                            },
                            {
                                "comment_id": 91,
                                "kind": "cleared_by_feedback",
                                "head_sha": "current-head",
                                "user": "alice",
                                "since": "2026-08-16T08:00:00Z",
                            },
                        ],
                        "copilot_review_requested": True,
                        "copilot_review_exists": True,
                        "copilot_review_stale": False,
                        "copilot_review_needed": False,
                        "is_maintenance_bot": False,
                        "is_draft": False,
                        "approval_count": 1,
                        "conflicts": "no",
                        "created_at": "2026-08-16T08:00:00Z",
                        "last_activity_at": "2026-08-16T12:00:00Z",
                        "last_author_activity_at": "2026-08-16T11:00:00Z",
                        "last_approver_activity_at": "2026-08-16T10:00:00Z",
                        "last_new_activity_at": "",
                        "copilot_review_outstanding": True,
                        "copilot_review_unreported": False,
                        "copilot_review_request_needed": False,
                        "required_checks_settled": True,
                        "route_hold_expired": False,
                        "route_held_for_gates": False,
                        "waiting_since": "2026-08-16T08:00:00Z",
                        "waiting_age_basis": "last_author_activity",
                        "author_action_review_thread_urls": [
                            "https://github.com/open-telemetry/example/pull/123"
                            "#discussion_r1",
                        ],
                        "author_action_top_level_feedback_urls": [
                            "https://github.com/open-telemetry/example/pull/123"
                            "#pullrequestreview-2",
                        ],
                        "reviewers": [{
                            "login": "reviewer",
                            "approved": True,
                            "approved_non_team": False,
                            "pending_review": False,
                            "changes_requested": False,
                            "open_thread": False,
                            "unresolved_thread": False,
                            "top_level_feedback": False,
                        }],
                        "ci_failing_count": 0,
                        "ci_pending_count": 0,
                        "non_blocking_check_failures": ["CodeQL"],
                        "copilot_first_review_missing_since": (
                            "2026-08-16T08:30:00Z"
                        ),
                        "route_held_since": "2026-08-16T09:30:00Z",
                        "author_nudge_episode_id": "episode-1",
                    },
                    "top_level_history": {
                        "feedback": {
                            "kind": "commit",
                            "timestamp": "2026-08-16T10:00:00Z",
                        }
                    },
                },
            },
        }

        decoded = decode_dashboard_state(persisted)
        self.assertTrue(decoded.results[0].facts.author_can_act)
        expected = {
            **persisted,
            "version": DASHBOARD_STATE_VERSION,
            "draft_pr_numbers": [],
        }
        expected["prs"]["123"]["facts"]["author_can_act"] = True
        del expected["prs"]["123"]["facts"]["copilot_first_review_missing_since"]
        facts = expected["prs"]["123"]["facts"]
        facts["handoff"] = {
            "command_id": facts.pop("dashboard_override_bound_command_id"),
            "head_sha": facts.pop("dashboard_override_head_sha"),
            "since": facts.pop("dashboard_override_since"),
            "target": "reviewers",
            "released": facts.pop("dashboard_override_cleared_by_feedback"),
            "persistent": False,
        }
        self.assertEqual(expected, encode_dashboard_state(decoded))

    def test_notification_state_version_is_independent(self) -> None:
        self.assertEqual(BACKFILL_STATE_VERSION, 3)
        self.assertEqual(NOTIFICATION_STATE_VERSION, 3)
        self.assertEqual(DASHBOARD_STATE_VERSION, 28)
        self.assertEqual(DASHBOARD_STATE_COMPATIBLE_VERSIONS, (20, 21, 23))
        self.assertEqual(STATUS_COMMENT_ROLLOUT_STATE_VERSION, 3)
        self.assertEqual(STATUS_COMMENT_REVISION, 24)
        self.assertEqual(AUTHOR_NUDGE_STATE_VERSION, 6)
        self.assertEqual(COPILOT_REVIEW_REQUEST_STATE_VERSION, 14)

    def test_version_fifteen_dashboard_state_is_regenerated(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch("state._state_dir", Path(temp_dir)),
        ):
            dashboard_state_path().write_text(
                json.dumps({
                    "version": 15,
                    "initial_backfill_complete": True,
                    "prs": {},
                }),
                encoding="utf-8",
            )

            self.assertIsNone(load_dashboard_state_cache())

    def test_author_nudge_state_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("state._state_dir", Path(temp_dir)):
            save_author_nudges({
                "123": {
                    "waiting_since": "2026-07-10T00:00:00Z",
                    "nudged_at": "",
                }
            })

            self.assertEqual(
                load_author_nudges(),
                {
                    "123": {
                        "waiting_since": "2026-07-10T00:00:00Z",
                        "nudged_at": "",
                    }
                },
            )
            self.assertTrue(author_nudge_state_path().exists())

    def test_author_nudge_state_preserves_waits_and_reminders_for_migration(self) -> None:
        for version in (2, 3, 4, 5):
            with (
                self.subTest(version=version),
                tempfile.TemporaryDirectory() as temp_dir,
                patch("state._state_dir", Path(temp_dir)),
            ):
                nudges = {
                    "122": {
                        "waiting_since": "2026-07-14T00:00:00Z",
                        "nudged_at": "",
                        "episode_id": "episode-2",
                    },
                    "123": {
                        "waiting_since": "2026-07-10T00:00:00Z",
                        "nudged_at": "2026-07-17T00:00:00Z",
                        "episode_id": "episode-1",
                    },
                }
                author_nudge_state_path().write_text(
                    json.dumps({
                        "version": version,
                        "prs": nudges,
                    }),
                    encoding="utf-8",
                )

                self.assertEqual(nudges, load_author_nudges())

    def test_author_nudge_previous_versions_preserve_pending_cleanup_and_receipts(self) -> None:
        entries = {
            "123": {
                "waiting_since": "2026-07-10T00:00:00Z",
                "nudged_at": "2026-07-17T00:00:00Z",
                "episode_id": "current-episode",
                "completions": [{
                    "episode_id": "pending-episode",
                    "completed_at": "2026-07-17T00:00:00Z",
                }],
                "delivered_completion_ids": ["completed-episode"],
            },
        }
        for version in (4, 5):
            with (
                self.subTest(version=version),
                tempfile.TemporaryDirectory() as temp_dir,
                patch("state._state_dir", Path(temp_dir)),
            ):
                author_nudge_state_path().write_text(
                    json.dumps({"version": version, "prs": entries}),
                    encoding="utf-8",
                )

                self.assertEqual(entries, load_author_nudges())
                save_author_nudges(load_author_nudges())

                self.assertEqual(
                    {"version": 6, "prs": entries},
                    json.loads(author_nudge_state_path().read_text(encoding="utf-8")),
                )

    def test_retry_snapshot_preserves_posted_author_nudge(self) -> None:
        self.assertEqual(
            {
                "7": {
                    "waiting_since": "2026-07-10T02:00:00Z",
                    "nudged_at": "2026-07-20T02:00:00Z",
                    "episode_id": "episode-1",
                    "delivered_completion_ids": ["earlier-episode", "old-episode"],
                }
            },
            union_merge_author_nudges(
                {
                    "7": {
                        "waiting_since": "2026-07-10T02:00:00Z",
                        "nudged_at": "",
                        "pending_at": "2026-07-20T01:00:00Z",
                        "head_sha": "head",
                        "routing_input_fingerprint": "fingerprint",
                        "delivered_completion_ids": ["earlier-episode"],
                    },
                },
                {
                    "7": {
                        "waiting_since": "2026-07-10T02:00:00Z",
                        "nudged_at": "2026-07-20T02:00:00Z",
                        "episode_id": "episode-1",
                        "delivered_completion_ids": ["old-episode", "earlier-episode"],
                    }
                },
            ),
        )

    def test_retry_snapshot_preserves_pending_nudge_completion(self) -> None:
        self.assertEqual(
            {
                "7": {
                    "waiting_since": "2026-07-10T02:00:00Z",
                    "nudged_at": "2026-07-20T02:00:00Z",
                    "episode_id": "episode-1",
                    "delivered_completion_ids": ["old-episode"],
                    "completions": [{
                        "episode_id": "previous-episode",
                        "completed_at": "2026-07-21T02:00:00Z",
                    }],
                }
            },
            union_merge_author_nudges(
                {
                    "7": {
                        "waiting_since": "2026-07-10T02:00:00Z",
                        "nudged_at": "2026-07-20T02:00:00Z",
                        "episode_id": "episode-1",
                        "delivered_completion_ids": ["old-episode"],
                        "completions": [{
                            "episode_id": "previous-episode",
                            "completed_at": "2026-07-21T02:00:00Z",
                        }, {
                            "episode_id": "old-episode",
                            "completed_at": "2026-07-20T02:00:00Z",
                        }],
                    }
                },
                {
                    "7": {
                        "waiting_since": "2026-07-10T02:00:00Z",
                        "nudged_at": "2026-07-20T02:00:00Z",
                    }
                },
            ),
        )

    def test_retry_snapshot_completes_posted_nudge_without_suppressing_new_episode(
        self,
    ) -> None:
        self.assertEqual(
            {
                "7": {
                    "waiting_since": "2026-07-20T02:00:00Z",
                    "nudged_at": "",
                    "episode_id": "episode-2",
                    "delivered_completion_ids": ["old-episode"],
                    "completions": [{
                        "episode_id": "episode-1",
                        "completed_at": "2026-07-17T02:00:00Z",
                        "kind": "routing_changed",
                    }],
                }
            },
            union_merge_author_nudges(
                {
                    "7": {
                        "waiting_since": "2026-07-20T02:00:00Z",
                        "nudged_at": "",
                        "episode_id": "episode-2",
                    }
                },
                {
                    "7": {
                        "waiting_since": "2026-07-10T02:00:00Z",
                        "nudged_at": "2026-07-17T02:00:00Z",
                        "episode_id": "episode-1",
                        "delivered_completion_ids": ["old-episode"],
                    }
                },
            ),
        )

    def test_retry_snapshot_retains_receipt_without_a_baseline_entry(self) -> None:
        self.assertEqual(
            {"7": {"delivered_completion_ids": ["old-episode"]}},
            union_merge_author_nudges(
                {}, {"7": {"delivered_completion_ids": ["old-episode"]}},
            ),
        )

    def test_retry_snapshot_completes_posted_nudge_removed_from_baseline(self) -> None:
        self.assertEqual(
            {
                "7": {
                    "completions": [{
                        "episode_id": "episode-1",
                        "completed_at": "2026-07-17T02:00:00Z",
                        "kind": "routing_changed",
                    }],
                }
            },
            union_merge_author_nudges(
                {},
                {
                    "7": {
                        "waiting_since": "2026-07-10T02:00:00Z",
                        "nudged_at": "2026-07-17T02:00:00Z",
                        "episode_id": "episode-1",
                    }
                },
            ),
        )

    def test_status_comment_rollout_state_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("state._state_dir", Path(temp_dir)):
            save_status_comment_rollout_state({
                "target_revision": 2,
                "completed_revision": 1,
                "pending_pr_numbers": [34, 12, 34],
                "draft_reconciliation_cursor": 56,
            })

            self.assertEqual(
                load_status_comment_rollout_state(),
                {
                    "version": STATUS_COMMENT_ROLLOUT_STATE_VERSION,
                    "target_revision": 2,
                    "completed_revision": 1,
                    "pending_pr_numbers": [34, 12],
                    "draft_reconciliation_cursor": 56,
                },
            )

    def test_enqueue_status_comment_update_is_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("state._state_dir", Path(temp_dir)):
            enqueue_status_comment_update(34)
            enqueue_status_comment_update(12)
            enqueue_status_comment_update(34)

            self.assertEqual(
                [34, 12],
                load_status_comment_rollout_state()["pending_pr_numbers"],
            )

    def test_status_intent_is_suppressed_by_receipt_until_it_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            accepted = root / "accepted"
            receipts = root / "receipts"
            accepted.mkdir()
            receipts.mkdir()
            accepted_state = {
                "version": STATUS_COMMENT_ROLLOUT_STATE_VERSION,
                "intent_revisions": {"34": "snapshot-a"},
            }
            (accepted / "status-comment-rollout-state.json").write_text(
                json.dumps(accepted_state),
                encoding="utf-8",
            )
            receipt_state = {
                "version": STATUS_COMMENT_ROLLOUT_STATE_VERSION,
                "delivered_intent_revisions": {"34": "snapshot-a"},
            }
            (receipts / "status-comment-rollout-state.json").write_text(
                json.dumps(receipt_state),
                encoding="utf-8",
            )
            with (
                patch("state._state_dir", receipts),
                patch("state._accepted_state_dir", accepted),
                patch("state._using_delivery_state", True),
            ):
                self.assertNotIn(
                    34,
                    load_status_comment_rollout_state()["pending_pr_numbers"],
                )
                accepted_state["intent_revisions"]["34"] = "snapshot-b"
                (accepted / "status-comment-rollout-state.json").write_text(
                    json.dumps(accepted_state),
                    encoding="utf-8",
                )
                self.assertIn(
                    34,
                    load_status_comment_rollout_state()["pending_pr_numbers"],
                )

    def test_author_nudge_receipts_suppress_delivered_intent_and_completion(self) -> None:
        intent = {
            "1": {
                "episode_id": "episode-1",
                "waiting_since": "2026-07-01T00:00:00Z",
                "pending_at": "2026-07-08T00:00:00Z",
                "completions": [
                    {
                        "episode_id": "old-episode",
                        "completed_at": "2026-07-08T00:00:00Z",
                        "kind": "routing_changed",
                    }
                ],
            }
        }
        receipts = {
            "1": {
                "episode_id": "episode-1",
                "waiting_since": "2026-07-01T00:00:00Z",
                "nudged_at": "2026-07-08T00:00:00Z",
                "delivered_completion_ids": ["old-episode"],
                "completions": [{
                    "episode_id": "old-episode",
                    "completed_at": "2026-07-08T00:00:00Z",
                }],
            }
        }

        reconciled = reconcile_author_nudges(intent, receipts)["1"]

        self.assertEqual("2026-07-08T00:00:00Z", reconciled["nudged_at"])
        self.assertNotIn("pending_at", reconciled)
        self.assertNotIn("completions", reconciled)
        self.assertEqual(["old-episode"], reconciled["delivered_completion_ids"])

    def test_each_delivery_version_rejects_older_workers(self) -> None:
        baseline = current_delivery_versions()
        for name in baseline:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp_dir:
                with patch("state._state_dir", Path(temp_dir)):
                    self.assertTrue(claim_delivery_versions())
                    newer = dict(baseline)
                    newer[name] += 1
                    with patch("state.current_delivery_versions", return_value=newer):
                        self.assertTrue(claim_delivery_versions())
                    self.assertFalse(claim_delivery_versions())
                    self.assertEqual(newer, load_delivery_versions())

    def test_new_delivery_version_rejects_older_workers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("state._state_dir", Path(temp_dir)):
            self.assertTrue(claim_delivery_versions())
            with patch("state.FUTURE_STATE_VERSION", 1, create=True):
                newer = current_delivery_versions()
                self.assertEqual(1, newer["FUTURE_STATE_VERSION"])
                self.assertTrue(claim_delivery_versions())

            self.assertFalse(claim_delivery_versions())
            self.assertEqual(newer, load_delivery_versions())

    def test_lifecycle_state_version_blocks_incompatible_delivery_workers(self) -> None:
        current = current_delivery_versions()
        for old_version in (13, 16):
            old_worker = {**current, "DASHBOARD_STATE_VERSION": old_version}
            with self.subTest(old_version=old_version), tempfile.TemporaryDirectory() as temp_dir:
                with patch("state._state_dir", Path(temp_dir)):
                    with patch(
                        "state.current_delivery_versions",
                        return_value=old_worker,
                    ):
                        self.assertTrue(claim_delivery_versions())

                    self.assertTrue(claim_delivery_versions())

                    with patch(
                        "state.current_delivery_versions",
                        return_value=old_worker,
                    ):
                        self.assertFalse(claim_delivery_versions())
                    self.assertEqual(current, load_delivery_versions())

    def test_delivery_versions_fail_closed(self) -> None:
        malformed_versions = [
            "not json",
            json.dumps([]),
            json.dumps({"DASHBOARD_STATE_VERSION": None}),
            json.dumps({"DASHBOARD_STATE_VERSION": False}),
            json.dumps({"DASHBOARD_STATE_VERSION": -1}),
            json.dumps({"DASHBOARD_STATE_VERSION": 1.5}),
        ]
        for contents in malformed_versions:
            with self.subTest(contents=contents), tempfile.TemporaryDirectory() as temp_dir:
                with patch("state._state_dir", Path(temp_dir)):
                    delivery_state = Path(temp_dir) / "delivery-versions.json"
                    delivery_state.write_text(contents, encoding="utf-8")

                    self.assertFalse(claim_delivery_versions())

    def test_backfill_state_preserves_version_three_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("state._state_dir", Path(temp_dir)):
            backfill_state_path().write_text(
                json.dumps(
                    {
                        "version": 3,
                        "cursor": {"last_pr_number": 78},
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(
                load_backfill_state(),
                {
                    "version": BACKFILL_STATE_VERSION,
                    "cursor": {"last_pr_number": 78},
                },
            )

    def test_targeted_update_preserves_initial_backfill_marker(self) -> None:
        state = empty_state().with_initial_backfill_complete()

        updated = update_dashboard_state_for_pr(state, 123, None)

        self.assertEqual(DashboardState(initial_backfill_complete=True), updated)

    def test_notification_state_write_ignores_dashboard_version(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch("state._state_dir", Path(temp_dir)),
            patch("state.DASHBOARD_STATE_VERSION", 4),
        ):
            save_notifications({"123": {"last_notified_at": "2026-07-14T03:00:00Z"}})

            state = json.loads(notification_state_path().read_text(encoding="utf-8"))
            self.assertEqual(state["version"], NOTIFICATION_STATE_VERSION)

    def test_stored_result_preserves_top_level_history(self) -> None:
        result = stored_result(
            evaluation_success(
                123,
                top_level_history={
                    "pr-review-456": {
                        "evidence": {
                            "commit": "2026-07-14T03:00:00Z",
                            "description": "2026-07-14T04:00:00Z",
                        },
                    },
                },
                pending_actions={
                    "inline-thread": {
                        "action": "author",
                        "since": "2026-07-14T02:00:00Z",
                    },
                },
            )
        )

        self.assertEqual(
            {
                key: {
                    nested_key: dict(nested_value)
                    for nested_key, nested_value in value.items()
                }
                for key, value in result.top_level_history.items()
            },
            {
                "pr-review-456": {
                    "evidence": {
                        "commit": "2026-07-14T03:00:00Z",
                        "description": "2026-07-14T04:00:00Z",
                    },
                },
            },
        )

    def test_notification_state_survives_dashboard_version_bump(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("state._state_dir", Path(temp_dir)):
            notification_state_path().write_text(
                json.dumps(
                    {
                        "version": 3,
                        "prs": {
                            "123": {
                                "last_notified_at": "2026-07-14T03:00:00Z",
                                "last_notification_kind": "initial",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(
                load_notifications(),
                {
                    "123": {
                        "last_notified_at": "2026-07-14T03:00:00Z",
                        "last_notification_kind": "initial",
                    }
                },
            )


if __name__ == "__main__":
    unittest.main()