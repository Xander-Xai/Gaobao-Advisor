# Gaobao session compaction: opt-in versioned SQL snapshot (2026-11-24)

## Problem and scope

[PR #148](https://github.com/Xander-Xai/Gaobao-Advisor/pull/148) fixes a genuine
wiring gap by routing synchronous and public asynchronous LLM calls through
bounded conversation context. The first semantic compactor is **ephemeral**:
the next turn can re-summarize the same old SQL conversation history.

This stacked PR adds a separately controlled, SQLAlchemy-backed snapshot
for a single **authenticated session**, not reusable user-profile memory.
The original `conversations` and `conversation_messages` remain canonical.

## Safety and persistence contract

- One `session_compaction_snapshots` record per numeric Conversation PK.
  `session_id` alone is **not** a durable identity; deleting and recreating a
  session must not revive the previous snapshot.
- Exactly covered oldest message prefix: count + canonical SHA-256 digest.
  The read rejects changed/deleted/reordered messages, mismatched model/prompt
  policy, expired TTL, corrupted summary, or a different conversation.
- Optimistic update via `version` and SQL compare-and-swap. Concurrent
  writers may pay for redundant model calls; only a version winner is
  published, **not exactly-once summary execution**.
- Reuse the committed summary without another summary-model call when the old
  prefix is unchanged. Newly uncovered messages remain verbatim until their
  estimated size reaches the incremental refresh threshold (500 by default).
  Refresh summarizes prior **untrusted** summary plus new old-history messages.
- Every summary is injected as an unprivileged user-role hint, not policy,
  admissions data or tool authorization. Current SQL/RAG evidence and current
  user task are never truncated to fit a summary.
- The cache uses a default 24-hour expiry (configurable in the store up to
  seven days), ORM cascade and ON DELETE CASCADE for conversation deletion.
  `prune_expired` performs bounded physical expiry cleanup when scheduled by
  an operator; **there is no scheduled cron in this PR**.
- DB reads/writes are checked against the currently committed conversation.
  Read/write backend errors fail to uncached context, without exposing raw
  exceptions or credentials to the model or API.

## Flags

The standard local synthetic Java/MySQL/Redis stack defaults both flags to **true**. Raw/lightweight and production paths remain off by default. The module is only integrated into the public async reply candidate. Both flags are required; explicit false disables the corresponding feature:

```bash
export GAOBAO_CHAT_COMPACTION_ENABLED=true
export GAOBAO_CHAT_SNAPSHOT_ENABLED=true
export APP_ENV=development  # explicitly required; "production" and unset are blocked
```

Only explicit `APP_ENV=development|test|testing|local` enables the experimental snapshot path; unset, staging and production environments **reject it** until an
approved account/privacy design exists. Do not enable with real student data,
sensitive conversations, or an unreviewed model provider: the legacy lightweight path can store plaintext. The standard local stack requires AES-GCM encryption; original conversation messages are not encrypted by this change. There is no frontend consent
interface, encryption-at-rest integration, recovery interface or backup purge.

The standard Quality Worker now performs bounded expiry cleanup during its recovery cycle, including when no chat snapshot is being written. Manual operator cleanup remains available via the SQL store. Removing a session through
SQLAlchemy ORM deletes its associated snapshot; bulk SQL deletion on SQLite
without FK enforcement and external report/backup erasure are **not proven**.

## SQL migration

`20261124_0006_session_compaction.py` upgrades the current master head
`20261031_0005` and adds one table. SQLite demo `create_all` also registers
the table; MySQL uses Alembic upgrade. Included smoke tests validate
idempotent migration and schema contract on the latest head.

The migration conflict recorded at the time of this snapshot was resolved by
`20261124_0008_merge_memory_heads.py`, which joins the session snapshot and
preference-vault branches without changing their published revision IDs.
`20261125_0009_chat_turn_receipts.py` is the current head in the reviewed
checkout. See the [database schema matrix](../database-schema-matrix.md) and
verify `alembic heads` before applying migrations.

## Evaluation

```bash
pytest -q -o addopts='' tests/test_session_compaction_snapshot.py
pytest -q -o addopts='' tests/test_chat_context_compaction.py
pytest -q -o addopts='' tests/test_migrations.py
ruff check server/agent/session_compaction_store.py server/agent/durable_chat_context.py tests/test_session_compaction_snapshot.py
ruff format --check server/agent/session_compaction_store.py server/agent/durable_chat_context.py tests/test_session_compaction_snapshot.py
```

Tests run a real transient SQLAlchemy SQLite engine and use fake LLM summaries.
They cover immediate cache reuse (one summarization for two calls), incremental
refresh, stale write CAS, cross-session isolation, prefix edits, policy changes,
TTL, cleanup and ORM deletion. The results do **not** prove savings on any
actual provider, multi-worker locks, encryption, real student advice,
MySQL transaction isolation under load or legal erasure of backups.

The historical feature-branch CI is separate from real-provider evaluation.
Current tests and migration checks must be interpreted against the exact
reviewed commit; none of them demonstrate production privacy or model benefit.

CI validation note: the first GitHub Actions lint run flagged an incorrect
`typing.Callable` import (UP035); the branch now uses `collections.abc.Callable`.
Use a new workflow run at the updated commit to verify formatting and tests.


## Current standard deployment update

The local full stack sets `GAOBAO_CHAT_SNAPSHOT_REQUIRE_ENCRYPTION=true`. `setup_full_env.py` generates a 32-byte secret (64 hex characters) and `--add-java-reader` extends older files without replacing existing keys. AES-GCM ciphertext is bound to conversation PK, covered count, prefix digest and policy digest. Wrong key, tampering, cross-session ciphertext and old plaintext in encrypted mode are cache misses; no plaintext fallback. The original dialogue remains canonical. Key rotation invalidates cached summaries; securely destroy old keys/caches and retain backups according to approved policy. Mainline CI includes synthetic MySQL/runtime checks; this is not full conversation encryption or production privacy approval.
