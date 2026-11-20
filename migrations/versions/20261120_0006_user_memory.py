"""Opt-in cross-task user-memory vaults and explicit preference entries.

Revision ID: 20261120_0006
Revises: 20261031_0005
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20261120_0006"
down_revision = "20261031_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = set(inspect(op.get_bind()).get_table_names())
    if "user_memory_vaults" not in existing:
        op.create_table(
            "user_memory_vaults",
            sa.Column("id", sa.String(length=32), primary_key=True, nullable=False),
            sa.Column("token_hash", sa.String(length=64), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("token_hash", name="uq_user_mem_token_hash"),
        )
        op.create_index("ix_user_mem_vault_token", "user_memory_vaults", ["token_hash"], unique=False)
    if "user_memory_entries" not in existing:
        op.create_table(
            "user_memory_entries",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
            sa.Column("vault_id", sa.String(length=32), sa.ForeignKey("user_memory_vaults.id"), nullable=False),
            sa.Column("memory_key", sa.String(length=32), nullable=False),
            sa.Column("value", sa.String(length=160), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("vault_id", "memory_key", name="uq_user_mem_vault_key"),
        )
        op.create_index("ix_user_mem_expiry", "user_memory_entries", ["expires_at"], unique=False)


def downgrade() -> None:
    existing = set(inspect(op.get_bind()).get_table_names())
    if "user_memory_entries" in existing:
        op.drop_index("ix_user_mem_expiry", table_name="user_memory_entries")
        op.drop_table("user_memory_entries")
    if "user_memory_vaults" in existing:
        op.drop_index("ix_user_mem_vault_token", table_name="user_memory_vaults")
        op.drop_table("user_memory_vaults")
