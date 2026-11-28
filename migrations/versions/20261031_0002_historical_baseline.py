"""historical schema baseline: the 14 legacy business tables

Revision ID: 20261031_0002
Revises: 20261030_0001
Create Date: 2026-10-31

Strategy
--------
* Fresh database: revision 0001 creates the four admin tables, this revision
  creates the 14 historical tables in FK-safe order.
* Existing database at 0001 (or an adopted create_all database): existing
  historical tables are verified against ``db.schema_contract`` before being
  skipped. A drifted table aborts the migration instead of being silently
  accepted.
* MySQL DDL is non-transactional. The migration preflights all existing tables
  first; if creation fails midway, rerunning is safe because already-created
  tables are verified and skipped.
* Downgrade is intentionally refused: this is a baseline for tables that may
  already contain real data. Use a backup/restore procedure for rollback.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

from db.schema_contract import HISTORICAL_TABLES, verify_table

revision = "20261031_0002"
down_revision = "20261030_0001"
branch_labels = None
depends_on = None


def _create_schools() -> None:
    op.create_table(
        "schools",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("province", sa.String(length=20), nullable=False),
        sa.Column("city", sa.String(length=30), nullable=False),
        sa.Column("level", sa.String(length=20), nullable=False),
        sa.Column("school_type", sa.String(length=20), nullable=False),
        sa.Column("ranking", sa.Integer(), nullable=True),
        sa.Column("is_985", sa.Integer(), nullable=True),
        sa.Column("is_211", sa.Integer(), nullable=True),
        sa.Column("is_double_first_class", sa.Integer(), nullable=True),
        sa.Column("website", sa.String(length=200), nullable=True),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("special_type", sa.String(length=20), nullable=True),
        sa.Column("data_source_note", sa.String(length=200), nullable=True),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_schools_province", "schools", ["province"])
    op.create_index("ix_schools_level", "schools", ["level"])


def _create_majors() -> None:
    op.create_table(
        "majors",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("sub_category", sa.String(length=50), nullable=True),
        sa.Column("employment_rate", sa.Float(), nullable=True),
        sa.Column("avg_salary", sa.Float(), nullable=True),
        sa.Column("median_salary", sa.Float(), nullable=True),
        sa.Column("salary_range", sa.Text(), nullable=True),
        sa.Column("top_industries", sa.Text(), nullable=True),
        sa.Column("employment_locations", sa.Text(), nullable=True),
        sa.Column("postgraduate_rate", sa.Float(), nullable=True),
        sa.Column("overseas_rate", sa.Float(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("job_directions", sa.Text(), nullable=True),
        sa.Column("is_hot", sa.Integer(), nullable=True),
        sa.Column("education_level", sa.String(length=10), nullable=True),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_majors_category", "majors", ["category"])


def _create_admission_scores() -> None:
    op.create_table(
        "admission_scores",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("school_id", sa.Integer(), sa.ForeignKey("schools.id"), nullable=False),
        sa.Column("major_id", sa.Integer(), sa.ForeignKey("majors.id"), nullable=True),
        sa.Column("province", sa.String(length=20), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("batch", sa.String(length=20), nullable=False),
        sa.Column("standardized_batch", sa.String(length=20), nullable=True),
        sa.Column("subject_type", sa.String(length=10), nullable=False),
        sa.Column("min_score", sa.Integer(), nullable=True),
        sa.Column("avg_score", sa.Float(), nullable=True),
        sa.Column("max_score", sa.Integer(), nullable=True),
        sa.Column("min_rank", sa.Integer(), nullable=True),
        sa.Column("plan_count", sa.Integer(), nullable=True),
        sa.UniqueConstraint(
            "school_id",
            "major_id",
            "province",
            "year",
            "batch",
            "subject_type",
            name="uq_admission_score",
        ),
    )
    op.create_index("ix_adm_lookup", "admission_scores", ["school_id", "province", "year", "subject_type"])
    op.create_index(
        "ix_adm_dedup",
        "admission_scores",
        ["school_id", "province", "year", "batch", "subject_type", "major_id"],
    )
    op.create_index("ix_adm_year", "admission_scores", ["year"])


def _create_enrollment_plans() -> None:
    op.create_table(
        "enrollment_plans",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("school_id", sa.Integer(), sa.ForeignKey("schools.id"), nullable=False),
        sa.Column("major_id", sa.Integer(), sa.ForeignKey("majors.id"), nullable=False),
        sa.Column("province", sa.String(length=20), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("plan_count", sa.Integer(), nullable=True),
        sa.Column("subject_requirement", sa.String(length=100), nullable=True),
        sa.Column("batch", sa.String(length=20), nullable=True),
        sa.Column("duration", sa.Integer(), nullable=True),
        sa.Column("tuition", sa.Integer(), nullable=True),
        sa.UniqueConstraint("school_id", "major_id", "province", "year", name="uq_enrollment_plan"),
    )


def _create_subject_rankings() -> None:
    op.create_table(
        "subject_rankings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("school_id", sa.Integer(), sa.ForeignKey("schools.id"), nullable=False),
        sa.Column("major_category", sa.String(length=50), nullable=False),
        sa.Column("ranking_source", sa.String(length=50), nullable=False),
        sa.Column("ranking_year", sa.Integer(), nullable=False),
        sa.Column("ranking_position", sa.Integer(), nullable=True),
        sa.Column("grade", sa.String(length=10), nullable=True),
        sa.UniqueConstraint(
            "school_id",
            "major_category",
            "ranking_source",
            "ranking_year",
            name="uq_subject_ranking",
        ),
    )


def _create_conversations() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("user_label", sa.String(length=50), nullable=True),
        sa.Column("province", sa.String(length=20), nullable=True),
        sa.Column("score_rank", sa.String(length=30), nullable=True),
        sa.Column("subject", sa.String(length=50), nullable=True),
        sa.Column("slots_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_conversations_session_id", "conversations", ["session_id"], unique=True)


def _create_conversation_messages() -> None:
    op.create_table(
        "conversation_messages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_conversation_messages_conversation_id",
        "conversation_messages",
        ["conversation_id"],
    )


def _create_yi_fen_yi_duan() -> None:
    op.create_table(
        "yi_fen_yi_duan",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("province", sa.String(length=20), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("subject_type", sa.String(length=10), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("cumulative_count", sa.Integer(), nullable=False),
        sa.UniqueConstraint("province", "year", "subject_type", "score", name="uq_yi_fen_yi_duan"),
    )
    op.create_index("ix_yfdd_province_year", "yi_fen_yi_duan", ["province", "year"])


def _create_highlights() -> None:
    op.create_table(
        "highlights",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_highlights_session_id", "highlights", ["session_id"])


def _create_feedbacks() -> None:
    op.create_table(
        "feedbacks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("message_index", sa.Integer(), nullable=False),
        sa.Column("rating", sa.String(length=10), nullable=False),
        sa.Column("quality_score_id", sa.Integer(), nullable=True),
        sa.Column("feedback_text", sa.Text(), nullable=True),
        sa.Column("implicit_negative", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_feedbacks_session_id", "feedbacks", ["session_id"])
    op.create_index("ix_feedback_session_msg", "feedbacks", ["session_id", "message_index"])


def _create_quality_scores() -> None:
    op.create_table(
        "quality_scores",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column(
            "conversation_id",
            sa.String(length=64),
            sa.ForeignKey("conversations.session_id"),
            nullable=False,
        ),
        sa.Column("message_id", sa.String(length=64), nullable=False),
        sa.Column("factual_score", sa.Float(), nullable=True),
        sa.Column("relevance_score", sa.Float(), nullable=True),
        sa.Column("helpfulness_score", sa.Float(), nullable=True),
        sa.Column("style_score", sa.Float(), nullable=True),
        sa.Column("aggregate_score", sa.Float(), nullable=False),
        sa.Column("hallucination_flags", sa.Text(), nullable=True),
        sa.Column("judge_model", sa.String(length=64), nullable=False),
        sa.Column("judge_latency_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_quality_score_conversation", "quality_scores", ["conversation_id"])


def _create_graduate_program() -> None:
    op.create_table(
        "graduate_program",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("school_name", sa.String(length=100), nullable=False),
        sa.Column("school_level", sa.String(length=20), nullable=True),
        sa.Column("province", sa.String(length=20), nullable=True),
        sa.Column("major_name", sa.String(length=100), nullable=False),
        sa.Column("major_category", sa.String(length=50), nullable=True),
        sa.Column("degree_type", sa.String(length=20), nullable=True),
        sa.Column("acceptance_rate", sa.Float(), nullable=True),
        sa.Column("avg_score", sa.Integer(), nullable=True),
        sa.Column("plan_count", sa.Integer(), nullable=True),
    )
    op.create_index("ix_graduate_program_school_name", "graduate_program", ["school_name"])
    op.create_index("idx_grad_program_school_major", "graduate_program", ["school_name", "major_name"])


def _create_graduate_score() -> None:
    op.create_table(
        "graduate_score",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("program_id", sa.Integer(), sa.ForeignKey("graduate_program.id"), nullable=True),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("subject_type", sa.String(length=20), nullable=True),
        sa.Column("total_score", sa.Integer(), nullable=True),
        sa.Column("politics_score", sa.Integer(), nullable=True),
        sa.Column("english_score", sa.Integer(), nullable=True),
        sa.Column("major_score", sa.Integer(), nullable=True),
    )


def _create_career_trend() -> None:
    op.create_table(
        "career_trend",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("major_name", sa.String(length=100), nullable=True),
        sa.Column("industry", sa.String(length=100), nullable=True),
        sa.Column("job_title", sa.String(length=100), nullable=True),
        sa.Column("salary_median", sa.Integer(), nullable=True),
        sa.Column("employment_rate", sa.Float(), nullable=True),
        sa.Column("growth_rate", sa.Float(), nullable=True),
        sa.Column("year", sa.Integer(), nullable=True),
    )
    op.create_index("ix_career_trend_major_name", "career_trend", ["major_name"])


_CREATORS = {
    "schools": _create_schools,
    "majors": _create_majors,
    "admission_scores": _create_admission_scores,
    "enrollment_plans": _create_enrollment_plans,
    "subject_rankings": _create_subject_rankings,
    "conversations": _create_conversations,
    "conversation_messages": _create_conversation_messages,
    "yi_fen_yi_duan": _create_yi_fen_yi_duan,
    "highlights": _create_highlights,
    "feedbacks": _create_feedbacks,
    "quality_scores": _create_quality_scores,
    "graduate_program": _create_graduate_program,
    "graduate_score": _create_graduate_score,
    "career_trend": _create_career_trend,
}


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(inspect(bind).get_table_names())

    # Preflight every table that already exists before creating anything.
    problems: list[str] = []
    for table_name in HISTORICAL_TABLES:
        if table_name in existing:
            # This access-path index was introduced after the historical
            # baseline; revision 0010 creates it after the legacy schema has
            # been adopted without treating the old table as structural drift.
            ignored = frozenset({"ix_adm_history_scope_rank"}) if table_name == "admission_scores" else frozenset()
            problems.extend(verify_table(bind, table_name, ignore_indexes=ignored))
    if problems:
        raise RuntimeError("existing historical schema does not match the contract: " + "; ".join(problems))

    for table_name in HISTORICAL_TABLES:
        if table_name in existing:
            continue
        _CREATORS[table_name]()
        existing.add(table_name)


def downgrade() -> None:
    raise RuntimeError(
        "20261031_0002 is a historical-schema baseline and may cover pre-existing data; "
        "refusing destructive downgrade. Restore from a verified backup instead."
    )
