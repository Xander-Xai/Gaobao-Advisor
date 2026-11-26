# Engineering conventions

This file records codebase conventions. Agent-specific workflow and workspace
safety rules live in [`AGENTS.md`](AGENTS.md); contributor setup and exact test
commands live in [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Runtime and repository shape

| Area | Current implementation |
|---|---|
| Web frontend | Vue 3, Vite, Pinia, Vue Router; Node.js 20 in CI |
| Python backend | Python 3.10+; CI tests 3.10 and 3.11; FastAPI, Pydantic, LangGraph |
| Standard full-stack profile | Java 21 Gateway/Catalog, FastAPI, MySQL 8.4, Redis, Worker and Nginx via Compose |
| Lightweight development | SQLite for local smoke and isolated experiments; it is not the standard full-stack persistence layer |
| Research runtime | One explicit operator-approved Deep Agents Worker and private TaskRun journal |
| Knowledge retrieval | Synthetic BM25 corpus by default; external embedding providers require explicit configuration |

Important source areas:

- `server/routes/` — HTTP, SSE and WebSocket transport.
- `server/graph/` — deterministic consultation state and graph nodes.
- `server/agent/` — bounded research, journal, evidence and sandbox services.
- `db/`, `migrations/` — SQLAlchemy schema, contracts and Alembic migrations.
- `java-services/` — Gateway and read-only Catalog.
- `frontend/src/` — user interface and API clients.
- `benchmarks/`, `tests/` — evaluation tools and regression evidence.
- `docs/architecture/`, `docs/superpowers/adr/` — architecture references and decisions.

Use the source and tests as the authority when an older tree or test count in a
dated document differs from the current code.

## Coding and testing

- Follow neighboring style, keep changes scoped, preserve public API and data
  contracts, and document intentional error/fallback behavior.
- Use Ruff (`ruff check .`, `ruff format --check .`) for Python quality gates.
- Keep tests deterministic and network-free unless the test explicitly targets
  an isolated integration boundary. Never use a paid model in default CI.
- Changes to schema, APIs, session ownership, approvals or Sandbox boundaries
  require contract and negative-path tests appropriate to the risk.
- Use `python -m pytest tests/` for the Python suite and `cd frontend && npm ci
  && npm test -- --run && npm run build` for frontend checks.
- The exact required CI checks are in GitHub branch protection and
  `.github/workflows/`; check them against the current PR head.

## Safety and data

- Keep repository fixtures synthetic or demonstrably redistributable. Never
  commit real student data, private conversation exports, working credentials or
  third-party corpora without rights.
- Parameterize data access and validate all scope/identity/approval boundaries
  in code. Model output cannot grant permissions or self-verify admissions facts.
- Do not restore historical sensitive blobs or rewrite public history. Follow
  [`SECURITY.md`](SECURITY.md) for vulnerability reports and
  [`DATA_LICENSE.md`](DATA_LICENSE.md) for dataset provenance.
- CI success, a merge, and a synthetic evaluation are distinct from real-data,
  real-model or production acceptance.

## Git and review

- Default branch: `master`. Use focused branches and commits; PRs must pass the
  current required checks and resolve review conversations.
- Do not weaken branch protection or bypass scans to obtain a merge.
- Document user-visible or operator-facing contract changes in the appropriate
  current guide and OpenAPI files when relevant.
