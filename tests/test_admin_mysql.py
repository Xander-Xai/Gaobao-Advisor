"""Optional MySQL integration tests for the operations workbench.

Run locally with a disposable database, e.g.::

    docker run --rm -d --name gaobao-mysql-test \\
      -e MYSQL_ROOT_PASSWORD=testroot -e MYSQL_DATABASE=gaobao_test \\
      -p 3307:3306 mysql:8.4
    MYSQL_TEST_URL='mysql+pymysql://root:testroot@127.0.0.1:3307/gaobao_test?charset=utf8mb4' \\
      .venv/bin/python -m pytest tests/test_admin_mysql.py -q

The database name must contain ``test``; the fixture never touches a
non-test schema.
"""

from __future__ import annotations

import datetime
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from db.database import Base
from db.models import DataQualityJob, QualityTaskOutbox
from server.admin import repository
from server.deps import get_db
from server.main import app
from tests.conftest import _seed, auth_header, login
from tests.mysql_guard import ensure_test_database

MYSQL_URL = os.getenv("MYSQL_TEST_URL", "").strip()
pytestmark = [
    pytest.mark.mysql,
    pytest.mark.skipif(not MYSQL_URL, reason="MYSQL_TEST_URL is not set"),
]


def _database_name(url: str) -> str:
    return url.rsplit("/", 1)[-1].split("?", 1)[0]


@pytest.fixture
def mysql_factory():
    engine = create_engine(MYSQL_URL, pool_pre_ping=True, pool_recycle=1800)
    ensure_test_database(engine, MYSQL_URL)
    with engine.begin() as conn:
        conn.execute(text("SET FOREIGN_KEY_CHECKS=0"))
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = factory()
    try:
        _seed(session)
    finally:
        session.close()
    yield factory
    with engine.begin() as conn:
        conn.execute(text("SET FOREIGN_KEY_CHECKS=0"))
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture
def mysql_client(admin_env, mysql_factory):
    def override_get_db():
        session = mysql_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        yield client
    finally:
        app.dependency_overrides.pop(get_db, None)
        client.close()


def test_mysql_school_listing_and_detail(mysql_client):
    token = login(mysql_client, "viewer")
    listing = mysql_client.get(
        "/api/v1/admin/schools",
        params={"page_size": 3, "sort": "name", "order": "asc"},
        headers=auth_header(token),
    )
    assert listing.status_code == 200, listing.text
    body = listing.json()
    assert body["total"] == 8
    assert len(body["items"]) == 3
    assert all(item["score_count"] >= 0 for item in body["items"])

    school_id = body["items"][0]["id"]
    detail = mysql_client.get(f"/api/v1/admin/schools/{school_id}", headers=auth_header(token))
    assert detail.status_code == 200, detail.text
    assert "scores" in detail.json()
    assert "plans" in detail.json()


def test_mysql_quality_job_and_review_transaction(mysql_client, mysql_factory):
    operator = login(mysql_client, "operator")
    created = mysql_client.post(
        "/api/v1/admin/quality/jobs",
        json={"filters": {"province": "示例省·甲"}},
        headers={**auth_header(operator), "Idempotency-Key": "mysql-job-key-0001"},
    )
    assert created.status_code == 201, created.text
    job = created.json()["job"]
    assert job["status"] == "SUCCEEDED"
    assert job["issue_count"] > 0

    admin = login(mysql_client, "admin")
    reviewed = mysql_client.post(
        f"/api/v1/admin/quality/jobs/{job['id']}/review",
        json={"decision": "APPROVED", "note": "mysql integration", "expected_version": job["version"]},
        headers=auth_header(admin),
    )
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["review_status"] == "APPROVED"

    session = mysql_factory()
    try:
        count = session.execute(text("SELECT COUNT(*) FROM operation_audit_logs")).scalar_one()
        assert count >= 3
    finally:
        session.close()


def test_mysql_rollback_leaves_no_partial_job(mysql_client, mysql_factory, monkeypatch):
    from server.admin import repository

    def explode(*args, **kwargs):
        raise RuntimeError("mysql audit failure")

    monkeypatch.setattr(repository, "record_audit", explode)
    operator = login(mysql_client, "operator")
    response = mysql_client.post(
        "/api/v1/admin/quality/jobs",
        json={"filters": {}},
        headers={**auth_header(operator), "Idempotency-Key": "mysql-rollback-key-01"},
    )
    assert response.status_code == 500
    session = mysql_factory()
    try:
        assert session.execute(text("SELECT COUNT(*) FROM data_quality_jobs")).scalar_one() == 0
        assert session.execute(text("SELECT COUNT(*) FROM quality_task_outbox")).scalar_one() == 0
    finally:
        session.close()


def test_mysql_uses_mysql_dialect(mysql_client):
    token = login(mysql_client, "viewer")
    response = mysql_client.get("/api/v1/admin/system/status", headers=auth_header(token))
    assert response.status_code == 200
    assert response.json()["database_dialect"] == "mysql"
    assert response.json()["admin_enabled"] is True


def test_mysql_orphaned_dispatched_job_is_recovered(mysql_factory):
    """The PENDING+DISPATCHED compensation scan must work on MySQL too."""
    session = mysql_factory()
    try:
        job, _ = repository.create_job_with_outbox(
            session,
            filters={},
            idempotency_key="mysql-crash-window-key-01",
            actor="operator",
            role="operator",
            request_id="req-mysql-crash",
            ip_hash=None,
        )
        job_id = job.id
        repository.mark_outbox_dispatched(session, job_id)
        job = session.get(DataQualityJob, job_id)
        job.updated_at = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) - datetime.timedelta(
            seconds=120
        )
        session.commit()

        assert repository.requeue_orphaned_dispatched(session, older_than_seconds=60) == 1
        outbox = session.query(QualityTaskOutbox).filter_by(job_id=job_id).one()
        assert outbox.status == "PENDING"
        assert outbox.attempts == 1
        assert outbox.dispatched_at is None
    finally:
        session.close()


def test_mysql_industrial_session_concurrent_turns_and_erasure(mysql_factory, monkeypatch, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from db.models import Conversation, ConversationMessage
    from server.graph.nodes.memory import memory_node
    from server.report.storage import ReportStorage
    from server.services.industrial_session import SessionConflict, load_turn
    from server.user_data_erasure import erase_live_session_data

    monkeypatch.setenv("GAOBAO_RUNTIME_PROFILE", "industrial")
    monkeypatch.setattr("db.database.get_session", mysql_factory)
    monkeypatch.setattr("server.graph.nodes.memory.get_session", mysql_factory)
    sid = "mysql-industrial-race"

    def persist(version, score):
        result = memory_node(
            {
                "session_id": sid,
                "input_text": f"{score}分",
                "reply": "fixture reply",
                "slots": {"province": "广东", "score_rank": f"{score}分"},
                "atomic_memory": True,
                "industrial_turn_version": version,
            }
        )
        return result["trace"][-1]["event"]

    _, _, fresh = load_turn(sid, None, fresh=True)
    assert persist(fresh, 600) == "persisted"
    _, _, version = load_turn(sid, None, fresh=False)
    barrier = Barrier(2, timeout=10)

    def competing(score):
        barrier.wait()
        return persist(version, score)

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(competing, [610, 620])) == ["persist_failed", "persisted"]
    with mysql_factory() as db:
        assert db.query(ConversationMessage).count() == 4
    _, _, pending = load_turn(sid, None, fresh=False)
    barrier = Barrier(2, timeout=10)

    def erase():
        barrier.wait()
        return erase_live_session_data(sid, ReportStorage(tmp_path / "reports"))

    def write():
        barrier.wait()
        return persist(pending, 630)

    with ThreadPoolExecutor(max_workers=2) as pool:
        deleting = pool.submit(erase)
        writing = pool.submit(write)
        assert deleting.result(timeout=20)["live_data_erased"]
        assert writing.result(timeout=20) in {"persisted", "persist_failed"}
    with pytest.raises(SessionConflict):
        load_turn(sid, None, fresh=False)
    with mysql_factory() as db:
        assert db.query(Conversation).filter_by(session_id=sid).count() == 0
        assert db.query(ConversationMessage).count() == 0
