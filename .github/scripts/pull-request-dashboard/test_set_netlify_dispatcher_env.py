"""Contract tests for Netlify dispatcher environment updates."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
EXISTING_ERROR = (
    "Setting the context and scope at the same time on an existing env var "
    "is not allowed. Run the set command separately for each update."
)
KEYS = (
    ("OTELBOT_SHARED_WORKFLOWS_CLIENT_ID", "client-id", False),
    ("OTELBOT_SHARED_WORKFLOWS_PRIVATE_KEY_BASE64", "private-key-value", True),
    ("PR_DASHBOARD_QUEUE_MODE", "all", False),
)


class NetlifyDispatcherEnvTest(unittest.TestCase):
    def run_set(
        self, mode: str, key: str, value: str, secret: bool = False
    ) -> tuple[subprocess.CompletedProcess[str], list[list[str]]]:
        with tempfile.TemporaryDirectory() as directory:
            mock = Path(directory) / "npx"
            mock.write_text(
                "#!/usr/bin/env bash\n"
                "printf '%s\\n' '---' \"$@\" >> \"$MOCK_CALLS\"\n"
                "if [[ \" $* \" == *' --scope functions '* ]]; then\n"
                "  if [[ \"$MOCK_MODE\" == create-error ]]; then\n"
                "    echo \"Bad credentials: $MOCK_SECRET $NETLIFY_AUTH_TOKEN\" >&2\n"
                "    exit 7\n"
                "  fi\n"
                "  if [[ \"$MOCK_MODE\" != new ]]; then\n"
                f"    echo '{EXISTING_ERROR}' >&2\n"
                "    exit 1\n"
                "  fi\n"
                "fi\n"
                "if [[ \"$MOCK_MODE\" == update-error ]]; then\n"
                "  echo \"Update failed: $MOCK_SECRET $NETLIFY_AUTH_TOKEN\" >&2\n"
                "  exit 8\n"
                "fi\n"
                "echo \"Saved $MOCK_SECRET $NETLIFY_AUTH_TOKEN\"\n",
                encoding="utf-8",
            )
            mock.chmod(0o755)
            calls = Path(directory) / "calls"
            env = os.environ.copy()
            env.update(
                PATH=f"{directory}{os.pathsep}{env['PATH']}",
                MOCK_CALLS=str(calls),
                MOCK_MODE=mode,
                MOCK_SECRET=value,
                NETLIFY_SITE_ID="site-id",
                NETLIFY_AUTH_TOKEN="do-not-log-this-token",
            )
            command = [
                shutil.which("bash") or "bash",
                "./set_netlify_dispatcher_env.sh",
                key,
                value,
            ]
            if secret:
                command.append("--secret")
            result = subprocess.run(
                command,
                cwd=SCRIPT_DIR,
                env=env,
                capture_output=True,
                text=True,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            recorded = (
                [
                    line.splitlines()
                    for line in calls.read_text(encoding="utf-8").split("---\n")[1:]
                ]
                if calls.exists()
                else []
            )
            self.assertNotIn(value, result.stdout + result.stderr)
            self.assertNotIn("do-not-log-this-token", result.stdout + result.stderr)
            return result, recorded

    def test_first_deploy_creates_all_keys_in_production_functions(self) -> None:
        for key, value, secret in KEYS:
            with self.subTest(key=key):
                result, calls = self.run_set("new", key, value, secret)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(len(calls), 1)
                self.assertEqual(
                    calls[0][:5],
                    ["--yes", "netlify-cli@26.0.2", "env:set", key, value],
                )
                self.assertEqual(
                    calls[0][5:],
                    [
                        "--context", "production", "--scope", "functions",
                        "--site", "site-id", "--auth", "do-not-log-this-token",
                        *(["--secret"] if secret else []), "--force",
                    ],
                )

    def test_repeat_deploy_updates_values_without_changing_scope(self) -> None:
        for key, value, secret in KEYS:
            with self.subTest(key=key):
                result, calls = self.run_set("existing", key, value, secret)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(len(calls), 2)
                self.assertEqual(
                    calls[1],
                    [arg for arg in calls[0] if arg not in ("--scope", "functions")],
                )
                self.assertEqual("--secret" in calls[1], secret)
                self.assertIn("production", calls[1])

    def test_unrelated_create_failure_stops_without_retry(self) -> None:
        result, calls = self.run_set("create-error", *KEYS[0][:2])
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(calls), 1)
        self.assertIn("Netlify env:set failed for", result.stderr)

    def test_update_failure_stops_deployment(self) -> None:
        key, value, secret = KEYS[1]
        result, calls = self.run_set("update-error", key, value, secret)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(calls), 2)
        self.assertIn("Netlify env:set failed while updating", result.stderr)


if __name__ == "__main__":
    unittest.main()
