# Contributing

Contributions that improve correctness, security, maintainability and
reproducibility are welcome. Please read [`AGENTS.md`](AGENTS.md),
[`SECURITY.md`](SECURITY.md) and the current
[project status](docs/PROJECT_STATUS.md) before changing runtime behavior.

## Before starting

1. Search existing Issues and pull requests; discuss significant behavior or
   architecture changes before implementation.
2. Keep changes within the approved architecture: deterministic, SQL-grounded
   ordinary consultation; one operator-approved Deep Agents research lifecycle;
   synthetic benchmark paths isolated from user-facing business routes.
3. Confirm that all fixtures and documentation use synthetic or demonstrably
   redistributable data. Do not include real student records, private
   conversations, private corpora or secrets.

## Development setup

Python 3.10 or 3.11 is tested in CI. The standard local full stack also requires
Docker Engine and Docker Compose v2.

```bash
python -m pip install -e '.[dev]'
python -m scripts.seed_demo_data --db data/demo.db --reset
```

For the standard Java/MySQL/Redis/Worker demo, use the tested setup documented
in the [README](README.md): `python scripts/setup_full_env.py`
followed by `python scripts/compose_standard.py up --build -d`.

Optional Deep Agents packages are deliberately separate from ordinary API
startup. Read `requirements-deepagents.txt` and the approved research runbook
before installing or running a model-backed Worker.

## Change and test workflow

Create a focused branch from the latest `master`, make the smallest complete
change and update current docs/contracts. Never overwrite uncommitted user work
or publish changes based on an unverified historical CI run.

```bash
# Python
ruff check . && ruff format --check .
python -m pytest tests/ -q

# Frontend (Node.js 20)
cd frontend
npm ci
npm test -- --run
npm run build

# Optional full-stack integration tests with disposable local services
cd ..
make test-integration
```

Select additional verification based on risk:

- Public API/schema changes: contract tests and generated OpenAPI consistency.
- Database/migration changes: single Alembic head plus disposable MySQL tests.
- Auth, session, research, privacy or Sandbox changes: cross-owner, failure,
  cancellation and negative-path regressions; run applicable MySQL/Redis and
  Docker checks.
- Documentation-only changes: validate changed links and anchors, code
  snippets, local file references and Mermaid; run `git diff --check`.

Repository CI adds Python 3.10/3.11, Ruff, MySQL/Redis integration, frontend
tests/build, no-key container and Compose checks, redistribution and secret
scans, dependency audits, optional Deep Agents no-network smoke and CodeQL.
Check the workflows and required branch checks for the latest exact PR head.

## Dependencies and data

- Runtime dependency changes require a reason, lock regeneration and dependency
  audit (`make audit-deps`). Check [`docs/dependency-exceptions.md`](docs/dependency-exceptions.md).
- The frontend uses `frontend/package-lock.json`; use `npm ci` for validation
  and update the lockfile with deliberate package changes.
- Do not add network scraping or provider calls to default startup or CI.
- Keep external-model usage opt-in and document who authorizes it, where data
  is sent, what is persisted and how cost is bounded.

## Pull requests

- Use the repository [PR template](.github/PULL_REQUEST_TEMPLATE.md).
- Explain the problem, scope, relevant tests and any known limitation. Link the
  relevant Issue without closing acceptance work that still lacks external
  evidence.
- Report synthetic, model-backed, licensed-data and production results
  separately. Never claim production readiness, real admissions accuracy,
  measured cost improvement or model quality without corresponding evidence.
- Do not bypass required checks or security controls to make a PR mergeable.

## Security reports

Do not report vulnerabilities in a public Issue or PR. Follow the private
reporting path described in [`SECURITY.md`](SECURITY.md). Do not attach real
personal data, credentials or third-party exploit payloads.
