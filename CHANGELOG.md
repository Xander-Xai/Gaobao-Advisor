# Changelog

Notable changes are recorded here. Unreleased changes are not a release and do
not imply production validation. The application version is maintained in
`server/__init__.py`; published tags and GitHub Releases are tracked separately.

## Unreleased

- Added an LLM Resource Governor for the internal model calls: provider level
  RPM, TPM and concurrency, with an atomic Redis Lua admission that several API
  and Worker instances share. Concurrency uses a lease semaphore with expiry
  reclamation, so a crashed worker cannot hold a provider slot forever.
- Routed every model call path through one governed gateway, including the
  history summary, the quality judge, the rejected-answer repair, the
  embedding batches, the voice rendering and the Deep Agents framework
  internal calls. A retry and a provider fallback are new admissions, so
  neither bypasses the provider budget.
- Added token reservation and reconciliation with the real provider usage, and
  an explicit Redis failure policy that fails closed by default instead of
  allowing unbounded calls.
- Added Prometheus metrics for model requests, refusals, concurrency, reserved
  and consumed tokens, admission wait, provider 429s, retries and fallbacks.
- Fixed the minimal YAML loader, which flattened nested mappings and therefore
  silently ignored `config/llm_providers.yaml`. The effective default provider
  is unchanged.

## 3.0.0 Preview — 2026-11-26

- Isolated the standard Compose topology by project name, removed fixed
  container names, and made published HTTP ports configurable for parallel
  local validation.
- Migrated GitHub Actions from deprecated Node.js 20 action revisions to
  current Node.js 24-compatible releases pinned by full commit SHA; upgraded
  CodeQL Action to v4.
- Documented that the default `InMemorySaver` does not restore internal model
  graph state across worker processes; the durable TaskRun journal tracks
  business lifecycle, not graph checkpoints.
- Added admissions source manifests and row-level lineage checks for the
  industrial SQL consultation boundary (#203).
- Added a structured Chat entry for selected complex admissions-research
  requests, bound to the owning session and the existing private TaskRun journal
  (#205, #208).
- Added HTTP regression coverage across request creation, independent approval,
  Worker execution, private report retrieval and session isolation (#206).
- Excluded synthetic schools from the managed admissions research options and
  execution boundary (#207).
- Preserved the deterministic consultation path for simple requests and kept
  paid research execution behind explicit configuration, approval and worker
  authorization.
- Updated evidence, architecture and release documentation to distinguish
  synthetic verification from real provider, real-data and production
  acceptance.
- Documented the missing user-facing volunteer-plan audit separately from the
  synthetic decision-card regression and recorded the 2026-11-26 Harness and
  business acceptance evidence.
- Formatted offline consultation output from major and rank lookup records as
  bounded user-facing facts instead of exposing Python dictionary reprs.

## Version history

- `3.0.0` is the current application version declared in
  `server/__init__.py` and `pyproject.toml`; GitHub Release `v3.0.0` is published
  as a prerelease preview at `fbcf979ff22bc8a3746106f99f0217568ba70837`.
  It distributes source only, uses synthetic demo data, and does not establish
  real admissions quality or production validation.
- Prior notable changes are preserved in Git history and dated records under
  `docs/audits/`, `docs/governance/`, `docs/validation/` and `docs/performance/`.
