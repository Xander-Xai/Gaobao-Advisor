# Gaobao Advisor — Memory and Context Engineering specialization

> Architecture and delivery status as of 2026-11-24. The repository belongs
> to a **later, different employer/project** from the earlier employer's Beauty
> RAG and Customer Service Agent. The dated follow-up commits here must not
> be retrospectively claimed as features shipped in a previous employment era.

## Assignment by trust and lifetime

| Layer | Authoritative input / retention | Source implementation | Delivery boundary |
|---|---|---|---|
| Working memory | current authenticated request, model state and SQL receipts | `server/graph/nodes/llm_node.py` | Keep latest SQL evidence, current task and source IDs verbatim |
| Session / episodic | recent history plus lower-trust structured summary | `server/agent/chat_context.py`, `memory_manager.py` | PR #148 merged into master; semantic summary off by default |
| Versioned session snapshot | history-prefix SHA, policy/model/provider fingerprint, TTL, CAS and DB primary key | `server/agent/session_compaction_store.py`, `durable_chat_context.py` | PR #149 merged into master; default disabled and plaintext blocked except explicit dev/test |
| Cross-task preferences | explicitly opt-in, owner-scoped, permitted preference types, revocable | PR #159 (replacement for #135), merged into master | Default-off development experiment; real identity/consent/encryption/backup erasure still require work |
| Business evidence | verified admissions SQL and provenance, source version | existing query, context policy and evidence layer | Never promote summaries/preferences to admissions evidence |
| Procedural | `AGENTS.md`, repo docs, eval and test manifests | project version control | Keep cost, privacy, replays and test evidence auditable |

## Mechanisms to borrow, not copy blindly

- **Pi**: persisted compaction entry with first-kept boundary; append-only
  session-tree history and branch summaries. In Gaobao, use immutable covered
  prefix hashes, current SQL source IDs and CAS to reject stale snippets.
  Full editable/forkable coding-agent session trees are not currently needed.
- **OpenCode**: recent-token retention, safe compaction fallback and
  structured Objective / Next Move handoff. Proactive/background summary is a
  future cost/quality experiment, not automatically beneficial: discarded
  precomputed summaries still incur provider charges.
- **Procedural** `AGENTS.md`: maintain repo process instructions, never use
  them as dynamic unreviewed user memory.

## P0-P2 gap ownership and release gates

1. **P0 already merged** (#148): unify sync, streaming and public SSE input
   budgeting, partial-history signaling and summary trust level. Do not change
   statement of CI/production until verified.
2. **P0 merged** (#149): versioned persistent snapshot reuse, live DB
   prefix verification, CAS, expiry and stale-cleanup tests. Release gate:
   independent Python 3.10/3.11/SQLite/MySQL migration/Compose/Lint checks on
   the exact head. Explicit production-off environment gate.
3. **P1 implemented, production privacy/identity pending** (#159): opt-in cross-task preferences. The
   user preference vault must not share a table, privilege boundary, or
   implicit extraction path with session summaries. Require owner proof, UI
   consent, encryption and revocation/retention review first.
4. **P1 evaluation** (#160 merged synthetic paired tool-context A/B; #162 follow-up): same-case provider A/B — no compaction vs
   per-request compaction vs snapshot reuse, measure current admissions
   evidence support, referent resolution, refusal precision, token accounting,
   latency and cost. Existing synthetic green tests are *not* real provider
   economics.
5. **P2 Pi-style workflow expansion**: if durable multi-step work really
   appears, add structured work-episode receipts and branch provenance behind
   explicit use cases, not speculative complexity.

## Migration warning

The original #135 and #149 introduced separate `0006` children. #149 and #159 are merged; the reconciliation migration is `20261124_0008`. Continue to require one migration head and exact-head SQLite/MySQL checks. The old #135 stack is not an independent merge target.

## Source documentation

- Pi: https://docs.pi.dev/latest/compaction
- OpenCode session compaction: https://opencode.ai/docs/compaction
- OpenCode persistent instructions: https://opencode.ai/docs/instructions/
- Gaobao session context: `docs/validation/pi-opencode-session-compaction-20261124.md`
- Gaobao snapshot: `docs/validation/session-compaction-snapshot-20261124.md`
