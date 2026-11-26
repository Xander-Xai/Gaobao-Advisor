# Product Documentation Governance Design

Date: 2026-11-26

Scope: GB-07, public repository content governance

## Goal

Make current public project documentation describe Gaobao Advisor as a product
and engineering repository. Remove first-person employment, job-search,
interview, and résumé-purpose positioning while preserving accurate product
architecture, implementation status, test evidence, limitations, provenance,
and data-source statements.

## Constraints

- Work from public `master` snapshot `9eef17468933eff515b44321259fe4330fb3cbf9`.
- Do not overwrite the existing dirty checkout. Implement in the isolated
  `docs/product-doc-governance` worktree.
- Preserve legitimate product capabilities and data such as career-planning
  consultation, employment directions, employment rates, and related schemas,
  examples, code, and tests.
- Do not rewrite Git history or modify historical merged/closed PR, Issue, or
  Release records. Scan them for the audit and record findings; correct current
  repository documentation and current open product records only if they
  contain irrelevant personal-purpose material.
- Keep dated engineering evidence dated. Retain factual implementation,
  verification, failure, limitation, and data provenance; express acceptance
  against product behavior and maintenance responsibilities.
- Do not make unsupported claims about real-model performance, licensed
  admissions data, production readiness, or multi-tenant sandbox isolation.

## Content design

1. Move `docs/portfolio/2026-agent-interview-positioning.md` to
   `docs/architecture/product-capabilities-and-boundaries.md` and rewrite it as
   a product architecture and capability-boundary document. Describe normal
   consultation, approved research, deterministic evidence, evaluation,
   deployment, and remaining external acceptance gates.
2. Move `docs/portfolio/AGENT_V1_OWNERSHIP_AND_ACCEPTANCE.md` to
   `docs/architecture/module-responsibilities-and-acceptance.md` and rewrite it
   as a module responsibility and engineering acceptance ledger. Preserve
   useful module paths, tests, states, and evidence limits; remove job-search
   framing and résumé-oriented execution advice. Remove `docs/portfolio/` if it
   becomes empty.
3. Update `README.md` to remove personal job-use references and links, directing
   readers instead to product architecture, repository status, evaluation, and
   contribution/verification documentation. Keep statements tied to the
   current source snapshot and do not misstate career consultation as
   unrelated personal planning.
4. Update `AGENTS.md`'s project-integrity guidance as needed so it states
   repository attribution and evidence-separation rules without personal
   employment chronology.
5. Search all tracked public text for direct individual career-purpose
   references. Rewrite direct-purpose language in active engineering and
   acceptance documents (including README-linked Harness/Eval evidence pages)
   into product responsibilities or evidence criteria. Keep technical model,
   token, task-success, career-planning, and employment terms when they describe
   real product behavior or correctly bounded engineering evidence.
6. Inspect live open GitHub Issues/PRs and available Release metadata. Do not
   edit merged/closed records as part of this package.

## Verification

- Review the complete diff for the two replacement documents and README.
- Search tracked content for direct job-search, personal employment, résumé,
  and interview-positioning language; manually classify hits rather than using
  broad substring deletion.
- Confirm career consultation, employment-direction, and employment-data
  product references remain present.
- Confirm old portfolio paths and inbound links are absent; the replacement
  product-architecture paths resolve.
- Confirm direct personal-purpose hits in the public engineering documents and
  repository instructions have been resolved or explicitly documented as
  historical records that are out of edit scope.
- Validate Markdown links affected by the edits and run repository-provided
  documentation or link checks if available.
- Run the relevant lightweight repository checks and report exact commands and
  results. Do not run paid provider calls or claim external acceptance.

## Out of scope

- Product behavior, model prompts, tool permissions, APIs, data schemas, and
  evaluation logic.
- Modifying historical GitHub PR/Issue/Release descriptions or rewriting Git
  history.
- Removing legitimate career-guidance product content.
- Replacing archived evidence with new claims or deleting valid test records.
