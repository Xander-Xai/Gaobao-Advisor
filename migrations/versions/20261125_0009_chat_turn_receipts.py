"""Session-owned completed chat receipts.

Revision ID: 20261125_0009
Revises: 20261124_0008
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20261125_0009"
down_revision = "20261124_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "chat_turn_receipts" in inspect(op.get_bind()).get_table_names():
        from db.schema_contract import verify_table

        problems = verify_table(op.get_bind(), "chat_turn_receipts")
        if problems:
            raise RuntimeError("chat receipt schema drift: " + "; ".join(problems))
        return
    op.create_table(
        "chat_turn_receipts",
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("key_digest", sa.String(64), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("response", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("conversation_id", "key_digest"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
    )


def downgrade() -> None:
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM chat_turn_receipts")).scalar_one():
        raise RuntimeError("Chat receipt downgrade requires explicit retention review")
    op.drop_table("chat_turn_receipts")
