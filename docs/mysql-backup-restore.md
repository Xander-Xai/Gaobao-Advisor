# MySQL backup and restore runbook

> 合成数据范围的公开运行手册模板；非历史私有环境报告，不包含真实部署规模或实际学生数据。

This runbook is for an operator-managed MySQL 8.4/InnoDB business database. It
does not run automatically and is not evidence of a production backup policy.
Use an authorized environment, a private client option file and an isolated
restore target. Do not put credentials on a command line or in shell history.

## 1. Preconditions and backup

1. Confirm the database endpoint, schema, MySQL version, available destination
   capacity, encryption-at-rest policy, retention period and recovery owner.
2. Create a mode-`0600` MySQL client option file outside the repository, readable
   only by the backup operator. Use a backup principal with the minimum required
   `SELECT`, `SHOW VIEW`, `TRIGGER`, `EVENT`, and `LOCK TABLES` privileges; for
   InnoDB `--single-transaction`, avoid writes to nontransactional tables.
3. Write the dump to an access-controlled destination on a different failure
   domain from MySQL. Encrypt it using the organization's managed key before
   off-host transfer. Record the schema name, server version, UTC time, Alembic
   head, dump exit status and SHA-256 in the backup record.

Example (operator supplies paths and environment-specific values):

```bash
umask 077
BACKUP_DIR=/secure/backups/gaobao
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$BACKUP_DIR"
mysqldump --defaults-extra-file=/secure/mysql-backup.cnf \
  --single-transaction --quick --routines --triggers --events --hex-blob \
  --set-gtid-purged=OFF gaobao \
  | gzip -1 > "$BACKUP_DIR/gaobao-${STAMP}.sql.gz"
gzip -t "$BACKUP_DIR/gaobao-${STAMP}.sql.gz"
sha256sum "$BACKUP_DIR/gaobao-${STAMP}.sql.gz" \
  > "$BACKUP_DIR/gaobao-${STAMP}.sql.gz.sha256"
```

Pipeline failures must fail the backup job; run shells with `set -o pipefail`.
Do not delete the previous known-good backup until the new archive has passed
integrity and restore checks. Retention is deployment-specific and must account
for legal hold, deletion requests, encryption-key retention and storage cost.

## 2. Restore rehearsal (never overwrite the live schema)

1. Provision a disposable MySQL 8.4 schema on an isolated endpoint. Verify it is
   not the production endpoint and that sufficient capacity exists.
2. Verify the recorded archive checksum and decrypt it into a protected path.
3. Create an empty scratch schema on the isolated restore endpoint. The example
   dump intentionally omits `--databases`, so restoration is directed to that
   explicitly named scratch schema:

```bash
gzip -dc /secure/backups/gaobao-<stamp>.sql.gz \
  | mysql --defaults-extra-file=/secure/mysql-restore.cnf gaobao_restore_test
```

4. Check restore output/exit code, `SELECT VERSION()`, table engines, schema
   inventory and `alembic_version`. Confirm expected table/row counts and run
   `python -m db.migrate current` with a read-only/verification connection.
5. Compare source and restored row counts or approved table checksums, including
   admissions history, provenance manifest pin/version, conversations covered
   by the selected retention window, and outstanding quality jobs/Outbox rows.
   Do not print or export student-level records into the report.
6. Exercise a read-only Catalog query and a bounded, non-mutating FastAPI
   readiness check against the restore candidate. Record restore duration,
   backup age/RPO, completion time/RTO, mismatches and operator identity.
7. Destroy the scratch instance according to the environment's secure disposal
   policy. Keep the rehearsal evidence without copied personal data.

## 3. Recovery and migration rollback

- On suspected corruption, stop writers and preserve the affected volume/logs
  under incident handling. Select a verified backup and point-in-time recovery
  target approved by the data owner.
- Restore to a new isolated instance first; validate schema head, required row
  counts, release-manifest pin and application readiness before controlled
  cutover. Preserve the pre-cutover database for rollback until acceptance.
- MySQL DDL is not assumed transactional. For an additive index migration,
  `20261127_0010` can be rolled back by dropping only
  `ix_adm_history_scope_rank`; if migration state or DDL completion is
  ambiguous, inspect `information_schema` and Alembic revision before taking
  action. Do not downgrade historical baseline migrations or delete business
  tables as a rollback strategy.
- Keep migration credentials separate from runtime API and Java Catalog
  SELECT-only credentials. The Catalog principal is not a backup or migration
  principal.

## 4. Acceptance record

Required per deployment: backup timestamp, source version/schema head, archive
checksum, encrypted destination, restore target identity, verification queries,
row-count/checksum results, RTO/RPO, operator and follow-up. A successful dump
alone is not a successful recovery test. No production restore claim is made by
this repository without a deployment-specific completed record.
