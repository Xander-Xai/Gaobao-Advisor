# Documentation

Product-facing documentation:

- [Architecture](architecture/README.md) — components, LangGraph flow, RAG
  chain, data flow, worker flow, deployment topology, source map.
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
- [Java microservices](java-microservices.md) — optional Java Gateway + read-only
  Catalog overlay: startup, CI gates, distributed rate limiting, deployment
  boundaries.
- [Performance](performance/) — MySQL/worker benchmarks, Redis delivery
  comparison, and the Java/Python load methodology.
- [Security notes](security/) — admin auth, sensitive-data handling, test
  isolation.
- [Governance ledger](governance/repository-audit.md) — historical state and
  action log.
- [Repository status](governance/repository-status-2026-11-07.md) — current
  architecture/acceptance snapshot and the PR/Issue/branch decisions taken on
  2026-11-07.
- [Audits](audits/) — redacted governance/security reports.

Default demo data is synthetic. Do not add real admissions databases, private
conversations, third-party corpora without redistribution rights, or credentials
to any documentation, example or screenshot.
