"""Alembic migration smoke tests on disposable databases."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

import db.models  # noqa: F401 - register metadata
from db.database import Base
from db.models import School
from db.schema_contract import ADMIN_TABLES, ALL_TABLES, HISTORICAL_TABLES, verify_tables

ROOT = Path(__file__).resolve().parent.parent


def _run(db: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db}", "APP_ENV": "development"}
    result = subprocess.run(
        [sys.executable, "-m", "db.migrate", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 and "No module named 'alembic'" in result.stderr:
        pytest.skip("alembic is not installed (install the mysql/dev extra)")
    return result


def test_alembic_upgrade_creates_all_22_tables(tmp_path):
    db = tmp_path / "migrate.db"
    result = _run(db, "upgrade")
    assert result.returncode == 0, result.stderr
    engine = create_engine(f"sqlite:///{db}")
    try:
        tables = set(inspect(engine).get_table_names())
        assert set(HISTORICAL_TABLES) <= tables
        assert set(ADMIN_TABLES) <= tables
        assert "alembic_version" in tables
        assert verify_tables(engine, ALL_TABLES) == []
    finally:
        engine.dispose()


def test_migration_is_idempotent(tmp_path):
    db = tmp_path / "migrate-idempotent.db"
    for _ in range(2):
        result = _run(db, "upgrade")
        assert result.returncode == 0, result.stderr


def test_seed_runs_after_migration_without_create_all(tmp_path):
    db = tmp_path / "migrate-seed.db"
    assert _run(db, "upgrade").returncode == 0
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db}", "APP_ENV": "development"}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.seed_demo_data",
            "--database-url",
            f"sqlite:///{db}",
            "--no-create-schema",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "schools" in result.stdout
    engine = create_engine(f"sqlite:///{db}")
    try:
        with engine.connect() as connection:
            assert connection.execute(text("SELECT COUNT(*) FROM schools")).scalar_one() == 8
    finally:
        engine.dispose()


def test_adopt_existing_schema_preserves_data(tmp_path):
    db = tmp_path / "adopt.db"
    engine = create_engine(f"sqlite:///{db}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        session.add(School(name="adopt-marker", province="示例省"))
        session.commit()
    finally:
        session.close()
        engine.dispose()

    result = _run(db, "adopt-existing")
    assert result.returncode == 0, result.stderr
    assert _run(db, "upgrade").returncode == 0

    engine = create_engine(f"sqlite:///{db}")
    try:
        with engine.connect() as connection:
            assert connection.execute(text("SELECT COUNT(*) FROM schools WHERE name='adopt-marker'")).scalar_one() == 1
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "20261124_0007"
        assert verify_tables(engine, ALL_TABLES) == []
    finally:
        engine.dispose()


def test_adopt_existing_rejects_incomplete_schema(tmp_path):
    db = tmp_path / "incomplete.db"
    engine = create_engine(f"sqlite:///{db}")
    School.__table__.create(engine)
    engine.dispose()
    result = _run(db, "adopt-existing")
    assert result.returncode != 0
    assert "cannot be adopted" in result.stderr or "missing table" in result.stderr
