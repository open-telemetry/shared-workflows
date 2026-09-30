from __future__ import annotations

import base64
import json
import subprocess
import unittest
from unittest.mock import patch

import check_full_publish_health as health


class FullPublishHealthTest(unittest.TestCase):
    def test_reads_versioned_generation_and_surfaces_api_failure(self) -> None:
        encoded = base64.b64encode(
            json.dumps({"version": 1, "generation": 3}).encode("utf-8")
        ).decode("ascii")
        with patch.object(
            health.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(
                ["gh"], 0, json.dumps({"encoding": "base64", "content": encoded}), ""
            ),
        ) as request:
            self.assertEqual(
                3,
                health.remote_generation("example", "otelbot/state", "full-publish-needed.json"),
            )
        self.assertEqual(
            ["gh", "api", "--method", "GET",
             "repos/open-telemetry/shared-workflows/contents/example/full-publish-needed.json",
             "-f", "ref=otelbot/state/example"],
            request.call_args.args[0],
        )
        with patch.object(
            health.subprocess, "run",
            return_value=subprocess.CompletedProcess(["gh"], 1, "", "rate limited"),
        ):
            with self.assertRaisesRegex(RuntimeError, "rate limited"):
                health.remote_generation("example", "otelbot/state", "full-publish-needed.json")

    def test_pending_full_publish_is_unhealthy_even_if_matrix_succeeded(self) -> None:
        with (
            patch.object(health, "needed_generation", return_value=(6, True)),
            patch.object(health, "remote_generation", return_value=5),
        ):
            self.assertFalse(health.check_health(["example"], {"example"}, set()))

    def test_initial_backfill_does_not_report_unpublished_generation(self) -> None:
        with (
            patch.object(health, "needed_generation", return_value=(6, False)),
            patch.object(health, "remote_generation", return_value=5),
            patch.object(health, "initial_backfill_complete") as fetch_large_state,
        ):
            self.assertTrue(health.check_health(["example"], {"example"}, set()))
            self.assertFalse(health.check_health(["example"], {"example"}, {"canary"}))
            fetch_large_state.assert_not_called()

    def test_legacy_generation_uses_dashboard_state_readiness(self) -> None:
        with (
            patch.object(health, "needed_generation", return_value=(6, None)),
            patch.object(health, "remote_generation", return_value=5),
            patch.object(health, "initial_backfill_complete", return_value=False) as readiness,
        ):
            self.assertTrue(health.check_health(["example"], {"example"}, set()))
            readiness.assert_called_once_with("example")

    def test_reads_readiness_from_needed_generation(self) -> None:
        with patch.object(
            health,
            "remote_state_file",
            return_value={"version": 1, "generation": 6, "initial_backfill_complete": False},
        ) as fetch:
            self.assertEqual((6, False), health.needed_generation("example"))
            fetch.assert_called_once_with(
                "example", "otelbot/pull-request-dashboard-state", "full-publish-needed.json"
            )
        with patch.object(
            health,
            "remote_state_file",
            return_value={"version": 1, "generation": 6, "initial_backfill_complete": None},
        ):
            with self.assertRaisesRegex(RuntimeError, "incompatible full publish state"):
                health.needed_generation("example")

    def test_reads_initial_backfill_readiness_strictly(self) -> None:
        with patch.object(health, "remote_state_file", return_value=None):
            self.assertFalse(health.initial_backfill_complete("example"))
        with patch.object(
            health,
            "remote_state_file",
            return_value={"version": health.DASHBOARD_STATE_VERSION, "initial_backfill_complete": False},
        ) as fetch:
            self.assertFalse(health.initial_backfill_complete("example"))
            fetch.assert_called_once_with(
                "example", "otelbot/pull-request-dashboard-state", "dashboard-state.json"
            )
        with patch.object(
            health,
            "remote_state_file",
            return_value={"version": health.DASHBOARD_STATE_VERSION, "initial_backfill_complete": True},
        ):
            self.assertTrue(health.initial_backfill_complete("example"))
        with patch.object(
            health,
            "remote_state_file",
            return_value={"version": health.DASHBOARD_STATE_VERSION, "initial_backfill_complete": "false"},
        ):
            with self.assertRaisesRegex(RuntimeError, "incompatible dashboard state"):
                health.initial_backfill_complete("example")

    def test_coalesced_job_is_healthy_after_replacement_delivers(self) -> None:
        with (
            patch.object(health, "needed_generation", return_value=(6, True)),
            patch.object(health, "remote_generation", return_value=6),
        ):
            self.assertTrue(health.check_health(["example"], {"example"}, {"canary"}))

    def test_canceled_legacy_stable_job_retains_cancellation_tolerance(self) -> None:
        with patch.object(health, "needed_generation", return_value=(0, None)) as fetch:
            self.assertTrue(health.check_health(["example"], set(), {"stable"}))
            self.assertTrue(health.check_health(["example"], set(), set()))
            self.assertEqual(2, fetch.call_count)

    def test_canceled_canary_job_requires_receipt_protocol(self) -> None:
        with patch.object(health, "needed_generation", return_value=(0, None)):
            self.assertFalse(health.check_health(["example"], {"example"}, {"canary"}))

    def test_canceled_stable_job_checks_receipts_when_protocol_is_available(self) -> None:
        with (
            patch.object(health, "needed_generation", return_value=(6, True)),
            patch.object(health, "remote_generation", side_effect=[5, 6]),
        ):
            self.assertFalse(health.check_health(["example"], set(), {"stable"}))
            self.assertTrue(health.check_health(["example"], set(), {"stable"}))

    def test_health_reads_at_most_two_files_per_repository(self) -> None:
        with (
            patch.object(health, "needed_generation", side_effect=[(3, True), (0, None)]) as fetch,
            patch.object(health, "remote_generation", return_value=3),
        ):
            self.assertTrue(health.check_health(["canary", "stable"], {"canary"}, set()))
            self.assertEqual(2, fetch.call_count)
