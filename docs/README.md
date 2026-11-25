# Documentation

Product-facing documentation:

- [Architecture](architecture/README.md) — components, LangGraph flow, RAG
  chain, data flow, worker flow, deployment topology, source map.
- [Industrial release contract](architecture/industrial-release-contract.md) — current business paths, deployment candidate, release gates and open evidence.
- [Approved admissions research TaskRun](admissions-research-taskrun.md) — existing operator-approved Deep Agents lifecycle and the non-production Chat request surface.
- [Tutorial](../TUTORIAL.md) — no-key demo and reproducible scripts.
- [Data licence and provenance](../DATA_LICENSE.md)
- [Privacy](../PRIVACY.md)
- [Security policy](../SECURITY.md)
- [Database schema matrix](database-schema-matrix.md)
- [RAG demo](rag-demo.md)
- [FAQ / troubleshooting](FAQ-TROUBLESHOOTING.md)
- [Dependency policy](dependency-exceptions.md)

Engineering evidence:

- [ADRs](superpowers/adr/) — architecture decision records.
- [ADR-010: Chat-submitted admissions research](superpowers/adr/ADR-010-chat-submitted-admissions-research.md) — explicit request, same journal, independent approval and no automatic model execution.
- [Java microservices](java-microservices.md) — standard Java Gateway + read-only
  Catalog overlay: startup, CI gates, distributed rate limiting, deployment
  boundaries.
- [Performance](performance/) — MySQL/worker benchmarks, Redis delivery
  comparison, and the Java/Python load methodology.
- [Security notes](security/) — admin auth, sensitive-data handling, test
  isolation.
- [Governance ledger](governance/repository-audit.md) — historical state and
  action log.
- [Current repository lookup](../README.md#仓库状态怎么查) — GitHub branch, PR,
  Issue and Actions sources.
- [2026-11-07 repository snapshot](governance/repository-status-2026-11-07.md) —
  architecture, acceptance and governance decisions for that baseline.
- [Audits](audits/) — redacted governance/security reports.

Default demo data is synthetic. Do not add real admissions databases, private
conversations, third-party corpora without redistribution rights, or credentials
to any documentation, example or screenshot.

- [Industrial public HTTP admission](security/industrial-public-http-admission.md) — Redis atomic admission, failure and deployment boundaries.
