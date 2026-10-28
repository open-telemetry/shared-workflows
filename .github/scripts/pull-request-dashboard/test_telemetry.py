# OpenTelemetry-dependent tests are skipped when the optional SDK is not installed.
# pyright: reportMissingImports=false
from __future__ import annotations

import contextlib
import hashlib
import io
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import telemetry

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPT = SCRIPT_DIR / "github_traceparent.sh"
WORKFLOWS = SCRIPT_DIR.parents[1] / "workflows"
ENTRY_POINTS = (
    "enqueue_dashboard", "drain_queue", "refresh_author_nudges", "report_rate_limits",
    "promote_rollout", "process_queue_batch", "dashboard", "delivery", "publish_dashboard",
    "state",
)
ENTRY_POINT_CALL = re.compile(
    r"\bpython3?\s+(?:\S*/)?(?:" + "|".join(ENTRY_POINTS) + r")\.py\b"
)
TRACEPARENT_EXPORT = re.compile(
    r'export TRACEPARENT="\$\(bash (?:\S*/)?github_traceparent\.sh\)"'
)
TEST_OR_INSTALL_RUN = re.compile(r"-m\s+(?:unittest|pip)\b")
try:
    import opentelemetry.sdk  # noqa: F401
except ImportError:
    OTEL_INSTALLED = False
else:
    OTEL_INSTALLED = True
TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
PARENT_SPAN_ID = "00f067aa0ba902b7"
TRACEPARENT = f"00-{TRACE_ID}-{PARENT_SPAN_ID}-01"
ENDPOINT = {"PR_DASHBOARD_OTEL_ENABLED": "true", "OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:9"}


class TelemetryEnabledTest(unittest.TestCase):
    def test_requires_the_dashboard_flag_and_an_otlp_endpoint(self) -> None:
        cases = (
            ({}, False),
            ({"OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector:4318"}, False),
            ({"PR_DASHBOARD_OTEL_ENABLED": "true"}, False),
            ({"PR_DASHBOARD_OTEL_ENABLED": "1", "OTEL_EXPORTER_OTLP_ENDPOINT": "http://c:4318"}, False),
            ({"PR_DASHBOARD_OTEL_ENABLED": "true", "OTEL_EXPORTER_OTLP_ENDPOINT": " "}, False),
            ({**ENDPOINT, "OTEL_SDK_DISABLED": "TRUE"}, False),
            (ENDPOINT, True),
            ({"PR_DASHBOARD_OTEL_ENABLED": "true", "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://c"}, True),
            ({"PR_DASHBOARD_OTEL_ENABLED": "true", "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT": "http://c"}, True),
        )
        for env, expected in cases:
            with self.subTest(env=env):
                self.assertEqual(expected, telemetry.telemetry_enabled(env))


class ResourceAttributesTest(unittest.TestCase):
    def test_maps_github_actions_environment_to_vcs_and_cicd_conventions(self) -> None:
        attributes = telemetry.resource_attributes({
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_REPOSITORY": "open-telemetry/shared-workflows",
            "GITHUB_REPOSITORY_OWNER": "open-telemetry",
            "GITHUB_REF_NAME": "main",
            "GITHUB_REF_TYPE": "branch",
            "GITHUB_SHA": "abc123",
            "GITHUB_WORKFLOW": "Pull request dashboard queue drain",
            "GITHUB_RUN_ID": "42",
            "GITHUB_RUN_ATTEMPT": "2",
        })
        instance_id = attributes.pop("service.instance.id")

        self.assertEqual(36, len(instance_id))
        self.assertEqual(
            {
                "service.name": "pull-request-dashboard",
                "vcs.provider.name": "github",
                "vcs.repository.url.full": "https://github.com/open-telemetry/shared-workflows",
                "vcs.repository.name": "shared-workflows",
                "vcs.owner.name": "open-telemetry",
                "vcs.ref.head.name": "main",
                "vcs.ref.head.type": "branch",
                "vcs.ref.head.revision": "abc123",
                "cicd.pipeline.name": "Pull request dashboard queue drain",
                "cicd.pipeline.run.id": "42",
                "cicd.pipeline.run.url.full": (
                    "https://github.com/open-telemetry/shared-workflows/actions/runs/42/attempts/2"
                ),
            },
            attributes,
        )

    def test_outside_actions_only_service_identity_is_set(self) -> None:
        attributes = telemetry.resource_attributes({"OTEL_SERVICE_NAME": "custom"})
        self.assertEqual({"service.name", "service.instance.id"}, set(attributes))
        self.assertEqual("custom", attributes["service.name"])

    def test_pull_request_head_ref_wins_and_unknown_ref_types_are_omitted(self) -> None:
        attributes = telemetry.resource_attributes({
            "GITHUB_HEAD_REF": "feature",
            "GITHUB_REF_NAME": "7/merge",
            "GITHUB_REF_TYPE": "other",
        })
        self.assertEqual("feature", attributes["vcs.ref.head.name"])
        self.assertNotIn("vcs.ref.head.type", attributes)


class SessionDisabledTest(unittest.TestCase):
    def test_disabled_session_creates_no_providers_and_writes_nothing(self) -> None:
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            patch("telemetry._setup_providers") as setup,
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
            telemetry.session({"OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector:4318"}),
        ):
            pass
        setup.assert_not_called()
        self.assertEqual("", stdout.getvalue())
        self.assertEqual("", stderr.getvalue())

    def test_session_does_not_change_the_wrapped_outcome(self) -> None:
        with self.assertRaises(SystemExit) as raised, telemetry.session({}):
            raise SystemExit(3)
        self.assertEqual(3, raised.exception.code)
        with self.assertRaisesRegex(ValueError, "boom"), telemetry.session({}):
            raise ValueError("boom")

    def test_child_environment_keeps_existing_keys(self) -> None:
        env = {"PATH": "/bin", "TRACEPARENT": TRACEPARENT}
        with telemetry.session({}):
            child = telemetry.child_environment(env)
        self.assertEqual(env, child)
        self.assertIsNot(env, child)


@unittest.skipUnless(OTEL_INSTALLED, "OpenTelemetry SDK is not installed")
class PropagationTest(unittest.TestCase):
    def test_parent_context_from_environment_is_injected_into_children(self) -> None:
        env = {"TRACEPARENT": TRACEPARENT, "TRACESTATE": "vendor=value"}
        with telemetry.session(env):
            child = telemetry.child_environment({"PATH": "/bin"})
        self.assertEqual(TRACEPARENT, child["TRACEPARENT"])
        self.assertEqual("vendor=value", child["TRACESTATE"])
        self.assertEqual("/bin", child["PATH"])

    def test_context_is_detached_when_the_session_ends(self) -> None:
        with telemetry.session({"TRACEPARENT": TRACEPARENT}):
            pass
        self.assertNotIn("TRACEPARENT", telemetry.propagation_environment())

    def test_without_parent_context_no_keys_are_added(self) -> None:
        with telemetry.session({}):
            self.assertEqual({}, telemetry.propagation_environment())

    def test_invalid_traceparent_is_ignored(self) -> None:
        with telemetry.session({"TRACEPARENT": "not-a-traceparent"}):
            self.assertNotIn("TRACEPARENT", telemetry.propagation_environment())


@unittest.skipUnless(OTEL_INSTALLED, "OpenTelemetry SDK is not installed")
class ProviderSetupTest(unittest.TestCase):
    def test_enabled_session_sets_up_and_shuts_down_traces_and_logs(self) -> None:
        from opentelemetry.sdk._logs import LoggerProvider
        from opentelemetry.sdk.trace import TracerProvider

        with (
            patch("opentelemetry.trace.set_tracer_provider") as set_tracer,
            patch("opentelemetry._logs.set_logger_provider") as set_logger,
            patch.object(TracerProvider, "shutdown") as tracer_shutdown,
            patch.object(LoggerProvider, "shutdown") as logger_shutdown,
            telemetry.session(ENDPOINT),
        ):
            pass

        tracer = set_tracer.call_args.args[0]
        self.assertIsInstance(tracer, TracerProvider)
        self.assertIsInstance(set_logger.call_args.args[0], LoggerProvider)
        self.assertEqual("pull-request-dashboard", tracer.resource.attributes["service.name"])
        tracer_shutdown.assert_called_once_with()
        logger_shutdown.assert_called_once_with()

    def test_signal_specific_endpoint_only_sets_up_that_signal(self) -> None:
        env = {"PR_DASHBOARD_OTEL_ENABLED": "true", "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT": "http://127.0.0.1:9"}
        with (
            patch("opentelemetry.trace.set_tracer_provider") as set_tracer,
            patch("opentelemetry._logs.set_logger_provider") as set_logger,
            telemetry.session(env),
        ):
            pass
        set_tracer.assert_not_called()
        set_logger.assert_called_once()

    def test_setup_failure_is_reported_on_stderr_and_does_not_raise(self) -> None:
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            patch("telemetry._tracer_provider", side_effect=RuntimeError("secret detail")),
            patch("telemetry._logger_provider", side_effect=RuntimeError("secret detail")),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
            telemetry.session(ENDPOINT),
        ):
            pass
        self.assertEqual("", stdout.getvalue())
        self.assertEqual(
            "pull-request-dashboard telemetry: traces setup failed: RuntimeError\n"
            "pull-request-dashboard telemetry: logs setup failed: RuntimeError\n",
            stderr.getvalue(),
        )


@unittest.skipIf(sys.platform == "win32", "bash helper is used on Linux runners")
class GithubTraceparentScriptTest(unittest.TestCase):
    def run_script(self, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(SCRIPT)], env={"PATH": os.environ.get("PATH", ""), **env},
            capture_output=True, text=True, check=False,
        )

    def test_matches_githubreceiver_step_span_ids(self) -> None:
        result = self.run_script({
            "GITHUB_RUN_ID": "123",
            "GITHUB_RUN_ATTEMPT": "1",
            "OTEL_GITHUB_CHECK_RUN_ID": "456",
            "OTEL_GITHUB_STEP_NAME": "Process owned queue work",
        })
        trace_id = hashlib.sha256(b"1231t").hexdigest()[:32]
        span_id = hashlib.sha256(b"456-Process owned queue work-s").hexdigest()[16:32]
        self.assertEqual(0, result.returncode)
        self.assertEqual(f"00-{trace_id}-{span_id}-01\n", result.stdout)

    def test_missing_inputs_print_nothing_and_succeed(self) -> None:
        result = self.run_script({"GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1"})
        self.assertEqual((0, "", ""), (result.returncode, result.stdout, result.stderr))


class WithoutOpenTelemetryTest(unittest.TestCase):
    def test_missing_packages_make_session_and_propagation_silent_no_ops(self) -> None:
        code = f"""
import os, sys
sys.modules["opentelemetry"] = None
sys.path.insert(0, os.getcwd())
import telemetry
with telemetry.session({{"TRACEPARENT": "{TRACEPARENT}"}}):
    child = telemetry.child_environment({{"PATH": "/bin"}})
    propagation = telemetry.propagation_environment()
if child != {{"PATH": "/bin"}} or propagation != {{}}:
    sys.exit(f"unexpected child={{child!r}} propagation={{propagation!r}}")
"""
        env = {
            key: value
            for key, value in os.environ.items()
            if key not in {"TRACEPARENT", "TRACESTATE", "BAGGAGE", "PR_DASHBOARD_OTEL_ENABLED"}
        }
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=SCRIPT_DIR, env=env,
            capture_output=True, text=True, check=False,
        )
        self.assertEqual((0, "", ""), (result.returncode, result.stdout, result.stderr))


@unittest.skipUnless(OTEL_INSTALLED, "OpenTelemetry SDK is not installed")
class MisconfiguredPropagatorsTest(unittest.TestCase):
    def test_unknown_propagator_is_reported_and_does_not_stop_the_program(self) -> None:
        # opentelemetry.propagate loads OTEL_PROPAGATORS at import time, so use a fresh interpreter.
        code = f"""
import os, sys
sys.path.insert(0, os.getcwd())
import telemetry
with telemetry.session({{"TRACEPARENT": "{TRACEPARENT}"}}):
    child = telemetry.child_environment({{"PATH": "/bin"}})
    print("body ran")
if child != {{"PATH": "/bin"}}:
    sys.exit(f"unexpected child={{child!r}}")
"""
        env = {
            key: value
            for key, value in os.environ.items()
            if key not in {"TRACEPARENT", "TRACESTATE", "BAGGAGE", "PR_DASHBOARD_OTEL_ENABLED"}
        }
        env["OTEL_PROPAGATORS"] = "not-a-real-propagator"
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=SCRIPT_DIR, env=env,
            capture_output=True, text=True, check=False,
        )
        self.assertEqual((0, "body ran\n"), (result.returncode, result.stdout), result.stderr)
        self.assertIn("telemetry: extract failed: ValueError", result.stderr)
        self.assertIn("telemetry: inject failed: ValueError", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


def workflow_steps(text: str) -> list[list[str]]:
    """Split workflow text into the lines of each item under every `steps:` key."""
    steps: list[list[str]] = []
    current: list[str] | None = None
    steps_indent: int | None = None
    item_indent: int | None = None
    for line in text.splitlines():
        stripped = line.strip()
        indent = len(line) - len(line.lstrip(" "))
        if steps_indent is not None and stripped and not stripped.startswith("#"):
            if indent <= steps_indent:
                steps_indent = item_indent = current = None
            elif stripped.startswith("- ") and indent == (item_indent or indent):
                item_indent = indent
                current = [line]
                steps.append(current)
                continue
        if steps_indent is None:
            header = re.match(r"^( *)steps:\s*$", line)
            if header:
                steps_indent = len(header.group(1))
            continue
        if current is not None:
            current.append(line)
    return steps


def step_sections(lines: list[str]) -> dict[str, str]:
    """Return each top-level step key with its inline value and nested lines."""
    indent = len(lines[0]) - len(lines[0].lstrip(" "))
    key_line = re.compile(rf"^ {{{indent + 2}}}([A-Za-z][\w-]*):\s*(.*?)\s*$")
    sections: dict[str, list[str]] = {}
    section: list[str] | None = None
    for line in [lines[0].replace("- ", "  ", 1), *lines[1:]]:
        match = key_line.match(line)
        if match:
            section = sections.setdefault(str(match.group(1)), [])
            section.append(str(match.group(2)))
        elif section is not None:
            section.append(line)
    return {key: "\n".join(value) for key, value in sections.items()}


def unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def step_env(section: str) -> dict[str, str]:
    return {
        match.group(1): unquote(match.group(2))
        for match in re.finditer(r"^\s+([A-Z_][A-Z0-9_]*):\s*(.*?)\s*$", section, re.MULTILINE)
    }


def first_entry_point_call(run: str) -> re.Match[str] | None:
    """Return the first entry-point call on a line that does not run unittest or pip."""
    for call in ENTRY_POINT_CALL.finditer(run):
        line_start = run.rfind("\n", 0, call.start()) + 1
        line_end = run.find("\n", call.end())
        line = run[line_start:] if line_end == -1 else run[line_start:line_end]
        if not TEST_OR_INSTALL_RUN.search(line):
            return call
    return None


class WorkflowTraceparentWiringTest(unittest.TestCase):
    def workflow_steps(self) -> list[tuple[str, dict[str, str]]]:
        return [
            (path.name, step_sections(lines))
            for path in sorted(WORKFLOWS.glob("*.yml"))
            for lines in workflow_steps(path.read_text(encoding="utf-8"))
        ]

    def test_dashboard_entry_point_steps_export_the_step_traceparent(self) -> None:
        checked = 0
        for workflow, sections in self.workflow_steps():
            run = sections.get("run", "")
            call = first_entry_point_call(run)
            if call is None:
                continue
            checked += 1
            name = unquote(sections.get("name", ""))
            with self.subTest(workflow=workflow, step=name):
                env = step_env(sections.get("env", ""))
                self.assertTrue(name)
                self.assertEqual(name, env.get("OTEL_GITHUB_STEP_NAME"))
                self.assertEqual("${{ job.check_run_id }}", env.get("OTEL_GITHUB_CHECK_RUN_ID"))
                export = TRACEPARENT_EXPORT.search(run)
                self.assertIsNotNone(export)
                assert export is not None
                self.assertLess(export.start(), call.start())
        self.assertGreaterEqual(checked, 6)

    def test_every_traceparent_step_names_itself(self) -> None:
        checked = 0
        for workflow, sections in self.workflow_steps():
            if "github_traceparent.sh" not in sections.get("run", ""):
                continue
            checked += 1
            name = unquote(sections.get("name", ""))
            with self.subTest(workflow=workflow, step=name):
                self.assertTrue(name)
                env = step_env(sections.get("env", ""))
                self.assertEqual(name, env.get("OTEL_GITHUB_STEP_NAME"))
        self.assertGreaterEqual(checked, 6)


if __name__ == "__main__":
    unittest.main()
