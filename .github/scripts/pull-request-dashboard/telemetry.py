"""Optional OpenTelemetry setup and environment-variable context propagation."""

# OpenTelemetry is optional at runtime: some entry points run without requirements.txt,
# so every opentelemetry import below is lazy and guarded.
# pyright: reportMissingImports=false

from __future__ import annotations

import os
import sys
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import PurePath
from typing import Any

SERVICE_NAME = "pull-request-dashboard"
TRACER_NAME = SERVICE_NAME
ENABLED_ENV = "PR_DASHBOARD_OTEL_ENABLED"
TRACES_ENDPOINT_ENVS = ("OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
LOGS_ENDPOINT_ENVS = ("OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT")
COPILOT_PROPAGATION_KEYS = ("TRACEPARENT", "TRACESTATE")


def _has_any(env: Mapping[str, str], names: tuple[str, ...]) -> bool:
    return any((env.get(name) or "").strip() for name in names)


def telemetry_enabled(env: Mapping[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    return (
        env.get(ENABLED_ENV) == "true"
        and env.get("OTEL_SDK_DISABLED", "").strip().lower() != "true"
        and _has_any(env, TRACES_ENDPOINT_ENVS + LOGS_ENDPOINT_ENVS)
    )


def resource_attributes(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return service identity plus VCS and CICD semantic conventions from GitHub Actions."""
    env = os.environ if env is None else env
    attributes = {
        "service.name": env.get("OTEL_SERVICE_NAME") or SERVICE_NAME,
        "service.instance.id": str(uuid.uuid4()),
    }
    server = (env.get("GITHUB_SERVER_URL") or "https://github.com").rstrip("/")
    repository = env.get("GITHUB_REPOSITORY", "")
    run_id = env.get("GITHUB_RUN_ID", "")
    run_attempt = env.get("GITHUB_RUN_ATTEMPT", "")
    ref_type = env.get("GITHUB_REF_TYPE", "")
    if repository:
        attributes["vcs.provider.name"] = "github"
        attributes["vcs.repository.url.full"] = f"{server}/{repository}"
        attributes["vcs.repository.name"] = repository.rpartition("/")[2]
    if repository and run_id and run_attempt:
        attributes["cicd.pipeline.run.url.full"] = (
            f"{server}/{repository}/actions/runs/{run_id}/attempts/{run_attempt}"
        )
    optional = {
        "vcs.owner.name": env.get("GITHUB_REPOSITORY_OWNER", ""),
        "vcs.ref.head.name": env.get("GITHUB_HEAD_REF") or env.get("GITHUB_REF_NAME", ""),
        "vcs.ref.head.type": ref_type if ref_type in {"branch", "tag"} else "",
        "vcs.ref.head.revision": env.get("GITHUB_SHA", ""),
        "cicd.pipeline.name": env.get("GITHUB_WORKFLOW", ""),
        "cicd.pipeline.run.id": run_id,
    }
    attributes.update({key: value for key, value in optional.items() if value})
    return attributes


def propagation_environment() -> dict[str, str]:
    """Inject the current context into new environment variables for a child process."""
    try:
        from opentelemetry import propagate
        from opentelemetry.propagators._envcarrier import EnvironmentSetter
    except ImportError:
        return {}
    except Exception as error:  # e.g. an unknown OTEL_PROPAGATORS entry fails at import
        _warn("inject", error)
        return {}
    carrier: dict[str, str] = {}
    try:
        propagate.inject(carrier, setter=EnvironmentSetter())
    except Exception as error:
        _warn("inject", error)
        return {}
    return carrier


def child_environment(env: Mapping[str, str]) -> dict[str, str]:
    """Return a copy of env with the current context added; existing keys are never removed."""
    return {**env, **propagation_environment()}


@contextmanager
def session(env: Mapping[str, str] | None = None, *, name: str | None = None) -> Iterator[None]:
    """Attach the parent context from env, optionally set up exporters, and shut down on exit.

    With name, the body runs inside an INTERNAL CLI execution span (semantic conventions for
    CLI programs) that ends before the providers shut down.
    Telemetry failures are reported on stderr and never change the wrapped program's outcome.
    """
    env = os.environ if env is None else env
    token = _attach_parent_context(env)
    providers = _setup_providers(env) if telemetry_enabled(env) else []
    try:
        if name is None:
            yield
        else:
            with _execution_span(name):
                yield
    finally:
        for provider in providers:
            try:
                provider.shutdown()
            except Exception as error:
                _warn("shutdown", error)
        if token is not None:
            try:
                from opentelemetry import context

                context.detach(token)
            except Exception as error:
                _warn("detach", error)


@contextmanager
def child_process_span(command: Sequence[str]) -> Iterator[Callable[[int], None]]:
    """Run a child process spawn inside a CLIENT CLI span; yield a function that records its exit code.

    Start the span before child_environment() so that the child's parent is this span.
    """
    executable = PurePath(command[0]).name if command else ""
    exit_code: int | None = None

    def record_exit(returncode: int) -> None:
        nonlocal exit_code
        exit_code = returncode

    with _current_span(
        _child_span_name(command), "CLIENT", {"process.executable.name": executable}
    ) as span:
        try:
            yield record_exit
        except BaseException as error:
            _finish(span, exit_code, _error_type(error))
            raise
        _finish(span, exit_code, str(exit_code) if exit_code else None)


@contextmanager
def _execution_span(name: str) -> Iterator[None]:
    attributes = {
        "process.executable.name": PurePath(sys.executable).name,
        "process.pid": os.getpid(),
    }
    with _current_span(name, "INTERNAL", attributes) as span:
        try:
            yield
        except SystemExit as exit_request:
            code = _exit_code(exit_request.code)
            _finish(span, code, str(code) if code else None)
            raise
        except BaseException as error:
            _finish(span, 1 if isinstance(error, Exception) else None, _error_type(error))
            raise
        _finish(span, 0, None)


@contextmanager
def _current_span(name: str, kind: str, attributes: Mapping[str, Any]) -> Iterator[Any]:
    """Yield a started span (or None) that is current for the body; end it on exit."""
    try:
        from opentelemetry import context, trace
    except ImportError:
        yield None
        return
    except Exception as error:
        _warn("span", error)
        yield None
        return
    span = token = None
    try:
        span = trace.get_tracer(TRACER_NAME).start_span(
            name,
            kind=getattr(trace.SpanKind, kind),
            attributes=dict(attributes),
            record_exception=False,
            set_status_on_exception=False,
        )
        token = context.attach(trace.set_span_in_context(span))
    except Exception as error:
        _warn("span", error)
    try:
        yield span
    finally:
        try:
            if token is not None:
                context.detach(token)
            if span is not None:
                span.end()
        except Exception as error:
            _warn("span", error)


def _finish(span: Any, exit_code: int | None, error_type: str | None) -> None:
    """Record the outcome without exception messages or stack traces (they can hold secrets)."""
    if span is None:
        return
    try:
        from opentelemetry.trace import StatusCode

        if exit_code is not None:
            span.set_attribute("process.exit.code", exit_code)
        if error_type is not None:
            span.set_attribute("error.type", error_type)
            span.set_status(StatusCode.ERROR)
    except Exception as error:
        _warn("span", error)


def _exit_code(code: object) -> int:
    """Return the process exit code that Python uses for SystemExit(code)."""
    if code is None:
        return 0
    if isinstance(code, int):
        return int(code)  # SystemExit(True) exits with 1, not True
    return 1


def _error_type(error: BaseException) -> str:
    error_class = type(error)
    if error_class.__module__ == "builtins":
        return error_class.__qualname__
    return f"{error_class.__module__}.{error_class.__qualname__}"


def _child_span_name(command: Sequence[str]) -> str:
    """Name a dashboard script child by its script file; other commands by their executable."""
    if len(command) > 1 and command[1].endswith(".py"):
        return PurePath(command[1]).name
    return PurePath(command[0]).name if command else ""


def _attach_parent_context(env: Mapping[str, str]) -> Any:
    try:
        from opentelemetry import context, propagate
        from opentelemetry.propagators._envcarrier import EnvironmentGetter
    except ImportError:
        return None
    except Exception as error:  # e.g. an unknown OTEL_PROPAGATORS entry fails at import
        _warn("extract", error)
        return None
    try:
        return context.attach(propagate.extract(env, getter=EnvironmentGetter()))
    except Exception as error:
        _warn("extract", error)
        return None


def _setup_providers(env: Mapping[str, str]) -> list[Any]:
    try:
        from opentelemetry.sdk.resources import Resource

        resource = Resource.create(resource_attributes(env))
    except Exception as error:
        _warn("setup", error)
        return []
    providers = []
    for name, endpoints, factory in (
        ("traces", TRACES_ENDPOINT_ENVS, _tracer_provider),
        ("logs", LOGS_ENDPOINT_ENVS, _logger_provider),
    ):
        if not _has_any(env, endpoints):
            continue
        try:
            providers.append(factory(resource))
        except Exception as error:
            _warn(f"{name} setup", error)
    return providers


def _tracer_provider(resource: Any) -> Any:
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    return provider


def _logger_provider(resource: Any) -> Any:
    from opentelemetry._logs import set_logger_provider
    from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor

    provider = LoggerProvider(resource=resource)
    provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
    set_logger_provider(provider)
    return provider


def _warn(stage: str, error: BaseException) -> None:
    print(f"{SERVICE_NAME} telemetry: {stage} failed: {type(error).__name__}", file=sys.stderr)
