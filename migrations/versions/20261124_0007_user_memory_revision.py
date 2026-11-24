"""Non-reusable vault-wide revisions for unpublished user-memory experiments.

Revision ID: 20261124_0007
Revises: 20261120_0006
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20261124_0007"
down_revision = "20261120_0006"
branch_labels = None
depends_on = None


def _require_empty_vaults() -> None:
    # A legacy vault may already have deleted entries whose revisions are
    # unknowable. Do not infer the historical high-water mark from live rows.
    # Operators must explicitly resolve those experimental vaults first.
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM user_memory_vaults")).scalar_one():
        raise RuntimeError("Existing experimental vaults require explicit migration review")


def upgrade() -> None:
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("user_memory_vaults")}
    if "last_version" in columns:
        return
    _require_empty_vaults()
    op.add_column(
        "user_memory_vaults",
        sa.Column("last_version", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("user_memory_vaults")}
    if "last_version" not in columns:
        return
    _require_empty_vaults()
    with op.batch_alter_table("user_memory_vaults") as batch:
        batch.drop_column("last_version")
