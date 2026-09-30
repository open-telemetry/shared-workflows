from __future__ import annotations

import base64
import json
import os
import subprocess
import unittest
from unittest.mock import patch

import check_full_publish_health as health


class FullPublishHealthTest(unittest.TestCase):
    def setUp(self) -> None:
        environment = patch.dict(
            os.environ,
            {
                "GITHUB_REPOSITORY": "open-telemetry/shared-workflows",
                "DASHBOARD_STATE_BRANCH_PREFIX": "otelbot/pull-request-dashboard-state",
                "DASHBOARD_DELIVERY_STATE_BRANCH_PREFIX": "otelbot/pull-request-dashboard-delivery",
            },
        )
        environment.start()
        self.addCleanup(environment.stop)

    def test_health_uses_configured_repository_and_state_prefixes(self) -> None:
        responses = [
            subprocess.CompletedProcess(
                ["gh"],
                0,
                json.dumps({
                    "encoding": "base64",
                    "content": base64.b64encode(json.dumps(data).encode("utf-8")).decode("ascii"),
                }),
                "",
            )
            for data in (
                {"version": 1, "generation": 6, "initial_backfill_complete": True},
                {"version": 1, "generation": 5},
            )
        ]
        with (
            patch.dict(
                os.environ,
                {
                    "GITHUB_REPOSITORY": "example/fork",
                    "DASHBOARD_STATE_BRANCH_PREFIX": "custom/state",
                    "DASHBOARD_DELIVERY_STATE_BRANCH_PREFIX": "custom/delivery",
                },
            ),
            patch.object(health.subprocess, "run", side_effect=responses) as request,
        ):
            self.assertFalse(health.check_health(["example"], {"example"}, set()))
        self.assertEqual(
            [
                ["gh", "api", "--method", "GET",
                 "repos/example/fork/contents/example/full-publish-needed.json",
                 "-f", "ref=custom/state/example"],
                ["gh", "api", "--method", "GET",
                 "repos/example/fork/contents/example/full-publish-delivered.json",
                 "-f", "ref=custom/delivery/example"],
            ],
            [call.args[0] for call in request.call_args_list],
        )

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
        ):
            self.assertTrue(health.check_health(["example"], {"example"}, set()))
            self.assertFalse(health.check_health(["example"], {"example"}, {"canary"}))

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
    def test_missing_generation_has_no_publication_obligation(self) -> None:
        with patch.object(health, "remote_state_file", return_value=None):
            self.assertEqual((0, False), health.needed_generation("example"))

    def test_missing_or_invalid_readiness_reports_marker_regeneration(self) -> None:
        for data in (
            {"version": 1, "generation": 6},
            {"version": 1, "generation": 6, "initial_backfill_complete": None},
            {"version": 1, "generation": 6, "initial_backfill_complete": "false"},
            {"version": 1, "generation": 6, "initial_backfill_complete": 1},
        ):
            with (
                self.subTest(marker=data),
                patch.object(health, "remote_state_file", return_value=data) as fetch,
            ):
                with self.assertRaisesRegex(RuntimeError, "run a backfill to regenerate the marker"):
                    health.needed_generation("example")
                fetch.assert_called_once_with(
                    "example", "otelbot/pull-request-dashboard-state", "full-publish-needed.json"
                )

    def test_health_reads_only_generation_markers(self) -> None:
        with patch.object(
            health,
            "remote_state_file",
            side_effect=[
                {"version": 1, "generation": 6, "initial_backfill_complete": False},
                {"version": 1, "generation": 5},
            ],
        ) as fetch:
            self.assertTrue(health.check_health(["example"], {"example"}, set()))
        self.assertEqual(
            [
                ("example", "otelbot/pull-request-dashboard-state", "full-publish-needed.json"),
                ("example", "otelbot/pull-request-dashboard-delivery", "full-publish-delivered.json"),
            ],
            [call.args for call in fetch.call_args_list],
        )

    def test_coalesced_job_is_healthy_after_replacement_delivers(self) -> None:
        with (
            patch.object(health, "needed_generation", return_value=(6, True)),
            patch.object(health, "remote_generation", return_value=6),
        ):
            self.assertTrue(health.check_health(["example"], {"example"}, {"canary"}))

    def test_canceled_legacy_stable_job_retains_cancellation_tolerance(self) -> None:
        with patch.object(health, "needed_generation", return_value=(0, False)) as fetch:
            self.assertTrue(health.check_health(["example"], set(), {"stable"}))
            self.assertTrue(health.check_health(["example"], set(), set()))
            self.assertEqual(2, fetch.call_count)

    def test_canceled_canary_job_requires_receipt_protocol(self) -> None:
        with patch.object(health, "needed_generation", return_value=(0, False)):
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
            patch.object(health, "needed_generation", side_effect=[(3, True), (0, False)]) as fetch,
            patch.object(health, "remote_generation", return_value=3),
        ):
            self.assertTrue(health.check_health(["canary", "stable"], {"canary"}, set()))
            self.assertEqual(2, fetch.call_count)
