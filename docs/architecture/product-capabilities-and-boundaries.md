# Product Capabilities and Architecture Boundaries

**Snapshot:** public `master` at `22d2dc3f01b95cc0d8ae807d7b8673e865fcafae`, checked 2026-11-26.

**Purpose:** describe the shipped product architecture and distinguish code,
synthetic validation, external acceptance, and production readiness. The current
master documentation head is #209; that closeout is documentation-only. Runtime
and CI evidence below is pinned to the named implementation snapshot and its
exact Actions runs.

## Product purpose

Gaobao Advisor supports high-school admissions consultation, admissions-data
exploration, institution and major research, and decision support. It combines
deterministic business rules and SQL-backed facts with bounded model use in an
explicitly approved complex-research workflow.

Admission eligibility, policy constraints, numeric admissions facts, and source
citations must come from deterministic rules or verifiable data. Model prose,
conversation history, summaries, and user preferences cannot replace those
authoritative sources.

Career planning, employment outlook, and career-direction questions are valid
product capabilities. They use the product's career scene, knowledge content,
and available employment data; they are distinct from repository-maintenance
documentation.

## Runtime paths

### Normal consultation

The Vue application calls the Python FastAPI chat endpoint through the standard
Java Gateway/Nginx topology. Python owns chat/SSE, authorization, the
preparation-only LangGraph, deterministic SQL/RAG orchestration, final answer
checks, and persistence. Java Gateway routes requests and applies its catalog
boundary; Java Catalog provides bounded read-only catalogue queries. MySQL is
the standard data store, Redis supports throttling/worker coordination, and a
separate Python worker handles quality jobs.

Simple factual consultation remains on the deterministic chat path. SQL and
verified source identifiers are authoritative for admissions facts; RAG
provides cited explanatory material where available. Missing, stale, conflicting,
or out-of-scope evidence must remain visible and can require refusal or review.

### Approved complex research

The authorized research lifecycle is owned by one private TaskRun journal:

1. A user explicitly submits a bounded structured research scope.
2. The request is tied to its owner and immutable specification digest.
3. A different administrator approves that exact digest.
4. A separately invoked worker claims the run and invokes the existing Deep
   Agents research engine under the approved scope and budget.
5. Fixed SQL tools collect admissions history and plan evidence; the analysis
   sandbox receives only the approved captured projection.
6. The worker checks task-bound receipts, coverage, sandbox cleanup, and an
   independent deterministic aggregate before persisting a private report.

The Chat intent detector only offers the structured form. It does not extract
school/major IDs, create a TaskRun, authorize model spending, run SQL, or launch a
sandbox. Browser activity cannot approve or execute work. Expired paid tasks
are not automatically replayed. The journal provides single-host task
durability, not distributed queue semantics or cross-process graph resume.

The enabled Chat-to-report flow is not production or real-data accepted. Issue
[#204](https://github.com/Xander-Xai/Gaobao-Advisor/issues/204) tracks authorized
non-production acceptance; Issues [#104](https://github.com/Xander-Xai/Gaobao-Advisor/issues/104)
and [#147](https://github.com/Xander-Xai/Gaobao-Advisor/issues/147) track
real-provider, independent-gold, data authorization, and external evaluation
requirements.

## Agent SDK and tools

Deep Agents is the complex-research engine; ordinary chat does not import or
run it as a second default engine. The Native Harness is retained as a bounded
synthetic comparison baseline. The SDK receives a narrow set of typed tools
for an already approved task. Admissions rows are queried by fixed,
parameterized host-side SQL; arbitrary model-generated SQL, public shell,
unrestricted filesystem access, and user-profile access are not tools in the
admissions research agent.

Subagents are appropriate only where the independent research dimensions need
separate bounded work. Current history and enrollment-plan specialists use
fixed projections and return task-bound receipts. The host reconstructs and
validates final evidence; model-generated final prose is not accepted as an
evidence receipt.

External provider execution requires explicit task approval, a fixed model
identity, a configured key, and operator-controlled budget. No default CI job
spends external model funds. Provider-reported usage is not an independent
invoice or a hard billing cap.

## Analysis sandbox

The admissions analysis sandbox runs generated Python only against the
previously captured, bounded data projection. Its Docker baseline disables
networking, mounts inputs read-only, uses a non-root user, read-only root,
resource and output limits, timeout, and verified cleanup. The host independently
recomputes supported descriptive statistics and rejects mismatched output.

These controls and real-container synthetic tests are useful containment
evidence, not production multi-tenant sandbox certification. Do not expose
arbitrary public code execution or pass host credentials, Docker socket, user
home directories, or database credentials to generated code.

## Context, memory, and Skills

Current task state, verified SQL/tool receipts, recent dialogue, optional
compaction summaries, and explicitly opted-in cross-task preferences are
separate trust classes. Summaries are untrusted context; they do not establish
admissions facts, policy, permissions, approval, or citations. Current SQL/RAG
evidence and system instructions are not silently truncated to meet a context
budget. Compaction snapshots are session-bound, versioned, time-limited, and
encrypted where enabled by the configured local policy. Production privacy
acceptance for memory features remains separate.

Skills provide bounded methodology and safety guidance. Retrieved evidence and
Skill instructions remain distinct context channels. A Skill is not a source
of admissions facts and cannot extend tool permissions.

## Evaluation and optimization

The repository contains deterministic SQL-oracle evaluations, synthetic task
fixtures, TaskRun lifecycle checks, trace contracts, Context A/B tooling, and an
offline optimization-review gate. These support regression and controlled
experiments; they are not evidence of real admissions accuracy or measured
real-model quality. Objective facts and numeric values require independent SQL
or business-rule verification. Human review is required for explanation
quality and independently authored gold labels. LLM-as-Judge is not the sole
correctness oracle.

Optimization follows failure analysis, a bounded candidate change, paired
same-model regression, safety/quality gates, and human review. Holdout labels,
permissions, refusal requirements, and historical evidence must not be changed
to manufacture improvement. No Agent automatically modifies a production
prompt, tool grant, or gold standard.

## Deployment and release boundary

The standard local stack consists of Vue, Nginx, Java Gateway/Catalog, Python
FastAPI and worker, MySQL, and Redis. The implementation baseline at
`9eef17468933eff515b44321259fe4330fb3cbf9` passed Python tests, frontend quality,
MySQL/Redis integration, full synthetic Compose E2E, optional Deep Agents SDK
construction without provider calls, Docker sandbox checks, lint, security
audit, secret scan, and CodeQL.

CI uses synthetic data and does not establish real-data rights, real-provider
quality, user acceptance, privacy approval, public production readiness,
multi-host task durability, or a production SLA. The industrial Compose profile
is a release candidate with external authorization and deployment gates, not a
production approval. See the [industrial release contract](industrial-release-contract.md)
and current [architecture overview](README.md).

## Reference documents

- [Authorized admissions research TaskRun](../admissions-research-taskrun.md)
- [Authorized research evaluation contract](../validation/authorized-research-eval.md)
- [Current project status and acceptance ledger](../PROJECT_STATUS.md)
- [Java Gateway and Catalog responsibilities](../java-microservices.md)
- [Module responsibilities and engineering acceptance](module-responsibilities-and-acceptance.md)
