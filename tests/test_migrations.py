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


def test_alembic_upgrade_creates_all_23_tables(tmp_path):
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
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "20261124_0008"
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


@pytest.mark.parametrize("legacy_vault", [False, True])
def test_vault_revision_migration_does_not_guess_deleted_history(tmp_path, legacy_vault):
    from importlib import import_module

    pytest.importorskip("alembic")
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    migration = import_module("migrations.versions.20261124_0007_user_memory_revision")
    engine = create_engine(f"sqlite:///{tmp_path / 'old-vault.db'}")
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE user_memory_vaults (id VARCHAR(32) PRIMARY KEY)"))
            if legacy_vault:
                connection.execute(text("INSERT INTO user_memory_vaults (id) VALUES ('legacy')"))
            with Operations.context(MigrationContext.configure(connection)):
                if legacy_vault:
                    with pytest.raises(RuntimeError, match="explicit migration review"):
                        migration.upgrade()
                    assert connection.execute(text("SELECT COUNT(*) FROM user_memory_vaults")).scalar_one() == 1
                    columns = {c["name"] for c in inspect(connection).get_columns("user_memory_vaults")}
                    assert "last_version" not in columns
                else:
                    migration.upgrade()
                    migration.upgrade()
                    connection.execute(text("INSERT INTO user_memory_vaults (id) VALUES ('new')"))
                    assert connection.execute(text("SELECT last_version FROM user_memory_vaults")).scalar_one() == 0
    finally:
        engine.dispose()


@pytest.mark.parametrize("prior_head", ["20261124_0006", "20261124_0007"])
def test_memory_merge_head_upgrades_either_existing_branch(tmp_path, prior_head):
    db = tmp_path / "prior-head.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db}", "APP_ENV": "development"}
    script = (
        "from alembic import command; from db.migrate import alembic_config; "
        "import sys; command.upgrade(alembic_config(), sys.argv[1])"
    )
    initial = subprocess.run(
        [sys.executable, "-c", script, prior_head],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if initial.returncode != 0 and "No module named 'alembic'" in initial.stderr:
        pytest.skip("alembic is not installed")
    assert initial.returncode == 0, initial.stderr
    engine = create_engine(f"sqlite:///{db}")
    try:
        with engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == prior_head
        before = set(inspect(engine).get_table_names())
        if prior_head == "20261124_0006":
            assert "session_compaction_snapshots" in before
            assert "user_memory_vaults" not in before
        else:
            assert "user_memory_vaults" in before
            assert "session_compaction_snapshots" not in before
        upgraded = _run(db, "upgrade")
        assert upgraded.returncode == 0, upgraded.stderr
        assert verify_tables(engine, ALL_TABLES) == []
        with engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "20261124_0008"
        assert before <= set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
