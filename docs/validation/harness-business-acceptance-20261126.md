# Harness and business acceptance snapshot — 2026-11-26

This is a dated synthetic/local engineering acceptance record, not a live
status page, real-provider result, licensed-data attestation, or production
approval. GitHub state below was checked on 2026-11-26.

## Live repository state at review

- Default branch: `master`; PR #210 was merged at
  `b63e9dc5c029b01b25d3c4916aceb965bd42bf15`.
- Exact merge-head [CI](https://github.com/Xander-Xai/Gaobao-Advisor/actions/runs/45783708130)
  and [CodeQL](https://github.com/Xander-Xai/Gaobao-Advisor/actions/runs/45783708035)
  completed successfully. CI included Python 3.10/3.11 tests, frontend tests/build,
  MySQL/Redis admin smoke, full Compose E2E, no-key Docker container and
  restricted admissions Sandbox checks, optional Deep Agents SDK construction
  without provider calls, lint, security, redistribution and secret scans.
- The PR #210 exact head `f78cc43ffd19c3259217364391f89435bc96b8a1` also passed
  its full required CI and CodeQL checks before merge.
- Open external acceptance Issues: [#104](https://github.com/Xander-Xai/Gaobao-Advisor/issues/104),
  [#147](https://github.com/Xander-Xai/Gaobao-Advisor/issues/147), and
  [#204](https://github.com/Xander-Xai/Gaobao-Advisor/issues/204). No open PRs
  or formal GitHub Releases were listed at review time.

## Existing business paths and boundaries

| Direction | Current entry, scope and tools | Output and evidence |
|---|---|---|
| Institution/major research | Chat `/api/v1/chat/research-runs` form or operator `/api/v1/admin/admissions-research/runs`; two schools, one major, province/subject/batch and at most five continuous years. Independent approval precedes the existing worker and Deep Agents; fixed SQL tools collect history and enrollment plans. | Actual TaskRun journal state drives SSE/status. Only verified/private report is returned. Managed runs require the pinned Sandbox analysis against the bounded captured history projection. Synthetic rows are excluded. `tests/test_chat_research_runs.py`, `test_admissions_research_taskrun.py`, and `test_admissions_deepagents.py` cover injected worker, approval, source changes, missing evidence, cancellation/expiry, ownership and report checks. Feature is disabled by default and production/industrial profiles do not expose it. |
| Admissions data analysis | Existing descriptive-analysis Sandbox used by the approved research worker; parameterized SQL obtains bounded rows, then pinned Docker image executes submitted Python with no network, read-only input, non-root identity and resource/output/time limits. Host independently recomputes supported statistics and verifies cleanup/receipt. | Verified descriptive statistics only, not admission probability or qualification. `tests/test_admissions_analysis_sandbox.py`, `test_admissions_deepagents.py`, `test_admissions_research_taskrun.py`; main CI No-Key Container Smoke and Full Compose E2E passed. |
| Volunteer-plan review | No dedicated user-facing submission/audit route or accepted plan schema. Existing `/api/v1/admin/agent-experiments/decision-card` is a synthetic-only decision-card fixture. | Synthetic eight-case SQL-oracle and negative-control evidence only; it is not a review of a user's actual plan. A future feature needs an agreed input schema, deterministic rule/data coverage and bounded output contract. No duplicate Issue was opened. |

Skills used on ordinary consultation live under `skills/gaokao/`; the approved
research agent loads its bounded research Skill from code. Tool access is
explicitly constrained to SQL-backed evidence collection, report submission,
and optionally the restricted analysis Sandbox. Native Harness remains an
isolated synthetic comparison baseline; it is not a second business executor.

## Verification performed in the isolated current-head worktree

The focused suites below were run locally from a worktree based on exact current
head `b63e9dc`. Runtime code is unchanged by the local governance patch; the
Sandbox regression adds coverage for timeout handling and verified cleanup.

```text
python3 -m pytest tests/test_admissions_deepagents.py tests/test_chat_research_runs.py tests/test_admissions_research_taskrun.py tests/test_taskrun_deepagents_worker.py tests/test_deepagents_checkpoint_journal_guard.py tests/test_deepagents_durable_tool_ledger.py tests/test_admissions_analysis_sandbox.py tests/test_authorized_research_eval.py tests/test_context_ab_eval.py tests/test_harness_optimizer.py tests/test_decision_card_business_eval.py -q --no-cov -o addopts=''
117 passed, 3 skipped

python3 -m pytest tests/test_synthetic_decision_card_e2e.py tests/test_decision_card_business_eval.py tests/test_decision_card_model_planner.py tests/test_skill_service.py tests/test_context_governance.py tests/test_chat_context_compaction.py tests/test_session_compaction_snapshot.py tests/test_cross_task_user_memory.py tests/test_chat_sse.py -q --no-cov -o addopts=''
126 passed

python3 -m pytest tests/test_admissions_analysis_sandbox.py -q --no-cov -o addopts=''
8 passed (including the timeout/cleanup regression)
```

Additional checks at this worktree state: `ruff check .`,
`ruff format --check .` (487 files), `git diff --check`, 22 frontend test files
(128 tests), frontend production build, local Markdown-link validation for the
changed documentation, and the no-provider synthetic decision-card and Context
smoke commands. The standard Java/MySQL/Redis topology's Compose configuration
parsed successfully with a temporary generated `.env.full`, which was removed
afterward. The stack was not launched locally because host port 80 is already
occupied by an unrelated running service. The latest Java workflow run
([`45629195155`](https://github.com/Xander-Xai/Gaobao-Advisor/actions/runs/45629195155))
passed Maven verification and Java/MySQL/Redis Compose smoke on
`1a1f37315c30f0730a6ca33e7f406bcfb488d361`; this is not an exact-`b63e9dc` run.
No Java toolchain is installed in this local environment.

A full local `python3 -m pytest tests/ --tb=short` attempt was terminated by the
execution environment's 240-second command limit at 34% progress and produced
no pytest summary; this is an incomplete local run, not a test failure or pass.
The exact `b63e9dc` CI Python 3.10 and 3.11 full-suite jobs both passed, and the
focused local suites above cover the changed test and related business paths.

The deterministic `decision_card_business_eval` run covered all 8/8 synthetic
cases (4/4 Holdout), including 4 safe abstentions; it used
`DETERMINISTIC_OBSERVATION_POLICY_NOT_LLM`, with provider usage and real
admissions accuracy `NOT_MEASURED`. The scripted Context comparison completed
one no-provider trial over 4 Holdout observations per arm with no scripted
regression, but was marked `NOT_ELIGIBLE_SCRIPTED`; token reduction, provider
equality and model effectiveness remain `NOT_MEASURED`.

Negative-path coverage includes independent-approval failures, cancellation,
expired TaskRun leases without paid replay, worker/report failure, missing or
changed evidence, cross-session access denial, and Sandbox timeout with verified
cleanup and no result publication. The synthetic Eval `ProviderBudget` tests
exercise request caps, reported-usage stop thresholds and unknown-usage stop;
the token threshold is response-after accounting, explicitly not a hard billing
cap. The research SDK has a per-run model-call limit and per-call timeout, but
no real provider run was authorized in this acceptance.

The approved research evaluator's `--help` and the repeated-model experiment's
`--help` were inspected. The former is offline and requires private receipts or
managed-run IDs plus separately reviewed gold. The latter's non-execution mode
is a dry-run plan; actual execution requires explicit flags, provider access and
authorization. No provider was called, no account key or paid budget was used,
and no authorized real admissions data or human gold was supplied.

## Required external continuation

- #104 E1/E3: authorized provider and spend limit, approved dataset/source
  manifest, independent gold and complete real run traces.
- #147 E2/E4: approved same-model single-variable trials, independent quality
  review, protected environment, privacy/security review, recovery/capacity
  checks and business-user UAT.
- #204: enable the opt-in research flow only in an authorized non-production
  environment and verify real terminal worker state, report checks, cancellation
  and cross-session isolation.
- User-facing volunteer-plan review remains a product-scope gap pending an
  explicit schema/rule/output contract; it is not an external-provider blocker.

Keep these Issues open until their stated evidence exists. Production remains
behind the current feature flags and industrial release gates.
