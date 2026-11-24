# Repository Agent Instructions — Gaobao Advisor

## Project / employment integrity
- `Gaobao-Advisor` is the **later project for a different employer**. `Customer-Service-AI-Agent` and `Beauty-Industry-RAG-QA-System` were from an earlier employment period at the same employer. Do not attribute code, metrics or hiring-era implementation from one to another.
- November 2026 repository improvements and unmerged Draft PRs are distinct from historical production experience. State implementation/CI/real-data validation separately.
- Resolve default-branch head and PR/Issue/CI status before describing current functionality; historical README claims are not run-time evidence.

## Context engineering, session snapshots and preferences
- SQL admissions tables and verified source IDs are the **only authoritative admissions evidence**. Session dialogue, summaries and optional preferences remain lower-trust context and cannot alter admissions-policy facts, permissions, tool approvals or citations.
- Working memory is the current state and SQL/tool receipts; session memory is bounded recent turns + optional untrusted compaction; snapshots are a versioned, TTL-bound optimization; opted-in cross-task user preferences belong to a separate owner-scoped, revocable feature and are NEVER inferred automatically.
- Keep the public synchronous, legacy streaming and SSE final-candidate context behavior aligned; prove integration tests call the real construction path rather than merely an unused MemoryManager.
- Snapshot cache requires both feature flags and explicit nonproduction APP_ENV; do not enable plaintext in production. Bind reuse to authenticated session/conversation PK, current committed history-prefix hash, policy/model/provider fingerprint, TTL and CAS version. Never truncate current SQL/RAG evidence to save summary tokens.
- A stale/failed summary should surface explicit partial-context status; a backend read failure must not turn into a successful empty conversation; do not log raw user text or provider exception bodies.
- Keep migration heads linear. Draft cross-task memory PR #135 and session snapshot PR #149 have independent 0006 children and need explicit rebase/migration reconciliation before both are merged.
- Synthetic tests or CI passing do not establish provider Token savings, factual answer quality, privacy compliance, or production availability. Validate real-model/no-compaction vs compaction vs snapshot reuse with same cases before making measured claims.
- See `docs/architecture/memory-engineering-positioning-20261124.md` and `docs/validation/session-compaction-snapshot-20261124.md`.
