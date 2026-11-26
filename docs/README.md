# Documentation

This index is the stable entry point for the repository documentation. Current
implementation and validation status lives in [PROJECT_STATUS.md](PROJECT_STATUS.md);
dated audit records remain historical snapshots and should not be read as live state.

## Start here

- [Main README](../README.md) — project overview, architecture, tested setup paths
  and capability boundaries.
- [Tutorial](../TUTORIAL.md) — command-line no-key demo and local development.
- [FAQ / troubleshooting](FAQ-TROUBLESHOOTING.md)
- [Project status](PROJECT_STATUS.md) — implementation, integration, verification
  and external acceptance status, with live GitHub queries.

## Architecture and interfaces

- [Architecture](architecture/README.md) — routes, module ownership, data flow,
  deployment topology and source map.
- [Industrial release contract](architecture/industrial-release-contract.md) —
  deployment candidate, configuration and release gates; not a production approval.
- [Approved admissions research TaskRun](admissions-research-taskrun.md) —
  private journal, approval, worker, evidence checks and report boundary.
- [Chat research report](admissions-research-private-report-20261125.md) —
  session-owned request and report behavior.
- [API reference](../openapi.yaml) / [JSON schema](../openapi.json) — generated
  OpenAPI contract; a running service also exposes `/docs`.
- [Database schema matrix](database-schema-matrix.md)
- [ADRs](superpowers/adr/) — recorded architecture decisions.

## Guides and operations

- [Development and contribution](../CONTRIBUTING.md)
- [Java Gateway and Catalog](java-microservices.md)
- [Deployment candidate gates](deploy-checklist-updated.md)
- [Dependency policy and exceptions](dependency-exceptions.md)
- [Data license and provenance](../DATA_LICENSE.md)
- [Privacy statement and runtime boundaries](../PRIVACY.md)
- [Security policy](../SECURITY.md)
- [Third-party notices](../THIRD_PARTY_NOTICES.md)

## Validation and engineering evidence

- [Benchmark and evaluation entry point](../benchmarks/README.md)
- [Authorized research evaluation](validation/authorized-research-eval.md)
- [Context/provider budget evaluation](validation/context-ab-provider-budget.md)
- [Session snapshot validation](validation/session-compaction-snapshot-20261124.md)
- [Performance studies](performance/)
- [Security implementation notes](security/)
- [Historical audits](audits/) and [governance snapshots](governance/)
- [Reference-project governance review](governance/engineering-reference-review-2026-11-26.md)

The repository ships synthetic demonstration data only. Do not add real student
records, private conversations, credentials, or third-party content without
documented redistribution rights. A passing synthetic test is not evidence of
real model quality, real admissions accuracy, or production readiness.
