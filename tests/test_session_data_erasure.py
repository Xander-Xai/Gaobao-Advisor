"""Session data deletion protects ownership, SQL integrity and report paths."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.database import Base
from db.models import Conversation, ConversationMessage, Feedback, Highlight, QualityScore
from server.auth import create_session_token
from server.report.models import Report
from server.report.storage import ReportStorage
from server.routes import profile as profile_routes
from server.user_data_erasure import erase_live_session_data


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///" + str(tmp_path / "session-privacy.sqlite"))
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    monkeypatch.setattr("db.database.get_session", session_factory)
    storage = ReportStorage(tmp_path / "reports")
    yield session_factory, storage
    engine.dispose()


def seed(session_factory, storage, session_id="synthetic-session-privacy"):
    with session_factory() as db:
        conversation = Conversation(session_id=session_id, slots_json='{"score":"550"}')
        db.add(conversation)
        db.flush()
        db.add(ConversationMessage(conversation_id=conversation.id, role="user", content="private message"))
        db.add(Feedback(session_id=session_id, message_index=1, rating="helpful"))
        db.add(Highlight(session_id=session_id, content="private highlight"))
        db.add(
            QualityScore(
                conversation_id=session_id,
                message_id="synthetic-m1",
                aggregate_score=0.5,
                judge_model="synthetic-judge",
            )
        )
        db.commit()
    report_dir = storage._base / session_id
    report_dir.mkdir(parents=True)
    (report_dir / "synthetic-report.json").write_text('{"private":"content"}')
    return report_dir


def test_erasure_covers_sql_and_report_file(isolated):
    session_factory, storage = isolated
    directory = seed(session_factory, storage)
    result = erase_live_session_data("synthetic-session-privacy", storage)
    assert result["live_data_erased"] is True
    assert result["deleted_row_counts"] == {
        "quality_scores": 1,
        "feedback": 1,
        "highlights": 1,
        "messages": 1,
        "conversations": 1,
        "reports": 1,
    }
    assert not directory.exists()
    assert result["backups_and_external_processors_erased"] is False
    with session_factory() as db:
        assert db.query(Conversation).count() == 0
        assert db.query(ConversationMessage).count() == 0
        assert db.query(Feedback).count() == 0
        assert db.query(QualityScore).count() == 0
        assert db.query(Highlight).count() == 0
    assert erase_live_session_data("synthetic-session-privacy", storage)["deleted_row_counts"]["reports"] == 0


def test_reports_symlinks_fail_before_database_mutation(isolated, tmp_path):
    session_factory, storage = isolated
    report_dir = seed(session_factory, storage)
    (report_dir / "escape.json").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(ValueError, match="unrecognized"):
        erase_live_session_data("synthetic-session-privacy", storage)
    with session_factory() as db:
        assert db.query(Conversation).count() == 1


def test_api_rejects_unauthenticated_erasure(isolated, monkeypatch):
    session_factory, storage = isolated
    seed(session_factory, storage)
    monkeypatch.setattr(profile_routes, "_report_storage", storage)
    app = FastAPI()
    app.include_router(profile_routes.router)
    with TestClient(app) as client:
        url = "/api/v1/profile/synthetic-session-privacy/data"
        assert client.delete(url).status_code == 401
        assert client.delete(url, headers={"Authorization": "Bearer invalid"}).status_code == 401
        with session_factory() as db:
            assert db.query(Conversation).count() == 1
        token = create_session_token("synthetic-session-privacy")
        response = client.delete(url, headers={"Authorization": "Bearer " + token})
        assert response.status_code == 200, response.text
        assert response.json()["deleted_row_counts"]["messages"] == 1
        assert client.delete(url, headers={"Authorization": "Bearer " + token}).status_code == 200


def test_sql_rollback_restores_report_visibility(isolated, monkeypatch):
    session_factory, storage = isolated
    directory = seed(session_factory, storage)

    class BrokenSession:
        def query(self, _table):
            raise RuntimeError("synthetic database failure")

        def rollback(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr("db.database.get_session", BrokenSession)
    with pytest.raises(RuntimeError, match="synthetic"):
        erase_live_session_data("synthetic-session-privacy", storage)
    assert directory.is_dir()
    assert (directory / "synthetic-report.json").is_file()
    with session_factory() as db:
        assert db.query(Conversation).count() == 1


def test_report_storage_rejects_symlink_escape(isolated, tmp_path):
    """Synthetic report IDs must not read, write or erase outside storage root."""
    _, storage = isolated
    storage._base.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    session_link = storage._base / "synthetic-linked"
    try:
        session_link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation is unavailable")

    report = Report(id="synthetic-report", session_id="synthetic-linked")
    with pytest.raises(ValueError, match="Unsafe"):
        storage.save(report)
    with pytest.raises(ValueError, match="Unsafe"):
        storage.load_by_session(report.id, report.session_id)
    with pytest.raises(ValueError, match="Unsafe"):
        storage.list_by_session(report.session_id)
    with pytest.raises(ValueError, match="Unsafe"):
        storage.stage_session_deletion(report.session_id)
    assert storage.load(report.id) is None

    local = storage._base / "synthetic-local"
    local.mkdir()
    external_file = outside / "synthetic-report.json"
    external_file.write_text("{}", encoding="utf-8")
    (local / "synthetic-report.json").symlink_to(external_file)
    with pytest.raises(ValueError, match="Unsafe"):
        storage.save(Report(id="synthetic-report", session_id="synthetic-local"))
    with pytest.raises(ValueError, match="Unsafe"):
        storage.load_by_session("synthetic-report", "synthetic-local")
    with pytest.raises(ValueError, match="Unsafe"):
        storage.list_by_session("synthetic-local")
    with pytest.raises(ValueError, match="Unsafe"):
        storage.load("synthetic-report")


def test_report_component_requires_full_match(isolated):
    _, storage = isolated
    with pytest.raises(ValueError, match="unsafe report_id"):
        storage.save(Report(id="synthetic-report\\n", session_id="synthetic-session"))
