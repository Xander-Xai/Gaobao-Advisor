"""Durable real-data task admission, approval, cancellation, and receipt gate."""

import pytest

from server.agent.admissions_research_taskrun import (
    ResearchConflict,
    ResearchJournal,
    ResearchNotFound,
    canonical,
    digest,
    execute_approved,
    render_aggregate_report,
)


@pytest.fixture
def journal(tmp_path):
    tmp_path.chmod(0o700)
    return ResearchJournal(tmp_path / "research.sqlite")


@pytest.fixture
def spec():
    return {
        "scope": {
            "school_ids": [101, 102],
            "major_id": 42,
            "province": "广东",
            "subject_type": "物理",
            "batch": "本科",
            "year_from": 2022,
            "year_to": 2024,
        },
        "model_id": "openai:approved-model",
        "dataset_ref": "approved-catalog-v1",
        "image_id": "sha256:" + "a" * 64,
        "approval_ref": "review-2026-001",
    }


def test_maker_checker_idempotency_and_dataset_binding(journal, spec):
    item = journal.create("operator", "request-0001", spec)
    run_id = item["run_id"]
    assert journal.create("operator", "request-0001", spec)["run_id"] == run_id
    assert item["spec"]["approval_ref"] is None
    with pytest.raises(ResearchNotFound):
        journal.get(run_id, "other")
    with pytest.raises(ResearchConflict):
        journal.approve(run_id, "operator", item["spec_sha256"])
    with pytest.raises(ResearchConflict):
        journal.approve(run_id, "admin", "b" * 64)
    journal.approve(run_id, "admin", item["spec_sha256"])
    with pytest.raises(ResearchConflict):
        journal.claim(run_id, "wrong-dataset")
    _, lease = journal.claim(run_id, spec["dataset_ref"])
    with pytest.raises(ResearchConflict):
        journal.claim(run_id, spec["dataset_ref"])
    journal.cancel(run_id, "operator")
    with pytest.raises(ResearchConflict):
        journal.guard(run_id, lease)
    with pytest.raises(ResearchConflict):
        journal.finish(run_id, lease, None, "cancelled")


@pytest.mark.parametrize("mismatch", [None, "input_sha256", "image_id", "cleanup_verified", "oracle"])
def test_worker_only_publishes_bound_verified_aggregate(journal, spec, mismatch):
    item = journal.create("operator", "request-0002", spec)
    journal.approve(item["run_id"], "admin", item["spec_sha256"])

    def runner(scope, session_factory, model_id, **kwargs):
        assert model_id == spec["model_id"]
        assert kwargs["analysis_image_id"] == spec["image_id"]
        assert kwargs["authorized"] and kwargs["model_authorized"]
        kwargs["lease_guard"]()
        from server.agent.admissions_analysis_sandbox import _expected
        from server.agent.admissions_deepagents import _coverage

        rows = [
            {
                "id": 17,
                "school_id": 101,
                "major_id": 42,
                "year": 2022,
                "min_score": 600,
                "max_score": 620,
                "min_rank": 12000,
            }
        ]
        evidence = {"admission-history": rows, "enrollment-plans": []}
        analysis = _expected(rows, ("school_id", "year"))
        report = {
            "status": "VERIFIED_PARTIAL_RESEARCH",
            "scope": spec["scope"],
            "approval_ref_sha256": digest(spec["approval_ref"]),
            "admission_recommendation_verified": False,
            "evidence": evidence,
            "evidence_sha256": digest(evidence),
            "sandbox_analysis": {
                "status": "VERIFIED_DESCRIPTIVE_ANALYSIS",
                "result": analysis,
                "result_sha256": digest(analysis),
                "cleanup_verified": True,
                "image_id": spec["image_id"],
                "input_sha256": digest({"rows": rows, "group_by": ["school_id", "year"]}),
            },
            "coverage": {kind: _coverage(records, scope, kind) for kind, records in evidence.items()},
            "receipt_ids": {},
            "provider_usage": None,
        }
        if mismatch == "oracle":
            analysis["groups"][0]["best_min_rank"] = 1
            report["sandbox_analysis"]["result_sha256"] = digest(analysis)
        elif mismatch:
            report["sandbox_analysis"][mismatch] = False
        return report

    result = execute_approved(journal, item["run_id"], spec["dataset_ref"], None, runner=runner)
    if mismatch:
        assert result["status"] == "FAILED"
        assert result["result"] is None
        return
    assert result["status"] == "NEEDS_REVIEW"
    assert "evidence" not in result["result"]
    assert spec["approval_ref"] not in canonical(result)
    rendered = render_aggregate_report(result)
    assert "NEEDS_REVIEW" in rendered
    assert "12000" in rendered
    assert spec["approval_ref"] not in rendered
    result["result"]["analysis"]["groups"][0]["best_min_rank"] = 1
    with pytest.raises(ResearchConflict, match="evidence mismatch"):
        render_aggregate_report(result)
    with pytest.raises(ResearchConflict):
        journal.claim(item["run_id"], spec["dataset_ref"])


def test_blocked_result_is_not_published(journal, spec):
    item = journal.create("operator", "request-0003", spec)
    journal.approve(item["run_id"], "admin", item["spec_sha256"])
    result = execute_approved(
        journal, item["run_id"], spec["dataset_ref"], None, runner=lambda *a, **k: {"status": "BLOCKED"}
    )
    assert result["status"] == "FAILED"
    assert result["result"] is None
    with pytest.raises(ResearchConflict, match="No verified"):
        render_aggregate_report(result)


def test_research_report_http_requires_authorized_owner_and_terminal_result(admin_client, monkeypatch, tmp_path, spec):
    from tests.conftest import auth_header, login

    tmp_path.chmod(0o700)
    monkeypatch.setenv("ADMISSIONS_RESEARCH_ENABLED", "true")
    monkeypatch.setenv("ADMISSIONS_RESEARCH_DATASET_REF", spec["dataset_ref"])
    monkeypatch.setenv("ADMISSIONS_RESEARCH_JOURNAL", str(tmp_path / "research-http.sqlite"))
    base = "/api/v1/admin/admissions-research/runs"
    operator = auth_header(login(admin_client, "operator"))
    administrator = auth_header(login(admin_client, "admin"))
    viewer = auth_header(login(admin_client, "viewer"))
    payload = {key: value for key, value in spec.items() if key != "dataset_ref"}
    payload["idempotency_key"] = "research-http-001"
    created = admin_client.post(base, json=payload, headers=operator)
    assert created.status_code == 201, created.text
    run_id = created.json()["run_id"]
    report_url = f"{base}/{run_id}/report"
    assert admin_client.get(report_url).status_code == 401
    assert admin_client.get(report_url, headers=viewer).status_code == 403
    assert admin_client.get(report_url, headers=operator).status_code == 409
    assert admin_client.get(report_url, headers=administrator).status_code == 409
    assert admin_client.post(f"{base}/{run_id}/execute", headers=operator).status_code == 404


def test_task_journals_cannot_share_storage_even_without_environment_configuration(tmp_path, monkeypatch):
    from server.agent.taskrun_store import SyntheticTaskRunStore

    monkeypatch.delenv("ADMISSIONS_RESEARCH_JOURNAL", raising=False)
    monkeypatch.delenv("SYNTHETIC_TASKRUN_JOURNAL", raising=False)
    tmp_path.chmod(0o700)
    private = tmp_path / "private.sqlite"
    ResearchJournal(private)
    with pytest.raises(ValueError, match="private research schema"):
        SyntheticTaskRunStore(private)

    synthetic = tmp_path / "synthetic.sqlite"
    SyntheticTaskRunStore(synthetic)
    synthetic.chmod(0o600)
    with pytest.raises(ValueError, match="synthetic task schema"):
        ResearchJournal(synthetic)


def test_synthetic_journal_rejects_reserved_private_path_before_initialization(tmp_path, monkeypatch):
    from server.agent.taskrun_store import SyntheticTaskRunStore

    private = tmp_path / "private.sqlite"
    monkeypatch.setenv("ADMISSIONS_RESEARCH_JOURNAL", str(private))
    with pytest.raises(ValueError, match="separate from private research journal"):
        SyntheticTaskRunStore(private)
    assert not private.exists()

def test_late_finish_persists_expiry_even_when_publication_is_rejected(journal, spec):
    item = journal.create("operator", "request-expired-001", spec)
    run_id = item["run_id"]
    journal.approve(run_id, "admin", item["spec_sha256"])
    _, lease = journal.claim(run_id, spec["dataset_ref"])
    with journal.tx() as db:
        db.execute("UPDATE authorized_research_runs SET deadline=0 WHERE id=?", (run_id,))

    with pytest.raises(ResearchConflict, match="Lease lost"):
        journal.finish(run_id, lease, {"status": "VERIFIED_RESEARCH"}, None)

    # Inspect raw storage, not get(): get() would itself lazily repair the expiry.
    with journal.tx() as db:
        row = db.execute(
            "SELECT state, lease, deadline, result, failure FROM authorized_research_runs WHERE id=?", (run_id,)
        ).fetchone()
        assert tuple(row) == ("EXPIRED", None, None, None, "lease_expired_no_replay")
    with pytest.raises(ResearchConflict):
        journal.claim(run_id, spec["dataset_ref"])



def test_worker_slot_remains_held_after_cancel_until_runner_returns(journal, spec):
    first = journal.create("operator", "request-concurrent-001", spec)
    second = journal.create("operator", "request-concurrent-002", spec)
    for item in (first, second):
        journal.approve(item["run_id"], "admin", item["spec_sha256"])
    competing_worker = ResearchJournal(journal.path)

    def must_not_run(*args, **kwargs):
        pytest.fail("competing worker must not spend model budget")

    def first_runner(*args, **kwargs):
        journal.cancel(first["run_id"], "operator")
        with pytest.raises(ResearchConflict, match="worker already active"):
            execute_approved(
                competing_worker, second["run_id"], spec["dataset_ref"], None, runner=must_not_run
            )
        assert competing_worker.get(second["run_id"], "operator")["status"] == "APPROVED"
        kwargs["lease_guard"]()

    with pytest.raises(ResearchConflict, match="Lease lost"):
        execute_approved(journal, first["run_id"], spec["dataset_ref"], None, runner=first_runner)

    # Only after the active runner has returned may another worker claim.
    result = execute_approved(
        competing_worker,
        second["run_id"],
        spec["dataset_ref"],
        None,
        runner=lambda *args, **kwargs: {"status": "BLOCKED"},
    )
    assert result["status"] == "FAILED"


def test_worker_lock_rejects_symlink(journal, tmp_path):
    target = tmp_path / "other.lock"
    target.write_text("do not modify")
    lock = journal.path.with_name(journal.path.name + ".worker.lock")
    lock.symlink_to(target)
    with pytest.raises(OSError):
        with journal.execution_slot():
            pytest.fail("symlink must not be accepted")
    assert target.read_text() == "do not modify"
