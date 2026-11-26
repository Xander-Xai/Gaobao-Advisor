# Module Responsibilities and Engineering Acceptance

**Snapshot:** public `master` at `9eef17468933eff515b44321259fe4330fb3cbf9`, checked 2026-11-26.

**Status vocabulary:** `IMPLEMENTED` means source exists; `SCRIPTED_EVAL` means deterministic/fixture checks pass; `VERIFIED_LOCAL` means a specified local integration was exercised; `REAL_MODEL_VERIFIED` and `PRODUCTION_VERIFIED` require separate external evidence.

## Module responsibility ledger

| Module | Authoritative paths | Responsibility | Current evidence and boundary |
|---|---|---|---|
| Deterministic consultation | `server/routes/chat.py`, `server/graph/`, `server/services/` | Chat/SSE, profile and intent flow, SQL/RAG preparation, source and output checks | `SCRIPTED_EVAL`; synthetic data and CI do not establish real-world admissions accuracy |
| Approved complex research | `server/routes/chat_research_runs.py`, `server/routes/admissions_research_runs.py`, `server/agent/admissions_research_taskrun.py` | Owner-bound request, independent approval, private journal, worker claim/cancel/finalization, private report | Code and fixture lifecycle are integrated; external authorized acceptance remains open under #204 |
| Deep Agents research | `server/agent/admissions_deepagents.py`, `server/agent/deepagents_adapter.py` | The approved complex-research runtime and the isolated synthetic comparison adapter | SDK constructor and synthetic/fake execution are covered; provider quality and authorized real-data outcomes are not established |
| Admissions evidence | `server/services/admissions_lineage.py`, `server/agent/portfolio_sql_oracle.py`, `benchmarks/authorized_research_eval.py` | Source manifests, row lineage, bounded SQL evidence, independent aggregate/gold validation | Structural and synthetic checks exist; source rights and independent real-data labels remain external gates |
| Analysis sandbox | `server/agent/admissions_analysis_sandbox.py`, `server/agent/sandbox_runtime.py`, `Dockerfile.sandbox` | Restricted descriptive analysis of captured approved rows; independent result comparison and cleanup checks | Synthetic real-container checks exist; this is not public arbitrary-code execution or production multi-tenant certification |
| Context and memory | `server/agent/chat_context.py`, `session_compaction_store.py`, `context_policy.py`, `memory_manager.py`, `server/user_memory.py` | Bounded history, untrusted summaries, session snapshots, explicit owner-scoped preferences | Unit/integration and opt-in tests exist; provider savings and production privacy acceptance are not measured |
| Skills | `skills/`, `skills/service.py`, `server/graph/nodes/quality.py` | Select and place methodology/safety content without changing fact authority or permissions | Context-construction tests cover current paths; Skills are not factual evidence or tool grants |
| Evaluation and optimization | `benchmarks/`, `tests/test_authorized_research_eval.py`, `tests/test_harness_optimizer.py` | Independent task contracts, synthetic SQL oracle, trace checks, paired runs, review gate | CI evidence is synthetic or scripted; actual model/dataset/business evaluations are tracked separately by #104 and #147 |
| Java services and data | `java-services/`, `docker-compose.java.yml`, `docker-compose.industrial.yml` | Java Gateway and read-only Catalog; MySQL/Redis are standard local stack dependencies | CI verifies synthetic/local integration; industrial TLS, real-data rights, external operational acceptance, and production SLO remain open |
| Vue research workbench | `frontend/src/views/ChatView.vue`, `frontend/src/components/chat/ChatResearchRequest.vue`, `frontend/src/components/admin/AdminAuthorizedResearchPanel.vue` | Explicit scope request, actual TaskRun status, approval and private result views | Frontend tests and CI pass; displayed lifecycle is not evidence that a real provider or approved dataset was used |

## Required invariants

1. Admissions rules, qualification, and numeric facts come from deterministic
   code or current verifiable records—not free-form model output, summary, or
   user preference.
2. The private approved research TaskRun is the only managed research
   lifecycle. Synthetic TaskRun storage and the Native Harness remain isolated
   regression/comparison assets.
3. Model use, external data use, and sandbox execution require explicit scope,
   authorization, and budget. No paid run is automatically replayed after an
   uncertain failure.
4. A successful report is bound to the approved task, source/evidence digests,
   verified coverage, independently recomputed analysis, and verified sandbox
   cleanup. Incomplete/conflicting evidence is visible and cannot be promoted
   to a recommendation.
5. Context summaries and cross-task preferences are lower-trust than current
   task state and verified evidence. Personal preferences require explicit
   opt-in, owner binding, and revocation.
6. Synthetic test results and provider-declared usage are reported as such;
   neither establishes licensed data, factual quality, payment, privacy
   acceptance, or production readiness.

## Repeatable no-cost acceptance

Run from the repository root after installing the documented development
dependencies and generating the synthetic database where needed:

```bash
python -m pytest tests/test_admissions_deepagents.py tests/test_chat_research_runs.py tests/test_authorized_research_eval.py -q --no-cov -o addopts=''
python -m pytest tests/test_admissions_analysis_sandbox.py tests/test_admissions_research_taskrun.py -q --no-cov -o addopts=''
python -m pytest tests/test_chat_context_compaction.py tests/test_session_compaction_snapshot.py tests/test_cross_task_user_memory.py -q --no-cov -o addopts=''
python -m benchmarks.authorized_research_eval --help
python -m benchmarks.decision_card_business_eval --out /tmp/gaobao-business-baseline.json
```

The benchmark command above is the deterministic synthetic baseline. A model
run requires a separate explicit provider/model/budget authorization and must
use a distinct output path. The private real-data research evaluator requires
independently authored labels and private receipts; it does not call a model.

## External acceptance gates

| Gate | Required evidence | Tracking |
|---|---|---|
| Authorized Chat-to-report flow | Authorized non-production dataset/version, independent operator approval, approved provider/model and spend cap, actual terminal TaskRun, report/source verification, cancellation/error and session-isolation checks | [#204](https://github.com/Xander-Xai/Gaobao-Advisor/issues/204) |
| Real-provider business evaluation | Frozen reviewed task set, complete success/failure denominator, model identity, tool correctness, independent SQL/rule oracle, citations, latency, provider usage and cost | [#104](https://github.com/Xander-Xai/Gaobao-Advisor/issues/104) |
| Context/Skills/Memory evaluation and deployment acceptance | Same-model paired runs, frozen Holdout, safety/quality non-regression, authorized privacy review, operational restore/capacity checks, and user acceptance | [#147](https://github.com/Xander-Xai/Gaobao-Advisor/issues/147) |

If an authorization, independent reviewer, dataset, provider budget, or target
environment is unavailable, record the gate as `BLOCKED` or `NOT_MEASURED`; do
not replace the required evidence with a scripted controller, synthetic fixture,
or LLM-as-Judge score.

## Related architecture and implementation

- [Product capabilities and architecture boundaries](product-capabilities-and-boundaries.md)
- [Current architecture](README.md)
- [Private research lifecycle](../admissions-research-taskrun.md)
- [Research evaluation contract](../validation/authorized-research-eval.md)
