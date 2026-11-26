# Agent guidance — Gaobao Advisor

## Project and evidence boundaries

- Attribute implementation and evaluation claims to this repository only when
  supported by its source history. Keep upstream MIT attribution and the
  distinction between original implementation and later public maintenance.
- Resolve live default-branch, PR, Issue, Release and exact-SHA CI state before
  describing current delivery. Distinguish `IMPLEMENTED`, `INTEGRATED`,
  `VERIFIED`, `MERGED` and `PRODUCTION_VALIDATED`; synthetic tests never prove
  real model quality, data authorization, privacy compliance or production use.
- Keep `docs/PROJECT_STATUS.md` as the one current status ledger. Dated reports
  are immutable snapshots, not live status sources.

## Workspace and Git safety

- Inspect `git status`, remotes and worktrees before acting. Preserve all
  existing user changes. If the primary checkout is dirty or ambiguous, fetch
  without altering it and work in a separate worktree from the latest default
  branch.
- Never use `reset`, `clean`, destructive checkout, automatic stash, force-push
  or public-history rewriting to resolve unrelated changes. Do not remove a
  branch or worktree until its owner and contents are verified.
- Use focused commits and PRs. Before merge, inspect the complete diff and run
  the repository's required checks on the exact PR head. If a merge command
  times out, inspect PR state and default-branch history before retrying.

## Architecture constraints

- Ordinary Chat stays on the deterministic LangGraph consultation path; SQL
  facts and verified source receipts are authoritative. LLM prose, dialogue,
  memory and summaries cannot override them.
- Deep Agents is the single approved complex-research engine. Keep its private
  journal, immutable scope, independent operator approval and Worker lifecycle
  separate from synthetic TaskRun fixtures. A user request must not directly
  invoke a model or sandbox.
- No parallel engine, journal, queue or public arbitrary-shell path without an
  approved architecture change. Keep the bounded Sandbox limited to authorized
  admissions analysis and verify results outside the model.
- Preserve one Alembic head. Update OpenAPI and user/developer documentation
  when a public contract or configuration changes.

## Data, security and privacy

- Do not add real student records, private conversations, credentials or
  third-party corpora without documented permission and redistribution rights.
  Default fixtures and demo data must remain explicitly synthetic.
- Never log, commit or place API keys in build contexts, URLs, screenshots,
  fixtures or artifacts. Do not perform external model calls, paid actions or
  production deployment without explicit authorization.
- Treat prompts, model/tool output, imported data and persisted state as
  untrusted. Enforce identity, permissions, budgets and filesystem/network
  boundaries in code, not through model instructions.
- Follow [`SECURITY.md`](SECURITY.md) for private vulnerability reporting and
  [`PRIVACY.md`](PRIVACY.md) for session, snapshot, memory and deletion limits.

## Verification and reporting

- For documentation-only changes, validate links, anchors, commands, Mermaid
  and file references affected by the diff; run `git diff --check`.
- For Python/frontend changes, run focused tests and relevant lint/type/build
  checks. Runtime, schema, permission or Sandbox changes require the applicable
  integration, contract and security regressions. Prefer existing CI over
  duplicating broad local checks.
- Report the exact commands and results. Identify tests not run and their
  reason. Do not turn a plan, synthetic test, historical CI run or merged PR
  into a claim of real-world acceptance.
- Do not create dependencies, features, configuration surfaces or governance
  files merely to imitate another project; keep changes scoped to the request.
