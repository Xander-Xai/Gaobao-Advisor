# Project status and acceptance ledger

This is the single maintained status entry for Gaobao Advisor. It describes the
checked-in implementation and evidence boundaries, not a promise of production
service. Verify live GitHub state at the time of use; do not copy old audit
snapshots as current status.

## Status vocabulary

These states describe separate questions and may coexist:

- `IMPLEMENTED` — implementation exists in the repository.
- `INTEGRATED` — a supported application path invokes it.
- `VERIFIED` — named automated checks or acceptance tests exercise it.
- `MERGED` — code is present on the default branch.
- `PRODUCTION_VALIDATED` — an authorized real deployment and acceptance have
  been completed. This status is **not currently claimed**.
- `BLOCKED` — required external authorization, resource or environment is absent.
- `NOT_MEASURED` — no valid measurement is available.

## Current product and architecture

| Capability | Code path / business integration | Evidence | Status and boundary |
|---|---|---|---|
| Standard consultation | `server/routes/chat.py` → deterministic intent routing → `server/graph/` preparation → SQL/RAG evidence and final-answer gates → persisted SSE response | Exact-release SHA CI [`fbcf979f`](https://github.com/Xander-Xai/Gaobao-Advisor/actions/runs/45883688788) | IMPLEMENTED / INTEGRATED / MERGED / VERIFIED for synthetic behavior; real admissions quality NOT_MEASURED |
| Dedicated volunteer-plan audit | No user-facing plan submission or audit route | [`tests/test_synthetic_decision_card_e2e.py`](../tests/test_synthetic_decision_card_e2e.py), [`benchmarks/decision_card_business_eval.py`](../benchmarks/decision_card_business_eval.py) cover synthetic decision cards only | NOT IMPLEMENTED as a user workflow; synthetic admin fixture is not user-plan validation or an admissions recommendation; input/output/rule contract required before scope |
| Data-source manifest and row lineage | Industrial SQL evidence path in `server/services/industrial_admissions.py` and manifest validation | PR #203 and its tests, merged before reviewed baseline | IMPLEMENTED / INTEGRATED / MERGED; real-source authorization remains external |
| Chat complex-research request | `server/routes/chat.py` → structured form → `server/routes/chat_research_runs.py` → session-owned private journal; request does not execute a model | PRs #205–#208; `tests/test_chat_research_runs.py` | IMPLEMENTED / INTEGRATED / MERGED / VERIFIED in synthetic/injected-worker tests; feature is opt-in, not production validated |
| Approved research execution | `server/routes/admissions_research_runs.py` → `server/agent/admissions_research_taskrun.py` → dedicated worker → `server/agent/admissions_deepagents.py` | Managed TaskRun, approval and evidence-integrity tests; optional SDK no-network CI smoke | IMPLEMENTED / INTEGRATED; real provider/data and operational deployment NOT_MEASURED |
| SQL/RAG and provenance | MySQL/SQLAlchemy, `server/services/rag.py`, BM25 and optional configured embeddings | CI tests, redistribution gate and source/row-lineage regressions | VERIFIED against bundled synthetic data; genuine data rights and source correctness need external review |
| Context and session snapshots | `server/agent/chat_context.py`, encrypted SQL snapshot path and explicit opt-in preference vault | Unit/integration regressions and standard local Compose CI | IMPLEMENTED / INTEGRATED; personal-data privacy acceptance and real-user value NOT_MEASURED |
| Skills and Sandbox | `skills/gaokao/`; bounded admissions analysis sandbox and separate coding fixtures | Synthetic safety tests and CI Docker isolation probes | IMPLEMENTED; bounded test scope VERIFIED; production multi-tenant isolation NOT validated |
| Agent Eval and optimizer gates | `benchmarks/`, independent SQL/gold checks and review-only optimization registry | CI artifacts are synthetic/scripted, with short retention | IMPLEMENTED / VERIFIED for the stated fixtures; real model outcome, cost and improvement NOT_MEASURED |
| Full-stack application | Vue 3, FastAPI, Java Gateway/Catalog, MySQL, Redis, Worker and Compose | Exact-release SHA CI and Java Services workflow; local Java/Gateway standard topology run | IMPLEMENTED / INTEGRATED / MERGED / VERIFIED for synthetic behavior; no production deployment claim |
| Internal model resource governance | `server/services/llm_governor.py` (provider RPM, TPM, lease-based concurrency, Redis Lua admission) → `server/services/llm_gateway.py` (the only provider request path) → every model call site; `server/agent/governed_chat_model.py` for the framework-internal Deep Agents calls | [`tests/test_llm_governor.py`](../tests/test_llm_governor.py), [`tests/test_llm_governor_redis.py`](../tests/test_llm_governor_redis.py) (real Redis 7), [`tests/test_llm_governance_coverage.py`](../tests/test_llm_governance_coverage.py); design and coverage matrix in [`docs/architecture/llm-resource-governance.md`](architecture/llm-resource-governance.md) | IMPLEMENTED / INTEGRATED / VERIFIED for synthetic behaviour with a mock provider; the shipped quota values are a **TEST configuration**, not a real provider contract — real quota admission, provider 429 behaviour and multi-instance production behaviour are NOT_MEASURED |

## Verification snapshot

PR #213 was merged as `fbcf979ff22bc8a3746106f99f0217568ba70837` on
2026-11-26. Exact-head [master CI](https://github.com/Xander-Xai/Gaobao-Advisor/actions/runs/45883688788),
[CodeQL](https://github.com/Xander-Xai/Gaobao-Advisor/actions/runs/45883688638),
and [Java Services](https://github.com/Xander-Xai/Gaobao-Advisor/actions/runs/45883688559)
completed successfully. CI included Python 3.10/3.11, Ruff, MySQL/Redis/Admin,
frontend tests/build, no-key container and Sandbox checks, full Compose, Java
Maven/HTTP smoke, security, redistribution and secret scans, and optional Deep
Agents SDK construction without provider calls. The local standard Java/Gateway
topology was also built and exercised with synthetic data. These are engineering
verification results, not real-provider, real-data or production acceptance.

The [v3.0.0 Preview](https://github.com/Xander-Xai/Gaobao-Advisor/releases/tag/v3.0.0)
is a GitHub prerelease source distribution at the SHA above. It is explicitly a
synthetic demo; the release does not claim real admissions service readiness.

The exact current head and its checks are always obtained from GitHub. Avoid
copying a SHA into additional documents as a live pointer.

## External acceptance still pending

| Acceptance | Status | Required evidence / owner dependency |
|---|---|---|
| E1: real provider experiment on frozen synthetic business cases | BLOCKED / NOT_MEASURED | Explicit provider/model authorization, operator-managed key and budget; complete runs, usage, failure denominator and independently checked results |
| E2: Context / Skills / Memory comparison | BLOCKED / NOT_MEASURED | Same approved model/version, frozen independent cases, controlled single-variable arms, complete cost/latency/quality receipts and independent review |
| E3: authorized real admissions data and independent gold | BLOCKED | Data rights and version manifest, independently prepared/reviewed gold, operator approval and protected data environment |
| E4: production-like deployment and UAT | BLOCKED | Authorized environment, privacy/security review, TLS/credential controls, backup/restore and failure recovery, actual user acceptance and operational sign-off |

No production metrics, real admissions accuracy, model quality improvement,
token savings, cost savings or SLA are asserted here. Synthetic evidence cannot
close these gates.

## Open acceptance Issues

- [#104](https://github.com/Xander-Xai/Gaobao-Advisor/issues/104) owns E1 and
  E3: real-provider business evaluation and authorized admissions evidence.
- [#147](https://github.com/Xander-Xai/Gaobao-Advisor/issues/147) owns E2 and
  E4: controlled context/harness comparisons and production-like acceptance.
- [#204](https://github.com/Xander-Xai/Gaobao-Advisor/issues/204) records the
  user-facing research integration; implementation is merged, while enabled
  environment acceptance remains pending and is not a real-model result.
- [#216](https://github.com/Xander-Xai/Gaobao-Advisor/issues/216) owns the
  real-provider acceptance of the LLM Resource Governor: the shipped quota
  values are a test configuration, and the admission, the degradation under a
  real 429 and the shared budget across two instances are not yet measured.

Keep all three open until their explicitly scoped acceptance criteria are met;
do not count old Draft PR or historical SHA state as current evidence.

## Live repository status commands

From an authenticated Git checkout with `gh` installed:

```bash
git fetch origin --prune
git status --short
git branch -avv
gh repo view Xander-Xai/Gaobao-Advisor
gh pr list --repo Xander-Xai/Gaobao-Advisor --state open
gh issue list --repo Xander-Xai/Gaobao-Advisor --state open
gh run list --repo Xander-Xai/Gaobao-Advisor --limit 30
gh release list --repo Xander-Xai/Gaobao-Advisor
```

Use `gh run list --commit <sha>` and inspect required checks for the exact
candidate commit before merge. Do not force-push or rewrite published history.

Related entry points: [README](../README.md), [documentation index](README.md),
[architecture](architecture/README.md), [industrial release gates](architecture/industrial-release-contract.md).
