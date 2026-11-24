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
            "school_ids": [101, 102], "major_id": 42, "province": "广东",
            "subject_type": "物理", "batch": "本科", "year_from": 2022, "year_to": 2024,
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


def test_worker_only_publishes_bound_verified_aggregate(journal, spec):
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
        return {
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

    result = execute_approved(journal, item["run_id"], spec["dataset_ref"], None, runner=runner)
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
