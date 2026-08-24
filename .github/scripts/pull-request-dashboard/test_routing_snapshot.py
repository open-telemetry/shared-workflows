from __future__ import annotations

from dataclasses import FrozenInstanceError
import unittest
from unittest.mock import patch

from routing_snapshot import build_routing_snapshot, fetch_routing_snapshot


def representative_raw() -> dict:
    return {
        "pr": {
            "id": "PR_node",
            "state": "OPEN",
            "isDraft": False,
            "headRefOid": "0123456789abcdef",
            "baseRefName": "main",
            "title": "Routing snapshot",
            "body": "Line one\r\nLine two",
            "mergeable": "CONFLICTING",
            "mergeStateStatus": "DIRTY",
        },
        "checks": None,
        "issue_comments": [
            {
                "id": 1,
                "user": {"login": "opentelemetry-pr-dashboard[bot]"},
                "body": "ignored",
            },
            {"id": 2, "user": {"login": "alice"}, "body": "current"},
        ],
        "review_comments": [{"id": 3, "body": "inline"}],
        "reviews": [{"id": 4, "state": "COMMENTED"}],
        "review_requests": [{"__typename": "User", "login": "bob"}],
        "review_threads": [{"isResolved": False, "isOutdated": False}],
    }


class RoutingSnapshotTest(unittest.TestCase):
    def test_preserves_characterized_fingerprints_and_component_digests(self) -> None:
        snapshot = build_routing_snapshot(representative_raw())

        self.assertEqual(
            "fc3243ccd8b9169e27ec128f80a7e46bacb8bc4a9a2b339d34c4033cc2115db2",
            snapshot.routing_input_fingerprint,
        )
        self.assertEqual(
            "595afd8e310dbfad434cb30f0cf440fad6474410b16bd92b36a1fecf3537fa99",
            snapshot.copilot_request_fingerprint,
        )
        self.assertEqual(
            {
                "base_branch": "e4a9e4d8baa71a9d",
                "conflicts": "6c76a9331e7f5ff9",
                "issue_comments": "d1a544630abe1668",
                "pr_text": "0dee730982f93bdc",
                "review_comments": "a1cf7f0fc24a4306",
                "review_requests": "82e676e616a4fbaa",
                "reviews": "1d365633abded53c",
                "review_threads": "7f11a46599095171",
            },
            snapshot.copilot_request_component_digests,
        )

    def test_exposes_delivery_state_and_is_frozen(self) -> None:
        raw = representative_raw()
        snapshot = build_routing_snapshot(raw)

        self.assertEqual("OPEN", snapshot.state)
        self.assertFalse(snapshot.is_draft)
        self.assertEqual("PR_node", snapshot.node_id)
        self.assertEqual("0123456789abcdef", snapshot.head_sha)
        self.assertIsNone(snapshot.checks)
        self.assertEqual(raw["review_requests"], snapshot.review_requests)
        self.assertEqual(raw["review_threads"], snapshot.review_threads)
        with self.assertRaises(FrozenInstanceError):
            snapshot.state = "CLOSED"  # type: ignore[misc]

    def test_dashboard_fingerprint_ignores_dashboard_comments(self) -> None:
        raw = representative_raw()
        baseline = build_routing_snapshot(raw).routing_input_fingerprint

        raw["issue_comments"][0]["body"] = "updated dashboard status"

        self.assertEqual(
            baseline,
            build_routing_snapshot(raw).routing_input_fingerprint,
        )

    def test_dashboard_fingerprint_tracks_checks_but_copilot_does_not(self) -> None:
        raw = representative_raw()
        raw["checks"] = [{"name": "build", "bucket": "fail"}]
        baseline = build_routing_snapshot(raw)

        raw["checks"][0]["bucket"] = "pass"
        updated = build_routing_snapshot(raw)

        self.assertNotEqual(
            baseline.routing_input_fingerprint,
            updated.routing_input_fingerprint,
        )
        self.assertEqual(
            baseline.copilot_request_fingerprint,
            updated.copilot_request_fingerprint,
        )

    def test_fingerprints_track_review_requests_pr_text_base_and_conflicts(self) -> None:
        changes = (
            lambda raw: raw["review_requests"].append(
                {"__typename": "Team", "slug": "maintainers"}
            ),
            lambda raw: raw["pr"].update(title="Updated title"),
            lambda raw: raw["pr"].update(body="Updated body"),
            lambda raw: raw["pr"].update(baseRefName="release"),
            lambda raw: raw["pr"].update(
                mergeable="MERGEABLE",
                mergeStateStatus="CLEAN",
            ),
        )
        for change in changes:
            with self.subTest(change=change):
                raw = representative_raw()
                baseline = build_routing_snapshot(raw)
                change(raw)
                updated = build_routing_snapshot(raw)
                self.assertNotEqual(
                    baseline.routing_input_fingerprint,
                    updated.routing_input_fingerprint,
                )
                self.assertNotEqual(
                    baseline.copilot_request_fingerprint,
                    updated.copilot_request_fingerprint,
                )

    def test_normalizes_crlf_in_pr_body(self) -> None:
        crlf = representative_raw()
        lf = representative_raw()
        lf["pr"]["body"] = "Line one\nLine two"

        self.assertEqual(
            build_routing_snapshot(crlf).routing_input_fingerprint,
            build_routing_snapshot(lf).routing_input_fingerprint,
        )

    def test_preserves_unavailable_checks(self) -> None:
        unavailable = representative_raw()
        available = representative_raw()
        available["checks"] = []

        self.assertIsNone(build_routing_snapshot(unavailable).checks)
        self.assertEqual([], build_routing_snapshot(available).checks)
        self.assertNotEqual(
            build_routing_snapshot(unavailable).routing_input_fingerprint,
            build_routing_snapshot(available).routing_input_fingerprint,
        )

    @patch("routing_snapshot.fetch_pr_routing_raw")
    def test_fetches_and_builds_one_snapshot(self, fetch_raw) -> None:
        fetch_raw.return_value = representative_raw()

        snapshot = fetch_routing_snapshot("open-telemetry/example", 7)

        self.assertEqual("PR_node", snapshot.node_id)
        fetch_raw.assert_called_once_with(
            "open-telemetry/example",
            "open-telemetry",
            "example",
            7,
        )


if __name__ == "__main__":
    unittest.main()
