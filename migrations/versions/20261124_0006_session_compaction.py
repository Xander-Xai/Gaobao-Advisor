"""Opt-in session compaction cache tied to existing conversation identity.

Revision ID: 20261124_0006
Revises: 20261031_0005

IMPORTANT: stacked Draft user-memory PR #135 also proposes a different 0006
revision. These branches must be rebased into one linear Alembic history before
both are merged; this migration is independently based on current master.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20261124_0006"
down_revision = "20261031_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "session_compaction_snapshots" in set(inspect(op.get_bind()).get_table_names()):
        # Existing demo create_all schemas need an explicit shape check before
        # accepting the table; never silently trust an unknown structure.
        from db.schema_contract import verify_table

        problems = verify_table(op.get_bind(), "session_compaction_snapshots")
        if problems:
            raise RuntimeError("session compaction schema drift: " + "; ".join(problems))
        return
    op.create_table(
        "session_compaction_snapshots",
        sa.Column("conversation_id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("covered_count", sa.Integer(), nullable=False),
        sa.Column("covered_sha256", sa.String(length=64), nullable=False),
        sa.Column("policy_sha256", sa.String(length=64), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_session_compaction_expires", "session_compaction_snapshots", ["expires_at"])


def downgrade() -> None:
    if "session_compaction_snapshots" in set(inspect(op.get_bind()).get_table_names()):
        op.drop_index("ix_session_compaction_expires", table_name="session_compaction_snapshots")
        op.drop_table("session_compaction_snapshots")
