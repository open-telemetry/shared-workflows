from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import drain_queue
from drain_queue import (
    DrainResult,
    GitHubAppTokenClient,
    WaveResult,
    drain_queue as run_drain,
    process_claim_wave,
    unresolved_acknowledgments,
)
from process_queue_batch import Claim


def claim(item_key: str, *, attempts: int = 0) -> Claim:
    return Claim(item_key, 1, "example", 1, "", attempts)


class DrainQueueTest(unittest.TestCase):
    def test_stops_without_claiming_when_initial_queue_is_empty(self) -> None:
        claim_wave = mock.Mock()
        process_wave = mock.Mock()

        result = run_drain([], 100, claim_wave, process_wave, now=lambda: 0)

        self.assertEqual(result, DrainResult(0, 0, "queue_empty", 0))
        claim_wave.assert_not_called()
        process_wave.assert_not_called()

    def test_continues_through_bounded_waves_until_claim_is_empty(self) -> None:
        processed: list[list[str]] = []
        queued = iter([[claim("example#pr:2")], []])

        result = run_drain(
            [claim("example#pr:1")],
            1_000,
            lambda _wave, _excluded: next(queued),
            lambda claims, _wave: (
                processed.append([item.item_key for item in claims])
                or WaveResult(0, ())
            ),
            now=lambda: 0,
        )

        self.assertEqual(
            processed,
            [["example#pr:1"], ["example#pr:2"]],
        )
        self.assertEqual(result, DrainResult(2, 0, "queue_empty", 2))

    def test_stops_before_claiming_past_the_safe_deadline(self) -> None:
        claim_wave = mock.Mock()

        result = run_drain(
            [claim("example#pr:1")],
            100,
            claim_wave,
            lambda _claims, _wave: WaveResult(0, ()),
            now=lambda: 100,
        )

        self.assertEqual(result, DrainResult(1, 0, "deadline", 1))
        claim_wave.assert_not_called()

    def test_continues_after_dead_letters_and_excludes_retries(self) -> None:
        exclusions: list[list[str]] = []
        queued = iter([[claim("example#pr:3")], []])

        def claim_wave(_wave: int, excluded: list[str]) -> list[Claim]:
            exclusions.append(excluded)
            return next(queued)

        def process_wave(_claims: list[Claim], wave: int) -> WaveResult:
            return WaveResult(1, ("example#pr:2",)) if wave == 1 else WaveResult(0, ())

        result = run_drain(
            [claim("example#pr:1"), claim("example#pr:2")],
            1_000,
            claim_wave,
            process_wave,
            now=lambda: 0,
        )

        self.assertEqual(exclusions, [["example#pr:2"], ["example#pr:2"]])
        self.assertEqual(result, DrainResult(3, 1, "queue_empty", 2))

    def test_stops_before_retry_exclusions_become_too_large(self) -> None:
        claim_wave = mock.Mock()

        result = run_drain(
            [claim("example#pr:1"), claim("example#pr:2")],
            1_000,
            claim_wave,
            lambda _claims, _wave: WaveResult(
                0,
                ("example#pr:1", "example#pr:2"),
            ),
            now=lambda: 0,
            maximum_exclusions=2,
        )

        self.assertEqual(result, DrainResult(2, 0, "exclusion_limit", 1))
        claim_wave.assert_not_called()

    def test_stops_before_retry_exclusions_exceed_the_byte_budget(self) -> None:
        claim_wave = mock.Mock()
        long_keys = ("a" * 500, "b" * 500)

        result = run_drain(
            [claim(item_key) for item_key in long_keys],
            1_000,
            claim_wave,
            lambda _claims, _wave: WaveResult(0, long_keys),
            now=lambda: 0,
            maximum_exclusion_bytes=800,
        )

        self.assertEqual(result, DrainResult(2, 0, "exclusion_limit", 1))
        claim_wave.assert_not_called()

    def test_main_claims_each_later_wave_with_limit_16(self) -> None:
        class Client:
            def __init__(self, _endpoint: str) -> None:
                self.calls: list[dict[str, object]] = []

            def call(self, action: str, **payload: object) -> dict[str, object]:
                self.calls.append({"action": action, **payload})
                return {"claims": []}

        client = Client("https://example.test/queue")

        def process_wave(*_args: object, **_kwargs: object) -> WaveResult:
            self.assertNotIn("PR_DASHBOARD_CLIENT_ID", os.environ)
            self.assertNotIn("PR_DASHBOARD_PRIVATE_KEY", os.environ)
            return WaveResult(0, ())

        with tempfile.TemporaryDirectory() as directory:
            claims_path = Path(directory) / "claims.json"
            claims_path.write_text(
                json.dumps(
                    [
                        {
                            "itemKey": "example#pr:1",
                            "claimGeneration": 1,
                            "repository": "example",
                            "prNumber": 1,
                            "attempts": 0,
                        }
                    ]
                ),
                encoding="utf-8",
            )
            argv = [
                "drain_queue.py",
                "--claims",
                str(claims_path),
                "--deadline",
                "2147483647",
                "--generation",
                "7",
                "--worker",
                "worker",
                "--endpoint",
                "https://example.test/queue",
            ]
            with (
                mock.patch.object(drain_queue, "QueueWorkerClient", return_value=client),
                mock.patch.object(drain_queue, "GitHubAppTokenClient") as token_client,
                mock.patch.object(
                    drain_queue,
                    "process_claim_wave",
                    side_effect=process_wave,
                ),
                mock.patch.object(sys, "argv", argv),
                mock.patch.dict(
                    os.environ,
                    {
                        "PR_DASHBOARD_CLIENT_ID": "client",
                        "PR_DASHBOARD_PRIVATE_KEY": "private-key",
                    },
                ),
            ):
                self.assertEqual(drain_queue.main(), 0)

        token_client.assert_called_once_with("client", "private-key")
        self.assertEqual(
            client.calls,
            [
                {
                    "action": "claim",
                    "generation": 7,
                    "workerId": "worker",
                    "limit": 16,
                    "excludeItemKeys": [],
                }
            ],
        )


class TokenClientTest(unittest.TestCase):
    def test_sends_credentials_only_through_helper_stdin(self) -> None:
        calls: list[dict[str, object]] = []

        def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append({"command": command, **kwargs})
            payload = json.loads(str(kwargs["input"]))
            stdout = (
                '{"token":"wave-token"}'
                if payload["operation"] == "mint"
                else '{"revoked":true}'
            )
            return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

        with mock.patch.dict(
            os.environ,
            {
                "PR_DASHBOARD_CLIENT_ID": "environment-client",
                "PR_DASHBOARD_PRIVATE_KEY": "environment-key",
            },
        ):
            client = GitHubAppTokenClient("client", "private-key", run=run)
            self.assertEqual(client.mint(["repo-a"]), "wave-token")
            client.revoke("wave-token")

        mint_payload = json.loads(str(calls[0]["input"]))
        self.assertEqual(mint_payload["privateKey"], "private-key")
        for call in calls:
            environment = call["env"]
            self.assertNotIn("PR_DASHBOARD_CLIENT_ID", environment)
            self.assertNotIn("PR_DASHBOARD_PRIVATE_KEY", environment)


class ProcessClaimWaveTest(unittest.TestCase):
    def test_dead_letters_exhausted_claim_when_token_creation_fails(self) -> None:
        class TokenClient:
            def mint(self, _repositories: list[str]) -> str:
                raise RuntimeError("token failed")

        class Client:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            def call(self, action: str, **payload: object) -> dict[str, object]:
                self.calls.append({"action": action, **payload})
                return {"status": "retry"}

        client = Client()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "token failed"):
                process_claim_wave(
                    [claim("example#pr:1", attempts=2)],
                    Path(directory) / "results.json",
                    client,
                    1,
                    "worker",
                    TokenClient(),
                )

        self.assertEqual(client.calls[0]["action"], "acknowledge")
        self.assertEqual(client.calls[0]["outcome"], "dead")

    def test_processes_with_scoped_token_and_reports_before_revocation(self) -> None:
        lifecycle: list[str] = []

        class TokenClient:
            def mint(self, repositories: list[str]) -> str:
                self.repositories = repositories
                return "wave-token"

            def revoke(self, token: str) -> None:
                lifecycle.append(f"revoke:{token}")

        token_client = TokenClient()
        results = [
            {
                "itemKey": "example#pr:1",
                "claimGeneration": 1,
                "outcome": "success",
            }
        ]

        def process(
            *_args: object,
            processor_env: dict[str, str],
            **_kwargs: object,
        ) -> tuple[dict[str, int], list[dict[str, object]]]:
            self.assertEqual(processor_env["GH_TOKEN"], "wave-token")
            self.assertNotIn("PR_DASHBOARD_PRIVATE_KEY", processor_env)
            return {"dead_letters": 0}, results

        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.object(drain_queue, "process_claims", side_effect=process),
        ):
            result = process_claim_wave(
                [claim("example#pr:1")],
                Path(directory) / "results.json",
                mock.Mock(),
                1,
                "worker",
                token_client,
                report_limits=lambda threshold, token: lifecycle.append(
                    f"report:{threshold}:{token}"
                ),
            )

        self.assertEqual(token_client.repositories, ["example"])
        self.assertEqual(result, WaveResult(0, ()))
        self.assertEqual(lifecycle, ["report:20:wave-token", "revoke:wave-token"])

    def test_only_unresolved_claims_receive_failure_acknowledgments(self) -> None:
        claims = [
            claim("example#pr:1"),
            claim("example#pr:2"),
            claim("example#pr:3", attempts=2),
        ]
        results = [{"itemKey": "example#pr:1", "outcome": "success"}]

        acknowledgments = unresolved_acknowledgments(
            claims,
            results,
            RuntimeError("processor stopped"),
        )

        self.assertEqual(
            [(item["itemKey"], item["outcome"]) for item in acknowledgments],
            [("example#pr:2", "retry"), ("example#pr:3", "dead")],
        )


if __name__ == "__main__":
    unittest.main()
