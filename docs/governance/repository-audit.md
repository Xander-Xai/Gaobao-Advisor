# Repository governance audit — 2026-10-31

> **Current state pointer:** the authoritative repository state is
> [`docs/governance/repository-status-2026-11-07.md`](repository-status-2026-11-07.md).
> This file is the 2026-10-31 historical ledger and is not updated for later changes.
>
> **Final addendum (2026-10-31, end of day):** the authoritative final report is
> [`docs/audits/2026-10-31-gaobao-final-governance.md`](../audits/2026-10-31-gaobao-final-governance.md).
> Current state: `master` code baseline `0c7ff79` (Issue #58 report follows), **0 Open
> PRs**, **Issue #58 OPEN/BLOCKED** (historical 72 MiB database exposure; Phase A–D
> evidence in `docs/audits/gaobao-issue-58-remediation.md`),
>
> **Status update (2026-11-07):** Issue #58 was closed on GitHub as **CLOSED / NOT_PLANNED / ACCEPTED_RISK**
> by owner disposition (2026-10-31 13:50 UTC); historical objects/PR refs were not purged and
> redistribution authorization is not established — closure is administrative risk acceptance,
> not remediation. Statements below dated 2026-10-31 describe the then-current OPEN state.
> **13 required checks** (original 10 + CodeQL), secret scanning/push protection
> and Dependabot security updates enabled, CodeQL **0 open alerts**. Do not reuse
> the earlier "0 open issues / 10 checks" snapshot as current. Issue #58
> Support-first recheck: non-PR refs are clean; the 17 sensitive commits are
> reachable only through 14 PR refs; no force push is required. Support request
> remains unsubmitted pending owner authorization.
>
> Scope: `Xander-Xai/gaobao-advisor`, default branch `master`.
> This ledger records the state found in Phase 0, the asset decision, and the
> final action. It is intentionally factual; branch/PR/issue state must be
> rechecked after every mutation.

## 0. Verified post-governance baseline (2026-10-31)

| Item | Observed |
|---|---|
| Default branch | `master` |
| `master` SHA | `52b96a553f9df6f7a1bfa40cf7ea4197ee2ba8c5` |
| Open PRs | 0 |
| Open issues | none |
| Closed issues | #10, #22, #24, #36, #37, #38, #39, #40 |
| Remote branches | 1 (`master`) + archive tag |
| Worktrees | only `/home/dev/projects/gaobao/gaobao-advisor` |
| Branch protection | **ENABLED** on `master` (required checks, no force/delete, admins enforced) |
| Required checks | 10: Lint, Test 3.10/3.11, MySQL+Redis+Admin Smoke, Security, Secret, Frontend, Container, Redistribution, Full Compose E2E |
| Baseline master CI | **10/10 SUCCESS** (`52b96a5`, [run 41183132938](https://github.com/Xander-Xai/gaobao-advisor/actions/runs/41183132938)) |
| PR #35 CI | 9/9 success at `1f3afab` (run 41119912593), merged `b98225a` |
| PR #31 CI | 9/9 success at `51b8936` (run 41120400909), merged `4d05916` |
| PR #43 CI | 10/10 success at `000d5a4` (run 41151056672), merged `e647226` |
| PR #44 CI | 10/10 success at `d44fe14` (run 41155009440), merged `6e1760c` |
| PR #46 CI | 10/10 success at `886ab63` (run 41160654590), merged `ab80a55` |

## 1. Remote branch inventory

| Branch | HEAD | merge-base | ahead/behind vs master | Unique assets | Decision |
|---|---|---|---|---|---|
| `master` | `ab80a55` | — | — | canonical baseline; full SHA in Section 0 | protected; no force push/delete |
| `fix/haibote-acceptance-hardening-20261031` | `1f3afab` | `1622c3f` | merged via #35 | PR #35 fixes + reliability work | **deleted** after merge |
| `docs/portfolio-reference-boundaries-20261030` | `51b8936` | `65bdb40` | merged via #31 | README boundary section only | **deleted** after merge |
| `feat/vue3-ts-readonly-data-workbench-20261030` | `8fe4f61` | `65bdb40` | 1 ahead / 10 behind | old read-only TS prototype + design doc | **deleted**; archived tag `archive/vue3-ts-readonly-20261030` |
| `feat/haibote-phase1-admin-workbench-20261030` | `4b81aaa` | `4b81aaa` | ancestor of master | none | **deleted** |
| `feat/haibote-phase2-mysql-redis-worker-20261030` | `aab68c2` | `aab68c2` | ancestor of master | none | **deleted** |
| `feat/haibote-phase3-acceptance-20261030` | `4a3c6d5` | `4a3c6d5` | ancestor of master | none | **deleted** |
| `ci/full-compose-e2e-and-governance-20261031` | `000d5a4` | `f43be01` | merged via #43 | Full Compose E2E + governance docs | **deleted** |
| `feat/mysql-full-alembic-migration` | `d44fe14` | `e647226` | merged via #44 | full 18-table migration + schema contract | **deleted** |
| `fix/admin-distributed-security` | `886ab63` | `00b89ba` | merged via #46 | Redis limiting, revocation, trusted proxy | **deleted** |

Local stale branches `docs/mark-private-env-docs-historical-20261021` and
`fix/dependency-security-runtime-20261021` point at already-merged commits and
their remote refs are gone; they are safe to prune locally after the audit.

### Vue/TS prototype semantic review

The prototype branch predates the admin workbench. Its unique files are:

- `frontend/src/api/admin.ts` — typed public `/data/schools` cursor client,
  runtime response validation, AbortSignal support.
- `frontend/src/views/AdminView.vue` — read-only health + school list, filters,
  cursor pagination, loading/empty/error/retry.
- `frontend/src/api/__tests__/admin.test.ts`,
  `frontend/src/views/__tests__/AdminView.spec.ts`, `frontend/tsconfig.json`.
- `docs/frontend-vue3-ts-upgrade.md` — design/evidence note.

The current `master` admin workbench (PR #32/#35) already provides typed
`frontend/src/api/admin.ts`, `stores/admin.ts`, DTOs, auth/RBAC, offset
pagination, quality jobs, review and audit, with 86 Vitest tests and
`vue-tsc`. The prototype's cursor client targets a different public endpoint
and would conflict with the admin API. **Decision: do not merge the old
AdminView.** Preserve the prototype commit SHA (`8fe4f61`) and this asset
summary in the governance ledger; delete the remote branch only after the
reliability PR is merged and no PR depends on it.

## 2. PR inventory

| PR | State | Head | Action |
|---|---|---|---|
| #35 | **MERGED** | `1f3afab` | merged `b98225a`; CI 9/9 |
| #31 | **MERGED** | `51b8936` | merged `4d05916`; CI 9/9 |
| #41 | **MERGED** | — | governance ledger, merged `d41f4ef` |
| #42 | **MERGED** | — | branch protection documentation, merged `f43be01` |
| #43 | **MERGED** | `000d5a4` | Full Compose E2E + governance, merged `e647226`; CI 10/10 |
| #44 | **MERGED** | `d44fe14` | full 18-table Alembic migration, merged `6e1760c`; CI 10/10 |
| #45 | **MERGED** | `a2d5e15` | governance ledger + ADR-009, merged `00b89ba`; CI 10/10 |
| #46 | **MERGED** | `886ab63` | distributed security hardening, merged `ab80a55`; CI 10/10 |
| #34/#33/#32 | MERGED | — | verify commits in master; phase branches deletable |
| #30/#29/#28/#27/#26/#25/#23/#21/#20/#19/#18/#17/#16/#15/#14/#13/#12/#11/#9/#8/#7/#6/#4/#2/#1 | MERGED | — | no action |
| #5 | CLOSED (draft, conflicting) | — | record; no unique asset to merge |
| #3 | CLOSED (unmerged) | — | record; no unique asset to merge |

No review threads or explicit approval submissions existed on #35/#31 when
reviewed. That was a historical governance gap, not the final state: `master`
protection was subsequently enabled, with required checks as recorded below.
The connected GitHub read-only inventory does not independently expose the
protection-rule details; re-check GitHub branch settings before relying on them.

## 3. Issue inventory

| Issue | State | Evidence | Decision |
|---|---|---|---|
| #10 session token ownership | CLOSED | PR #11 + `tests/test_auth*` | keep closed; re-verify in final suite |
| #22 version/RAG wording drift | CLOSED | PR #23, `pyproject.toml` 3.0.0 | keep closed |
| #24 synthetic E2E/no-key behavior | CLOSED | PR #25, `test_public_demo_acceptance.py` | keep closed |
| #36 14-table MySQL migration | CLOSED | PR #44, `test_mysql_migrations.py` | completed 2026-10-31 |
| #37 full Compose in CI | CLOSED | PR #43, Full Compose E2E | completed 2026-10-31 |
| #38 capacity benchmarks | CLOSED | PR #49, `benchmarks/`, `docs/performance/mysql-benchmark.md` | completed 2026-10-31 |
| #39 distributed auth hardening | CLOSED | PR #46, `test_admin_security.py`, `test_admin_limits_redis.py` | completed 2026-10-31 |
| #40 consumer-group evaluation | CLOSED | PR #52, `benchmarks/queue_recovery/`, `docs/performance/redis-queue-recovery.md`, ADR-009 | completed 2026-10-31 |

Open issues at audit time: **0**. #36/#37/#38/#39/#40 are now closed with
evidence. #40 was closed by PR #52 after the List/BLMOVE/Streams fault matrix,
load comparison, ADR-009 correction and Decision A (keep List + Outbox).
#10/#22/#24 were re-verified and commented with current master evidence.

## 4. CI/CD and security snapshot

- Workflow: `.github/workflows/ci.yml`, **10 jobs** (Full Compose E2E added).
- `master@1622c3f` failed `Security Audit`: legacy Pillow 12.3.0, 31 advisories.
- PR #35 branch already contains `streamlit 1.66.1 + pillow 13.0.0`,
  `dompurify 3.4.17`, production Compose credential tests, MySQL/Redis/admin
  integration job, and the earlier reliability compensation.
- Required checks/branch protection: **ENABLED** with all 10 CI jobs,
  `strict=true`, `enforce_admins=true`, no force push, no branch deletion,
  conversation resolution required. No protection rule was bypassed.
- No real secrets are committed; `.env*` production files are gitignored.

## 5. Action log

| Time | Action | Result |
|---|---|---|
| Phase 0 | fetched all refs via SSH; pruned stale tracking | 7 remote branches confirmed |
| Phase 0 | audited PR/Issue/review threads/branch protection | no review threads, no protection, 0 open issues |
| Phase 1 | reliability code + tests | committed `1f3afab`; full suite 1085/14; integration 13 |
| Phase 3 | PR #35 merge | **MERGED** `b98225a`; CI 9/9 (run 41119912593) |
| Phase 3 | PR #31 sync/merge | **MERGED** `4d05916`; CI 9/9 (run 41120400909) |
| Phase 4 | branch cleanup | 6 remote branches deleted; archive tag pushed; only `master` remains |
| Phase 5 | issue governance | #36–#40 created; #10/#22/#24 re-verified/commented |
| Phase 7 | final master verification | pytest 1108/27, coverage 80.91%; frontend 88; integration 26; CI success 41160889301 |
| Phase 7 | branch protection | enabled on `master`: 10 required checks (incl. Full Compose E2E), strict, enforce admins, no force/delete |
| Phase 1 (#43) | full Compose E2E | merged `e647226`; six services healthy, Nginx async chain, 10/10 CI |
| Phase 2 (#44) | full MySQL migration | merged `6e1760c`; 18 tables, adopt-existing, seed no create_all, 10/10 CI |
| Phase 3 | issue closure | #37 and #36 closed with run/SHA/test evidence; #38–#40 remain open |
| Phase 4 | branch cleanup | `ci/full-compose...`, `feat/mysql...`, `fix/admin-distributed-security` deleted; only `master` remains |
| Phase 8 | security merge | PR #46 merged `ab80a55`; 10/10 CI; #39 closed with tests/evidence |
| Phase 9 | security docs | `docs/security/admin-auth-hardening.md`, ADR-007/008 updated; #38/#40 remain open |
| Phase 10 | P0 test isolation | PR #48 merged `c7a052a`; MySQL/Redis namespace guards; 10/10 CI run 41163132208; integration 27 |
| Phase 11 | P1 benchmark | PR #49 merged `8bbdd97`; MySQL/Worker benchmarks + audit index `0004`; 10/10 CI run 41167467864; #38 closed |
| Phase 12 | final verification | pytest 1112/28, coverage 80.91%; frontend 88; integration 27; remote only `master`; #40 remains open |
| Phase 13 | ownership hardening | PR #51 merged `68f12a7`; marker schema/owner validation; 10/10 CI |
| Phase 14 | queue recovery | PR #52 merged `52b96a5`; 172 fault runs, 0 duplicate executions/lost jobs; Decision A; #40 closed |
| Phase 15 | final verification | pytest 1132/34, coverage 80.91%; frontend 88; integration 33; master CI run 41183132938 10/10 |

## 6. Rollback and safety

- No force push, no history rewrite, no deletion before asset check.
- PR #43 merge `e647226c3e235d0bf7811730f7733d2a9848452c`; PR #44 merge
  `6e1760cf1440b99029b0a8d77f1fa78350d24e32`; PR #46 merge
  `ab80a55e8aacbd14e42ff6a045dbf3e15197b9f2`; PR #48 merge
  `c7a052a1722967e782970ac45a3e9df2c6ae566c`; PR #49 merge
  `8bbdd9791f17e05534cc8c9d096fc1bf64ab3c80`; PR #51 merge
  `68f12a7c04f421aefb9712bd799a53b8b800eb37`; PR #52 merge
  `52b96a553f9df6f7a1bfa40cf7ea4197ee2ba8c5`.
- Alembic `0002`/`0003` downgrade is intentionally refused; use verified
  backup/restore.
- PR #35 was merged with a **merge commit** (`b98225a69c0f3dc4e82e1763e9d9f9cad31d8e75`),
  not a linear or squash range. If a rollback is actually required, first inspect
  dependent commits, branch protection, database compatibility and current refs;
  the merge-specific command is `git revert -m 1 b98225a69c0f3dc4e82e1763e9d9f9cad31d8e75`.
  Do **not** run it blindly: subsequent PRs may depend on the change. A squash
  commit instead uses `git revert <squash_sha>`, while ordinary linear commits
  must be identified in the actual landed history. The historical expression
  `1622c3f..<merge-base>` was not a valid general rollback recipe.
- Branch deletion is recoverable from the recorded SHA; phase branches are
  ancestors of `master` and already merged.
- Local `.env.full` is generated, gitignored, mode 0600, and restored after
  Compose tests; no test secrets are committed.

## 7. Follow-up work: full Compose CI and migration (historical note)

- Historical baseline before this work: `master@f43be01`, 0 open PRs, 1 remote
  branch (`master`), 5 open issues (#36–#40), 9 successful CI jobs in run
  41123250301.
- PR #43 added the `Full Compose E2E` job and merged `e647226`; master CI
  10/10 passed (run 41151378153). #37 was closed with that evidence.
- PR #44 added the full 18-table MySQL Alembic baseline and merged `6e1760c`;
  master CI 10/10 passed (run 41155246215). #36 was closed with that evidence.
  PR #46 later added `20261031_0003` for token revocation; PR #49 added
  `20261031_0004` audit index; current schema is 19 tables.
- There are no open issues. #38 was closed by PR #49 with MySQL/Worker benchmark
  evidence; #40 was closed by PR #52 with real fault-injection, recovery-latency,
  duplicate-delivery, load and consistency data. Decision A keeps List + Outbox;
  no production queue change was made.
- No production SLA or production database migration is implied by CI tests.
