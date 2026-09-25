from __future__ import annotations

import contextlib
import io
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
    DASHBOARD_WORKFLOW_DISPATCH_URL,
    DashboardWorkflowDispatcher,
    DrainResult,
    GitHubAppTokenClient,
    WaveResult,
    drain_queue as run_drain,
    process_claim_wave,
    process_repository_claims,
    unresolved_acknowledgments,
)
from process_queue_batch import Claim


def claim(item_key: str, *, attempts: int = 0) -> Claim:
    return Claim(item_key, 1, "example", 1, "", attempts)


class DrainQueueTest(unittest.TestCase):
    def test_main_reports_invalid_canary_repositories_json(self) -> None:
        argv = [
            "drain_queue.py",
            "--claims",
            "claims.json",
            "--deadline",
            "1",
            "--generation",
            "1",
            "--worker",
            "worker",
            "--endpoint",
            "https://example.test/queue",
            "--canary-repositories-json",
            "{",
        ]
        stderr = io.StringIO()

        with (
            mock.patch.object(sys, "argv", argv),
            contextlib.redirect_stderr(stderr),
            self.assertRaises(SystemExit) as exit_context,
        ):
            drain_queue.main()

        self.assertEqual(exit_context.exception.code, 2)
        self.assertIn(
            "argument --canary-repositories-json: expected a JSON array of "
            'repository names, for example ["opentelemetry-java-instrumentation"]',
            stderr.getvalue(),
        )
        self.assertNotIn("Traceback", stderr.getvalue())

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
                "--canary-repositories-json",
                '["example"]',
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
                        "GITHUB_TOKEN": "actions-token",
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


class WorkflowDispatcherTest(unittest.TestCase):
    def test_resolves_a_head_to_its_open_pull_request(self) -> None:
        class Response(io.BytesIO):
            status = 200

            def __enter__(self) -> Response:
                return self

            def __exit__(self, *_args: object) -> None:
                pass

        def open_request(_request: object, timeout: int) -> Response:
            self.assertEqual(timeout, 30)
            return Response(
                json.dumps(
                    [
                        {"number": 9, "state": "closed", "head": {"sha": "a" * 40}},
                        {"number": 7, "state": "open", "head": {"sha": "a" * 40}},
                    ]
                ).encode()
            )

        dispatcher = DashboardWorkflowDispatcher(
            "actions-token",
            opener=open_request,
        )

        with mock.patch.object(drain_queue, "search_open_head_pull_request") as search:
            self.assertEqual(
                dispatcher.resolve_head("stable", "a" * 40, "stable-token"),
                7,
            )
            search.assert_not_called()

    def test_resolves_a_fork_head_with_no_commit_association(self) -> None:
        class Response(io.BytesIO):
            status = 200

        def open_request(_request: object, timeout: int) -> Response:
            self.assertEqual(timeout, 30)
            return Response(b"[]")

        dispatcher = DashboardWorkflowDispatcher("actions-token", opener=open_request)
        with mock.patch.object(
            drain_queue, "search_open_head_pull_request", return_value=20254
        ) as search:
            self.assertEqual(
                dispatcher.resolve_head("example", "a" * 40, "stable-token"),
                20254,
            )
            search.assert_called_once_with(
                "open-telemetry/example", "a" * 40, token="stable-token"
            )

    def test_dispatches_the_coalesced_claim_to_the_targeted_workflow(self) -> None:
        requests: list[tuple[object, int]] = []

        class Response:
            status = 204

            def __enter__(self) -> Response:
                return self

            def __exit__(self, *_args: object) -> None:
                pass

        def open_request(request: object, timeout: int) -> Response:
            requests.append((request, timeout))
            return Response()

        dispatcher = DashboardWorkflowDispatcher(
            "actions-token",
            opener=open_request,
        )
        dispatcher.dispatch(
            Claim(
                "stable#pr:7",
                3,
                "stable",
                7,
                "",
                0,
                ("pull_request_review", "status"),
            )
        )

        request, timeout = requests[0]
        self.assertEqual(request.full_url, DASHBOARD_WORKFLOW_DISPATCH_URL)
        self.assertEqual(timeout, 30)
        self.assertEqual(
            json.loads(request.data),
            {
                "ref": "main",
                "inputs": {
                    "repository": "stable",
                    "pr_number": "7",
                    "head_sha": "",
                    "trigger_event": "pull_request_review",
                },
            },
        )
        self.assertEqual(request.get_header("Authorization"), "Bearer actions-token")


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
    def test_dispatches_stable_claim_without_minting_a_target_token(self) -> None:
        client = mock.Mock()
        client.call.return_value = {"dispatcher": True}
        token_client = mock.Mock()
        dispatched: list[str] = []
        stable = Claim(
            "stable#pr:7",
            3,
            "stable",
            7,
            "",
            0,
            ("pull_request_review",),
        )

        with tempfile.TemporaryDirectory() as directory:
            result = process_claim_wave(
                [stable],
                Path(directory) / "results.json",
                client,
                1,
                "worker",
                token_client,
                canary_repositories=frozenset({"canary"}),
                configured_repositories=frozenset({"stable"}),
                resolve_stable_head=mock.Mock(),
                dispatch_stable=lambda item: dispatched.append(item.item_key),
                report_limits=lambda *_args, **_kwargs: None,
            )

        self.assertEqual(dispatched, ["stable#pr:7"])
        token_client.mint.assert_not_called()
        self.assertEqual(result, WaveResult(0, ()))
        acknowledgment = next(
            call
            for call in client.call.call_args_list
            if call.args == ("acknowledge",)
        )
        self.assertEqual(acknowledgment.kwargs["itemKey"], "stable#pr:7")
        self.assertEqual(acknowledgment.kwargs["outcome"], "success")

    def test_coalesces_stable_head_and_pr_claims_before_dispatch(self) -> None:
        client = mock.Mock()
        client.call.return_value = {"dispatcher": True}
        dispatched: list[Claim] = []
        token_client = mock.Mock()
        token_client.mint.return_value = "stable-token"
        direct = Claim(
            "stable#pr:7",
            3,
            "stable",
            7,
            "",
            0,
            ("pull_request",),
        )
        head = Claim(
            "stable#head:abc",
            4,
            "stable",
            None,
            "a" * 40,
            0,
            ("status",),
        )
        resolve_stable_head = mock.Mock(return_value=7)

        with tempfile.TemporaryDirectory() as directory:
            result = process_claim_wave(
                [direct, head],
                Path(directory) / "results.json",
                client,
                1,
                "worker",
                token_client,
                canary_repositories=frozenset({"canary"}),
                configured_repositories=frozenset({"stable"}),
                resolve_stable_head=resolve_stable_head,
                dispatch_stable=dispatched.append,
                report_limits=lambda *_args, **_kwargs: None,
            )

        resolve_stable_head.assert_called_once_with(
            "stable",
            "a" * 40,
            "stable-token",
        )
        token_client.mint.assert_called_once_with(["stable"])
        token_client.revoke.assert_called_once_with("stable-token")
        self.assertEqual(len(dispatched), 1)
        self.assertEqual(dispatched[0].pr_number, 7)
        self.assertEqual(dispatched[0].head_sha, "")
        self.assertEqual(
            dispatched[0].trigger_events,
            ("pull_request", "status"),
        )
        self.assertEqual(result, WaveResult(0, ()))
        acknowledgments = [
            call.kwargs
            for call in client.call.call_args_list
            if call.args == ("acknowledge",)
        ]
        self.assertEqual(
            [(item["itemKey"], item["outcome"]) for item in acknowledgments],
            [("stable#pr:7", "success"), ("stable#head:abc", "success")],
        )

    def test_dead_letters_unconfigured_stable_claim_without_dispatching(self) -> None:
        client = mock.Mock()
        client.call.return_value = {"dispatcher": True}
        dispatch_stable = mock.Mock()

        with tempfile.TemporaryDirectory() as directory:
            result = process_claim_wave(
                [Claim("removed#pr:7", 3, "removed", 7, "", 0)],
                Path(directory) / "results.json",
                client,
                1,
                "worker",
                mock.Mock(),
                canary_repositories=frozenset({"canary"}),
                configured_repositories=frozenset({"stable"}),
                resolve_stable_head=mock.Mock(),
                dispatch_stable=dispatch_stable,
                report_limits=lambda *_args, **_kwargs: None,
            )

        dispatch_stable.assert_not_called()
        self.assertEqual(result, WaveResult(1, ()))
        acknowledgment = next(
            call
            for call in client.call.call_args_list
            if call.args == ("acknowledge",)
        )
        self.assertEqual(acknowledgment.kwargs["outcome"], "dead")
        self.assertEqual(
            acknowledgment.kwargs["error"],
            "repository is not configured: removed",
        )

    def test_retries_stable_claim_when_workflow_dispatch_fails(self) -> None:
        client = mock.Mock()
        client.call.return_value = {"dispatcher": True}

        def fail_dispatch(_claim: Claim) -> None:
            raise RuntimeError("dispatch unavailable")

        with tempfile.TemporaryDirectory() as directory:
            result = process_claim_wave(
                [Claim("stable#pr:7", 3, "stable", 7, "", 0)],
                Path(directory) / "results.json",
                client,
                1,
                "worker",
                mock.Mock(),
                canary_repositories=frozenset({"canary"}),
                configured_repositories=frozenset({"stable"}),
                resolve_stable_head=mock.Mock(),
                dispatch_stable=fail_dispatch,
                report_limits=lambda *_args, **_kwargs: None,
            )

        self.assertEqual(result, WaveResult(0, ("stable#pr:7",)))
        acknowledgment = next(
            call
            for call in client.call.call_args_list
            if call.args == ("acknowledge",)
        )
        self.assertEqual(acknowledgment.kwargs["outcome"], "retry")
        self.assertIn("dispatch unavailable", acknowledgment.kwargs["error"])

    def test_stops_stable_dispatches_when_lease_is_lost(self) -> None:
        client = mock.Mock()
        client.call.return_value = {"dispatcher": True}
        monitor = mock.Mock(spec=drain_queue.LeaseMonitor)
        monitor.assert_valid.side_effect = [
            None,
            RuntimeError("queue lease heartbeat failed: lease expired"),
        ]
        dispatch_stable = mock.Mock()
        first = Claim("stable#pr:7", 3, "stable", 7, "", 0)
        second = Claim("stable#pr:8", 3, "stable", 8, "", 0)

        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.object(drain_queue, "LeaseMonitor", return_value=monitor),
        ):
            result = process_claim_wave(
                [first, second],
                Path(directory) / "results.json",
                client,
                1,
                "worker",
                mock.Mock(),
                canary_repositories=frozenset({"canary"}),
                configured_repositories=frozenset({"stable"}),
                resolve_stable_head=mock.Mock(),
                dispatch_stable=dispatch_stable,
                report_limits=lambda *_args, **_kwargs: None,
            )

        dispatch_stable.assert_called_once_with(first)
        self.assertEqual(result, WaveResult(0, ("stable#pr:8",)))
        acknowledgments = [
            call.kwargs
            for call in client.call.call_args_list
            if call.args == ("acknowledge",)
        ]
        self.assertEqual(
            [(item["itemKey"], item["outcome"]) for item in acknowledgments],
            [("stable#pr:7", "success"), ("stable#pr:8", "retry")],
        )
        self.assertIn("lease expired", acknowledgments[1]["error"])

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
                process_repository_claims(
                    [claim("example#pr:1", attempts=2)],
                    Path(directory) / "results.json",
                    client,
                    1,
                    "worker",
                    TokenClient(),
                    lease_monitor=mock.Mock(),
                )

        self.assertEqual(client.calls[0]["action"], "acknowledge")
        self.assertEqual(client.calls[0]["outcome"], "dead")

    def test_processes_other_repository_when_one_token_cannot_be_minted(self) -> None:
        class TokenClient:
            def __init__(self) -> None:
                self.minted: list[str] = []

            def mint(self, repositories: list[str]) -> str:
                repository = repositories[0]
                self.minted.append(repository)
                if repository == "removed":
                    raise RuntimeError("repository is not accessible")
                return f"{repository}-token"

            def revoke(self, _token: str) -> None:
                pass

        class Client:
            def __init__(self) -> None:
                self.acknowledged: list[str] = []
                self.heartbeat_calls = 0

            def call(self, action: str, **payload: object) -> dict[str, object]:
                if action == "heartbeat":
                    self.heartbeat_calls += 1
                    return {"dispatcher": True}
                if action == "acknowledge":
                    self.acknowledged.append(str(payload["itemKey"]))
                return {"status": "retry"}

        processed: list[str] = []

        def process(
            claims: list[Claim],
            *_args: object,
            processor_env: dict[str, str],
            **_kwargs: object,
        ) -> tuple[dict[str, int], list[dict[str, object]]]:
            repository = claims[0].repository
            processed.append(repository)
            self.assertEqual(processor_env["GH_TOKEN"], f"{repository}-token")
            return (
                {"dead_letters": 0},
                [
                    {
                        "itemKey": claims[0].item_key,
                        "claimGeneration": 1,
                        "outcome": "success",
                    }
                ],
            )

        client = Client()
        token_client = TokenClient()
        claims = [
            Claim("valid#pr:1", 1, "valid", 1, "", 0),
            Claim("removed#pr:1", 1, "removed", 1, "", 0),
        ]
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.object(drain_queue, "process_claims", side_effect=process),
            self.assertRaisesRegex(RuntimeError, "removed: repository is not accessible"),
        ):
            process_claim_wave(
                claims,
                Path(directory) / "results.json",
                client,
                1,
                "worker",
                token_client,
                canary_repositories=frozenset({"removed", "valid"}),
                configured_repositories=frozenset({"removed", "valid"}),
                resolve_stable_head=mock.Mock(),
                dispatch_stable=mock.Mock(),
                report_limits=lambda *_args, **_kwargs: None,
            )

        self.assertEqual(sorted(token_client.minted), ["removed", "valid"])
        self.assertEqual(processed, ["valid"])
        self.assertEqual(client.acknowledged, ["removed#pr:1"])
        self.assertEqual(client.heartbeat_calls, 1)

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
            client = mock.Mock()
            client.call.return_value = {"dispatcher": True}
            result = process_claim_wave(
                [claim("example#pr:1")],
                Path(directory) / "results.json",
                client,
                1,
                "worker",
                token_client,
                canary_repositories=frozenset({"example"}),
                configured_repositories=frozenset({"example"}),
                resolve_stable_head=mock.Mock(),
                dispatch_stable=mock.Mock(),
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
