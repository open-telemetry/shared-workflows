from __future__ import annotations

import base64
import json
import unittest
from unittest.mock import patch

import check_full_publish_health as health
from github_cli import GhNotFoundError


class FullPublishHealthTest(unittest.TestCase):
    def test_reads_versioned_generation_and_surfaces_api_failure(self) -> None:
        encoded = base64.b64encode(
            json.dumps({"version": 1, "generation": 3}).encode("utf-8")
        ).decode("ascii")
        with patch.object(
            health,
            "gh_api",
            return_value={"encoding": "base64", "content": encoded},
        ) as request:
            self.assertEqual(
                3,
                health.remote_generation("example", "otelbot/state", "full-publish-needed.json"),
            )
        request.assert_called_once_with(
            "repos/open-telemetry/shared-workflows/contents/example/"
            "full-publish-needed.json?ref=otelbot%2Fstate%2Fexample"
        )
        with patch.object(
            health,
            "gh_api",
            side_effect=RuntimeError("rate limited"),
        ):
            with self.assertRaisesRegex(RuntimeError, "rate limited"):
                health.remote_generation("example", "otelbot/state", "full-publish-needed.json")

    def test_missing_generation_file_is_zero(self) -> None:
        errors = (
            GhNotFoundError("gh: Not Found (HTTP 404)"),
            RuntimeError("gh: No commit found for the ref state/example (HTTP 404)"),
        )
        for error in errors:
            with self.subTest(error=error), patch.object(
                health,
                "gh_api",
                side_effect=error,
            ):
                self.assertEqual(
                    0,
                    health.remote_generation(
                        "example",
                        "otelbot/state",
                        "full-publish-needed.json",
                    ),
                )

    def test_pending_full_publish_is_unhealthy_even_if_matrix_succeeded(self) -> None:
        with patch.object(health, "remote_generation", side_effect=[6, 5]):
            self.assertFalse(health.check_health(["example"], {"example"}, set()))

    def test_coalesced_job_is_healthy_after_replacement_delivers(self) -> None:
        with patch.object(health, "remote_generation", side_effect=[6, 6]):
            self.assertTrue(health.check_health(["example"], {"example"}, {"canary"}))

    def test_canceled_legacy_job_without_obligation_is_not_assumed_delivered(self) -> None:
        with patch.object(health, "remote_generation", return_value=0) as fetch:
            self.assertFalse(health.check_health(["example"], set(), {"stable"}))
            self.assertTrue(health.check_health(["example"], set(), set()))
            self.assertEqual(2, fetch.call_count)

    def test_health_reads_at_most_two_files_per_repository(self) -> None:
        with patch.object(health, "remote_generation", side_effect=[3, 3, 0]) as fetch:
            self.assertTrue(health.check_health(["canary", "stable"], {"canary"}, set()))
            self.assertEqual(3, fetch.call_count)
