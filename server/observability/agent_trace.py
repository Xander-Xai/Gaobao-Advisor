"""Privacy-bounded optional OpenTelemetry and Prometheus Agent instrumentation."""

from __future__ import annotations

import os
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from urllib.parse import urlparse

from prometheus_client import Counter, Histogram

_OPERATIONS = frozenset(
    {"http.request", "agent.taskrun", "agent.runtime", "agent.planner", "agent.tool", "agent.verify"}
)
_ENUMS = {
    "http.method": frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "OTHER"}),
    "http.route_group": frozenset({"agent_admin", "public_chat", "other"}),
    "agent.engine": frozenset({"portfolio", "deterministic", "deepagents", "unknown"}),
    "agent.status": frozenset({"SUCCEEDED", "NEEDS_REVIEW", "FAILED", "CANCELLED", "ERROR", "unknown"}),
    "agent.tool": frozenset(
        {
            "school_lookup",
            "major_lookup",
            "admission_lookup",
            "plan_lookup",
            "candidate_lookup",
            "knowledge_lookup",
            "portfolio_sql",
            "unknown",
        }
    ),
}
_NUMBERS = frozenset({"http.status_code", "agent.model_steps", "agent.tool_calls"})
_PARENT = re.compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")
_TOTAL = Counter("agent_safe_spans_total", "Finite Agent phases", ["operation", "outcome"])
_DURATION = Histogram("agent_safe_span_duration_seconds", "Agent phase seconds", ["operation"])
_TASKRUN_ATTEMPTS = Counter(
    "agent_taskrun_attempts_total",
    "Synthetic task execution attempts (not unique task completions)",
    ["engine", "status", "reason"],
)
_FAILURE_CODES = {
    "agent_execution_failed": "execution_failed",
    "insufficient_verified_evidence": "insufficient_evidence",
    "lease_conflict": "lease_conflict",
    "unhandled_worker_error": "unhandled_worker_error",
}


def safe_attributes(values: dict[str, Any] | None) -> dict[str, str | int]:
    """Never export prompts, user identifiers, SQL responses or errors."""
    accepted: dict[str, str | int] = {}
    if not isinstance(values, dict):
        return accepted
    for key, value in values.items():
        if key in _ENUMS and type(value) is str:
            accepted[key] = value if value in _ENUMS[key] else "unknown" if "unknown" in _ENUMS[key] else "OTHER"
        elif key in _NUMBERS and type(value) is int and 0 <= value <= 999999:
            accepted[key] = value
    return accepted


def valid_traceparent(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 55:
        return False
    match = _PARENT.fullmatch(value)
    return bool(match and int(match[1], 16) and int(match[2], 16))


def current_traceparent() -> str | None:
    try:
        from opentelemetry import trace

        ctx = trace.get_current_span().get_span_context()
        if not ctx.is_valid:
            return None
        return f"00-{ctx.trace_id:032x}-{ctx.span_id:016x}-{int(ctx.trace_flags):02x}"
    except (ImportError, AttributeError, ValueError):
        return None


@contextmanager
def continue_trace(traceparent: str | None) -> Iterator[None]:
    """Carry W3C context only; never accept user baggage or identity claims."""
    if not valid_traceparent(traceparent):
        yield
        return
    try:
        from opentelemetry import context, propagate
    except ImportError:
        yield
        return
    token = context.attach(propagate.extract({"traceparent": traceparent}))
    try:
        yield
    finally:
        context.detach(token)


@contextmanager
def safe_agent_span(operation: str, attrs: dict[str, Any] | None = None) -> Iterator[None]:
    """Errors are sanitized and never recorded as raw exceptions."""
    if operation not in _OPERATIONS:
        raise ValueError("Unsupported trace operation")
    started, outcome = time.monotonic(), "ok"
    try:
        try:
            from opentelemetry import trace
            from opentelemetry.trace import Status, StatusCode
        except ImportError:
            yield
            return
        with trace.get_tracer("gaobao.advisor.agent").start_as_current_span(
            operation, record_exception=False, set_status_on_exception=False
        ) as span:
            for key, value in safe_attributes(attrs).items():
                span.set_attribute(key, value)
            try:
                yield
            except BaseException:
                outcome = "error"
                span.set_status(Status(StatusCode.ERROR, "sanitized_error"))
                raise
    except BaseException:
        outcome = "error"
        raise
    finally:
        _TOTAL.labels(operation=operation, outcome=outcome).inc()
        _DURATION.labels(operation=operation).observe(max(0.0, time.monotonic() - started))


def initialize_agent_otel() -> bool:
    """No external export by default; explicit secure OTLP and SDK opt-in."""
    endpoint = os.getenv("AGENT_OTEL_OTLP_ENDPOINT", "").strip()
    if not endpoint:
        return False
    target = urlparse(endpoint)
    loopback = target.hostname in {"localhost", "127.0.0.1", "::1"}
    if (
        (target.scheme != "https" and not (target.scheme == "http" and loopback))
        or not target.hostname
        or target.username
        or target.password
        or target.query
        or target.fragment
    ):
        raise ValueError("Unsafe Agent OTLP endpoint")
    try:
        ratio = float(os.getenv("AGENT_OTEL_SAMPLE_RATE", "0.1"))
    except ValueError as exc:
        raise ValueError("Invalid sampling ratio") from exc
    if not 0 <= ratio <= 1:
        raise ValueError("Sampling ratio must be 0..1")
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.trace.sampling import TraceIdRatioBased
    except ImportError as exc:
        raise RuntimeError("Install optional OTLP SDK/exporter first") from exc
    provider = TracerProvider(
        sampler=TraceIdRatioBased(ratio),
        resource=Resource.create({"service.name": "gaobao-advisor-agent"}),
    )
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)
    return True


def set_current_span_attribute(key: str, value: Any) -> None:
    """Attach only allowlisted, low-cardinality data to the active span."""
    attrs = safe_attributes({key: value})
    if not attrs:
        return
    try:
        from opentelemetry import trace
    except ImportError:
        return
    span = trace.get_current_span()
    if span.is_recording():
        for name, safe_value in attrs.items():
            span.set_attribute(name, safe_value)


def record_taskrun_outcome(engine: str, status: str, failure_code: str | None = None) -> None:
    """One count per execution attempt, never per unique TaskRun.

    All label values come from bounded enums. Unknown error text cannot enter
    Prometheus or OpenTelemetry. This records a final stored status, not proof
    of real model quality or actual successful student delivery.
    """
    engine_label = safe_attributes({"agent.engine": engine}).get("agent.engine", "unknown")
    status_label = safe_attributes({"agent.status": status}).get("agent.status", "unknown")
    reason = _FAILURE_CODES.get(failure_code, "other" if failure_code else "none")
    _TASKRUN_ATTEMPTS.labels(engine=engine_label, status=status_label, reason=reason).inc()


def shutdown_agent_otel() -> None:
    """Flush a short-lived worker's optional OTLP spans before process exit."""
    try:
        from opentelemetry import trace
    except ImportError:
        return
    provider = trace.get_tracer_provider()
    shutdown = getattr(provider, "shutdown", None)
    if callable(shutdown):
        shutdown()
