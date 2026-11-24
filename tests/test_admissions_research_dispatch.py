"""Offline dispatcher acceptance: only approved policy matches, no replay."""

import pytest

from server.agent import admissions_research_taskrun as taskrun
from server.agent.admissions_research_dispatch import dispatch


def test_dispatch_respects_approval_model_image_and_attempt_budget(tmp_path):
    tmp_path.chmod(0o700)
    journal = taskrun.ResearchJournal(tmp_path / "research.db")
    spec = {
        "scope": {
            "school_ids": [101, 102],
            "major_id": 42,
            "province": "广东",
            "subject_type": "物理",
            "batch": "本科",
            "year_from": 2022,
            "year_to": 2024,
        },
        "dataset_ref": "approved-data",
        "model_id": "openai:approved-model",
        "image_id": "sha256:" + "a" * 64,
        "approval_ref": "review-001",
    }
    pending = journal.create("owner", "request-0001", spec)
    other = journal.create("owner", "request-0002", {**spec, "model_id": "openai:other-model"})
    first = journal.create("owner", "request-0003", spec)
    second = journal.create("owner", "request-0004", spec)
    for item in (other, first, second):
        journal.approve(item["run_id"], "admin", item["spec_sha256"])

    def execute(store, run_id, dataset, session_factory):
        _, lease = store.claim(run_id, dataset)
        return store.finish(run_id, lease, None, "fixture_failure")

    policy = (journal, spec["dataset_ref"], spec["model_id"], spec["image_id"], None)
    report = dispatch(*policy, max_tasks=1, execute=execute)
    assert report["results"] == [{"run_id": first["run_id"], "status": "FAILED"}]
    report = dispatch(*policy, max_tasks=3, execute=execute)
    assert report["results"] == [{"run_id": second["run_id"], "status": "FAILED"}]
    assert dispatch(*policy, max_tasks=3, execute=execute)["attempted"] == 0
    assert journal.get(pending["run_id"], "owner")["status"] == "PENDING_APPROVAL"
    assert journal.get(other["run_id"], "owner")["status"] == "APPROVED"
    with pytest.raises(ValueError):
        dispatch(*policy, max_tasks=4, execute=execute)


def test_expiry_persists_and_never_requeues(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    journal = taskrun.ResearchJournal(tmp_path / "research.db")
    spec = {
        "scope": {
            "school_ids": [101, 102],
            "major_id": 42,
            "province": "广东",
            "subject_type": "物理",
            "batch": "本科",
            "year_from": 2022,
            "year_to": 2024,
        },
        "dataset_ref": "approved-data",
        "model_id": "openai:approved-model",
        "image_id": "sha256:" + "a" * 64,
        "approval_ref": "review-001",
    }
    item = journal.create("owner", "request-0001", spec)
    journal.approve(item["run_id"], "admin", item["spec_sha256"])
    _, lease = journal.claim(item["run_id"], spec["dataset_ref"])
    now = taskrun.time.time()
    monkeypatch.setattr(taskrun.time, "time", lambda: now + 601)
    with pytest.raises(taskrun.ResearchConflict):
        journal.guard(item["run_id"], lease)
    with journal.tx() as db:
        state = db.execute("SELECT state FROM authorized_research_runs WHERE id=?", (item["run_id"],)).fetchone()
        assert state["state"] == "EXPIRED"
    assert journal.approved_candidates(spec["dataset_ref"], spec["model_id"], spec["image_id"]) == []
