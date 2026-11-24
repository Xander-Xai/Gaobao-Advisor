# Repository Agent Instructions — Gaobao Advisor

## Project / employment integrity
- `Gaobao-Advisor` is the **later project for a different employer**. `Customer-Service-AI-Agent` and `Beauty-Industry-RAG-QA-System` were from an earlier employment period at the same employer. Do not attribute code, metrics or hiring-era implementation from one to another.
- November 2026 repository improvements and unmerged Draft PRs are distinct from historical production experience. State implementation/CI/real-data validation separately.
- Resolve default-branch head and PR/Issue/CI status before describing current functionality; historical README claims are not run-time evidence.

## Context engineering, session snapshots and preferences
- SQL admissions tables and verified source IDs are the **only authoritative admissions evidence**. Session dialogue, summaries and optional preferences remain lower-trust context and cannot alter admissions-policy facts, permissions, tool approvals or citations.
- Working memory is the current state and SQL/tool receipts; session memory is bounded recent turns + optional untrusted compaction; snapshots are a versioned, TTL-bound optimization; opted-in cross-task user preferences belong to a separate owner-scoped, revocable feature and are NEVER inferred automatically.
- Keep the public synchronous, legacy streaming and SSE final-candidate context behavior aligned; prove integration tests call the real construction path rather than merely an unused MemoryManager.
- Standard local deployment requires both flags, nonproduction APP_ENV and a configured AES-GCM key; worker prunes expired rows. Lightweight smoke remains conservative. Do not enable snapshots or personal memory in production before privacy acceptance. Bind reuse to authenticated session/conversation PK, current committed history-prefix hash, policy/model/provider fingerprint, TTL and CAS version. Never truncate current SQL/RAG evidence to save summary tokens.
- A stale/failed summary should surface explicit partial-context status; a backend read failure must not turn into a successful empty conversation; do not log raw user text or provider exception bodies.
- Keep a single Alembic head. Session snapshot #149 and isolated cross-task preference vault #159 are merged; superseded #135 is closed without merge. Keep privacy/production validation separate and preserve the single Alembic head.
- Synthetic tests or CI passing do not establish provider Token savings, factual answer quality, privacy compliance, or production availability. Validate real-model/no-compaction vs compaction vs snapshot reuse with same cases before making measured claims.
- See `docs/architecture/memory-engineering-positioning-20261124.md` and `docs/validation/session-compaction-snapshot-20261124.md`.

## Reporting current project status

1. Read the live repository metadata for the default branch (currently `master`). Resolve its remote head SHA, then read the README, [implementation reference](README.md#仓库状态怎么查), relevant source, tests and [evidence](docs/README.md) at that exact SHA. Report the branch, SHA and check time. A local checkout may lag the remote.
2. Read current PR and Issue metadata from GitHub when reporting their state. Use the PR's actual base, draft flag and merged status. Verify that a claimed merge is present in the default branch before counting it as delivered. Use PR bodies, archived reports and conversation notes for historical context.
3. Check Actions for the exact SHA, and state what ran, passed, failed, was skipped or is pending. Report release/tag and deployment evidence separately. When a source is unavailable, say which claim could not be verified.
4. State the environment and dataset behind each finding. Distinguish implemented code, test evidence, measured results and production validation. Keep public-repository evidence separate from personal or employment history.
5. When changing a feature or interface, update its current documentation and existing generated contracts in the same PR. Keep dated audits labelled with the snapshot date.

## Architecture change gate
- Follow [architecture decision freeze](docs/architecture/README.md#architecture-decision-freeze--2026-11-25). Default chat remains deterministic and SQL-grounded. Native readonly Harness is a reference/eval baseline; Deep Agents is the sole approved complex-research engine, not a second default chat engine. Keep synthetic sandbox fixtures out of public business routes. PRs #192/#193 merged the architecture convergence; do not add parallel task lifecycles, new engines or production sandbox routes. Evidence-only work may proceed within that frozen architecture. See #190 for independent local smoke and #104/#147 for real acceptance.

- The standard local full deployment is the Java Gateway/Catalog + MySQL + Redis + worker stack. SQLite remains only for smoke and isolated research journal; use the checked-out `scripts/compose_standard.py`. For industrial deployment follow `docs/architecture/industrial-release-contract.md`: no synthetic seed, schema fallback, synthetic RAG, unsafe personal memory or automatic paid calls. Require exact-head CI and independent environment acceptance before claiming production delivery. Do not put credentials into Docker build contexts or logs.

- Industrial API and quality worker must each independently validate the same profile, schema head, data shape and Redis before processing work; Compose startup order is not authorization. Do not expose the release candidate as production before the [industrial release gates](docs/architecture/industrial-release-contract.md) pass. Catalog Redis throttling is not a global public-chat limiter.
