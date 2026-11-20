"""Prove CLI journal isolation without installing the optional Deep Agents SDK."""

from __future__ import annotations

import pytest

from scripts.seed_demo_data import DEMO_MAJORS, DEMO_SCHOOLS, DEMO_YEAR
from server.agent.taskrun_store import SyntheticTaskRunStore
from server.agent.taskrun_worker import execute_approved_run


def test_deepagents_checkpoint_cannot_overlap_argv_journal(tmp_path, monkeypatch):
    store = SyntheticTaskRunStore(tmp_path / "journal.sqlite")
    profile = {
        "school_a": DEMO_SCHOOLS[0][0],
        "school_b": DEMO_SCHOOLS[6][0],
        "major": DEMO_MAJORS[0][0],
        "province": "示例省·甲",
        "score": 550,
        "subject_type": "物理类",
        "year": DEMO_YEAR,
    }
    created = store.create(
        {"data_profile": "SYNTHETIC_DEMO", "profile": profile},
        "deepagents",
        "synthetic-operator",
        "checkpoint-journal-guard-2026",
    )
    store.approve(created["run_id"], "separate-admin")
    monkeypatch.setenv("ENABLE_SYNTHETIC_AGENT_EXPERIMENT", "true")
    monkeypatch.setenv("ENABLE_OUTBOUND_FETCH", "false")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-key-not-used")
    monkeypatch.delenv("SYNTHETIC_TASKRUN_JOURNAL", raising=False)
    monkeypatch.setenv("SYNTHETIC_DEEPAGENTS_CHECKPOINT_DB", str(store.path))
    with pytest.raises(ValueError, match="must not share"):
        execute_approved_run(
            store,
            created["run_id"],
            permit_external_model=True,
            model="openai:synthetic-no-call",
            agent_runner=lambda *_args, **_kwargs: pytest.fail("Paid model must never be invoked"),
        )
    assert store.get(created["run_id"])["status"] == "APPROVED"
    assert store.get(created["run_id"])["attempts"] == 0
