# Engineering reference review — 2026-11-26

Reviewed GitHub default-branch source through the authenticated GitHub API on
2026-11-26 UTC. Commit IDs below pin the reviewed snapshots; use those IDs when
reproducing the review rather than assuming `main` is immutable.

## OpenAI Agents SDK

- Repository: [openai/openai-agents-python](https://github.com/openai/openai-agents-python)
- Reviewed commit: `6afdd47b4fab14d61e0e6f7c002e6ec9b36fcd71` (`main`)
- Reviewed paths: `README.md`, `AGENTS.md`, `CONTRIBUTING.md`,
  `docs/quickstart.md`, `examples/README.md`, `tests/README.md`, `CHANGELOG.md`,
  `.github/RELEASING.md`, `.github/workflows/tests.yml`,
  `.github/workflows/docs.yml`
- Borrowed principles: front-page explains use case and core concepts before
  installation; links to runnable examples; central contributor/agent guidance
  routes details to authoritative docs; security guidance separates contribution
  checks from claims of certification; examples have an explicit run and log
  workflow. Its docs workflow builds the documentation site, and tests have a
  dedicated suite guide. The reviewed repository's private vulnerability
  reporting endpoint was disabled, so no live security intake was assumed.
- Not copied: its SDK-specific API material, collaborator-only PR policy,
  Python `uv` command set, release workflow and publisher requirements. Gaobao
  is an application repository with its own ownership, runtime and CI gates.

## LangChain Deep Agents

- Repository: [langchain-ai/deepagents](https://github.com/langchain-ai/deepagents)
- Reviewed commit: `6e874efa74e5d80cda80148381b7552ce418b3db` (`main`)
- Reviewed paths: `README.md`, `AGENTS.md`, `libs/DEVELOPMENT.md`,
  `libs/ARCHITECTURE.md`, `examples/README.md`, `.github/RELEASING.md`,
  `.github/workflows/ci.yml`, `.github/workflows/release.yml`,
  `.github/workflows/markdown_file_check.yml`
- Borrowed principles: explain the harness layer separately from its graph
  runtime; describe composable components and explicit boundaries; keep a short
  top-level agent rule file that points to package-specific development and
  release instructions; make evaluation, deployment and release documents
  discoverable without copying their full contents into README. It also has a
  docs-build workflow and a PR gate that asks maintainers to acknowledge new
  Markdown files; that gate is not a broken-link checker. The README documents
  the model/tool trust boundary, while the GitHub private vulnerability
  reporting endpoint was disabled in the reviewed repository.
- Not copied: the Deep Agents feature set or its production-readiness claims,
  monorepo package layout, release fan-out automation, or its model/tool trust
  assumptions. Gaobao uses Deep Agents only behind its own bounded approval,
  journal, tool and evidence-verification controls.

## Microsoft Agent Framework

- Requested URL: [microsoft/agent-framework](https://github.com/microsoft/agent-framework)
- Review result: GitHub REST returned HTTP 404 for the repository, and
  authenticated repository search did not return that exact repository on
  2026-11-26 UTC. There is therefore no verifiable commit SHA or file-level
  review to cite for this requested URL.
- Do not substitute a similarly named Microsoft sample repository or
  `microsoft/autogen` and describe it as the requested project. Re-run the
  repository lookup if Microsoft restores, renames or redirects the URL.

## Applied to Gaobao

The resulting governance keeps README as the user/developer entry point,
`docs/PROJECT_STATUS.md` as the single implementation/verification ledger,
`docs/README.md` as the document map, and dated audit/experiment records as
historical evidence. Local CI evidence and external real-data/model acceptance
remain separate states.
