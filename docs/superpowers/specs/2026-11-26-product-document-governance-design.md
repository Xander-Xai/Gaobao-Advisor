# Product Documentation Governance Design

Date: 2026-11-26

Scope: GB-07, public repository content governance

## Goal

Make current public project documentation describe Gaobao Advisor as a product
and engineering repository. Preserve accurate product architecture,
implementation status, test evidence, limitations, provenance, and data-source
statements; organize each page around product responsibilities and validation.

## Constraints

- Work from public `master` snapshot `22d2dc3f01b95cc0d8ae807d7b8673e865fcafae` (#209, docs-only closeout).
- Do not overwrite the existing dirty checkout. Implement in the isolated
  `docs/product-doc-governance` worktree.
- Preserve legitimate product capabilities and data such as career-planning
  consultation, employment directions, employment rates, and related schemas,
  examples, code, and tests.
- Do not rewrite Git history or modify historical merged/closed PR, Issue, or
  Release records. Scan them for the audit and record findings; correct current
  repository documentation and current open product records only if they
  contain material unrelated to the product.
- Keep dated engineering evidence dated. Retain factual implementation,
  verification, failure, limitation, and data provenance; express acceptance
  against product behavior and maintenance responsibilities.
- Do not make unsupported claims about real-model performance, licensed
  admissions data, production readiness, or multi-tenant sandbox isolation.

## Content design

1. Move the two legacy architecture/acceptance documents to
   `docs/architecture/product-capabilities-and-boundaries.md` and
   `docs/architecture/module-responsibilities-and-acceptance.md`. Describe
   normal consultation, approved research, deterministic evidence, evaluation,
   deployment, and remaining external acceptance gates. Remove
   `docs/portfolio/` if it becomes empty.
2. Update `README.md` to direct readers to product architecture, repository
   status, evaluation, and contribution/verification documentation. Keep
   statements tied to the current source snapshot and preserve career
   consultation as a supported product capability.
3. Update `AGENTS.md` project-integrity guidance to state repository
   attribution and evidence-separation rules.
4. Search all tracked public text for content unrelated to product purpose.
   Rewrite affected engineering and acceptance documents into product
   responsibilities or evidence criteria. Keep technical model, token,
   task-success, career-planning, and employment terms when they describe real
   product behavior or correctly bounded engineering evidence.
5. Inspect live open GitHub Issues/PRs and available Release metadata. Do not
   edit merged/closed records as part of this package.

## Verification

- Review the complete diff for the two replacement documents and README.
- Search tracked content for non-product-purpose copy and manually classify
  hits rather than using broad substring deletion.
- Confirm career consultation, employment-direction, and employment-data
  product references remain present.
- Confirm old portfolio paths and inbound links are absent; the replacement
  product-architecture paths resolve.
- Confirm unrelated copy in public engineering documents and repository
  instructions has been resolved or explicitly documented as
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
