from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import queue_worker_client
from queue_worker_client import OIDC_AUDIENCE, QueueWorkerClient, acknowledge_all


class Response(io.BytesIO):
    def __enter__(self) -> Response:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class QueueWorkerClientTest(unittest.TestCase):
    def test_requests_a_fresh_oidc_token_before_the_worker_call(self) -> None:
        requests = []
        responses = [
            Response(json.dumps({"value": "oidc-token"}).encode()),
            Response(json.dumps({"activated": True}).encode()),
        ]

        def opener(request, timeout):
            requests.append((request, timeout))
            return responses.pop(0)

        client = QueueWorkerClient(
            "https://example.test/worker",
            oidc_request_url="https://token.actions.test/request",
            oidc_request_token="request-token",
            opener=opener,
        )
        self.assertEqual(
            client.call("activate", generation=1, workerId="worker"),
            {"activated": True},
        )

        oidc_request = requests[0][0]
        self.assertIn(f"audience={OIDC_AUDIENCE}", oidc_request.full_url)
        self.assertEqual(
            oidc_request.get_header("Authorization"),
            "Bearer request-token",
        )
        worker_request = requests[1][0]
        self.assertEqual(
            worker_request.get_header("Authorization"),
            "Bearer oidc-token",
        )
        self.assertEqual(
            json.loads(worker_request.data),
            {"action": "activate", "generation": 1, "workerId": "worker"},
        )

    def test_requires_https_for_the_worker_endpoint(self) -> None:
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            QueueWorkerClient(
                "http://example.test/worker",
                oidc_request_url="https://token.actions.test/request",
                oidc_request_token="request-token",
            )


class RecordingClient:
    def __init__(self, failures: set[str] | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self.failures = failures or set()

    def call(self, action: str, **payload: object) -> dict[str, object]:
        self.calls.append({"action": action, **payload})
        if payload.get("itemKey") in self.failures:
            raise RuntimeError("stale acknowledgment")
        return {"status": "removed"}


class AcknowledgeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.results = Path(self.directory.name) / "results.json"

    def write_results(self, item_keys: list[str]) -> None:
        self.results.write_text(
            json.dumps(
                [
                    {
                        "itemKey": item_key,
                        "claimGeneration": 1,
                        "outcome": "success",
                        "error": "",
                        "retryAfterMs": 0,
                    }
                    for item_key in item_keys
                ]
            ),
            encoding="utf-8",
        )

    def test_reports_the_acknowledged_count(self) -> None:
        client = RecordingClient()
        self.write_results(["example#pr:1", "example#pr:2"])

        self.assertEqual(
            acknowledge_all(client, self.results, {"generation": 3, "workerId": "w"}),
            {"acknowledged": 2},
        )
        self.assertEqual([call["itemKey"] for call in client.calls], ["example#pr:1", "example#pr:2"])

    def test_one_rejected_item_does_not_strand_the_rest(self) -> None:
        client = RecordingClient(failures={"example#pr:1"})
        self.write_results(["example#pr:1", "example#pr:2", "example#pr:3"])

        with self.assertRaisesRegex(RuntimeError, "1 of 3 items"):
            acknowledge_all(client, self.results, {"generation": 3, "workerId": "w"})
        self.assertEqual(
            [call["itemKey"] for call in client.calls],
            ["example#pr:1", "example#pr:2", "example#pr:3"],
        )


class MainTest(unittest.TestCase):
    def test_acknowledge_command_succeeds(self) -> None:
        client = RecordingClient()
        with tempfile.TemporaryDirectory() as directory:
            results = Path(directory) / "results.json"
            results.write_text(
                json.dumps(
                    [{"itemKey": "example#pr:1", "claimGeneration": 1, "outcome": "success"}]
                ),
                encoding="utf-8",
            )
            argv = [
                "queue_worker_client.py",
                "--endpoint",
                "https://example.test/worker",
                "--generation",
                "4",
                "--worker-id",
                "worker",
                "acknowledge",
                "--results",
                str(results),
            ]
            with (
                mock.patch.object(queue_worker_client, "QueueWorkerClient", lambda *_a, **_k: client),
                mock.patch.object(sys, "argv", argv),
                contextlib.redirect_stdout(io.StringIO()) as stdout,
            ):
                self.assertEqual(queue_worker_client.main(), 0)
        self.assertEqual(json.loads(stdout.getvalue()), {"acknowledged": 1})


if __name__ == "__main__":
    unittest.main()
