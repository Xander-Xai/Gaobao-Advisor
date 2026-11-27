# Database Schema Matrix

> Source of truth: `db/models.py`. Generated/verified by `db/schema_contract.py`.
> Migration history: `20261030_0001` (admin tables) → `20261031_0002` (14 historical tables) → `20261031_0003` (admin token revocation) → `20261031_0004` (audit performance index) → `20261031_0005` (dataset snapshot provenance) → `20261124_0006` (session compaction cache) and `20261120_0006` (user memory vault) → `20261124_0007` (memory revision) → `20261124_0008` (merge both branches) → `20261125_0009` (chat turn receipts) → `20261127_0010` (admissions history scope/rank index). Confirm the current migration head with Alembic before migration work.
> Storage scope: SQLite is for lightweight development/smoke and isolated
> research journals; MySQL 8.4 backs the standard full-stack Compose profile.

## 1. Table inventory

| # | Table | Purpose | PK | Key constraints / indexes | FKs |
|---|---|---|---|---|---|
| 1 | `schools` | 院校 | `id` | `UNIQUE(name)`, `ix_schools_province`, `ix_schools_level` | — |
| 2 | `majors` | 专业 | `id` | `UNIQUE(name)`, `ix_majors_category` | — |
| 3 | `admission_scores` | 录取分数 | `id` | `uq_admission_score`, `ix_adm_lookup`, `ix_adm_dedup`, `ix_adm_history_scope_rank`, `ix_adm_year` | school_id→schools, major_id→majors |
| 4 | `enrollment_plans` | 招生计划 | `id` | `uq_enrollment_plan` | school_id→schools, major_id→majors |
| 5 | `subject_rankings` | 学科排名 | `id` | `uq_subject_ranking` | school_id→schools |
| 6 | `conversations` | 会话 | `id` | unique index `ix_conversations_session_id` | — |
| 7 | `conversation_messages` | 消息 | `id` | `ix_conversation_messages_conversation_id` | conversation_id→conversations |
| 8 | `yi_fen_yi_duan` | 一分一段 | `id` | `uq_yi_fen_yi_duan`, `ix_yfdd_province_year` | — |
| 9 | `highlights` | 金句 | `id` | `ix_highlights_session_id` | — |
| 10 | `feedbacks` | 反馈 | `id` | `ix_feedbacks_session_id`, `ix_feedback_session_msg` | — |
| 11 | `quality_scores` | 质量评分 | `id` | `ix_quality_score_conversation` | conversation_id→conversations.session_id |
| 12 | `graduate_program` | 考研院校专业 | `id` | `ix_graduate_program_school_name`, `idx_grad_program_school_major` | — |
| 13 | `graduate_score` | 考研分数 | `id` | — | program_id→graduate_program |
| 14 | `career_trend` | 职业趋势 | `id` | `ix_career_trend_major_name` | — |
| 15 | `data_quality_jobs` | 质检任务 | `id` | `uq_dq_jobs_idempotency`, status/review/actor indexes | — |
| 16 | `data_quality_issues` | 质检问题 | `id` | `uq_quality_issue_dedup`, job/severity/rule indexes | job_id→data_quality_jobs |
| 17 | `operation_audit_logs` | 审计 | `id` | actor/action/request/resource indexes + `ix_audit_created` | — |
| 18 | `quality_task_outbox` | 事务 Outbox | `id` | `uq_quality_task_outbox_job_id`, status/next indexes | job_id→data_quality_jobs |
| 19 | `admin_token_revocations` | 管理员 Token 撤销（持久化） | `id` | unique `jti_hash`, username/expires indexes | — |
| 20 | `dataset_snapshots` | 演示数据来源与生成时间 | `id` | — | — |
| 21 | `session_compaction_snapshots` | 可选会话摘要缓存（非业务事实） | `conversation_id` | `ix_session_compaction_expires`、CAS version、TTL | conversation_id→conversations.id (CASCADE) |
| 22 | `user_memory_vaults` | 用户显式创建的跨会话凭证摘要 | `id` | `uq_user_mem_token_hash`, `ix_user_mem_vault_token` | — |
| 23 | `user_memory_entries` | 用户确认偏好、版本号、TTL | `id` | `uq_user_mem_vault_key`, `ix_user_mem_expiry` | vault_id→user_memory_vaults |
| 24 | `chat_turn_receipts` | 工业会话已提交轮次的幂等回执 | `(conversation_id, key_digest)` | request digest, creation index | conversation_id→conversations.id (CASCADE) |

## 2. Column/type policy

- All integer PKs use `INTEGER` / MySQL `AUTO_INCREMENT`.
- Strings are `VARCHAR(n)`; JSON-like payloads are `TEXT`.
- Timestamps are naive UTC `DATETIME` (no timezone), matching existing SQLite/MySQL behavior.
- Python-side `default=` values in ORM are **not** server defaults; migrations intentionally do not invent server defaults.
- `quality_scores.conversation_id` references `conversations.session_id` (unique index), not the numeric PK.
- `feedbacks.quality_score_id` is a plain integer by design; no FK exists in the ORM.

## 3. Migration strategy

### Fresh database

```bash
DATABASE_URL=mysql+pymysql://... python -m db.migrate upgrade
```

- `20261030_0001` creates the four admin tables.
- `20261031_0002` creates the 14 historical tables in FK-safe order.
- `20261031_0003` creates the persistent admin token revocation table.
- `20261031_0004` adds `ix_audit_created`; the jobs `created_at` index was
  benchmarked and not adopted because MySQL did not use it.
- `20261031_0005` adds `dataset_snapshots` for public provenance metadata.
- `20261124_0006` adds opt-in session-bound compaction snapshots.
- `20261120_0006` creates the opt-in memory vault and preference tables.
- `20261124_0007` adds the monotonic vault revision and refuses unreviewed legacy vaults.
- `20261124_0008` joins both migration paths without changing published revision IDs.
- `20261125_0009` adds completed Chat-turn receipts scoped to the owning conversation.
- `20261127_0010` adds `(province, year, subject_type, major_id, min_rank)` for the bounded industrial history query; the historical-baseline verifier tolerates this post-baseline index until revision 0010 applies.

### Existing database at revision `20261030_0001`

- `upgrade` verifies every historical table that already exists against
  `db/schema_contract.py`; matching tables are skipped, missing tables are created.
- A column/index/unique/FK mismatch aborts the migration; it is never silently
  stamped or skipped.

### Existing database created by legacy `Base.metadata.create_all`

- Run the explicit, non-destructive adoption command:

```bash
DATABASE_URL=mysql+pymysql://... python -m db.migrate adopt-existing
DATABASE_URL=mysql+pymysql://... python -m db.migrate upgrade
```

- `adopt-existing` verifies all 18 tables (columns, PK, indexes, unique
  constraints, FKs) before `stamp 20261030_0001`.
- It never creates/drops tables and refuses an unexpected Alembic revision.
- Then `upgrade` verifies/skips existing historical tables and advances to the
  current head `20261125_0009`.

### Seed after migration

```bash
python -m scripts.seed_demo_data --database-url "$DATABASE_URL" --no-create-schema
```

`--no-create-schema` proves the seed does not rely on an implicit `create_all`.
The container entrypoint uses this flag in MySQL mode.

## 4. Failure and rollback

- MySQL DDL is non-transactional. `0002` preflights all existing tables before
  creating any; if a mid-migration failure leaves partial tables, rerunning
  verifies the created tables and continues.
- `downgrade` of `0002`/`0003` is intentionally refused. The baseline may cover
  pre-existing data; rollback requires a verified backup/restore, not a
  destructive DDL downgrade.
- `0010` is additive; its downgrade drops only the history lookup index. Verify
  a backup before production schema changes because MySQL DDL is non-transactional.
- Fresh databases can be recreated; existing databases must be backed up before
  schema changes.
- Schema drift examples tested: missing index, missing table, unexpected
  revision, duplicate upgrade, existing data preservation.

## 5. Verified environments

| Scenario | Test |
|---|---|
| SQLite fresh upgrade (24 tables) | `tests/test_migrations.py` |
| SQLite idempotent upgrade | `tests/test_migrations.py` |
| SQLite adopt existing + data preservation | `tests/test_migrations.py` |
| SQLite seed without `create_all` | `tests/test_migrations.py` |
| MySQL 8.4 fresh upgrade + seed + repeat (24 tables) | `tests/test_mysql_migrations.py` |
| Audit index Before/After | `benchmarks/mysql_admin_benchmark.py`, `docs/performance/mysql-benchmark.md` |
| MySQL adopt existing + data preservation | `tests/test_mysql_migrations.py` |
| MySQL index-drift rejection | `tests/test_mysql_migrations.py` |
