"""FastAPI application entry point."""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from server import __version__
from server.admin.errors import AdminExceptionMiddleware, register_admin_error_handlers
from server.admin.routes import router as admin_router
from server.metrics import metrics_app, metrics_middleware
from server.middleware.csp import CSPMiddleware
from server.middleware.ratelimit import RateLimitMiddleware
from server.middleware.request_id import RequestIDMiddleware
from server.middleware.security import SecurityMiddleware
from server.monitoring import init_sentry
from server.report.routes import router as report_router
from server.routes.chat import router as chat_router
from server.routes.data import router as data_router
from server.routes.health import router as health_router
from server.routes.knowledge import router as knowledge_router
from server.routes.onboarding import router as onboarding_router
from server.routes.profile import router as profile_router
from server.routes.voice import router as voice_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    # Fail fast: in production a missing SESSION_SECRET raises here rather than
    # later as broken session verification on the first request.
    from server.admin.auth import ensure_admin_ready
    from server.auth import ensure_secret_ready

    ensure_secret_ready()
    ensure_admin_ready()

    init_sentry()

    from db.database import IS_SQLITE, init_db

    if not IS_SQLITE and os.getenv("AUTO_MIGRATE", "true").strip().lower() not in {"0", "false", "no", "off"}:
        from db.migrate import upgrade_head

        upgrade_head()

    init_db()

    # Initialize RAG with the bundled SYNTHETIC_DEMO corpus.
    # The corpus ships with the repository and is enabled by default; set
    # ENABLE_RAG_KB=false to switch it off. A missing or corrupt corpus is
    # reported as an explicit error by server.services.rag.status() (fail
    # closed) — it must never be reported as healthy.
    import logging

    from server.services import rag as rag_service

    _logger = logging.getLogger(__name__)
    if rag_service.initialize_default():
        _logger.info("RAG ready: %s", rag_service.status())
    else:
        _logger.warning("RAG not ready: %s", rag_service.status())

    yield


app = FastAPI(
    title="gaobao-advisor",
    description="AI 高考志愿顾问 — 考研规划 — 职业方向",
    version=__version__,
    lifespan=lifespan,
)

register_admin_error_handlers(app)

# ── CORS Configuration ────────────────────────────────────
cors_origins_str = os.getenv("CORS_ORIGINS", "")
cors_origins = (
    [o.strip() for o in cors_origins_str.split(",")]
    if cors_origins_str
    else ["http://localhost:8000", "http://localhost:8501", "http://localhost:3080"]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Security Middleware ───────────────────────────────────
app.add_middleware(SecurityMiddleware)

# ── CSP Middleware (nonce-based) ──────────────────────────
app.add_middleware(CSPMiddleware)

# ── Rate Limiting Middleware ──────────────────────────────
# Defaults remain unchanged. Elevated quotas are explicit test-only configuration
# for measuring backend capacity without the 20 req/s demo limiter.
app.add_middleware(
    RateLimitMiddleware,
    rate=float(os.getenv("PUBLIC_API_RATE_PER_SECOND", "20")),
    capacity=int(os.getenv("PUBLIC_API_RATE_BURST", "40")),
)

# ── Prometheus Metrics Middleware ─────────────────────────
app.middleware("http")(metrics_middleware)

# ── Admin error envelope (only /api/v1/admin) ─────────────
app.add_middleware(AdminExceptionMiddleware)

# ── Request correlation ID (outermost so error bodies carry it) ──
app.add_middleware(RequestIDMiddleware)

# ── Mount Prometheus Metrics Endpoint ─────────────────────
app.mount("/metrics", metrics_app)

# ── Include Routers ───────────────────────────────────────
app.include_router(health_router)
app.include_router(onboarding_router)
app.include_router(chat_router)
app.include_router(profile_router)
app.include_router(data_router)
app.include_router(knowledge_router)
app.include_router(voice_router)
app.include_router(report_router)
app.include_router(admin_router)


@app.get("/")
async def root():
    """Root endpoint with API info."""
    return {
        "service": "gaobao-advisor",
        "version": __version__,
        "docs": "/docs",
        "metrics": "/metrics",
    }
