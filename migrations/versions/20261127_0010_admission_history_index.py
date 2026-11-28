"""Index the bounded industrial admissions history query.

Revision ID: 20261127_0010
Revises: 20261125_0009
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect

revision = "20261127_0010"
down_revision = "20261125_0009"
branch_labels = None
depends_on = None

INDEX = "ix_adm_history_scope_rank"
COLUMNS = ["province", "year", "subject_type", "major_id", "min_rank"]


def upgrade() -> None:
    indexes = {item["name"]: item for item in inspect(op.get_bind()).get_indexes("admission_scores")}
    existing = indexes.get(INDEX)
    if existing is not None:
        if list(existing.get("column_names") or []) != COLUMNS or existing.get("unique"):
            raise RuntimeError(f"{INDEX} exists with an unexpected definition")
        return
    op.create_index(INDEX, "admission_scores", COLUMNS)


def downgrade() -> None:
    indexes = {item["name"] for item in inspect(op.get_bind()).get_indexes("admission_scores")}
    if INDEX in indexes:
        op.drop_index(INDEX, table_name="admission_scores")
