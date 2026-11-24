# Approved admissions research TaskRun (operator-only)

The synthetic journal remains synthetic. The private business journal accepts
an explicitly authorized data reference and immutable model/image/scope.
The operator API creates and inspects the run; a different admin approves its
exact request digest. Only a separately invoked worker can spend model tokens
or start a sandbox. No browser click triggers model calls.

Configure ADMISSIONS_RESEARCH_ENABLED=true, ADMISSIONS_RESEARCH_DATASET_REF
(an operator-approved identifier; not independently verified provenance),
ADMISSIONS_RESEARCH_JOURNAL (absolute SQLite path in a worker-owned 0700
directory), DATABASE_URL (authorized data source), OPENAI_API_KEY, and the
existing Deep Agents and immutable local Docker image. Keep credentials
and private data outside Git. API routes:
POST /api/v1/admin/admissions-research/runs, GET /runs/{id},
POST /runs/{id}/approve (spec_sha256), POST /runs/{id}/cancel under the same
prefix. Maker cannot approve their own run. Worker:

```bash
python -m server.agent.admissions_research_taskrun --run-id research_<id> --authorize-model-call
```

Worker checks lease before every model step and before/after SQL and sandbox
execution. Cancellation stops future actions and publication, not an already
in-flight provider call. Expired leases are not automatically replayed.
The journal retains coverage, verified aggregate and hashes, not raw SQL rows
or provider secrets. This is a private local operator workflow, not anonymous
chat, distributed queue, hard process cancellation or production deployment.
Admin RBAC and actual source authorization are deployment requirements.
Fixture tests do not constitute real-data or paid-provider acceptance.
