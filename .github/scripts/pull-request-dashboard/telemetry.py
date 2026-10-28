"""Optional OpenTelemetry setup and environment-variable context propagation."""

# OpenTelemetry is optional at runtime: some entry points run without requirements.txt,
# so every opentelemetry import below is lazy and guarded.
# pyright: reportMissingImports=false

from __future__ import annotations

import os
import sys
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

SERVICE_NAME = "pull-request-dashboard"
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
def session(env: Mapping[str, str] | None = None) -> Iterator[None]:
    """Attach the parent context from env, optionally set up exporters, and shut down on exit.

    Telemetry failures are reported on stderr and never change the wrapped program's outcome.
    """
    env = os.environ if env is None else env
    token = _attach_parent_context(env)
    providers = _setup_providers(env) if telemetry_enabled(env) else []
    try:
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
