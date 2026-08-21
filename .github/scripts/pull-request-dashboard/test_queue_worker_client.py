from __future__ import annotations

import contextlib
import http.client
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import queue_worker_client
from queue_worker_client import (
    MAX_ATTEMPTS,
    OIDC_AUDIENCE,
    QueueWorkerClient,
    acknowledge_all,
    is_transient_error,
)


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

    def test_selects_only_expected_transient_failures(self) -> None:
        for status in (502, 503, 504):
            with self.subTest(status=status):
                self.assertTrue(
                    is_transient_error(
                        urllib.error.HTTPError(
                            "https://example.test/worker",
                            status,
                            "transient",
                            {},
                            None,
                        )
                    )
                )
        for status in (400, 401, 409, 429, 500):
            with self.subTest(status=status):
                self.assertFalse(
                    is_transient_error(
                        urllib.error.HTTPError(
                            "https://example.test/worker",
                            status,
                            "not transient",
                            {},
                            None,
                        )
                    )
                )
        for exc_type in (
            ConnectionResetError,
            ConnectionAbortedError,
            ConnectionRefusedError,
            BrokenPipeError,
        ):
            with self.subTest(exc_type=exc_type.__name__):
                self.assertTrue(
                    is_transient_error(
                        urllib.error.URLError(exc_type("transient"))
                    )
                )
        self.assertFalse(is_transient_error(urllib.error.URLError("name resolution failed")))

    def test_retries_transient_worker_failures_with_capped_jittered_backoff(self) -> None:
        requests = []
        sleeps = []
        worker_attempts = 0

        def opener(request, timeout):
            nonlocal worker_attempts
            requests.append((request, timeout))
            if "token.actions.test" in request.full_url:
                return Response(json.dumps({"value": "oidc-token"}).encode())
            worker_attempts += 1
            if worker_attempts < MAX_ATTEMPTS:
                raise urllib.error.HTTPError(
                    request.full_url,
                    503,
                    "unavailable",
                    {},
                    None,
                )
            return Response(json.dumps({"status": "removed"}).encode())

        client = QueueWorkerClient(
            "https://example.test/worker",
            oidc_request_url="https://token.actions.test/request",
            oidc_request_token="request-token",
            opener=opener,
            sleeper=sleeps.append,
            jitter=lambda _minimum, maximum: maximum,
            operation_id_factory=lambda: "operation-1",
        )

        self.assertEqual(
            client.call(
                "acknowledge",
                generation=1,
                workerId="worker",
                itemKey="example#pr:1",
                claimGeneration=1,
                outcome="success",
            ),
            {"status": "removed"},
        )
        self.assertEqual(worker_attempts, MAX_ATTEMPTS)
        self.assertEqual(sleeps, [1.0, 2.0, 4.0])
        self.assertEqual(len(requests), MAX_ATTEMPTS * 2)
        worker_bodies = [
            json.loads(request.data)
            for request, _timeout in requests
            if request.full_url == "https://example.test/worker"
        ]
        self.assertEqual(
            {body["operationId"] for body in worker_bodies if body["action"] == "acknowledge"},
            {"operation-1"},
        )

    def test_skips_operation_id_factory_when_caller_supplies_id(self) -> None:
        def fail_factory() -> str:
            raise RuntimeError("factory must not be called")

        def opener(request, timeout):
            if "token.actions.test" in request.full_url:
                return Response(json.dumps({"value": "oidc-token"}).encode())
            return Response(json.dumps({"status": "removed"}).encode())

        client = QueueWorkerClient(
            "https://example.test/worker",
            oidc_request_url="https://token.actions.test/request",
            oidc_request_token="request-token",
            opener=opener,
            operation_id_factory=fail_factory,
        )

        result = client.call(
            "acknowledge",
            generation=1,
            workerId="worker",
            itemKey="example#pr:1",
            claimGeneration=1,
            outcome="success",
            operationId="caller-supplied",
        )
        self.assertEqual(result, {"status": "removed"})

    def test_stops_after_the_bounded_attempt_count(self) -> None:
        worker_attempts = 0

        def opener(request, timeout):
            nonlocal worker_attempts
            if "token.actions.test" in request.full_url:
                return Response(json.dumps({"value": "oidc-token"}).encode())
            worker_attempts += 1
            raise TimeoutError("timed out")

        client = QueueWorkerClient(
            "https://example.test/worker",
            oidc_request_url="https://token.actions.test/request",
            oidc_request_token="request-token",
            opener=opener,
            sleeper=lambda _duration: None,
        )

        with self.assertRaises(TimeoutError):
            client.call("finish", generation=1, workerId="worker")
        self.assertEqual(worker_attempts, MAX_ATTEMPTS)

    def test_retries_transient_oidc_transport_failures(self) -> None:
        token_attempts = 0
        worker_attempts = 0

        def opener(request, timeout):
            nonlocal token_attempts, worker_attempts
            if "token.actions.test" in request.full_url:
                token_attempts += 1
                if token_attempts == 1:
                    raise http.client.IncompleteRead(b"partial")
                return Response(json.dumps({"value": "oidc-token"}).encode())
            worker_attempts += 1
            return Response(json.dumps({"dispatcher": True}).encode())

        client = QueueWorkerClient(
            "https://example.test/worker",
            oidc_request_url="https://token.actions.test/request",
            oidc_request_token="request-token",
            opener=opener,
            sleeper=lambda _duration: None,
        )

        self.assertEqual(
            client.call("heartbeat", generation=1, workerId="worker"),
            {"dispatcher": True},
        )
        self.assertEqual(token_attempts, 2)
        self.assertEqual(worker_attempts, 1)

    def test_does_not_retry_non_transient_http_failures(self) -> None:
        worker_attempts = 0

        def opener(request, timeout):
            nonlocal worker_attempts
            if "token.actions.test" in request.full_url:
                return Response(json.dumps({"value": "oidc-token"}).encode())
            worker_attempts += 1
            raise urllib.error.HTTPError(request.full_url, 500, "failed", {}, None)

        client = QueueWorkerClient(
            "https://example.test/worker",
            oidc_request_url="https://token.actions.test/request",
            oidc_request_token="request-token",
            opener=opener,
            sleeper=lambda _duration: self.fail("unexpected retry"),
        )

        with self.assertRaises(urllib.error.HTTPError):
            client.call("acknowledge", generation=1, workerId="worker")
        self.assertEqual(worker_attempts, 1)

    def test_does_not_retry_non_repeatable_actions(self) -> None:
        worker_attempts = 0

        def opener(request, timeout):
            nonlocal worker_attempts
            if "token.actions.test" in request.full_url:
                return Response(json.dumps({"value": "oidc-token"}).encode())
            worker_attempts += 1
            raise ConnectionResetError("connection reset")

        client = QueueWorkerClient(
            "https://example.test/worker",
            oidc_request_url="https://token.actions.test/request",
            oidc_request_token="request-token",
            opener=opener,
            sleeper=lambda _duration: self.fail("unexpected retry"),
        )

        with self.assertRaises(ConnectionResetError):
            client.call("claim", generation=1, workerId="worker")
        self.assertEqual(worker_attempts, 1)


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
