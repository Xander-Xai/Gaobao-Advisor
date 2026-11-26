# Changelog

Notable changes are recorded here. Unreleased changes are not a release and do
not imply production validation. The application version is maintained in
`server/__init__.py`; published tags and GitHub Releases are tracked separately.

## Unreleased

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

## Version history

- `3.0.0` is the current application version declared in
  `server/__init__.py` and `pyproject.toml`. There is no corresponding formal
  GitHub Release currently documented; inspect the live Releases and Tags pages
  before preparing a release.
- Prior notable changes are preserved in Git history and dated records under
  `docs/audits/`, `docs/governance/`, `docs/validation/` and `docs/performance/`.
