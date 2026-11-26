# Changelog

Notable changes are recorded here. Unreleased changes are not a release and do
not imply production validation. The application version is maintained in
`server/__init__.py`; published tags and GitHub Releases are tracked separately.

## Unreleased

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
  `server/__init__.py` and `pyproject.toml`. There is no corresponding formal
  GitHub Release currently documented; inspect the live Releases and Tags pages
  before preparing a release.
- Prior notable changes are preserved in Git history and dated records under
  `docs/audits/`, `docs/governance/`, `docs/validation/` and `docs/performance/`.
