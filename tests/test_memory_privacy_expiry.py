"""Privacy and bounded retention tests for synthetic memory governance."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from server.agent.governed_evidence_memory import SyntheticEvidenceMemoryStore
from server.agent.memory_manager import MemoryManager
from server.agent.taskrun_store import SyntheticTaskRunStore
from server.graph.nodes import memory as memory_module


@pytest.mark.asyncio
async def test_summary_exception_text_never_reaches_logs(caplog):
    manager = MemoryManager(llm_func=AsyncMock(side_effect=RuntimeError("secret=user-private-key")))
    with caplog.at_level(logging.WARNING):
        result = await manager._summarize_history([{"role": "user", "content": "fictional history"}])
    assert result == ""
    assert "secret=user-private-key" not in caplog.text
    assert "RuntimeError" in caplog.text


def test_memory_persistence_failure_trace_and_logs_are_redacted(monkeypatch, caplog):
    db = MagicMock()
    monkeypatch.setattr(memory_module, "get_session", lambda: db)

    def fail(_db, _session_id):
        raise RuntimeError("session=password-real-secret")

    monkeypatch.setattr(memory_module, "get_or_create_conversation", fail)
    with caplog.at_level(logging.WARNING):
        result = memory_module.memory_node(
            {"session_id": "synthetic-session", "input_text": "demo", "reply": "demo", "slots": {}}
        )
    assert result["trace"][-1] == {
        "node": "memory_update",
        "event": "persist_failed",
        "error": "persistence_error",
    }
    assert "session=password-real-secret" not in repr(result) + caplog.text
    db.close.assert_called_once()


def test_expired_pointer_cleanup_is_bounded_and_preserves_task_history(tmp_path):
    taskruns = SyntheticTaskRunStore(tmp_path / "synthetic-taskruns.sqlite")
    store = SyntheticEvidenceMemoryStore(taskruns)
    with taskruns._open() as conn:
        for index in range(3):
            conn.execute(
                "INSERT INTO synthetic_evidence_memory "
                "(owner,memory_key,source_run_id,receipt_sha256,published_by,created_at,expires_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    "synthetic-owner", str(index), "syn_" + f"{index:032x}",
                    "a" * 64, "synthetic-admin", 0, 0 if index < 2 else 9999999999,
                ),
            )
    assert store.prune_expired(limit=1)["expired_memory_pointers_deleted"] == 1
    assert store.prune_expired(limit=1)["expired_memory_pointers_deleted"] == 1
    assert store.prune_expired(limit=1)["expired_memory_pointers_deleted"] == 0
    with taskruns._open() as conn:
        assert conn.execute("SELECT COUNT(*) FROM synthetic_evidence_memory").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM synthetic_task_runs").fetchone()[0] == 0
    with pytest.raises(ValueError, match="limit"):
        store.prune_expired(limit=0)
