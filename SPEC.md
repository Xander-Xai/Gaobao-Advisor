# Gaobao Advisor — Current product and engineering specification

This specification describes the current application contract. Historical
acceptance snapshots and experiments belong under `docs/`; see
[`docs/PROJECT_STATUS.md`](docs/PROJECT_STATUS.md) for current evidence and
external acceptance gaps.

## Product boundary

Gaobao Advisor is a Chinese Gaokao admissions consultation application. Its
default repository data is synthetic and cannot support actual student
decisions. Licensed or otherwise authorized real data must be supplied by the
operator and independently reviewed before use.

## User-visible capabilities

1. Multi-turn consultation through a deterministic LangGraph preparation flow,
   with structured admissions queries, optional knowledge retrieval and source
   / quality checks before a response is persisted and emitted by SSE.
2. Public browsing of school, major, historical admissions and plan data exposed
   by the read-only catalog and Python data API.
3. Administrator operations for data-quality review and asynchronous jobs.
4. A narrowly routed, structured complex-research request in explicitly enabled
   non-production profiles. Chat detects a supported request pattern and links
   to a form; the user supplies scope, an authenticated session owns the run,
   and an independent operator approves an immutable request before a dedicated
   worker can invoke Deep Agents. The report is private and contains validated
   aggregates, not generated admission probabilities.

The research request route is off by default and unavailable in production and
industrial profiles. Submitting a request does not itself call a model or start
a sandbox. Ordinary consultation remains separate from research execution.

## Runtime architecture

- Vue 3 SPA for chat, data browsing and administration.
- Nginx as the HTTP entry point; Java Spring Gateway routes API traffic and
  Java Catalog provides bounded read-only catalog endpoints.
- FastAPI owns session authentication, chat/SSE, data and operator APIs.
- LangGraph is the deterministic consultation workflow. Optional LLM calls can
  draft ordinary consultation text when configured; evidence gates and
  deterministic fallbacks remain authoritative.
- MySQL stores application and admissions data; Redis supports rate limiting
  and the quality-job delivery path; a separate worker processes quality jobs.
- Complex research uses one private TaskRun journal, independent approval,
  one authorized Worker path, Deep Agents, fixed data tools, a restricted
  analysis sandbox where needed, independent evidence checks and a private
  report. It is not a distributed graph-resume or exactly-once side-effect
  contract.

The authoritative details are in [`docs/architecture/README.md`](docs/architecture/README.md)
and [`docs/admissions-research-taskrun.md`](docs/admissions-research-taskrun.md).

## Data, evidence and privacy rules

- SQL data and validated source receipts are authoritative for admissions
  facts. Conversation history, summaries and optional user preferences are
  lower-trust context and cannot override admissions evidence or approval.
- The checked-in sample dataset and corpus are synthetic. Missing or invalid
  required evidence must produce a bounded failure, review state or refusal,
  not fabricated results.
- Session credentials are capability tokens scoped to an anonymous session;
  they do not establish an account or real-world identity.
- Cross-task preferences are explicit opt-in, separately revocable and
  unavailable by default. Production use is blocked pending privacy acceptance.
- Provider calls occur only when separately configured. Repository CI does not
  make paid provider calls.

See [`DATA_LICENSE.md`](DATA_LICENSE.md), [`PRIVACY.md`](PRIVACY.md) and
[`SECURITY.md`](SECURITY.md).

## Development and acceptance

- Python support begins at 3.10; CI currently tests Python 3.10 and 3.11.
- Frontend development and CI use Node.js 20 and the committed `npm` lockfile.
- Standard full-stack synthetic Compose uses Java Gateway/Catalog, MySQL,
  Redis, FastAPI and Worker. SQLite is limited to lightweight development,
  smoke tests and isolated research journals.
- Required local checks and CI lanes are described in
  [`CONTRIBUTING.md`](CONTRIBUTING.md) and the live workflow files under
  `.github/workflows/`.
- Merge status is not production approval. Real provider, licensed-data,
  independent-gold and operational/UAT acceptance remain separate gates.

## Version and API contract

The application version is `server.__version__` in `server/__init__.py` and is
checked against `pyproject.toml` and generated OpenAPI metadata. Public API
details are generated in `openapi.yaml` and `openapi.json`; update them through
the repository's existing generation check when API behavior changes.
