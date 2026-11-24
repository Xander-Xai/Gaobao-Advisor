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

This module is deliberately **off by default** and only integrated into the
public async reply candidate. Both flags are required:

```bash
export GAOBAO_CHAT_COMPACTION_ENABLED=true
export GAOBAO_CHAT_SNAPSHOT_ENABLED=true
```

`APP_ENV=production` **rejects the experimental snapshot path** until an
approved account/privacy design exists. Do not enable with real student data,
sensitive conversations, or an unreviewed model provider: snapshot text is
stored plaintext in the application database. There is no frontend consent
interface, encryption-at-rest integration, recovery interface or backup purge.

Manual operator cleanup using the SQL store's `prune_expired(limit=100)`
requires a separate scheduled maintenance process. Removing a session through
SQLAlchemy ORM deletes its associated snapshot; bulk SQL deletion on SQLite
without FK enforcement and external report/backup erasure are **not proven**.

## SQL migration

`20261124_0006_session_compaction.py` upgrades the current master head
`20261031_0005` and adds one table. SQLite demo `create_all` also registers
the table; MySQL uses Alembic upgrade. Included smoke tests validate
idempotent migration and schema contract on the latest head.

**Known stacked migration conflict:** unmerged Draft [PR #135](https://github.com/Xander-Xai/Gaobao-Advisor/pull/135)
defines a *different* revision `20261120_0006`, also descended from
`20261031_0005`. Both branches **must not be merged blindly**. Once one
branch is integrated, rebase the other and rewrite its migration parent to
preserve a **single Alembic head**, then rerun SQLite + MySQL migrations.
This PR stays Draft until CI and this dependency are resolved.

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

The CI run for this Draft feature branch is deliberately separate from a
real-provider evaluation. Tests and migration checks must be interpreted against
the exact feature-branch commit, not inherited from #148 or historical green CI.

CI validation note: the first GitHub Actions lint run flagged an incorrect
`typing.Callable` import (UP035); the branch now uses `collections.abc.Callable`.
Use a new workflow run at the updated commit to verify formatting and tests.
