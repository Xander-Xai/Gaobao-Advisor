"""Real MySQL 8.4 migration/adoption tests.

Requires MYSQL_TEST_URL; the database name must contain ``test``. The fixture
drops/recreates only that schema.
"""

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
from tests.mysql_guard import ensure_test_database

MYSQL_URL = os.getenv("MYSQL_TEST_URL", "").strip()
ROOT = Path(__file__).resolve().parent.parent
pytestmark = [
    pytest.mark.mysql,
    pytest.mark.skipif(not MYSQL_URL, reason="MYSQL_TEST_URL is not set"),
]


def _database_name(url: str) -> str:
    return url.rsplit("/", 1)[-1].split("?", 1)[0]


def _drop_all(engine) -> None:
    with engine.begin() as connection:
        connection.execute(text("SET FOREIGN_KEY_CHECKS=0"))
    Base.metadata.drop_all(bind=engine)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
        connection.execute(text("SET FOREIGN_KEY_CHECKS=1"))


@pytest.fixture
def mysql_engine():
    engine = create_engine(MYSQL_URL, pool_pre_ping=True, pool_recycle=1800)
    ensure_test_database(engine, MYSQL_URL)
    _drop_all(engine)
    yield engine
    _drop_all(engine)
    engine.dispose()


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "DATABASE_URL": MYSQL_URL, "APP_ENV": "development"}
    return subprocess.run(
        [sys.executable, *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


def test_fresh_mysql_upgrade_seed_and_idempotency(mysql_engine):
    result = _run("-m", "db.migrate", "upgrade")
    assert result.returncode == 0, result.stderr

    tables = set(inspect(mysql_engine).get_table_names())
    assert set(HISTORICAL_TABLES) <= tables
    assert set(ADMIN_TABLES) <= tables
    assert "alembic_version" in tables
    assert verify_tables(mysql_engine, ALL_TABLES) == []

    seed = _run(
        "-m",
        "scripts.seed_demo_data",
        "--database-url",
        MYSQL_URL,
        "--no-create-schema",
    )
    assert seed.returncode == 0, seed.stderr
    with mysql_engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM schools")).scalar_one() == 8

    repeat = _run("-m", "db.migrate", "upgrade")
    assert repeat.returncode == 0, repeat.stderr
    with mysql_engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM schools")).scalar_one() == 8
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "20261127_0010"


def test_existing_create_all_schema_is_adopted_without_data_loss(mysql_engine):
    Base.metadata.create_all(bind=mysql_engine)
    session = sessionmaker(bind=mysql_engine)()
    try:
        session.add(School(name="mysql-adopt-marker", province="示例省"))
        session.commit()
    finally:
        session.close()

    adopt = _run("-m", "db.migrate", "adopt-existing")
    assert adopt.returncode == 0, adopt.stderr
    upgrade = _run("-m", "db.migrate", "upgrade")
    assert upgrade.returncode == 0, upgrade.stderr

    with mysql_engine.connect() as connection:
        assert (
            connection.execute(text("SELECT COUNT(*) FROM schools WHERE name='mysql-adopt-marker'")).scalar_one() == 1
        )
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "20261127_0010"
    assert verify_tables(mysql_engine, ALL_TABLES) == []


def test_adopt_existing_rejects_index_drift(mysql_engine):
    Base.metadata.create_all(bind=mysql_engine)
    with mysql_engine.begin() as connection:
        connection.execute(text("ALTER TABLE schools DROP INDEX ix_schools_province"))
    result = _run("-m", "db.migrate", "adopt-existing")
    assert result.returncode != 0
    assert "missing index ix_schools_province" in result.stderr


_SUBJECT = "sub_mysql_subject_0001"


@pytest.fixture
def mysql_memory(mysql_engine, monkeypatch):
    """Use only the guarded disposable MySQL schema and real transactions."""
    result = _run("-m", "db.migrate", "upgrade")
    assert result.returncode == 0, result.stderr
    from server import user_memory as memory

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("USER_MEMORY_ENABLED", "true")
    monkeypatch.setenv("USER_MEMORY_ENCRYPTION_KEY", "33" * 32)
    monkeypatch.setattr(memory, "get_session", sessionmaker(bind=mysql_engine))
    return memory


def test_mysql_concurrent_memory_cas_has_exactly_one_winner(mysql_memory):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    memory = mysql_memory
    token = memory.create_vault(_SUBJECT, consent=True)["memory_token"]
    initial = memory.put_memory(token, "preferred_regions", "华南", subject=_SUBJECT, expected_version=0)
    barrier = Barrier(2, timeout=10)

    def attempt(value):
        barrier.wait()
        try:
            return memory.put_memory(token, "preferred_regions", value, subject=_SUBJECT, expected_version=initial["version"])
        except memory.MemoryConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(attempt, "华北")
        second = pool.submit(attempt, "西南")
        results = [first.result(timeout=20), second.result(timeout=20)]

    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    assert winners[0]["version"] == initial["version"] + 1
    assert memory.list_memories(token, _SUBJECT) == winners


def test_mysql_rotation_rejects_stale_token_and_preserves_monotonic_revision(mysql_memory):
    memory = mysql_memory
    old_token = memory.create_vault(_SUBJECT, consent=True)["memory_token"]
    initial = memory.put_memory(old_token, "preferred_regions", "华南", expected_version=0)
    token = memory.rotate_token(old_token, _SUBJECT)["memory_token"]

    with pytest.raises(memory.MemoryUnauthorized):
        memory.put_memory(old_token, "preferred_regions", "华北", subject=_SUBJECT, expected_version=initial["version"])
    with pytest.raises(memory.MemoryUnauthorized):
        memory.delete_memory(old_token, "preferred_regions", subject=_SUBJECT, expected_version=initial["version"])
    with pytest.raises(memory.MemoryUnauthorized):
        memory.erase_vault(old_token, _SUBJECT)

    memory.delete_memory(token, "preferred_regions", subject=_SUBJECT, expected_version=initial["version"])
    recreated = memory.put_memory(token, "preferred_regions", "西南", subject=_SUBJECT, expected_version=0)
    assert recreated["version"] > initial["version"]
    with pytest.raises(memory.MemoryConflict):
        memory.put_memory(token, "preferred_regions", "过时", subject=_SUBJECT, expected_version=initial["version"])
    with pytest.raises(memory.MemoryConflict):
        memory.delete_memory(token, "preferred_regions", subject=_SUBJECT, expected_version=initial["version"])
    assert memory.list_memories(token, _SUBJECT) == [recreated]


def test_mysql_token_rotation_waits_for_credential_row_lock(mysql_memory, mysql_engine):
    from sqlalchemy.exc import OperationalError

    memory = mysql_memory
    token = memory.create_vault(_SUBJECT, consent=True)["memory_token"]
    factory = sessionmaker(bind=mysql_engine)
    with factory() as first, factory() as second:
        owner = memory._owner(first, token, _SUBJECT, write=True)
        try:
            previous = second.execute(text("SELECT @@SESSION.innodb_lock_wait_timeout")).scalar_one()
            second.execute(text("SET SESSION innodb_lock_wait_timeout=1"))
            try:
                with pytest.raises(OperationalError) as error:
                    second.execute(
                        text("UPDATE user_memory_vaults SET token_hash=token_hash WHERE id=:id"),
                        {"id": owner.id},
                    )
                assert error.value.orig.args[0] == 1205
                second.rollback()
            finally:
                second.execute(text(f"SET SESSION innodb_lock_wait_timeout={int(previous)}"))
                second.commit()
        finally:
            first.rollback()

    rotated = memory.rotate_token(token, _SUBJECT)["memory_token"]
    assert memory.put_memory(rotated, "career_goal", "软件工程", subject=_SUBJECT, expected_version=0)["version"] == 1
