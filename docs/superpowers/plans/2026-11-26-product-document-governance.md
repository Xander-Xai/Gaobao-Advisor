# Product Documentation Governance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the public repository documentation product-focused by removing personal job-search positioning while retaining truthful product features and engineering evidence.

**Architecture:** This is a documentation-only change from master snapshot `9eef17468933eff515b44321259fe4330fb3cbf9`. Move the two `docs/portfolio/` files to product-named paths under `docs/architecture/`, rewrite them as product architecture/acceptance records, update README and AGENTS governance language, then normalize direct personal-purpose language in linked engineering evidence. Leave application code, schemas, business career guidance, and historical GitHub records unchanged.

**Tech Stack:** Markdown, Git, Python 3 for read-only link/content verification; GitHub CLI for live issue/PR/release inspection.

---

## Files and responsibilities

- Modify `README.md`: product-facing overview and documentation entry points; remove personal-use link/copy.
- Modify `AGENTS.md`: retain evidence separation and upstream attribution rules without employment chronology.
- Move and rewrite `docs/portfolio/2026-agent-interview-positioning.md` as `docs/architecture/product-capabilities-and-boundaries.md`.
- Move and rewrite `docs/portfolio/AGENT_V1_OWNERSHIP_AND_ACCEPTANCE.md` as `docs/architecture/module-responsibilities-and-acceptance.md`.
- Modify direct-purpose evidence documents discovered by the live master scan:
  - `docs/agent-harness-eval-roadmap-20261120.md`
  - `docs/agent-readonly-harness-evidence-20261120.md`
  - `docs/four-layer-agent-taskrun-implementation-20261120.md`
  - `docs/governance/repository-status-2026-11-07.md`
  - `docs/decision-card-step-trace-20261120.md`
  - `docs/governed-synthetic-evidence-memory-20261120.md`
  - `docs/harness-bounded-replanning-20261120.md`
  - `docs/model-run-paired-evidence-20261120.md`
  - `docs/repeated-model-experiment-20261120.md`
  - `docs/synthetic-replanning-eval-20261120.md`
  - `docs/architecture/README.md`
  - `docs/architecture/memory-engineering-positioning-20261124.md`
- Do not edit application code, `SPEC.md` career capability descriptions, product career-planning tests, merged/closed PR/Issue records, or historical test evidence beyond wording that describes its personal use.

## Task 1: Remove personal positioning from primary entry points

**Files:** `README.md`, `AGENTS.md`, `docs/architecture/README.md`, `docs/architecture/memory-engineering-positioning-20261124.md`

- [ ] **Step 1: Confirm exact current lines and destination links**

Run:

```bash
git grep -n -i -E '求职|面试|任职阶段|简历|employment era|career.*résumé|写入简历' HEAD -- README.md AGENTS.md docs/architecture/README.md docs/architecture/memory-engineering-positioning-20261124.md
```

Expected: only personal-purpose or employment-era wording in the listed files; product terms such as employment rates are not targeted.

- [ ] **Step 2: Replace README personal-purpose sections with product documentation links**

Remove the README link to the interview-positioning document. Change “落到简历” to a product evaluation/reporting criterion. Change the README harness evidence reference so it describes implementation and evidence boundaries rather than interview/résumé use. Link to `docs/architecture/README.md` and `docs/README.md` only after verifying each path exists on this snapshot.

- [ ] **Step 3: Rewrite AGENTS integrity rule as repository attribution/evidence policy**

Retain the rules that distinguish upstream MIT code, current public changes, actual tests, real-data approval, and production validation. Remove employer chronology and hiring-era framing. Preserve all admissions evidence, context, privacy, and architecture guardrails in the remainder of `AGENTS.md`.

- [ ] **Step 4: Replace architecture employment-era wording with contribution provenance**

Describe this repository's product boundaries and require each claim to identify source snapshot, dataset, test environment, and validation class. Remove language about employment stage or other-project career positioning. Do not alter the frozen architecture decisions.

- [ ] **Step 5: Run a focused scan and inspect the diff**

Run:

```bash
git grep -n -i -E '求职|面试|任职阶段|简历|employment era|写入简历' -- README.md AGENTS.md docs/architecture/README.md docs/architecture/memory-engineering-positioning-20261124.md
git diff --check
git diff -- README.md AGENTS.md docs/architecture/README.md docs/architecture/memory-engineering-positioning-20261124.md
```

Expected: no personal-use positioning in these current entry/governance documents; legitimate career-planning product references remain elsewhere. Commit as `docs: make project entry points product-focused`.

## Task 2: Move portfolio documents to product responsibility and acceptance records

**Files:** Rename `docs/portfolio/2026-agent-interview-positioning.md` to `docs/architecture/product-capabilities-and-boundaries.md`; rename `docs/portfolio/AGENT_V1_OWNERSHIP_AND_ACCEPTANCE.md` to `docs/architecture/module-responsibilities-and-acceptance.md`.

- [ ] **Step 1: Capture existing factual material and verify current source links**

Cross-check the two documents against `docs/architecture/README.md`, `docs/admissions-research-taskrun.md`, `docs/validation/authorized-research-eval.md`, `README.md`, and the source/test paths they cite. Keep only claims that are true at the exact base snapshot.

- [ ] **Step 2: Rewrite the architecture document**

Use product sections: purpose and supported users; deterministic consultation flow; approved research flow; Skills/Context/Memory trust boundaries; Sandbox and data scope; evaluation/optimization gates; deployment topology; limitations and external acceptance. State that admission policies and numeric facts come from verified SQL/rules, and that summaries/preferences are not evidence. Keep true career-direction and employment-outlook consultation as product capabilities.

- [ ] **Step 3: Rewrite the module responsibility ledger**

Use module, authoritative path, responsibility, current evidence class, and acceptance command columns. Preserve truthful distinctions among `IMPLEMENTED`, `SCRIPTED_EVAL`, `VERIFIED_LOCAL`, `REAL_MODEL_VERIFIED`, and `PRODUCTION_VERIFIED`; report the latter two as not established where applicable. Replace job-search milestones with product-release and maintainer acceptance gates.

- [ ] **Step 4: Verify links, claims, and career-product content**

Run:

```bash
git grep -n -i -E '求职|面试|简历|就业方向|职业规划|career_goal|employer|任职阶段' -- docs/architecture/product-capabilities-and-boundaries.md docs/architecture/module-responsibilities-and-acceptance.md
git diff --check
```

Expected: no personal-purpose or employer-history copy; product career-planning references and cited acceptance boundaries remain. Open every relative link listed in the rewritten documents and confirm the target exists. Update all inbound references, verify the old paths no longer exist, remove `docs/portfolio/` if empty, and commit as `docs: publish product architecture acceptance docs`.

## Task 3: Convert direct personal-purpose language in engineering evidence

**Files:** The README-linked/evidence documents listed in “Files and responsibilities”, plus inbound links to the two renamed architecture documents.

- [ ] **Step 1: Classify every direct-purpose hit before editing**

Run:

```bash
git grep -n -i -E '求职|面试|简历|career.*positioning|写入简历|适合简历|简历 Claim|hiring-era|employment era' -- '*.md'
```

Classify results as (a) product/technical career content to retain, (b) engineering evidence to retain but rephrase as product acceptance, or (c) direct personal-purpose material to remove. Do not broaden matching into schema identifiers, employment-rate data, or user `career_goal` preferences.

- [ ] **Step 2: Rewrite evaluation and Harness documents**

In `docs/agent-harness-eval-roadmap-20261120.md`, `docs/agent-readonly-harness-evidence-20261120.md`, `docs/decision-card-step-trace-20261120.md`, `docs/harness-bounded-replanning-20261120.md`, `docs/model-run-paired-evidence-20261120.md`, `docs/repeated-model-experiment-20261120.md`, and `docs/synthetic-replanning-eval-20261120.md`, replace résumé/interview gates with evidence-quality, release, or maintainer acceptance gates. Keep the actual task counts, synthetic/model distinction, provider-use limits, and dated status intact.

- [ ] **Step 3: Rewrite implementation and governance documents**

In `docs/four-layer-agent-taskrun-implementation-20261120.md`, `docs/governed-synthetic-evidence-memory-20261120.md`, and `docs/governance/repository-status-2026-11-07.md`, remove personal pitch/claim language and retain reproducible product behavior, test evidence, and limits. Preserve original date labels and do not upgrade historical evidence.

- [ ] **Step 4: Scan all tracked text and classify remaining hits**

Run:

```bash
git grep -n -i -E '求职|面试|简历|写入简历|适合简历|简历 Claim|career.*positioning|hiring-era|employment era' -- '*.md' '*.py' '*.vue' '*.ts' '*.js' '*.yml' '*.yaml' '*.json'
```

Expected: remaining hits, if any, are explicitly historical attribution instructions or legitimate product/test identifiers; inspect each line and either convert direct personal-purpose prose or record why the technical/product hit remains. Do not remove career product logic or employment-data references.

- [ ] **Step 5: Commit normalized evidence language**

Run `git diff --check`, inspect each changed section against its original test/source references, then commit as `docs: align engineering evidence with product acceptance`.

## Task 4: Final documentation and GitHub audit

**Files:** All modified Markdown files; no application files.

- [ ] **Step 1: Verify current GitHub records without rewriting history**

Run:

```bash
gh issue list --repo Xander-Xai/Gaobao-Advisor --state open --limit 100 --json number,title,url
gh pr list --repo Xander-Xai/Gaobao-Advisor --state open --limit 100 --json number,title,isDraft,baseRefName,headRefName,url
gh release list --repo Xander-Xai/Gaobao-Advisor --limit 20
```

Record whether current open records contain direct personal-purpose copy. Do not update merged/closed PRs, closed Issues, or old Releases.

- [ ] **Step 2: Verify important product career guidance remains**

Run:

```bash
git grep -n -i -E '就业方向|职业规划|employment_rate|career_goal|career_future|employment_paths' -- README.md SPEC.md docs server frontend skills tests
```

Expected: valid feature/data/schema/test references remain; documentation edits did not remove product functions or examples.

- [ ] **Step 3: Validate Markdown links changed by this package**

For each changed Markdown file, extract inline relative links and verify each referenced local file exists relative to that Markdown file. Verify external links are unchanged unless they were personal-use destinations being removed. Record every checked path and any external URL left unverified.

- [ ] **Step 4: Run the documentation-relevant regression and review final diff**

Run:

```bash
pytest -q tests/test_public_demo_acceptance.py --no-cov -o addopts=''
git diff --check
git status --short --branch
git diff --stat
```

Expected: README public-copy acceptance passes, diff contains only approved documentation/spec/plan files, and the existing checkout remains untouched. Report exact scan residuals and links/checks in the final change summary.

## Completion criteria

- Both former portfolio documents now live at product-named architecture paths and contain product architecture/acceptance content without personal-purpose positioning.
- README and repository agent guidance describe product engineering, attribution, and verifiable evidence only.
- Direct personal-purpose language in identified engineering evidence is normalized; truthful dated evidence and limitations survive.
- Legitimate career-planning, employment-direction, and employment-data functionality remains represented.
- Historical GitHub audit records and Git history are not rewritten.
- Relevant docs test and integrity checks pass; working-tree changes are isolated to the GB-07 worktree.
