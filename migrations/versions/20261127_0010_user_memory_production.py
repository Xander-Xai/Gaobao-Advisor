"""Production hardening of the cross-task user-memory vault.

Revision ID: 20261127_0010
Revises: 20261125_0009

Changes:

* ``user_memory_vaults`` gains a pseudonymous ``subject_hash`` binding, an
  explicit consent timestamp and the consent version that was accepted.
* ``user_memory_entries.value`` becomes ``TEXT`` so that an AES-GCM envelope
  can be stored; ``key_id`` and ``data_class`` are added.
* ``user_memory_audit`` is created as a metadata-only trail for consent,
  mutation, rotation and revocation.

Existing vaults keep working as *data* but lose read/write access: a vault
without a subject binding can no longer prove ownership of a subject. Its
credential may still be used to destroy the vault. Existing plaintext entries
are preserved verbatim (they are reported as ``LEGACY_PLAINTEXT``) so that the
migration never loses user data; ``scripts/rekey_user_memory.py`` rewrites
them under the active key.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20261127_0010"
down_revision = "20261125_0009"
branch_labels = None
depends_on = None

_LEGACY_VALUE_LENGTH = 160


def _columns(table: str) -> set[str]:
    return {column["name"] for column in inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {index["name"] for index in inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())

    vault_columns = _columns("user_memory_vaults")
    if "subject_hash" not in vault_columns:
        op.add_column("user_memory_vaults", sa.Column("subject_hash", sa.String(length=64), nullable=True))
    if "consent_granted_at" not in vault_columns:
        op.add_column("user_memory_vaults", sa.Column("consent_granted_at", sa.DateTime(), nullable=True))
    if "consent_version" not in vault_columns:
        op.add_column("user_memory_vaults", sa.Column("consent_version", sa.String(length=32), nullable=True))
    if "ix_user_mem_vault_subject" not in _indexes("user_memory_vaults"):
        op.create_index("ix_user_mem_vault_subject", "user_memory_vaults", ["subject_hash"])

    entry_columns = _columns("user_memory_entries")
    if "key_id" not in entry_columns:
        op.add_column("user_memory_entries", sa.Column("key_id", sa.String(length=16), nullable=True))
    if "data_class" not in entry_columns:
        op.add_column("user_memory_entries", sa.Column("data_class", sa.String(length=32), nullable=True))
    if "value" in entry_columns:
        # SQLite cannot ALTER a column type; batch mode rebuilds the table.
        with op.batch_alter_table("user_memory_entries") as batch:
            batch.alter_column(
                "value",
                type_=sa.Text(),
                existing_type=sa.String(length=_LEGACY_VALUE_LENGTH),
                existing_nullable=False,
            )

    if "user_memory_audit" not in tables:
        op.create_table(
            "user_memory_audit",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("vault_id", sa.String(length=32), nullable=False),
            sa.Column("event", sa.String(length=32), nullable=False),
            sa.Column("memory_key", sa.String(length=32), nullable=True),
            sa.Column("version", sa.Integer(), nullable=True),
            sa.Column("occurred_at", sa.DateTime(), nullable=False),
            sa.Column("detail_json", sa.Text(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_user_mem_audit_vault", "user_memory_audit", ["vault_id"])
        op.create_index("ix_user_mem_audit_occurred", "user_memory_audit", ["occurred_at"])


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if "user_memory_audit" in tables:
        op.drop_index("ix_user_mem_audit_occurred", table_name="user_memory_audit")
        op.drop_index("ix_user_mem_audit_vault", table_name="user_memory_audit")
        op.drop_table("user_memory_audit")

    entry_columns = _columns("user_memory_entries")
    if "value" in entry_columns:
        with op.batch_alter_table("user_memory_entries") as batch:
            batch.alter_column(
                "value",
                type_=sa.String(length=_LEGACY_VALUE_LENGTH),
                existing_type=sa.Text(),
                existing_nullable=False,
            )
    if "data_class" in _columns("user_memory_entries"):
        with op.batch_alter_table("user_memory_entries") as batch:
            batch.drop_column("data_class")
    if "key_id" in _columns("user_memory_entries"):
        with op.batch_alter_table("user_memory_entries") as batch:
            batch.drop_column("key_id")

    if "ix_user_mem_vault_subject" in _indexes("user_memory_vaults"):
        op.drop_index("ix_user_mem_vault_subject", table_name="user_memory_vaults")
    for column in ("consent_version", "consent_granted_at", "subject_hash"):
        if column in _columns("user_memory_vaults"):
            with op.batch_alter_table("user_memory_vaults") as batch:
                batch.drop_column(column)
