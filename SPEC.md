# Gaobao Advisor — 高考志愿 AI 顾问

## Versioning

The canonical application version is declared in `server/__init__.py::__version__`
(currently **3.0.0**). `pyproject.toml` and `openapi.json` / `openapi.yaml` must
match it; `tests/test_version_consistency.py` enforces this so packaging and
runtime cannot drift again.

## Overview

Gaobao Advisor is an AI-powered college-application consultation workflow for the Chinese Gaokao system. The **public demo ships only a small, deterministic `SYNTHETIC_DEMO` dataset** (8 schools, 12 majors, 128 admission-score rows) plus an original 8-group synthetic knowledge corpus. It does **not** ship real admissions data or third-party corpora, and its output must not be used for real application decisions.

The source retains an import path for user-supplied or licensed data, and the private upstream project had a larger knowledge base; none of that is redistributed here (see `DATA_LICENSE.md` and `THIRD_PARTY_NOTICES.md`). The project targets Chinese high school students and their families, and runs as a FastAPI backend with a **Vue 3** SPA frontend, using **LangGraph** as the conversation state machine, deployable via Docker Compose with an nginx reverse proxy.

## Architecture

### Service Layer
- **FastAPI server** (`server/main.py`) — primary HTTP entry point with async support, CORS, rate limiting, security middleware, and Sentry monitoring
- **LangGraph workflow** (`server/graph/graph.py`) — 17-node state machine for multi-turn conversation flow with conditional branching (profile completeness check), offline reasoning-context assembly + streaming token emission
- **SQLite database** (`db/database.py`) — embedded persistence with WAL mode; 20 tables covering schools, majors, admission scores, enrollment plans, subject rankings, graduate programs, career trends, conversations, feedback, quality jobs, admin audit and yi-fen-yi-duan
- **Hybrid RAG** (`server/services/rag.py`) — BM25 keyword + optional dense retrieval over the bundled **SYNTHETIC_DEMO** corpus (`knowledge/synthetic_demo/`, 8 groups, 31 chunks); enabled by default, no key required, with citations and fail-closed error states. See README "RAG 能力" and `docs/rag-demo.md` for current behaviour.
- **Voice pipeline** (`server/services/voice.py`) — WebSocket-based ASR -> Graph -> TTS loop using DashScope API for phone-like interaction
- **Quality modules** (`quality/`) — 7 modules: AI-era risk detection, anti-pattern checking, cross-validation, emotion detection, decision heuristics, model selector, and contextual knowledge loader
- **Skills framework** (`skills/`) — plugin-style skill modules with Gaokao-specific consulting logic; 4 original markdown assets (mental models, heuristics, anti-patterns, safety rules) plus a fail-closed `SkillService`
- **Profile system** (`server/user_profile.py`, `server/routes/profile.py`) — 7-field user profiling (required: province, score, subject, interest; optional: region, family, goal) with SoulQuery engine

### Frontend
- **Vue 3 SPA** (`frontend/`) — Pinia state management, Tailwind CSS, SSE streaming chat with auto-scroll, WebSocket voice interaction, scene switching (gaokao/kaoyan/career)
- **nginx reverse proxy** (`nginx.conf`) — serves frontend static assets, proxies `/api/` to FastAPI, proxies `/ws/` with WebSocket upgrade

### Data Pipeline
- **Data importers** (`scrapers/`) — Baidu Gaokao parallel scrapers with checkpoint-based continuation for 30 provinces
- **Database schema** — 20 tables (school, major, admission_score, enrollment_plan, subject_ranking, graduate_program, graduate_score, career_trend, yi_fen_yi_duan, dataset_snapshot, conversation, conversation_message, feedback, highlight, quality_score, data_quality_job, data_quality_issue, quality_task_outbox, admin_token_revocation, operation_audit_log)

## Key Design Decisions

See `docs/superpowers/adr/ADR-001-fastapi-migration.md`:
- **ADR-001**: FastAPI over Streamlit (separation of concerns, SSE streaming, concurrent connections)
- **ADR-002**: LangGraph workflow (state machine for multi-turn conversation)
- **ADR-003**: SQLite + WAL (zero-dependency embedded DB with concurrent readers)
- **ADR-004**: Hybrid RAG (vector + keyword for recall quality)
- **ADR-005**: DashScope Voice (ASR + TTS for phone-like interaction)

## API Contracts

| Method | Path | Description | File |
|--------|------|-------------|------|
| GET | `/api/v1/health` | Health check (liveness + DB + optional capabilities) | `server/routes/health.py` |
| GET | `/api/v1/health/live` | Liveness probe (dependency-free) | `server/routes/health.py` |
| GET | `/api/v1/health/ready` | Readiness probe (DB required, RAG optional) | `server/routes/health.py` |
| POST | `/api/v1/chat` | SSE-streaming chat (LangGraph backed) | `server/routes/chat.py` |
| POST | `/api/v1/chat/feedback` | Session-owner feedback write (Bearer) | `server/routes/chat.py` |
| POST | `/api/v1/chat/highlight` | Session-owner highlight write (Bearer) | `server/routes/chat.py` |
| GET | `/api/v1/data/schools` | School search | `server/routes/data.py` |
| GET | `/api/v1/data/scores` | Admission scores query | `server/routes/data.py` |
| GET | `/api/v1/data/plans` | Enrollment plan lookup | `server/routes/data.py` |
| POST | `/api/v1/knowledge/search` | Knowledge base search (RAG) | `server/routes/knowledge.py` |
| GET | `/api/v1/knowledge/quotes` | Quote attribution retrieval | `server/routes/knowledge.py` |
| POST | `/api/v1/onboarding` | User onboarding submission | `server/routes/onboarding.py` |
| POST | `/api/v1/report/generate` | Generate report (Bearer, session owner) | `server/report/routes.py` |
| GET | `/api/v1/report/{report_id}` | Fetch report JSON (Bearer, session owner) | `server/report/routes.py` |
| GET | `/api/v1/report/{report_id}/html` | Fetch report HTML (Bearer, session owner) | `server/report/routes.py` |
| GET | `/api/v1/report/{report_id}/cover.svg` | Fetch report SVG cover (Bearer, session owner) | `server/report/routes.py` |
| GET | `/api/v1/profile/{session_id}` | Get user profile | `server/routes/profile.py` |
| PUT | `/api/v1/profile/{session_id}` | Update user profile | `server/routes/profile.py` |
| GET | `/api/v1/profile/{session_id}/next-question` | Get next profiling question | `server/routes/profile.py` |
| POST | `/api/v1/profile/{session_id}/skip` | Skip profiling question | `server/routes/profile.py` |
| WS | `/ws/call` | Real-time voice call (ASR -> Graph -> TTS) | `server/routes/voice.py` |

## LangGraph Workflow (17 nodes)

```
security_scan → intent_detect → scene_route → slot_extract → profile_check
                                                                    │
                                          ┌─────────────────────────┼──────────────────────────────┐
                                          ▼                         ▼                              ▼
                                   has_reply                  incomplete                     complete
                                          │                         │                              │
                                          ▼                         ▼                              ▼
                                    question_generate ──→ render_reply             quality_orchestrate
                                                                  │                         │
                                                                  ▼                         ▼
                                                              memory_update           data_query + rag_retrieve
                                                                                          │
                                                                                          ▼
                                                                                    reason → structure_output
                                                                                          │
                                                                                          ▼
                                                                             source_attribution → render_reply
                                                                                                      │
                                                                                                      ▼
                                                                                                  memory_update
```

The graph uses conditional branching at `profile_check`: incomplete profiles trigger a `question_generate → render_reply` shortcut; complete profiles run the full quality → data → RAG → reasoning → structured output pipeline.

## Two-Phase Streaming

The `/api/v1/chat` endpoint uses a two-phase architecture:
1. **Phase 1 (Sync):** `graph.invoke()` runs the full LangGraph pipeline synchronously, producing structured metadata (slots, emotion, structured card) plus a grounded deterministic template (`reply_source="template"`)
2. **Phase 2 (Streaming):** with a configured LLM key, tokens stream via `llm_node_stream()`; without a key (or on LLM failure), the grounded template is streamed and a `degraded` notice is emitted. The LLM is never called during `graph.invoke()`, avoiding double LLM calls.

SSE events emitted: `slots`, `emotion`, `structured`, `sources` / `rag_status`, `token`, `degraded`, `done` (with HMAC-signed session_token), and `error` if the graph fails

## Data Flow

```
User (browser/mobile)
  |
  v
nginx (:80)
  |-- /api/*  --> FastAPI (port 8000)
  |                |-- /api/v1/chat      --> LangGraph 17-node workflow + SSE stream
  |                |-- /api/v1/data/*    --> SQLite (schools, scores, plans, yi-fen-yi-duan)
  |                |-- /api/v1/knowledge/* --> Hybrid RAG (SYNTHETIC_DEMO corpus)
  |                |-- /api/v1/profile/* --> SQLite (user_profile slots)
  |                |-- /api/v1/onboarding --> SQLite (onboarding)
  |                |-- /api/v1/health    --> in-memory status
  |-- /ws/*       --> FastAPI (WS)
  |                     |-- /ws/call     --> VoiceService (ASR -> Graph -> TTS)
  |-- /           --> Vue 3 SPA (port 80)
```

## Deployment

- **Docker Compose** — three services: `api` (FastAPI), `frontend` (nginx serving Vue 3), `nginx` (reverse proxy on port 80)
- **Environment config** via `.env` (LLM API key, DashScope key, Sentry DSN, CORS origins)
- **No external DB dependency** — SQLite file lives in `./data/`
- **Security headers** configured in nginx (HSTS, CSP, X-Frame-Options, etc.)

## Testing Strategy

- **pytest** with httpx ASGI transport for integration tests (85 `test_*.py` files, **1234 collected**; the last local run was 1200 passed / 34 skipped, coverage 81.92% — the skipped ones require a live MySQL/Redis and run in CI)
- Key test suites:
  - `tests/test_agent_core.py` — core agent logic
  - `tests/test_chat_sse.py` — SSE streaming chat
  - `tests/test_langgraph.py` — LangGraph workflow
  - `tests/test_middleware_*` — security and rate limiting middleware
  - `tests/test_integration_e2e.py` — end-to-end conversation flows
  - `tests/test_integration_rag.py` — RAG retrieval quality
  - `tests/test_quality_*.py` — quality module unit tests
  - `tests/test_quote_attribution.py` — source attribution
  - `tests/test_p1_features.py`, `tests/test_p2_features.py` — phased feature validation
- Target: 70%+ coverage (enforced via pytest-cov)
