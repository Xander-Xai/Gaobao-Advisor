# Local validation results — 2026-11-28

## Candidate identity and boundaries

- Validation base: `origin/master` at `8b636db`.
- Candidate code changes are uncommitted in an isolated worktree and are not
  merged or production validated.
- No real provider was authorized or called. API billing and model quality are
  `NOT_MEASURED`.
- The historical local admissions snapshot has unverified third-party rights;
  it was read locally only. Conversation rows were excluded from the evaluation
  copy. No dataset, credentials, prompt text, or private run receipts are in
  this report.

## Remote baseline audit

- `origin/master` was `8b636dbc76c35f85714cb3f6d74294eb9f9a2737` at audit time.
- Exact-head CI and CodeQL runs for that SHA were completed successfully.
- Open PRs observed: #215, #217, #218 and #221.
- Issues #104, #147 and #204 were still OPEN with the external acceptance
  blockers described in the issue bodies; the v3.0.0 Preview remains a
  synthetic prerelease, not a production release.
- Existing eval assets included 3 graph-contract smoke cases, 30 scripted
  read-only harness cases, an 8-case synthetic decision-card baseline, and 20
  synthetic ground-truth draft cases in `PENDING_HUMAN_REVIEW`. The authorized
  research evaluator is an offline contract for externally supplied private
  gold/receipts; no valid authorized real-data manifest was present.
- Deterministic graders cover named contracts and SQL-oracle checks. Scripted
  stubs are explicitly not model comparisons. Real provider tokens, billing,
  admissions accuracy and user quality were `NOT_MEASURED`.
- The current candidate worktree is a synthetic demo configuration. The local
  browser flow therefore returned the expected synthetic disclaimer and
  degraded/no-provider answer.

## Evaluation assets

| Asset | Result | Evidence boundary |
|---|---:|---|
| Existing deterministic business baseline | 8/8 | Synthetic demo, independent SQL oracle; not real admissions accuracy |
| Existing scripted read-only harness | 30/30 | Contract compliance only; no model calls |
| TaskRun lifecycle regression | 8/8 | Synthetic/injected-worker outcomes |
| TaskRun crash recovery | 1/1 | Synthetic abrupt process-exit injection |
| Candidate consultation/security/runtime set | 145 cases | 35 SQL, 20 RAG, 20 multi-turn, 20 refusal/edge, 30 security, 20 runtime; all `PENDING` |
| Candidate complex-research scopes | 25 | All partial-coverage candidates; existing research contract cannot accept them as formal gold |
| Human-reviewed cases | **0** | No reviewer confirmation was fabricated |

The candidate JSON, manifest and CSV queue are preserved as private artifacts.
The one-off candidate-generation source is not present in the persistent
validation artifacts. The v2 split and review queue can be regenerated from the
preserved v1 candidate file, but regenerating the oracle-labeled case content
from the snapshot is therefore **not currently reproducible**. This is an
additional Phase 2 delivery gap and part of the `HOLD` verdict.

The private v2 candidate split is 100 development / 45 holdout (31% holdout).
A connected-component audit found zero institution-entity overlap across the
two splits; policy RAG cases also keep each corpus file in one split. The
policy RAG candidates bind to the shipped synthetic corpus version/file hash;
the corpus has no effective-date metadata, so no effective date is invented.
The 25 research cases are not formal gold: they all require partial research,
while the existing contract requires each split to contain a fully verified
case, and their sandbox-analysis digests require a reviewed independent
aggregate. The private review queue and data manifest remain local.

## Four-layer evaluation

1. **Component:** an independent SQL oracle cross-check on the isolated local
   snapshot passed 137/137 checks: admission scores, enrollment-plan totals,
   track-aware rank lookup, and match-school source bounds.
2. **Trajectory:** the existing read-only 30-case policy contract passed. The
   scripted Context A/B run observed no holdout regression but is not a model
   experiment.
3. **Outcome:** deterministic synthetic decision-card baseline passed 8/8;
   lifecycle and crash-recovery figures above remain synthetic.
4. **System/business:** local API readiness and UI routes were exercised. The
   browser completed one synthetic consultation through the SQLite demo stack.
   Complex-research acceptance in an enabled, authorized environment was not
   performed.

## Correctness finding and candidate correction

The prior one-score-one-rank implementation omitted `subject_type` from its
primary query. In the audited snapshot, 23,499/40,645 province/year/score keys
(57.82%) occur under multiple tracks. A physics-track query could therefore
return a history-track rank. The local cross-check observed 10 mismatches in 15
track probes before correction, including large rank differences.

The candidate correction now prefers an exact requested track, then a
documented legacy equivalent (`物理类` → `理科`, `历史类` → `文科`) when the exact
track is absent, and labels the actual track and reduced confidence. Scores
below the minimum band now refuse a rank instead of reusing the lowest band's
rank. After the change, the independent cross-check observed 0 mismatches in
15 track probes. Seven regression tests cover exact track preference,
no cross-track leakage, legacy-track fallback ordering, short-alias fallback,
below-band refusal in both lookup paths, and track-isolated admission-score
fallback. Another test verifies the slash-qualified Deep Agents model ID. The
changes are not yet merged.

The selected SiliconFlow model ID `Qwen/Qwen3-8B` initially failed the
Deep Agents model-ID allowlist because it contains one namespace slash. The
candidate allowlist now accepts one safe slash-separated provider ID while
still rejecting traversal-like or punctuation payloads. This validates only
local model-ID syntax; provider tool-calling compatibility and model behavior
remain unverified because no real call was made.

## Tests and experiments actually run

| Command / run | Result |
|---|---|
| Full Python suite on candidate tree | **1,940 passed, 46 skipped**, 0 failed |
| `make PYTHON=/home/dev/projects/gaobao/gaobao-advisor/.venv/bin/python test-integration` | **40 passed** with isolated MySQL 8.4 and Redis 7 containers |
| Security/session/Sandbox focused suite | 213 passed |
| Scripted Context A/B | 4/4 holdout and 4/4 development per arm; observation bytes 15,613 → 12,211 (−21.8%); provider tokens and model effect `NOT_MEASURED`; `NOT_ELIGIBLE_SCRIPTED` |
| SQL/RAG paired synthetic benchmark | serial P95 35.91 ms; parallel P95 39.23 ms; decision `HOLD`; no production parallelization claim |
| Local synthetic Docker Compose (SQLite, API, Vue frontend, Nginx) | API readiness, frontend health and Nginx health returned 200 |
| Browser smoke via Nginx | synthetic consultation rendered a structured card and synthetic-data disclaimer; response marked degraded because no model key was configured |
| Full Java/MySQL/Redis/Worker Compose overlay | **Blocked**: Docker registry TLS handshake timeout resolving `eclipse-temurin:21-jre-alpine`; no full-stack readiness claim |
| Locust | **Not measured**: local Locust entrypoint cannot import `zope.event` because system and user-site `zope` namespaces conflict |
| Concurrency diagnostic (Python stdlib; one synthetic request per concurrent client) | 1/5/10/20 clients all 200; observed p50/p95 total latency respectively 200/200, 208/263, 302/576, 641/1,237 ms; sanitized receipt retained privately; no sustained-load, percentile-confidence, or SLA claim |

The first `make test-integration` attempt using the system interpreter failed
because that interpreter lacks optional `PyMySQL`. Rerunning with the project
virtualenv, which has `PyMySQL`, passed all 40 integration tests. No test was
skipped to obtain a passing result and no assertion was weakened.

## Acceptance matrix

The requested business thresholds are not scored against the private candidate
set because none of its cases has independent human approval. Their formal
numerators and denominators are therefore `0 / 0` (excluded: all pending
candidate cases); the synthetic diagnostics below do not substitute for them.

| Metric / threshold | Formal numerator / denominator | Observed state |
|---|---:|---|
| Route accuracy ≥95% | 0 / 0 | `NOT_MEASURED`; no reviewed evaluation cases |
| SQL fact accuracy ≥98% | 0 / 0 | `NOT_MEASURED`; separate internal query agreement diagnostic was 60/60 on one sampled province/year |
| Tool execution success ≥98% | 0 / 0 | `NOT_MEASURED` for real model/business execution |
| Business task success ≥90% | 0 / 0 | `NOT_MEASURED`; synthetic baseline 8/8 is separate |
| Complex research success ≥85% | 0 / 0 | `NOT_MEASURED`; 25 partial-coverage candidate scopes are not formal gold |
| Required trajectory constraints ≥95% | 0 / 0 | `NOT_MEASURED` on reviewed holdout; synthetic policy contracts 30/30 are component regressions |
| RAG Recall@10 ≥85% | 0 / 0 | `NOT_MEASURED`; no human-reviewed policy gold |
| Evidence support ≥95% | 0 / 0 | `NOT_MEASURED` on business answers |
| Multi-turn consistency ≥90% | 0 / 0 | `NOT_MEASURED` on reviewed holdout |
| Correct refusal ≥95% | 0 / 0 | `NOT_MEASURED` on reviewed holdout |
| Severe admissions-fact hallucinations = 0 | 0 / 0 | `NOT_MEASURED`; candidate cases await review |
| Unauthorized execution / cross-user leak / sandbox escape / duplicate side effects = 0 | 0 / 0 formal adversarial acceptance cases | Named unit/integration regressions passed; this does not certify the full threat surface |

| Gate | Result |
|---|---|
| Deterministic and scripted regression | PASS for the named synthetic suites |
| Real data internal quality audit | Completed locally; third-party rights remain unverified; **not authorized E3 data** |
| Independent reviewed Golden Set | **HOLD** — 0 cases human-reviewed |
| Real-model Canary / Development / Holdout | **BLOCKED** — no provider/model/budget authorization or API key |
| Harness model ablation | **NOT_MEASURED** — scripted smoke is not model evidence |
| Security unit/integration matrix | PASS for the named tests; not a sandbox certification or full OWASP coverage claim |
| MySQL/Redis test integration | PASS for the 40-test targeted integration command |
| Full Java/Gateway/Catalog production-style deployment | **HOLD** — image pull/build blocked |
| Performance / costs | Partial diagnostic only; provider costs `NOT_MEASURED`; Locust unavailable |
| Business UAT / production acceptance | **BLOCKED** — no authorized environment or independent business sign-off |

## Overall verdict

**HOLD.** Local synthetic tests, database quality analysis, query cross-checks,
and a minimal synthetic Docker/UI smoke were completed. Formal acceptance is
blocked by human review, provider authorization, data-rights confirmation,
full deployment verification, and business UAT. This is not
`LOCAL_ACCEPTANCE_PASS`, `CONDITIONAL_PASS`, or `PRODUCTION_VALIDATED`.

## Minimum next actions

1. An independent reviewer must review the private candidate queue and approve
   only cases whose evidence and expected outcomes they can verify.
2. The owner must confirm the provider, pinned model ID, data-transfer policy,
   and spend ceiling. Keys should be injected through the existing environment
   secret mechanism, never sent in chat or committed.
3. Obtain written data rights/source evidence before treating any real snapshot
   as authorized E3 evaluation data.
4. Retry the full Compose image build when the configured registry can resolve
   the Java runtime base image; then run readiness, backup/restore and a
   controlled full-stack E2E.
5. Repair the local Locust Python namespace/dependency environment or run the
   existing load suite in the project CI/container environment before setting
   performance targets.
