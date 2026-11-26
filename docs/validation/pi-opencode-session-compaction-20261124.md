# Session compaction wiring — Pi / OpenCode mechanism study (2026-11-24)

## Scope and evidence

This PR fixes a concrete runtime disconnect: `server/agent/memory_manager.py` already
declared recent + summary memory, but all three LLM code paths in
`server/graph/nodes/llm_node.py` read database history via
`_load_memory_for_session` and silently capped message counts without
calling the summarizer. A trace flag from `memory_node.py` did **not**
establish that compression had happened.

Pi provides session persistence, compaction, and branch-aware summarization;
OpenCode documents automatic compaction and persistent project instructions.
These are design references, not imported SDK dependencies or proof of identical
feature sets. This change is **session compaction**, not automatic long-term
user memory, SQL evidence storage, or production-grade privacy compliance.

## Shipped code in this PR

- `server/agent/chat_context.py` is the shared, no-network bounded context
  policy for synchronous graph, legacy streaming and async public SSE candidate.
- `generate_chat_candidate` can opt in to a separate summary LLM call using
  the existing `DefaultMemoryManager._summarize_history` seam. This path
  keeps the most recent rounds verbatim, carries old dialogue in a *user-role*
  untrusted summary, and leaves the original database history untouched.
- Invalid stored `system`/`tool` roles, malformed records, insufficient budget
  for the current task and history backend outages fail closed to the existing
  grounded fallback. They are not disguised as an empty successful session.
- When old messages are removed without a successful summary, the model is
  explicitly told older context is incomplete. Final current-task text is never
  sliced, including RAG/SQL provenance labels.
- All three paths apply the same input-budget policy. The synchronous graph and
  legacy generator deliberately do not perform extra implicit paid API calls.

## Development-only feature flags

```bash
# Off by default: no extra model calls or old-conversation transfer for summary.
export GAOBAO_CHAT_COMPACTION_ENABLED=true
# Default: 10 recent rounds with compaction enabled, 20 rounds when disabled.
export GAOBAO_CHAT_KEEP_RECENT_ROUNDS=10
# Default: approximate 8000 input tokens. This is a heuristic, not the
# provider tokenizer or a model capability guarantee.
export GAOBAO_CHAT_INPUT_EST_TOKENS=8000
```

The public `generate_chat_candidate` path uses these flags. Existing chat
generation still needs its configured LLM API account. **Opting into semantic
compaction can incur an additional model request on each eligible turn**;
the summary is not yet persisted/reused. No recurring paid experiment is
automatically scheduled. Never enable on real personal data without the
appropriate permission, retention and provider-data review.

## Validating the actual wiring

```bash
pytest -q -o addopts='' tests/test_chat_context_compaction.py
pytest -q -o addopts='' tests/test_chat_final_answer_integrity.py tests/test_memory_manager.py
ruff check server/agent/chat_context.py server/graph/nodes/llm_node.py tests/test_chat_context_compaction.py
ruff format --check server/agent/chat_context.py server/graph/nodes/llm_node.py tests/test_chat_context_compaction.py
```

Tests use a local fake summary and fake streamed model replies; they do **not**
claim an actual provider summary was generated. A model-run benchmark must
separately compare same-model/no-compaction versus compaction with source
citation accuracy, user-constraint retention, provider-reported token usage,
latency and summarization cost.

## Known limitations / further work

1. The summary is **ephemeral per call**, not a durable versioned checkpoint.
   Each eligible turn may resummarize the same old history. Pi-style persistent
   compaction snapshots, CAS, replay/recovery and invalidation on branch
   changes need a separate implementation and data migration.
2. The baseline 8000-token budget is an **estimation** only, not model-tokenizer
   preflight; tool-result pairing is not handled here because SQL chat history
   has user/assistant roles, not raw OpenAI tool-call transcripts.
3. The optional summary can omit constraints or become stale; it is marked
   untrusted and **never** allowed to upgrade admissions data to facts.
4. Cross-task opted-in user preferences have an implementation in the merged
   branch (#159), but identity/account binding, preference encryption, consent
   UI and privacy-release gates remain unresolved; see
   [cross-task memory boundaries](../cross-task-user-memory.md).
5. Original conversation data deletion and backup retention are distinct from
   context trimming. This PR does not change storage retention or user identity.

This patch alone is not a verified reduction in tokens, hallucinations or
admissions-task errors. Those are measurable downstream acceptance goals.
