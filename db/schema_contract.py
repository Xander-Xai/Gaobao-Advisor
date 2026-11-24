"""Schema contract used by the historical-table Alembic baseline.

The contract is derived from ``db.models`` (the single source of truth), not
from a hand-maintained duplicate DDL. It is used to:

* verify an existing MySQL schema before adopting it into Alembic;
* verify tables that already exist before migration ``20261030_0002`` skips
  creating them.

A mismatch is a hard error. The migration never silently "stamps" or skips a
drifted table.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import inspect
from sqlalchemy.engine import Connection, Engine

import db.models  # noqa: F401 - register all tables on Base.metadata
from db.database import Base

HISTORICAL_TABLES: tuple[str, ...] = (
    "schools",
    "majors",
    "admission_scores",
    "enrollment_plans",
    "subject_rankings",
    "conversations",
    "conversation_messages",
    "yi_fen_yi_duan",
    "highlights",
    "feedbacks",
    "quality_scores",
    "graduate_program",
    "graduate_score",
    "career_trend",
)

ADMIN_TABLES: tuple[str, ...] = (
    "data_quality_jobs",
    "data_quality_issues",
    "operation_audit_logs",
    "quality_task_outbox",
)

SECURITY_TABLES: tuple[str, ...] = ("admin_token_revocations",)
METADATA_TABLES: tuple[str, ...] = ("dataset_snapshots", "session_compaction_snapshots")
USER_MEMORY_TABLES: tuple[str, ...] = ("user_memory_vaults", "user_memory_entries")

# The legacy baseline that existed before 20261031_0003. ``adopt-existing``
# must accept older create_all databases that lack the revocation and dataset
# metadata tables; migrations 0003/0005 create them afterwards.
BASELINE_TABLES: tuple[str, ...] = HISTORICAL_TABLES + ADMIN_TABLES
ALL_TABLES: tuple[str, ...] = BASELINE_TABLES + SECURITY_TABLES + METADATA_TABLES + USER_MEMORY_TABLES


def _type_signature(value: Any) -> tuple[str, int | None]:
    name = type(value).__name__.lower()
    if name in {"string", "varchar", "char"}:
        return "string", getattr(value, "length", None)
    if name in {"integer", "int", "bigint", "smallint"}:
        return "integer", None
    if name in {"float", "real", "double", "numeric", "decimal"}:
        return "float", None
    if name in {"text", "longtext", "mediumtext"}:
        return "text", None
    if name in {"datetime", "timestamp"}:
        return "datetime", None
    return name, getattr(value, "length", None)


def _table_problems(inspector, table_name: str) -> list[str]:
    expected = Base.metadata.tables[table_name]
    problems: list[str] = []
    actual_columns = {column["name"]: column for column in inspector.get_columns(table_name)}
    expected_columns = {column.name: column for column in expected.columns}

    for name in sorted(set(expected_columns) - set(actual_columns)):
        problems.append(f"{table_name}: missing column {name}")
    for name in sorted(set(actual_columns) - set(expected_columns)):
        problems.append(f"{table_name}: unexpected column {name}")

    expected_pk = [column.name for column in expected.primary_key.columns]
    actual_pk = list(inspector.get_pk_constraint(table_name).get("constrained_columns") or [])
    if expected_pk != actual_pk:
        problems.append(f"{table_name}: primary key expected={expected_pk} actual={actual_pk}")

    for name, column in expected_columns.items():
        actual = actual_columns.get(name)
        if actual is None:
            continue
        if bool(actual.get("nullable")) != bool(column.nullable):
            problems.append(f"{table_name}.{name}: nullable expected={column.nullable} actual={actual.get('nullable')}")
        expected_type = _type_signature(column.type)
        actual_type = _type_signature(actual.get("type"))
        if expected_type != actual_type:
            problems.append(f"{table_name}.{name}: type expected={expected_type} actual={actual_type}")

    actual_indexes = {index["name"]: index for index in inspector.get_indexes(table_name)}
    for index in expected.indexes:
        actual = actual_indexes.get(index.name)
        if actual is None:
            problems.append(f"{table_name}: missing index {index.name}")
            continue
        expected_cols = [column.name for column in index.columns]
        if list(actual.get("column_names") or []) != expected_cols:
            problems.append(f"{table_name}.{index.name}: index columns mismatch")
        if bool(actual.get("unique")) != bool(index.unique):
            problems.append(f"{table_name}.{index.name}: index uniqueness mismatch")

    actual_uniques = inspector.get_unique_constraints(table_name)
    for constraint in expected.constraints:
        if constraint.__class__.__name__ != "UniqueConstraint":
            continue
        expected_cols = [column.name for column in constraint.columns]
        found = any(
            list(item.get("column_names") or []) == expected_cols
            and (constraint.name is None or item.get("name") in (None, constraint.name))
            for item in actual_uniques
        )
        if not found:
            problems.append(f"{table_name}: missing unique constraint {constraint.name or expected_cols}")

    actual_fks = inspector.get_foreign_keys(table_name)
    for fk in expected.foreign_key_constraints:
        expected_cols = [element.parent.name for element in fk.elements]
        expected_ref = fk.elements[0].column.table.name
        expected_ref_cols = [element.column.name for element in fk.elements]
        found = any(
            list(item.get("constrained_columns") or []) == expected_cols
            and item.get("referred_table") == expected_ref
            and list(item.get("referred_columns") or []) == expected_ref_cols
            for item in actual_fks
        )
        if not found:
            problems.append(f"{table_name}: missing FK {expected_cols} -> {expected_ref}{expected_ref_cols}")
    return problems


def verify_table(bind: Connection | Engine, table_name: str) -> list[str]:
    """Return schema mismatches for one table; empty means it matches."""
    inspector = inspect(bind)
    if table_name not in inspector.get_table_names():
        return [f"{table_name}: missing table"]
    return _table_problems(inspector, table_name)


def verify_tables(
    bind: Connection | Engine,
    table_names: Iterable[str] = ALL_TABLES,
) -> list[str]:
    """Return all missing/drifted tables in the contract."""
    inspector = inspect(bind)
    existing = set(inspector.get_table_names())
    problems: list[str] = []
    for table_name in table_names:
        if table_name not in existing:
            problems.append(f"{table_name}: missing table")
            continue
        problems.extend(_table_problems(inspector, table_name))
    return problems
