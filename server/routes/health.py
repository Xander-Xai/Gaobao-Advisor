"""Health check endpoint.

Public demo behaviour: the endpoint reports the real state of every optional
subsystem. A disabled knowledge subsystem is reported explicitly as
``RAG_DISABLED`` rather than being folded into a generic ``ok`` — a system
that cannot retrieve evidence must never present itself as fully healthy.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Response

from db.database import is_db_connected
from server import __version__
from server.industrial_runtime import industrial_enabled, redis_ready
from server.services import rag
from server.services.data_profile import current_data_profile

router = APIRouter(tags=["health"])


@router.get("/api/v1/health/live")
async def liveness():
    """Liveness probe — the process is up and serving requests.

    Deliberately dependency-free: it must not fail because a downstream
    subsystem is degraded (that is what readiness is for).
    """
    return {"status": "ok"}


@router.get("/api/v1/health/ready")
async def readiness():
    """Readiness probe — required dependencies are usable.

    The database is required: if it is unreachable the service is not ready
    (HTTP 503). RAG is optional and reported as a degraded capability rather
    than blocking readiness.
    """
    db_status = "connected" if is_db_connected() else "disconnected"
    rag_status = rag.status()
    required_redis = industrial_enabled()
    redis_status = "connected" if required_redis and redis_ready() else "unavailable"
    ready = db_status == "connected" and (not required_redis or redis_status == "connected")
    body = {
        "status": "ready" if ready else "not_ready",
        "version": __version__,
        "database": db_status,
        "optional_services": {
            "rag": {"state": rag_status["state"], "reason": rag_status["reason"]},
        },
    }
    if required_redis:
        body["redis"] = redis_status
    return Response(
        content=json.dumps(body),
        media_type="application/json",
        status_code=200 if ready else 503,
    )


@router.get("/api/v1/health")
async def health_check():
    """Liveness probe with DB status and optional-subsystem state.

    ``status`` is ``ok`` only when required dependencies are healthy.
    ``optional_services`` reports each optional subsystem separately so a
    caller can tell "working" apart from "degraded".
    """
    db_status = "connected" if is_db_connected() else "disconnected"
    rag_status = rag.status()

    optional_services = {
        "rag": {
            "state": rag_status["state"],
            "reason": rag_status["reason"],
            "corpus_configured": rag_status["corpus_configured"],
            "documents": rag_status.get("documents", 0),
            "chunks": rag_status.get("chunks", 0),
            "integrity_ok": rag_status.get("integrity_ok", False),
        },
    }

    degraded = [name for name, s in optional_services.items() if s["state"] != "RAG_ENABLED"]

    required_redis = industrial_enabled()
    redis_status = "connected" if required_redis and redis_ready() else "unavailable"
    return {
        "status": "ok" if db_status == "connected" and (not required_redis or redis_status == "connected") else "degraded",
        **({"redis": redis_status} if required_redis else {}),
        "version": __version__,
        "database": db_status,
        "data_profile": current_data_profile(),
        "optional_services": optional_services,
        "degraded_services": degraded,
    }
