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

## Local validation work, 2026-11-28

This is a dated local validation snapshot against `origin/master` at
`8b636dbc76c35f85714cb3f6d74294eb9f9a2737`. Exact-head CI and CodeQL passed.
At audit time, PRs #215/#217/#218/#221 and Issues #104/#147/#204 were open; the
v3.0.0 Preview remained a synthetic prerelease.

### Existing evaluation assets

The default branch contained 3 graph-contract smoke cases, 30 scripted
read-only harness cases, an 8-case synthetic decision-card baseline, and 20
synthetic ground-truth draft cases (`PENDING_HUMAN_REVIEW`). The authorized
research evaluator is an offline contract for private gold and receipts; no
authorized real-data manifest was present. Scripted stubs do not measure model
quality, provider tokens, billing or real admissions accuracy.

### Local admissions snapshot and isolated ETL

A local read-only audit found a four-year historical admissions snapshot.
Its redistribution rights are not independently verified, so it is not
authorized E3 data and was not sent to any external model. The ETL made an
isolated schema-compatible copy and emptied personal conversation tables; the
original snapshot was not modified. Full hashes, source path, schema manifest,
private receipts, gold cases and review queue remain outside Git with
owner-only permissions.

| Domain | Local snapshot coverage |
|---|---:|
| Admission history | 400,194 rows, 2022–2025, 31 source provinces |
| Institutions / majors | 3,020 / 215; 22 major categories |
| One-score-one-rank | 66,420 rows; 30 provinces |
| Enrollment plans | 14,665 rows; 2024–2025 only |
| Populated `min_score` / `min_rank` | 100% / 97.69% |
| Populated `avg_score` / `max_score` | 0.02% / 0% |

Important scope: school-level rows (`major_id IS NULL`) and major-level rows
must not be joined as if they had the same grain; Hainan uses a 900-point scale;
province in history/plans is the candidate source province. Major-level
history exists only for 2024. The sample includes private conversation rows,
but those rows were excluded from the evaluation copy. See the private local
manifest for file hash and full aggregate data-quality details.

### Candidate gold and human review

145 consultation/security/runtime candidate cases were prepared privately:
35 SQL, 20 RAG, 20 multi-turn, 20 edge/refusal, 30 security and 20 runtime.
A separate 25-scope complex-research candidate set is partial-only and does
not satisfy the existing research contract's positive/full-coverage split
requirement. The 145-case v2 split is 100 development / 45 holdout (31%
holdout), with 0 institution-entity leakage across splits. All 145 remain
`PENDING`; **0 cases were human reviewed**. The one-off case generator source
is not present in the persistent tree, so regenerating the oracle-labeled case
content is not yet reproducible. These candidates are not formal gold and are
excluded from quality denominators.

### Agent, harness, security and system checks

| Check | Local result | Boundary |
|---|---|---|
| Full Python suite | 1,940 passed, 46 skipped | Synthetic/unit/integration fixtures; not real-model acceptance |
| MySQL 8.4 + Redis 7 targeted integration | 40 passed | Isolated local containers |
| Security/session/Sandbox focused suite | 213 passed | Named regressions only; not sandbox certification |
| Synthetic deterministic business baseline | 8/8 | Independent SQL oracle over fictional data |
| Scripted read-only harness | 30/30 | Not an LLM comparison |
| TaskRun lifecycle / crash recovery | 8/8 / 1/1 | Synthetic/injected-worker outcomes |
| Scripted Context A/B | 4/4 holdout and 4/4 development per arm | Observation bytes 15,613 → 12,211; tokens/model benefit `NOT_MEASURED` |
| SQL/RAG serial vs parallel | P95 35.91 / 39.23 ms | Decision `HOLD`; no significance or production gain |
| Private SQL-oracle/application comparison | 137 checks, 0 mismatches | Internal agreement only; no source correctness claim |
| Local synthetic SQLite Compose + browser consultation | API, frontend, Nginx health 200; synthetic disclaimer rendered | No real model; response was degraded/no-key |
| Full Java/MySQL/Redis/Worker Compose | Blocked | Registry TLS timeout resolving Java runtime base image |
| Locust | Not measured | Local Python namespace conflict prevents `zope.event` import |
| One-request-per-client synthetic latency diagnostic | 1/5/10/20 clients all HTTP 200 | Not sustained load or SLA; P50/P95 200/200, 208/263, 302/576, 641/1,237 ms |

The SQL-oracle cross-check receipt and candidate case files remain private, but
the one-off cross-check helper source is not retained in the repository. The
137-check result is recorded evidence, not yet a fully reproducible benchmark
command.

### Candidate correctness fixes

The default-branch one-score-one-rank lookup omitted `subject_type`; in the
local snapshot, 23,499/40,645 province/year/score keys (57.82%) occur under
multiple tracks. The candidate correction now prefers the requested track,
uses explicit track aliases when needed, keeps fallback queries track-specific,
and refuses scores below the available band. A private cross-check changed from
10 mismatches in 15 probes to 0. Regression tests also verify the fallback
source track. The Deep Agents model-ID allowlist accepts one slash-qualified
provider model ID such as `Qwen/Qwen3-8B`. These changes are in PR #222 and are
not yet merged; provider tool-call compatibility has not been verified.

No real provider call was made, provider usage/cost is `NOT_MEASURED`, the
human-reviewed gold count is zero, full Compose is blocked, and no business UAT
or production sign-off was performed. The verdict remains **HOLD**; none of
these results closes E1–E4 or establishes `PRODUCTION_VALIDATED`.

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
