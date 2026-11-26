# Approved admissions research TaskRun (operator-only)

The synthetic journal is an isolated evaluation fixture, not a second business research runtime. The private business journal is the sole managed authority for approved business research and accepts an explicitly authorized data reference and immutable model/image/scope. This separation is deliberate: the synthetic read-only recovery policy permits bounded replay, while approved paid research must never automatically replay. Do not merge their storage or expose the synthetic engine as a production fallback. Both stores now reject the other schema under a SQLite write transaction, even if environment variables are missing; a configured private journal path is reserved before synthetic initialization. The business dispatch layer invokes the same journal claim/execute path rather than creating another lifecycle.

## Chat-submitted requests (non-production opt-in)

The Chat page has an explicit structured form backed by
`POST /api/v1/chat/research-runs`, `GET /{run_id}`, `GET /{run_id}/events`,
`POST /{run_id}/cancel` and `GET /{run_id}/report`. It uses the current chat
session's existing Bearer capability; the journal owner is a SHA-256-derived
session identifier, not a user-supplied owner. The submitted scope is limited
to two school IDs, one major ID, province, subject type, batch and at most five
continuous years. The service supplies the configured dataset/model/image and
authorization reference; those values are not caller-controlled.
`GET /options` uses that same session proof to search the configured catalog
for school and major IDs; synthetic-demo schools are excluded. The form does
not ask users to guess database identifiers.

The route is **off by default** and is unavailable when `APP_ENV=production`
or `GAOBAO_RUNTIME_PROFILE=industrial`. In a non-production environment it
requires `ADMISSIONS_RESEARCH_ENABLED=true`,
`ADMISSIONS_RESEARCH_USER_REQUESTS_ENABLED=true`,
`ADMISSIONS_RESEARCH_DATASET_REF`, `ADMISSIONS_RESEARCH_JOURNAL`,
`ADMISSIONS_RESEARCH_MODEL_ID`, `ADMISSIONS_RESEARCH_IMAGE_ID` and
`ADMISSIONS_RESEARCH_APPROVAL_REF`. Keep the journal in the existing worker-
owned private directory. The approval reference is a deployment setting and
must be independently checked by the reviewing operator; its presence alone
does not prove data rights.

Submission only creates the existing journal's `PENDING_APPROVAL` record and
is idempotent by session and request key. It does not call the provider or
start a sandbox. The operator workbench lists pending items; a distinct
authorized admin approves the immutable spec; only the existing Worker or
dispatcher executes it. The browser's SSE stream projects actual journal
states (approval, worker wait, running, terminal), without invented percent
progress. Cancellation, private report access, fail-closed verification and
no automatic paid replay use the same TaskRun lifecycle. Ordinary `/chat`
answers stay deterministic and no free-text complexity classifier starts a
run. This is an opt-in request/submission path, not production authorization,
real-model evaluation, real-data acceptance or production deployment.

This research workflow compares bounded historical evidence and returns a
verified private research report; it does not accept or audit a complete user
volunteer-choice plan. The separate synthetic decision-card experiment is not
a substitute for that missing product workflow.

The fixed worker SQL projection joins the school registry and excludes rows
whose school is marked `SYNTHETIC_DEMO_DATA`. This guard runs again in the
worker's pre-model admission-history read, so a caller that forges synthetic
IDs cannot turn demo rows into a report or provider request; an empty approved
cohort returns the existing deterministic `no_admission_history` refusal.

The operator API creates and inspects the run; a different admin approves its
exact request digest. Only a separately invoked worker can spend model tokens
or start a sandbox. No browser click triggers model calls.
When `ADMISSIONS_RESEARCH_ENABLED=true`, the legacy standalone `admissions_deepagents` CLI refuses execution before opening output or calling a provider. Managed operators must use the approved journal worker or dispatcher below. The standalone CLI remains an explicitly authorized offline validation path only when managed research is disabled; it is not a second managed lifecycle or a production fallback.

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
Fixture tests do not constitute real-data or paid-provider acceptance. The enrollment-plan projection has subject requirements but no explicit subject-stream column; coverage is not proof of selection eligibility.

The worker independently recomputes descriptive statistics from the captured SQL
history, checks the sandbox input digest, immutable image and cleanup flag, and
recomputes coverage before publishing. Before accepting a successful aggregate, the worker also checks the SDK report schema, inner task identity and both delegation receipts against exact scope and rows. An entirely empty history cohort cannot enter the descriptive sandbox: the runner returns a deterministic refusal before model invocation, and the worker repeats the scoped SQL read before persisting `FAILED` / `no_admission_history`. This produces no report, and other failures remain unclassified. Independently labeled gold may credit only that specific refusal in the managed evaluator. A verified aggregate is available as a
private no-store text response from GET /runs/{id}/report, using the same
owner/admin access rule as run inspection. Failed, expired or cancelled runs
cannot produce a report. No raw rows, personal data, or approval reference are
included. This is not a browser UI or a production/provider acceptance result.

A failed sandbox program may be repaired once by the Deep Agents planner when
the error is execution_failed, invalid_analysis_output or oracle_mismatch and
container cleanup was verified. The retry uses the same captured SQL snapshot,
image and task lease. Timeout, isolation/cleanup failure, exhausted budget or
successful execution cannot trigger a retry. The model sees only the fixed
error code and retry eligibility; the receipt records attempts. This is bounded
reflection, not unrestricted replanning or automatic paid task replay.

The manual worker and dispatcher share a single-host exclusive execution slot
for each private journal. Lock contention returns ResearchConflict before claim
and no provider call is made. A cancelled run holds the slot until its local
runner exits; a worker crash releases the OS lock but does not requeue that run.
Use one canonical journal on local disk; distributed workers and provider-wide
budget accounting require separate deployment controls.

Private journal files must have exactly one filesystem link at initialization,
transaction entry and worker-slot acquisition. This prevents accidental hard-link
aliases from creating distinct lock pathnames for one journal. The worker-owned
0700 parent remains part of the trust boundary; this is not protection against a
malicious process with the same OS identity.
