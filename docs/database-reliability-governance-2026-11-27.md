# Database and data reliability review — 2026-11-27

## Snapshot and evidence classes

- Reviewed remote default branch `master` at `8b636dbc76c35f85714cb3f6d74294eb9f9a2737`.
- Review occurred in a separate worktree because the primary checkout had
  unrelated uncommitted user changes. The source checkout's prior state was
  preserved.
- Runtime verification used an isolated MySQL Community 8.4.13 container,
  Redis 7 container and synthetic benchmark/test schemas. No real admissions
  records, user sessions or production services were used.
- Exact-head remote CI and CodeQL workflow were successful for the baseline
  head; no Java Services workflow was attached to that exact SHA. Current live
  Actions state can change and is not a production deployment result.

## Findings and decisions

### Schema, source quality and publication

- School/major master keys are numeric IDs; current natural-name uniqueness is
  not a global stable-ID contract across institution renames or program-code
  changes. There is no verified authorized importer/source dataset in this
  repository. The industrial release flow instead requires a reviewed,
  hash-pinned manifest and read-only, consistent MySQL snapshot audit.
- `uq_admission_score` includes nullable `major_id`. MySQL unique indexes permit
  multiple `NULL` values, so this constraint alone does not prevent duplicate
  school-level rows. The existing `audit_admissions_release` checks logical
  school-level identity/ambiguity before an approved release; do not treat the
  database constraint as an import idempotency guarantee. No destructive
  natural-key conversion was attempted: there is no supported application
  importer, and release-published history is gated by logical-identity audit.
  Any future bulk-import path must add a NULL-safe database uniqueness strategy
  with duplicate preflight before it is authorized for use.
- `DatasetSnapshot` remains demo provenance metadata; imported data lineage and
  release eligibility are guarded by existing reviewed manifests and row
  digests. A hash or approval reference does not independently prove rights or
  authenticity. Real data rights and correctness remain externally blocked.
- Added one index from MySQL plan/latency evidence. Detailed SQL, execution plan,
  measured synthetic result and limits: `performance/mysql-admissions-history-index-20261127.md`.

### Transactions and Outbox

- Job row, Outbox row and creation audit commit in one MySQL transaction; Redis
  receives only the ID after commit. Job creation uniqueness, atomic DB claim,
  execution attempt fencing, issue deduplication and startup/periodic lease
  recovery already exist; no second Outbox/recovery system was added.
- Integration checks exercised MySQL migration/admin operations and real Redis
  queue delivery, duplicate delivery, worker races, fencing and stale-lease
  recovery. Delivery remains at-least-once, not exactly-once. Redis loss is
  recoverable from MySQL state; external provider calls with uncertain outcomes
  are not blindly replayed.

### Agent fact boundary, SQLite and privacy

- Industrial history lookup is parameterized SQL and binds exact year,
  province, cohort, rank and reviewed lineage. Current policy RAG is gated off
  for industrial release because shipped corpus is synthetic. Missing or
  ambiguous data fails closed; SQL rank facts remain authoritative.
- `docker-compose.prod.yml` is explicitly labeled a legacy production-auth
  smoke setup and uses SQLite; `docker-compose.industrial.yml` requires explicit
  external MySQL/Redis and turns off DB auto-create/seed. The canonical standard
  full profile is MySQL + Redis + Java Catalog/Gateway + FastAPI/worker.
- Session Bearer ownership, cross-session negative cases, idempotent deletion,
  live-data erasure scope and external/backup deletion limits have existing
  tests and `PRIVACY.md` disclosure. Conversation body/snapshots are not all
  encrypted at rest; optional preference values are not encrypted; account
  identity and backup erasure are outside current verification. No legal
  compliance claim is made.
- On the isolated restore clone, a dedicated Catalog-style principal was
  granted SELECT only on `schools`, `admission_scores`, and `enrollment_plans`;
  SELECT on `conversations` and INSERT into `schools` both returned MySQL
  privilege error 1142. This is a synthetic least-privilege check, not a
  production credential audit.
- Deep Agents research journal/checkpoint remains isolated and is not described
  as distributed graph recovery. No new persistence component was introduced.

## Changes in this review

- Added migration `20261127_0010` and ORM/schema-contract registration for
  `ix_adm_history_scope_rank (province, year, subject_type, major_id, min_rank)`.
- Kept legacy adoption viable by allowing the pre-index historical baseline to
  verify old schemas, then adding the index in the new forward migration.
- Added real-MySQL tests for index definition and upgrading from previous head
  `20261125_0009` while preserving an existing score row.
- Added/updated ADR, schema matrix, measured SQL report, this review record and
  MySQL backup/restore runbook.
- A `mysqldump --single-transaction` restore drill on MySQL 8.4.13 restored all
  24 tables and synthetic demo rows (8 schools, 384 scores) to a separate
  schema; every table row count and Alembic head `20261127_0010` matched. The
  benchmark dataset restore also checked 110,000 admission-score rows and
  119,520 total non-marker rows. Temporary archives and scratch schemas are
  under `/tmp/opencode/mysql-restore-drill/` and were not added to Git.

## Verification and boundary

Commands and results from the isolated worktree:

| Verification | Result | Evidence class |
|---|---:|---|
| `MYSQL_TEST_URL=... uv run --locked python -m pytest tests/test_mysql_migrations.py -q --no-header --no-cov` | 8 passed | Real MySQL 8.4.13; isolated disposable schema, synthetic migration marker. |
| `uv run --locked python -m pytest tests/test_migrations.py -q --no-header --no-cov` | 9 passed | SQLite migration/smoke. |
| `MYSQL_TEST_URL=... REDIS_TEST_URL=... uv run --locked python -m pytest tests/test_admin_mysql.py tests/test_admin_limits_redis.py tests/test_redis_worker.py tests/test_worker_integration.py tests/test_queue_recovery.py -q --no-header --no-cov` | 34 passed | Real MySQL 8.4.13 + Redis 7, synthetic jobs and recovery faults. |
| `uv run --locked python -m pytest tests/test_session_data_erasure.py tests/test_industrial_session_lifecycle.py tests/test_user_profile.py tests/test_cross_task_user_memory.py tests/test_memory_privacy_expiry.py -q --no-header --no-cov` | 75 passed | Synthetic FastAPI/session privacy and cross-owner negative cases; SQLite fixtures. |
| `uv run --locked python -m pytest tests/test_industrial_admissions_history.py tests/test_admin_quality_jobs.py -q --no-header --no-cov` | 81 passed | Synthetic SQL/manifest, refusal, quality-job and state-machine cases. |
| `uv run --locked ruff check ...` and `ruff format --check ...` on changed Python files | passed | Static style checks. |
| MySQL 8.4.13 before/after `EXPLAIN ANALYZE` and 30-sample query comparison | p50 15.267→4.838 ms; p95 17.630→8.272 ms | Real database engine with 110,000 synthetic score rows, not admissions data. |
| MySQL `mysqldump --single-transaction` to separate schema; compare all tables/rows/head | 24 tables and every row count matched; head `20261127_0010` | Real MySQL 8.4.13, synthetic demo records. |
| Catalog-style SELECT-only grants on restored scratch schema | public catalog SELECT succeeded; private-table SELECT and INSERT denied with 1142 | Real MySQL privilege enforcement, temporary test principal. |

MySQL/Redis results are real dependency integration against isolated synthetic
data. Benchmark values are not real admissions data or production SLAs. Java runtime
integration was not runnable in this environment: host Java/Maven are absent
and Docker failed to resolve `maven:3.9.11-eclipse-temurin-21` and
`eclipse-temurin:21-jre-alpine` through the configured registry mirror (TLS
handshake timeout). Exact-head CI and CodeQL passed for baseline
`8b636dbc`; the nearest prior Java Services workflow was successful on
PR #213's SHA, not this exact head. Authorized admissions data release, external rights review,
production privacy approval, TLS ingress and production backup/restore
acceptance remain separate gates and are not inferred from these checks.
