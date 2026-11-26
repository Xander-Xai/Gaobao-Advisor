# Product Documentation Governance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Organize public repository documentation around product capabilities, module responsibilities, and verifiable engineering evidence.

**Architecture:** This is a documentation-only change from master snapshot `22d2dc3f01b95cc0d8ae807d7b8673e865fcafae` (#209, docs-only closeout). Move two legacy overview/ledger documents to product-named paths under `docs/architecture/`, update README and AGENTS governance language, then align linked engineering evidence with product responsibilities and acceptance. Leave application code, schemas, career guidance, and historical GitHub records unchanged.

**Tech Stack:** Markdown, Git, Python 3 for read-only link/content verification; GitHub CLI for live issue/PR/release inspection.

---

## Files and responsibilities

- Modify `README.md`: product-facing overview and documentation entry points; remove unrelated links/copy.
- Modify `AGENTS.md`: retain evidence separation and upstream attribution rules without unrelated chronology.
- Move and rewrite the legacy product overview as `docs/architecture/product-capabilities-and-boundaries.md`.
- Move and rewrite the legacy responsibility ledger as `docs/architecture/module-responsibilities-and-acceptance.md`.
- Modify direct-purpose evidence documents discovered by the live master scan:
  - `docs/agent-harness-eval-roadmap-20261120.md`
  - `docs/agent-readonly-harness-evidence-20261120.md`
  - `docs/four-layer-agent-taskrun-implementation-20261120.md`
  - `docs/context-sandbox-governance-20261120.md`
  - `docs/governance/repository-status-2026-11-07.md`
  - `docs/decision-card-step-trace-20261120.md`
  - `docs/governed-synthetic-evidence-memory-20261120.md`
  - `docs/harness-bounded-replanning-20261120.md`
  - `docs/model-run-paired-evidence-20261120.md`
  - `docs/repeated-model-experiment-20261120.md`
  - `docs/synthetic-replanning-eval-20261120.md`
  - `docs/architecture/README.md`
  - `docs/architecture/memory-engineering-positioning-20261124.md`
- Do not edit application code, `SPEC.md` career capability descriptions, product career-planning tests, merged/closed PR/Issue records, or historical test evidence beyond clarifying its product scope.

## Task 1: Productize primary documentation entry points

**Files:** `README.md`, `AGENTS.md`, `docs/architecture/README.md`, `docs/architecture/memory-engineering-positioning-20261124.md`

- [x] **Step 1: Confirm exact current lines and destination links**

Inspect current README, AGENTS, and architecture text for copy that does not explain product scope, source attribution, or evidence boundaries. Keep product employment and career-data terminology.

- [x] **Step 2: Replace unrelated README sections with product documentation links**

Replace the README's legacy positioning link with product architecture documentation. Change the Token comparison sentence to a product evaluation/reporting criterion. Describe the Harness evidence reference in terms of implementation and acceptance boundaries. Link to `docs/architecture/README.md` and `docs/README.md` only after verifying each path exists on this snapshot.

- [x] **Step 3: Rewrite AGENTS integrity rule as repository attribution/evidence policy**

Retain the rules that distinguish upstream MIT code, current public changes, actual tests, real-data approval, and production validation. Replace non-product chronology with repository attribution and evidence rules. Preserve all admissions evidence, context, privacy, and architecture guardrails in the remainder of `AGENTS.md`.

- [x] **Step 4: Replace architecture employment-era wording with contribution provenance**

Describe this repository's product boundaries and require each claim to identify source snapshot, dataset, test environment, and validation class. Do not alter the frozen architecture decisions.

- [x] **Step 5: Run a focused scan and inspect the diff**

Run `git diff --check`, inspect the complete diff, and search the four files for any remaining non-product copy.

Expected: entry and governance documents describe product scope and evidence; legitimate career-planning product references remain. Commit as `docs: make project entry points product-focused`.

## Task 2: Move portfolio documents to product responsibility and acceptance records

**Files:** Move the two existing Markdown documents from `docs/portfolio/` to the exact product-named destinations listed in “Files and responsibilities”.

- [x] **Step 1: Capture existing factual material and verify current source links**

Cross-check the two documents against `docs/architecture/README.md`, `docs/admissions-research-taskrun.md`, `docs/validation/authorized-research-eval.md`, `README.md`, and the source/test paths they cite. Keep only claims that are true at the exact base snapshot.

- [x] **Step 2: Rewrite the architecture document**

Use product sections: purpose and supported users; deterministic consultation flow; approved research flow; Skills/Context/Memory trust boundaries; Sandbox and data scope; evaluation/optimization gates; deployment topology; limitations and external acceptance. State that admission policies and numeric facts come from verified SQL/rules, and that summaries/preferences are not evidence. Keep true career-direction and employment-outlook consultation as product capabilities.

- [x] **Step 3: Rewrite the module responsibility ledger**

Use module, authoritative path, responsibility, current evidence class, and acceptance command columns. Preserve truthful distinctions among `IMPLEMENTED`, `SCRIPTED_EVAL`, `VERIFIED_LOCAL`, `REAL_MODEL_VERIFIED`, and `PRODUCTION_VERIFIED`; report the latter two as not established where applicable. Use product-release and maintainer acceptance gates.

- [x] **Step 4: Verify links, claims, and career-product content**

Run `git diff --check` and inspect both documents for product function, sources, and acceptance criteria.

Expected: the documents describe product capability and acceptance; career-planning references and evidence boundaries remain. Open every relative link and confirm the target exists. Update all inbound references, verify the old paths no longer exist, remove `docs/portfolio/` if empty, and commit as `docs: publish product architecture acceptance docs`.

## Task 3: Align engineering evidence with product responsibilities

**Files:** The README-linked/evidence documents listed in “Files and responsibilities”, plus inbound links to the two renamed architecture documents.

- [x] **Step 1: Classify non-product copy before editing**

Scan tracked Markdown text and classify each result as (a) product or technical terminology to retain, (b) engineering evidence to retain but express as product acceptance, or (c) unrelated copy to remove. Do not broaden matching into schema identifiers, employment-rate data, or user `career_goal` preferences.

- [x] **Step 2: Rewrite evaluation and Harness documents**

In `docs/agent-harness-eval-roadmap-20261120.md`, `docs/agent-readonly-harness-evidence-20261120.md`, `docs/decision-card-step-trace-20261120.md`, `docs/harness-bounded-replanning-20261120.md`, `docs/model-run-paired-evidence-20261120.md`, `docs/repeated-model-experiment-20261120.md`, and `docs/synthetic-replanning-eval-20261120.md`, express evidence gates as product quality, release, or maintainer acceptance criteria. Keep actual task counts, synthetic/model distinction, provider-use limits, and dated status intact.

- [x] **Step 3: Rewrite implementation and governance documents**

In `docs/four-layer-agent-taskrun-implementation-20261120.md`, `docs/context-sandbox-governance-20261120.md`, `docs/governed-synthetic-evidence-memory-20261120.md`, and `docs/governance/repository-status-2026-11-07.md`, retain reproducible product behavior, test evidence, and limits while clarifying product responsibilities. Preserve original date labels and do not upgrade historical evidence.

- [x] **Step 4: Scan all tracked text and classify remaining hits**

Expected: remaining hits, if any, are historical attribution instructions or legitimate product/test identifiers; inspect each and either align the copy with product responsibility or record why the technical/product reference remains. Do not remove career product logic or employment-data references.

- [x] **Step 5: Commit normalized evidence language**

Run `git diff --check`, inspect each changed section against its original test/source references, then commit as `docs: align engineering evidence with product acceptance`.

## Task 4: Final documentation and GitHub audit

**Files:** All modified Markdown files; no application files.

- [x] **Step 1: Verify current GitHub records without rewriting history**

Run:

```bash
gh issue list --repo Xander-Xai/Gaobao-Advisor --state open --limit 100 --json number,title,url
gh pr list --repo Xander-Xai/Gaobao-Advisor --state open --limit 100 --json number,title,isDraft,baseRefName,headRefName,url
gh release list --repo Xander-Xai/Gaobao-Advisor --limit 20
```

Record whether current open records contain copy unrelated to product purpose. Do not update merged/closed PRs, closed Issues, or old Releases.

- [x] **Step 2: Verify important product career guidance remains**

Run:

```bash
git grep -n -i -E '就业方向|职业规划|employment_rate|career_goal|career_future|employment_paths' -- README.md SPEC.md docs server frontend skills tests
```

Expected: valid feature/data/schema/test references remain; documentation edits did not remove product functions or examples.

- [x] **Step 3: Validate Markdown links changed by this package**

For each changed Markdown file, extract inline relative links and verify each referenced local file exists relative to that Markdown file. Verify any removed external links were unrelated to product usage. Record every checked path and any external URL left unverified.

- [x] **Step 4: Run the documentation-relevant regression and review final diff**

Run:

```bash
pytest -q tests/test_public_demo_acceptance.py --no-cov -o addopts=''
git diff --check
git status --short --branch
git diff --stat
```

Expected: README public-copy acceptance passes, diff contains only approved documentation/spec/plan files, and the existing checkout remains untouched. Report exact scan residuals and links/checks in the final change summary.

## Completion criteria

- Both legacy documents now live at product-named architecture paths and contain product architecture/acceptance content.
- README and repository agent guidance describe product engineering, attribution, and verifiable evidence only.
- Product responsibilities and acceptance criteria are clear in the engineering evidence; truthful dated evidence and limitations survive.
- Legitimate career-planning, employment-direction, and employment-data functionality remains represented.
- Historical GitHub audit records and Git history are not rewritten.
- Relevant docs test and integrity checks pass; working-tree changes are isolated to the GB-07 worktree.
