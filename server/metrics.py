"""
Prometheus Metrics Exporter for FastAPI.

Exposes application metrics at /metrics endpoint for Prometheus scraping.

Metrics exposed:
- http_requests_total: Total HTTP requests (counter)
- http_request_duration_seconds: Request duration histogram
- llm_api_calls_total: LLM API calls (counter)
- llm_api_errors_total: LLM API errors (counter)
- db_connection_errors_total: Database connection errors (counter)
- rag_cache_hits_total: RAG cache hits (counter)
- rag_cache_misses_total: RAG cache misses (counter)

Usage:
    from server.metrics import metrics_app
    app.mount("/metrics", metrics_app)
"""

from __future__ import annotations

import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from starlette.responses import Response

# ── Metrics Definitions ────────────────────────────────────

# HTTP request metrics
HTTP_REQUESTS = Counter("http_requests_total", "Total HTTP requests", ["method", "endpoint", "status"])

HTTP_REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "endpoint"],
    buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

# LLM API metrics
LLM_API_CALLS = Counter("llm_api_calls_total", "Total LLM API calls", ["model", "provider"])

LLM_API_ERRORS = Counter("llm_api_errors_total", "Total LLM API errors", ["model", "provider", "error_type"])

# Database metrics
DB_CONNECTION_ERRORS = Counter("db_connection_errors_total", "Total database connection errors")

# RAG cache metrics
RAG_CACHE_HITS = Counter("rag_cache_hits_total", "Total RAG cache hits")

RAG_CACHE_MISSES = Counter("rag_cache_misses_total", "Total RAG cache misses")

# Active sessions
ACTIVE_SESSIONS = Counter("active_sessions_total", "Total active sessions")

# === LLM Resource Governor ===
#
# The governor owns the internal model budget: provider RPM, provider TPM and
# provider concurrency. Labels are limited to provider/model identifiers that
# come from configuration, plus a fixed set of outcome codes, so the metric
# cardinality stays bounded.

LLM_GOVERNOR_REQUESTS = Counter(
    "llm_governor_requests_total",
    "Model requests admitted by the LLM resource governor",
    ["provider", "model", "operation"],
)

LLM_GOVERNOR_REJECTIONS = Counter(
    "llm_governor_rejections_total",
    "Model requests refused by the LLM resource governor",
    ["provider", "reason"],
)

LLM_GOVERNOR_ACTIVE_CONCURRENCY = Gauge(
    "llm_governor_active_concurrency",
    "In-flight model calls currently holding a provider concurrency slot",
    ["provider"],
)

LLM_GOVERNOR_TOKENS_RESERVED = Counter(
    "llm_governor_tokens_reserved_total",
    "Tokens reserved at admission (estimated input plus reserved output)",
    ["provider", "model"],
)

LLM_GOVERNOR_TOKENS_USED = Counter(
    "llm_governor_tokens_used_total",
    "Tokens actually consumed after reconciling the reservation with provider usage",
    ["provider", "model"],
)

LLM_GOVERNOR_WAIT = Histogram(
    "llm_governor_wait_seconds",
    "Seconds spent waiting for the governor to admit a model call",
    ["provider"],
    buckets=[0.005, 0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0],
)

LLM_PROVIDER_429 = Counter(
    "llm_provider_429_total",
    "Provider rate-limit (429) responses observed by the gateway",
    ["provider"],
)

LLM_GOVERNOR_FALLBACK = Counter(
    "llm_governor_fallback_total",
    "Times the gateway moved to another provider in the fallback chain",
    ["provider"],
)

LLM_GOVERNOR_RETRIES = Counter(
    "llm_governor_retries_total",
    "Retried model calls, each of which passes the governor again",
    ["provider", "reason"],
)

_GOVERNOR_LABEL_MAX = 64
_GOVERNOR_REASONS = frozenset(
    {
        "rpm",
        "tpm",
        "concurrency",
        "redis_unavailable",
        "rejected",
        "unknown",
        "429",
        "500",
        "502",
        "503",
        "504",
        "none",
    }
)


def _safe_label(value: str | None, *, allowed: frozenset[str] | None = None) -> str:
    """Bound a Prometheus label value.

    Identifiers come from configuration, but a caller may still pass an
    arbitrary string, so the value is length bounded and, for reason codes,
    checked against a fixed set.
    """
    text = str(value or "").strip()
    if allowed is not None:
        return text if text in allowed else "unknown"
    if not text:
        return "unknown"
    cleaned = "".join(char if (char.isalnum() or char in "._:/-") else "_" for char in text)
    return cleaned[:_GOVERNOR_LABEL_MAX] or "unknown"


# === Quality Metrics ===

QUALITY_ANTI_PATTERN_HITS = Counter(
    "quality_anti_pattern_hits_total",
    "Anti-pattern hits by pattern type and severity",
    ["pattern_type", "severity"],
)

QUALITY_REWRITE = Counter(
    "quality_rewrite_total",
    "Low-quality rewrite triggers by reason",
    ["reason"],
)

QUALITY_SOURCE_ATTRIBUTION_RATE = Gauge(
    "quality_source_attribution_rate",
    "Rate of source-attributed data points in replies",
)

QUALITY_JUDGE_SCORE = Gauge(
    "quality_judge_score",
    "LLM judge quality score by dimension",
    ["dimension"],
)

QUALITY_USER_SATISFACTION = Gauge(
    "quality_user_satisfaction",
    "User satisfaction rate (thumbs up / total)",
)

RAG_CACHE_HIT_RATIO = Gauge(
    "rag_cache_hit_ratio",
    "RAG cache hit ratio (hits / total requests)",
)

QUALITY_HALLUCINATION_DETECTED = Counter(
    "quality_hallucination_detected_total",
    "Hallucination detections by type",
    ["hallucination_type"],
)


def _metric_endpoint(request: Request) -> str:
    """Use the trusted FastAPI route template, never a raw identifier path.

    In middleware the router may not have set scope["route"] yet. Group those
    requests by a finite application prefix instead of admitting path values
    that may contain run IDs, arbitrary strings or student information.
    """
    route = request.scope.get("route")
    template = getattr(route, "path", None)
    if isinstance(template, str) and template.startswith("/") and len(template) <= 160:
        return template
    path = request.scope.get("path", "")
    if path.startswith("/api/v1/admin/agent-"):
        return "/api/v1/admin/agent-*"
    if path.startswith("/api/v1/admin/"):
        return "/api/v1/admin/*"
    for prefix in ("/api/v1/chat", "/api/v1/data", "/api/v1/profile", "/api/v1/knowledge", "/api/v1/health"):
        if path == prefix or path.startswith(prefix + "/"):
            return prefix + "/*"
    return "other"


# A short-lived Worker cannot export its in-memory counter through FastAPI.
# Keep a separate durable snapshot gauge from its isolated SQLite TaskRun journal.
AGENT_TASKRUN_STORED = Gauge(
    "agent_taskrun_stored_jobs",
    "Current synthetic TaskRun journal rows by finite engine/status",
    ["engine", "status"],
)
_TASKRUN_ENGINES = ("deterministic", "portfolio", "deepagents")
_TASKRUN_STATES = ("PENDING_APPROVAL", "APPROVED", "RUNNING", "SUCCEEDED", "NEEDS_REVIEW", "FAILED", "CANCELLED")


AGENT_TASKRUN_SNAPSHOT_AVAILABLE = Gauge(
    "agent_taskrun_snapshot_available", "Whether the current synthetic journal snapshot was collected"
)


def _refresh_synthetic_taskrun_metrics() -> None:
    """Read only current counts; never expose stale rows on collection failure."""
    AGENT_TASKRUN_SNAPSHOT_AVAILABLE.set(0)
    AGENT_TASKRUN_STORED.clear()
    if os.getenv("ENABLE_SYNTHETIC_AGENT_EXPERIMENT", "").lower() != "true":
        return
    if os.getenv("APP_ENV", "").lower() not in {"development", "dev", "test", "testing", "local"}:
        return
    location = os.getenv("SYNTHETIC_TASKRUN_JOURNAL", "")
    if not location or not Path(location).is_absolute() or Path(location).is_symlink():
        return
    if not Path(location).is_file():
        return
    try:
        address = "file:" + quote(str(Path(location).resolve()), safe="/") + "?mode=ro"
        with closing(sqlite3.connect(address, uri=True, timeout=0.2)) as conn:
            rows = conn.execute(
                "SELECT engine, status, COUNT(*) FROM synthetic_task_runs GROUP BY engine, status"
            ).fetchall()
    except (sqlite3.Error, OSError):
        return  # Collector failure must not reveal file paths or break /metrics.
    counts = {(engine, state): 0 for engine in _TASKRUN_ENGINES for state in _TASKRUN_STATES}
    for engine, state, count in rows:
        if (engine, state) not in counts:
            return  # Unexpected journal schema/state: report unavailable, not partial success.
        counts[(engine, state)] = count
    for (engine, state), count in counts.items():
        AGENT_TASKRUN_STORED.labels(engine=engine, status=state).set(count)
    AGENT_TASKRUN_SNAPSHOT_AVAILABLE.set(1)


# ── Middleware for Automatic Metrics Collection ────────────


async def metrics_middleware(request: Request, call_next):
    """Middleware to automatically collect HTTP metrics."""
    start_time = time.monotonic()
    method = (
        request.method if request.method in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"} else "OTHER"
    )

    try:
        response = await call_next(request)

        # Record metrics
        duration = time.monotonic() - start_time
        endpoint = _metric_endpoint(request)
        HTTP_REQUESTS.labels(method=method, endpoint=endpoint, status=response.status_code).inc()

        HTTP_REQUEST_DURATION.labels(method=method, endpoint=endpoint).observe(duration)

        return response

    except Exception:
        # Record error metrics
        duration = time.monotonic() - start_time
        endpoint = _metric_endpoint(request)
        HTTP_REQUESTS.labels(method=method, endpoint=endpoint, status=500).inc()

        HTTP_REQUEST_DURATION.labels(method=method, endpoint=endpoint).observe(duration)

        raise


# ── Metrics Endpoint ──────────────────────────────────────


metrics_app = FastAPI()


@metrics_app.get("/")
async def metrics():
    """Expose Prometheus metrics."""
    _refresh_synthetic_taskrun_metrics()
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ── Helper Functions ──────────────────────────────────────


def record_llm_call(model: str, provider: str, success: bool = True, error_type: str | None = None):
    """Record LLM API call metrics."""
    LLM_API_CALLS.labels(model=model, provider=provider).inc()

    if not success and error_type:
        LLM_API_ERRORS.labels(model=model, provider=provider, error_type=error_type).inc()


def record_db_error():
    """Record database connection error."""
    DB_CONNECTION_ERRORS.inc()


def record_cache_hit():
    """Record RAG cache hit."""
    RAG_CACHE_HITS.inc()


def record_cache_miss():
    """Record RAG cache miss."""
    RAG_CACHE_MISSES.inc()


def set_active_sessions(count: int):
    """Set active sessions count."""
    # Note: Prometheus Gauge would be better for this, but keeping simple
    pass


def record_anti_pattern_hit(pattern_type: str, severity: str = "warn"):
    """Record an anti-pattern detection hit."""
    QUALITY_ANTI_PATTERN_HITS.labels(pattern_type=pattern_type, severity=severity).inc()


def record_quality_rewrite(reason: str = "low_score"):
    """Record a low-quality rewrite trigger."""
    QUALITY_REWRITE.labels(reason=reason).inc()


def record_judge_score(dimension: str, score: float):
    """Record an LLM judge quality score for a dimension."""
    QUALITY_JUDGE_SCORE.labels(dimension=dimension).set(score)


def record_hallucination(hallucination_type: str):
    """Record a hallucination detection event."""
    QUALITY_HALLUCINATION_DETECTED.labels(hallucination_type=hallucination_type).inc()


# === LLM Resource Governor helpers ===


def record_governor_admission(provider: str, model: str, operation: str) -> None:
    """Record one admitted model request."""
    LLM_GOVERNOR_REQUESTS.labels(
        provider=_safe_label(provider),
        model=_safe_label(model),
        operation=_safe_label(operation),
    ).inc()


def record_governor_rejection(provider: str, reason: str) -> None:
    """Record one refused model request."""
    LLM_GOVERNOR_REJECTIONS.labels(
        provider=_safe_label(provider),
        reason=_safe_label(reason, allowed=_GOVERNOR_REASONS),
    ).inc()


def record_governor_wait(provider: str, seconds: float) -> None:
    """Record how long a call waited for admission."""
    LLM_GOVERNOR_WAIT.labels(provider=_safe_label(provider)).observe(max(0.0, seconds))


def record_governor_tokens(provider: str, model: str, *, reserved: int, used: int) -> None:
    """Record the reserved and the actually consumed token amount."""
    provider_label = _safe_label(provider)
    model_label = _safe_label(model)
    LLM_GOVERNOR_TOKENS_RESERVED.labels(provider=provider_label, model=model_label).inc(max(0, reserved))
    LLM_GOVERNOR_TOKENS_USED.labels(provider=provider_label, model=model_label).inc(max(0, used))


def record_provider_429(provider: str) -> None:
    """Record a provider rate-limit response."""
    LLM_PROVIDER_429.labels(provider=_safe_label(provider)).inc()


def record_governor_retry(provider: str, reason: str) -> None:
    """Record a retried model call."""
    LLM_GOVERNOR_RETRIES.labels(
        provider=_safe_label(provider),
        reason=_safe_label(reason, allowed=_GOVERNOR_REASONS),
    ).inc()


def record_governor_fallback(provider: str) -> None:
    """Record a move to the next provider of the fallback chain."""
    LLM_GOVERNOR_FALLBACK.labels(provider=_safe_label(provider)).inc()


def adjust_governor_concurrency(provider: str, delta: int) -> None:
    """Track the in-flight model calls of one provider in this process."""
    LLM_GOVERNOR_ACTIVE_CONCURRENCY.labels(provider=_safe_label(provider)).inc(delta)


# === Admin operations ===

ADMIN_QUALITY_JOBS = Counter(
    "admin_quality_jobs_total",
    "Data-quality jobs by terminal state and execution mode",
    ["status", "execution_mode"],
)

ADMIN_QUEUE_UNAVAILABLE = Counter(
    "admin_quality_queue_unavailable_total",
    "Quality-job publishes that could not reach Redis",
)

ADMIN_WORKER_RECOVERY_RUNS = Counter(
    "admin_worker_recovery_runs_total",
    "Worker recovery scans by outcome",
    ["outcome"],
)

ADMIN_WORKER_RECOVERY_JOBS = Counter(
    "admin_worker_recovery_jobs_total",
    "Jobs recovered by recovery kind",
    ["kind"],
)

ADMIN_WORKER_RECOVERY_DURATION = Histogram(
    "admin_worker_recovery_duration_seconds",
    "Worker recovery scan duration",
    ["kind"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
)


def record_admin_job_finished(status: str, execution_mode: str) -> None:
    ADMIN_QUALITY_JOBS.labels(status=status, execution_mode=execution_mode or "unknown").inc()


def record_admin_queue_unavailable() -> None:
    ADMIN_QUEUE_UNAVAILABLE.inc()


def record_admin_worker_recovery(
    kind: str,
    recovered: int,
    duration_seconds: float,
    *,
    failed: bool = False,
) -> None:
    ADMIN_WORKER_RECOVERY_RUNS.labels(outcome="failed" if failed else "ok").inc()
    ADMIN_WORKER_RECOVERY_JOBS.labels(kind=kind).inc(max(0, recovered))
    ADMIN_WORKER_RECOVERY_DURATION.labels(kind=kind).observe(max(0.0, duration_seconds))
