# ADR-011: MySQL authoritative business data and storage boundaries

- Status: Accepted
- Date: 2026-11-27
- Scope: structured admissions facts, business state, deployment tiers and SQL read paths

## Context

The service has durable facts (schools, majors, admission scores/ranks, enrollment
plans), session and operator state, an at-least-once quality-job queue, and a
separate private research journal. Those records have different durability and
ownership requirements. The current code already supports MySQL 8.4 migrations,
Redis-backed delivery, SQLite smoke use and a physically separate research
journal. This decision records the authoritative boundary and the measured
history-query index addition; it does not authorize a production release.

## Decision

1. MySQL 8.4/InnoDB is the authoritative store for structured admissions facts,
their reviewed release evidence, conversations, quality-task state, and the
transactional Outbox. Database migrations use one Alembic head. The existing
`DatasetSnapshot`/release-manifest workflow remains the source/version audit
boundary; no new store or parallel import pipeline is introduced.
2. Redis is transport and runtime infrastructure (quality-job IDs, rate
   limiting). Queue delivery is at-least-once; MySQL state, idempotency
   constraints, conditional transitions and worker fencing are authoritative.
3. SQLite remains a local smoke/development option and the explicitly isolated
   research-journal implementation. The Deep Agents SQLite checkpoint remains
   an experiment, not a claim of distributed graph recovery.
4. Java Catalog is a parameterized SELECT-only projection over the same MySQL
   schema; FastAPI remains the Agent/query orchestration boundary. Neither
   vector retrieval nor session memory may supersede SQL admissions facts.
5. Keep MySQL; PostgreSQL and a vector database are not introduced absent a
   demonstrated business requirement and comparative acceptance evidence.
6. Add the measured index
   `(province, year, subject_type, major_id, min_rank)` for the bounded
   industrial historical-admissions query. This is an additive migration and
   does not change query semantics or the table's business key.

## Alternatives considered

| Alternative | Decision |
|---|---|
| Move business state to PostgreSQL | Rejected: no measured need, migration benefit or validated compatibility case. |
| Store admissions facts in Redis/vector search | Rejected: weaker durable relational constraints and not authoritative for exact facts. |
| Replace existing Outbox/worker with another broker or lifecycle | Rejected: existing recovery/fencing path is integrated and covered by real MySQL/Redis tests. |
| Add broad indexes from column names alone | Rejected: only the admissions index above was adopted after MySQL 8.4 plan and latency comparison. |

## Consequences and acceptance

- Query semantics remain exact-year/province/cohort and bounded rank-neighborhood;
  unsupported or unapproved records still fail closed.
- Migration `20261127_0010` is reversible by dropping only its index. Apply to a
  production database only after the operator's verified backup and restore
  path; MySQL DDL is not assumed transactional.
- Automated and local MySQL measurements use synthetic rows only. They do not
  establish actual admissions-data quality, production latency, privacy
  compliance or service availability.
- Production remains blocked on authorized data, independent review, privacy,
  TLS, backup/restore acceptance and release gates in
  `docs/architecture/industrial-release-contract.md`.
