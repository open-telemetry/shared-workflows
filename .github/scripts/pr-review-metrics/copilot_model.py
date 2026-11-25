import asyncio
import json
import os
import re
from tempfile import TemporaryDirectory

from github_data import DataError


DEFAULT_REASONING_EFFORT = "high"
MAX_INPUT = 180_000
REQUEST_TIMEOUT = 180
MAX_REASONING_REQUEST_TIMEOUT = 600
CLEANUP_TIMEOUT = 10


class AssessmentError(DataError):
    pass


class AssessmentSetupError(AssessmentError):
    pass


def evidence_prompt(data):
    return "Evidence JSON:\n" + json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def input_size(instructions, data):
    return len(instructions.strip().encode("utf-8")) + len(evidence_prompt(data).encode("utf-8"))


class Copilot:
    def __init__(self, model, reasoning_effort=DEFAULT_REASONING_EFFORT):
        self.model = model
        self.reasoning_effort = reasoning_effort
        self._request_timeout = MAX_REASONING_REQUEST_TIMEOUT if reasoning_effort == "max" else REQUEST_TIMEOUT
        self._runner = None

    def start(self):
        if self._runner is not None:
            return
        if not hasattr(asyncio, "Runner"):
            raise AssessmentSetupError("Assessment requires Python 3.11 or newer")
        try:
            from copilot import CopilotClient, JsonRpcError
        except ImportError as error:
            raise AssessmentSetupError(
                "Install the Copilot SDK with `python -m pip install -r requirements.txt`"
            ) from error
        token = os.environ.get("COPILOT_GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if not token:
            raise AssessmentSetupError("Set COPILOT_GITHUB_TOKEN to a token with Copilot access")
        env = {key: os.environ[key] for key in (
            "APPDATA", "COMSPEC", "LOCALAPPDATA", "PATH", "PATHEXT", "SYSTEMROOT", "TEMP", "TMP", "WINDIR",
            "SSL_CERT_DIR", "SSL_CERT_FILE", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "COPILOT_RUNTIME_PATH",
        ) if key in os.environ}
        self._directory = TemporaryDirectory()
        directory = self._directory.name
        env.update(HOME=directory, USERPROFILE=directory, XDG_CONFIG_HOME=directory, CI="true")
        self._sdk_errors = (JsonRpcError, RuntimeError, OSError, TimeoutError)
        try:
            self._client = CopilotClient(
                mode="empty", working_directory=directory, base_directory=directory, env=env,
                github_token=token, use_logged_in_user=False,
            )
        except (RuntimeError, OSError, ValueError) as error:
            self._directory.cleanup()
            raise AssessmentSetupError(f"Could not initialize Copilot SDK: {error}") from error
        self._runner = asyncio.Runner()

    async def _ask(self, instructions, prompt):
        session = None
        try:
            async with asyncio.timeout(self._request_timeout):
                session = await self._client.create_session(
                    model=self.model, reasoning_effort=self.reasoning_effort, available_tools=[],
                    system_message={"mode": "replace", "content": instructions.strip()},
                )
                response = await session.send_and_wait(prompt, timeout=self._request_timeout)
        except (TimeoutError, asyncio.CancelledError):
            if session is not None:
                await asyncio.wait_for(session.abort(), CLEANUP_TIMEOUT)
            raise
        finally:
            if session is not None:
                await asyncio.wait_for(session.disconnect(), CLEANUP_TIMEOUT)
        if response is None or not response.data.content or not response.data.content.strip():
            raise AssessmentError("Copilot did not return an assistant message")
        return response.data.content.strip()

    async def _stop(self):
        try:
            await asyncio.wait_for(self._client.stop(), CLEANUP_TIMEOUT)
        except self._sdk_errors:
            await asyncio.wait_for(self._client.force_stop(), CLEANUP_TIMEOUT)
            raise

    def close(self):
        if self._runner is not None:
            try:
                self._runner.run(self._stop())
            except self._sdk_errors as error:
                raise AssessmentError(f"Could not stop Copilot SDK cleanly: {error}") from error
            finally:
                self._runner.close()
                self._directory.cleanup()
                self._runner = None

    def ask(self, instructions, data):
        from shared_data import safe_payload

        if input_size(instructions, data) > MAX_INPUT:
            raise AssessmentError("Evidence exceeds the model input budget")
        if not safe_payload(data):
            raise AssessmentError("The evidence failed the public-data check")
        self.start()
        try:
            text = self._runner.run(self._ask(instructions, evidence_prompt(data)))
        except TimeoutError as error:
            raise AssessmentError(f"{self.model} ({self.reasoning_effort}) timed out after "
                                  f"{self._request_timeout} seconds") from error
        except self._sdk_errors as error:
            raise AssessmentError(f"Copilot SDK request failed: {error}") from error
        try:
            fence = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
            return json.loads(fence[1] if fence else text)
        except ValueError as error:
            raise AssessmentError("Copilot did not return valid JSON") from error
