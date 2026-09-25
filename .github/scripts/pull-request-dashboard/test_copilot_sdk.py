from __future__ import annotations

import asyncio
import io
import os
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from classification_execution import CopilotSdkModelRunner, ModelRunRequest
from classification_policy import CLASSIFIER_SYSTEM_PROMPT, RawModelResponse


class CopilotSdkModelRunnerTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.sessions = []
        self.client = MagicMock()
        self.client.stop = AsyncMock()
        self.client.force_stop = AsyncMock()
        self.client.create_session = AsyncMock(side_effect=self.create_session)
        factory = patch("classification_execution.CopilotClient", return_value=self.client)
        self.factory = factory.start()
        self.addCleanup(factory.stop)
        env = patch("classification_execution.os.environ", {"COPILOT_GITHUB_TOKEN": "copilot-token"})
        env.start()
        self.addCleanup(env.stop)

    def create_session(self, **_kwargs):
        session = SimpleNamespace(
            send_and_wait=AsyncMock(
                return_value=SimpleNamespace(data=SimpleNamespace(content='{"items":[]}'))
            ),
            abort=AsyncMock(),
            disconnect=AsyncMock(),
        )
        self.sessions.append(session)
        return session

    async def test_unused_runner_does_not_start_client_or_require_credentials(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            async with CopilotSdkModelRunner():
                pass
        self.factory.assert_not_called()

    async def test_client_is_shared_but_each_call_has_a_fresh_session(self) -> None:
        async with CopilotSdkModelRunner() as runner:
            results = await asyncio.gather(*(
                runner.run(ModelRunRequest(f"batch-{index}", "model"))
                for index in range(10)
            ))
            self.client.stop.assert_not_awaited()

        self.assertEqual([RawModelResponse(0, '{"items":[]}', "")] * 10, results)
        self.factory.assert_called_once()
        self.assertEqual(10, self.client.create_session.await_count)
        for index, session in enumerate(self.sessions):
            session.send_and_wait.assert_awaited_once_with(f"batch-{index}", timeout=180)
            session.disconnect.assert_awaited_once()
            session.abort.assert_not_awaited()
        self.client.stop.assert_awaited_once()
        for call in self.client.create_session.call_args_list:
            self.assertEqual(
                {
                    "model": "model",
                    "available_tools": [],
                    "system_message": {"mode": "replace", "content": CLASSIFIER_SYSTEM_PROMPT},
                    "enable_session_telemetry": True,
                },
                call.kwargs,
            )

    async def test_environment_is_isolated_and_telemetry_flushes_before_cleanup(self) -> None:
        def flush():
            env = self.factory.call_args.kwargs["env"]
            Path(env["COPILOT_OTEL_FILE_EXPORTER_PATH"]).write_text(
                '{"event":"flushed"}\n', encoding="utf-8"
            )

        self.client.stop.side_effect = flush
        stderr = io.StringIO()
        with (
            patch.dict(os.environ, {
                "GH_TOKEN": "app-token",
                "PR_DASHBOARD_PRIVATE_KEY": "private-key",
                "SLACK_WEBHOOK_URL": "slack-secret",
                "COPILOT_CLI_PATH": "ambient-runtime",
                "COPILOT_OTEL_EXPORTER_TYPE": "otlp-http",
                "HOME": "ambient-home",
                "PATH": "executable-path",
            }),
            redirect_stderr(stderr),
        ):
            async with CopilotSdkModelRunner() as runner:
                await runner.run(ModelRunRequest("untrusted input", "model"))
                config = self.factory.call_args.kwargs
                directory = Path(config["base_directory"])
                self.assertTrue(directory.exists())

        self.assertFalse(directory.exists())
        self.assertEqual("empty", config["mode"])
        self.assertEqual(str(directory), config["working_directory"])
        self.assertEqual("copilot-token", config["github_token"])
        self.assertFalse(config["use_logged_in_user"])
        self.assertEqual(
            {
                "PATH": "executable-path",
                "CI": "true",
                "HOME": str(directory),
                "COPILOT_GITHUB_TOKEN": "copilot-token",
                "COPILOT_OTEL_EXPORTER_TYPE": "file",
                "COPILOT_OTEL_FILE_EXPORTER_PATH": str(directory / "copilot-otel.jsonl"),
            },
            config["env"],
        )
        self.assertIn('{"event":"flushed"}', stderr.getvalue())
        self.assertEqual(1, stderr.getvalue().count("--- BEGIN COPILOT OTEL JSONL ---"))

    async def test_actions_auth_uses_workflow_token_not_app_token(self) -> None:
        with patch.dict(os.environ, {
            "GITHUB_ACTIONS": "true", "GITHUB_TOKEN": "workflow-token", "GH_TOKEN": "app-token",
        }, clear=True):
            async with CopilotSdkModelRunner() as runner:
                await runner.run(ModelRunRequest("prompt", "model"))
        config = self.factory.call_args.kwargs
        self.assertIsNone(config["github_token"])
        self.assertTrue(config["use_logged_in_user"])
        self.assertEqual("workflow-token", config["env"]["COPILOT_GITHUB_TOKEN"])
        self.assertEqual("workflow-token", config["env"]["GITHUB_TOKEN"])
        self.assertEqual("true", config["env"]["GITHUB_ACTIONS"])
        self.assertNotIn("GH_TOKEN", config["env"])

    async def test_missing_token_fails_explicitly(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            async with CopilotSdkModelRunner() as runner:
                with self.assertRaisesRegex(RuntimeError, "requires a GitHub token"):
                    await runner.run(ModelRunRequest("prompt", "model"))
        self.factory.assert_not_called()

    async def test_empty_response_and_session_error_do_not_poison_next_batch(self) -> None:
        for response in (None, SimpleNamespace(data=SimpleNamespace(content=" \n"))):
            with self.subTest(response=response):
                session = self.create_session()
                session.send_and_wait.return_value = response
                self.client.create_session.side_effect = [session, self.create_session()]
                async with CopilotSdkModelRunner() as runner:
                    stderr = io.StringIO()
                    with redirect_stderr(stderr):
                        result = await runner.run(ModelRunRequest("bad", "model"))
                    self.assertEqual(
                        RawModelResponse(0, "", "Copilot SDK returned an empty classification"),
                        result,
                    )
                    self.assertIn("empty classification", stderr.getvalue())
                    self.assertEqual(0, (await runner.run(ModelRunRequest("good", "model"))).returncode)
                session.disconnect.assert_awaited_once()

        session = self.create_session()
        session.send_and_wait.side_effect = RuntimeError("session failed")
        self.client.create_session.side_effect = [session, self.create_session()]
        async with CopilotSdkModelRunner() as runner:
            with self.assertRaisesRegex(RuntimeError, "session failed"):
                await runner.run(ModelRunRequest("bad", "model"))
            self.assertEqual(0, (await runner.run(ModelRunRequest("good", "model"))).returncode)
        session.disconnect.assert_awaited_once()

    async def test_timeout_aborts_and_disconnects_session(self) -> None:
        session = self.create_session()

        async def stall(*_args, **_kwargs):
            await asyncio.sleep(60)

        session.send_and_wait.side_effect = stall
        self.client.create_session.side_effect = [session]
        async with CopilotSdkModelRunner(timeout_seconds=0.01) as runner:
            with self.assertRaisesRegex(TimeoutError, "Copilot SDK timed out after 0.01s"):
                await runner.run(ModelRunRequest("prompt", "model"))
        session.abort.assert_awaited_once()
        session.disconnect.assert_awaited_once()
        self.client.stop.assert_awaited_once()

    async def test_cancellation_aborts_and_disconnects_session(self) -> None:
        session = self.create_session()
        started = asyncio.Event()

        async def stall(*_args, **_kwargs):
            started.set()
            await asyncio.Event().wait()

        session.send_and_wait.side_effect = stall
        self.client.create_session.side_effect = [session]
        async with CopilotSdkModelRunner() as runner:
            task = asyncio.create_task(runner.run(ModelRunRequest("prompt", "model")))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        session.abort.assert_awaited_once()
        session.disconnect.assert_awaited_once()
        self.client.stop.assert_awaited_once()

    async def test_startup_failure_closes_client_and_reports_error(self) -> None:
        self.client.create_session.side_effect = OSError("runtime unavailable")
        async with CopilotSdkModelRunner() as runner:
            with self.assertRaisesRegex(OSError, "runtime unavailable"):
                await runner.run(ModelRunRequest("prompt", "model"))
        self.client.stop.assert_awaited_once()

    async def test_shutdown_error_is_reported_and_temporary_files_are_removed(self) -> None:
        self.client.stop.side_effect = RuntimeError("shutdown failed")
        with self.assertRaisesRegex(RuntimeError, "shutdown failed"):
            async with CopilotSdkModelRunner() as runner:
                await runner.run(ModelRunRequest("prompt", "model"))
                directory = Path(self.factory.call_args.kwargs["base_directory"])
        self.assertFalse(directory.exists())
        self.client.force_stop.assert_awaited_once()

    async def test_shutdown_error_does_not_hide_body_error(self) -> None:
        self.client.stop.side_effect = RuntimeError("shutdown failed")
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            with self.assertRaisesRegex(ValueError, "classification failed"):
                async with CopilotSdkModelRunner() as runner:
                    await runner.run(ModelRunRequest("prompt", "model"))
                    directory = Path(self.factory.call_args.kwargs["base_directory"])
                    raise ValueError("classification failed")
        self.assertIn("failed to stop Copilot client", stderr.getvalue())
        self.assertIn("shutdown failed", stderr.getvalue())
        self.assertFalse(directory.exists())
        self.client.force_stop.assert_awaited_once()

    async def test_shutdown_timeout_force_stops_client(self) -> None:
        async def stall():
            await asyncio.sleep(60)

        self.client.stop.side_effect = stall
        async with CopilotSdkModelRunner(stop_timeout_seconds=0.01) as runner:
            await runner.run(ModelRunRequest("prompt", "model"))
        self.client.stop.assert_awaited_once()
        self.client.force_stop.assert_awaited_once()

    async def test_force_stop_error_does_not_hide_body_error(self) -> None:
        async def stall():
            await asyncio.sleep(60)

        self.client.stop.side_effect = stall
        self.client.force_stop.side_effect = RuntimeError("forced shutdown failed")
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            with self.assertRaisesRegex(ValueError, "classification failed"):
                async with CopilotSdkModelRunner(stop_timeout_seconds=0.01) as runner:
                    await runner.run(ModelRunRequest("prompt", "model"))
                    raise ValueError("classification failed")
        self.client.force_stop.assert_awaited_once()
        self.assertIn("forced shutdown failed", stderr.getvalue())

    async def test_disconnect_error_does_not_hide_request_error(self) -> None:
        session = self.create_session()
        session.send_and_wait.side_effect = ValueError("classification failed")
        session.disconnect.side_effect = RuntimeError("disconnect failed")
        self.client.create_session.side_effect = [session]
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            async with CopilotSdkModelRunner() as runner:
                with self.assertRaisesRegex(ValueError, "classification failed"):
                    await runner.run(ModelRunRequest("prompt", "model"))
        self.assertIn("failed to disconnect Copilot session", stderr.getvalue())
        self.assertIn("disconnect failed", stderr.getvalue())

    async def test_disconnect_error_is_reported_after_success(self) -> None:
        session = self.create_session()
        session.disconnect.side_effect = RuntimeError("disconnect failed")
        self.client.create_session.side_effect = [session]
        async with CopilotSdkModelRunner() as runner:
            with self.assertRaisesRegex(RuntimeError, "disconnect failed"):
                await runner.run(ModelRunRequest("prompt", "model"))

    async def test_session_creation_is_bounded_by_request_timeout(self) -> None:
        async def stall(**_kwargs):
            await asyncio.sleep(60)

        self.client.create_session.side_effect = stall
        async with CopilotSdkModelRunner(timeout_seconds=0.01) as runner:
            with self.assertRaisesRegex(TimeoutError, "timed out after 0.01s"):
                await runner.run(ModelRunRequest("prompt", "model"))
        self.client.stop.assert_awaited_once()

    async def test_abort_failure_is_logged_without_hiding_timeout(self) -> None:
        session = self.create_session()
        session.send_and_wait.side_effect = TimeoutError("send timeout")
        session.abort.side_effect = RuntimeError("abort failed")
        self.client.create_session.side_effect = [session]
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            async with CopilotSdkModelRunner() as runner:
                with self.assertRaisesRegex(TimeoutError, "timed out after 180s"):
                    await runner.run(ModelRunRequest("prompt", "model"))
        self.assertIn("failed to abort Copilot session", stderr.getvalue())
        self.assertIn("abort failed", stderr.getvalue())
        session.disconnect.assert_awaited_once()

    async def test_local_fallback_tokens_do_not_enable_logged_in_user(self) -> None:
        for name in ("GH_TOKEN", "GITHUB_TOKEN"):
            with self.subTest(name=name):
                with patch.dict(os.environ, {name: "local-token"}, clear=True):
                    async with CopilotSdkModelRunner() as runner:
                        await runner.run(ModelRunRequest("prompt", "model"))
                config = self.factory.call_args.kwargs
                self.assertEqual("local-token", config["github_token"])
                self.assertFalse(config["use_logged_in_user"])
                self.assertNotIn("GH_TOKEN", config["env"])
                self.assertNotIn("GITHUB_TOKEN", config["env"])

    async def test_runner_rejects_calls_outside_its_scope(self) -> None:
        runner = CopilotSdkModelRunner()
        with self.assertRaisesRegex(RuntimeError, "async context manager"):
            await runner.run(ModelRunRequest("prompt", "model"))
        async with runner:
            pass
        with self.assertRaisesRegex(RuntimeError, "async context manager"):
            await runner.run(ModelRunRequest("prompt", "model"))
