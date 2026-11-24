"""Gold labels are test fixtures, not real-world admissions evidence."""

from copy import deepcopy

import pytest

from benchmarks.authorized_research_eval import SCHEMA, digest, evaluate


def cohort(index):
    return {
        "school_ids": [101 + index * 2, 102 + index * 2],
        "major_id": 42,
        "province": "广东",
        "subject_type": "物理",
        "batch": "本科",
        "year_from": 2022,
        "year_to": 2024,
    }


def fixture():
    cases = []
    receipts = {}
    for i in range(4):
        scope = cohort(i)
        key = f"case-{i}"
        partial = i % 2 == 1
        status = "VERIFIED_PARTIAL_RESEARCH" if partial else "VERIFIED_RESEARCH"
        coverage = {
            "admission-history": {"missing": [], "ambiguous": [], "incomplete": []},
            "enrollment-plans": {
                "missing": [[scope["school_ids"][0], 2022]] if partial else [],
                "ambiguous": [],
                "incomplete": [],
            },
        }
        evidence = {"admission-history": [{"id": i}], "enrollment-plans": []}
        analysis = {"groups": [{"records": i + 1}]}
        cases.append(
            {
                "case_id": key,
                "split": "development" if i < 2 else "holdout",
                "scope": scope,
                "expected_status": status,
                "expected_coverage": coverage,
                "expected_evidence_sha256": digest(evidence),
                "expected_analysis_sha256": digest(analysis),
                "source_ref": f"fixture-review-{i}",
            }
        )
        receipts[key] = {
            "schema": "admissions-research/v1",
            "task_id": "research_" + format(i, "032x"),
            "scope": scope,
            "engine": "deepagents",
            "status": status,
            "admission_recommendation_verified": False,
            "coverage": coverage,
            "evidence": evidence,
            "evidence_sha256": digest(evidence),
            "receipt_ids": {
                kind: digest({"task": "research_" + format(i, "032x"), "scope": scope, "kind": kind, "rows": rows})
                for kind, rows in evidence.items()
            },
            "sandbox_analysis": {
                "status": "VERIFIED_DESCRIPTIVE_ANALYSIS",
                "result": analysis,
                "result_sha256": digest(analysis),
                "cleanup_verified": True,
            },
            "provider_usage": None,
        }
    return {"schema": SCHEMA, "dataset_ref": "fixture", "review_ref": "fixture-review", "cases": cases}, receipts


def test_gold_suite_requires_complete_population_and_preserves_missing_usage():
    manifest, receipts = fixture()
    result = evaluate(manifest, receipts)
    assert result["status"] == "REVIEW_REQUIRED"
    assert result["splits"]["holdout"]["passed"] == 2
    assert result["splits"]["holdout"]["provider_usage_declared"] is None
    assert result["production_release_authorized"] is False
    assert "evidence" not in result["cases"][0]
    with pytest.raises(ValueError):
        evaluate(manifest, {"case-0": receipts["case-0"]})


def test_independently_frozen_gold_rejects_self_consistent_wrong_analysis():
    manifest, receipts = fixture()
    altered = deepcopy(receipts)
    analysis = altered["case-2"]["sandbox_analysis"]
    analysis["result"] = {"groups": [{"records": 999}]}
    analysis["result_sha256"] = digest(analysis["result"])
    result = evaluate(manifest, altered)
    assert result["status"] == "FAILED"
    assert result["splits"]["holdout"]["passed"] == 1
    assert "gold_sandbox_mismatch" in result["cases"][2]["reasons"]


def test_split_leakage_and_reused_run_rejected():
    manifest, receipts = fixture()
    manifest["cases"][2]["scope"] = {**manifest["cases"][0]["scope"], "school_ids": [102, 101]}
    with pytest.raises(ValueError):
        evaluate(manifest, receipts)
    manifest, receipts = fixture()
    receipts["case-2"]["task_id"] = receipts["case-0"]["task_id"]
    with pytest.raises(ValueError):
        evaluate(manifest, receipts)


@pytest.mark.parametrize("error", ["research_failed", "unverified_finish", "source_changed"])
def test_full_receipt_does_not_credit_unclassified_block(error):
    manifest, receipts = fixture()
    case = manifest["cases"][1]
    case.update(
        expected_status="BLOCKED",
        expected_coverage=None,
        expected_analysis_sha256=None,
        expected_evidence_sha256=None,
    )
    receipt = receipts["case-1"]
    receipt.update(status="BLOCKED", error=error)
    for field in ("evidence", "sandbox_analysis", "receipt_ids"):
        receipt.pop(field, None)
    result = evaluate(manifest, receipts)
    assert result["status"] == "FAILED"
    assert result["splits"]["development"]["passed"] == 1
    assert "full_receipt_block_reason_not_independently_classified" in result["cases"][1]["reasons"]


@pytest.mark.parametrize("replacement", [None, {}, {"admission-history": "0" * 64, "enrollment-plans": "1" * 64}])
def test_full_receipt_requires_task_bound_delegation(replacement):
    manifest, receipts = fixture()
    receipts["case-0"]["receipt_ids"] = replacement
    result = evaluate(manifest, receipts)
    assert result["status"] == "FAILED"
    assert "delegation_receipt_binding_mismatch" in result["cases"][0]["reasons"]


@pytest.mark.parametrize("coordinate", [[9999, 2022], [103, 2020], [103, 2022]])
def test_gold_rejects_out_of_scope_or_duplicate_coverage(coordinate):
    manifest, receipts = fixture()
    manifest["cases"][1]["expected_coverage"]["enrollment-plans"]["ambiguous"].append(coordinate)
    with pytest.raises(ValueError, match="Coverage coordinate outside scope or repeated"):
        evaluate(manifest, receipts)


def managed_fixture(tmp_path):
    from server.agent.admissions_research_taskrun import ResearchJournal

    manifest, receipts = fixture()
    tmp_path.chmod(0o700)
    journal = ResearchJournal(tmp_path / "private-research.sqlite")
    image = "sha256:" + "a" * 64
    model = "openai:fixture-model"
    run_map = {}
    for case in manifest["cases"]:
        key = case["case_id"]
        spec = {
            "scope": case["scope"],
            "dataset_ref": manifest["dataset_ref"],
            "model_id": model,
            "image_id": image,
            "approval_ref": "fixture-only",
        }
        item = journal.create("operator", "request-" + key, spec)
        run_id = item["run_id"]
        journal.approve(run_id, "admin", item["spec_sha256"])
        _, lease = journal.claim(run_id, manifest["dataset_ref"])
        receipt = receipts[key]
        analysis = receipt["sandbox_analysis"]["result"]
        journal.finish(
            run_id,
            lease,
            {
                "status": receipt["status"],
                "coverage": receipt["coverage"],
                "evidence_sha256": receipt["evidence_sha256"],
                "analysis": analysis,
                "analysis_sha256": digest(analysis),
                "receipt_ids": {"admission-history": "b" * 64, "enrollment-plans": "c" * 64},
                "provider_usage": None,
                "admission_recommendation_verified": False,
                "spec_sha256": item["spec_sha256"],
            },
            None,
        )
        run_map[key] = run_id
    return manifest, run_map, journal, model, image


def test_managed_eval_uses_existing_journal_without_raw_export(tmp_path):
    from benchmarks.authorized_research_eval import evaluate_managed

    manifest, run_map, journal, model, image = managed_fixture(tmp_path)
    original = journal.path.read_bytes()
    result = evaluate_managed(manifest, run_map, journal.path, model_id=model, image_id=image)
    assert result["status"] == "REVIEW_REQUIRED"
    assert result["splits"]["holdout"]["passed"] == 2
    assert result["evidence_lane"] == "MANAGED_JOURNAL_AGGREGATE"
    assert result["raw_evidence_independently_recomputed"] is False
    assert result["provider_transport_verified"] is False
    assert result["provider_billing_verified"] is False
    assert result["production_release_authorized"] is False
    assert journal.path.read_bytes() == original
    assert "fixture-only" not in str(result)
    assert "research_" not in str(result["cases"])


def test_managed_eval_rejects_policy_mismatch_and_reused_run(tmp_path):
    from benchmarks.authorized_research_eval import evaluate_managed

    manifest, run_map, journal, model, image = managed_fixture(tmp_path)
    result = evaluate_managed(manifest, run_map, journal.path, model_id="openai:other-model", image_id=image)
    assert result["status"] == "FAILED"
    assert all("dataset_or_model_mismatch" in case["reasons"] for case in result["cases"])
    run_map["case-2"] = run_map["case-0"]
    with pytest.raises(ValueError, match="distinct managed run"):
        evaluate_managed(manifest, run_map, journal.path, model_id=model, image_id=image)


def test_managed_eval_does_not_credit_unclassified_failure_as_safe_refusal(tmp_path):
    from benchmarks.authorized_research_eval import evaluate_managed

    manifest, run_map, journal, model, image = managed_fixture(tmp_path)
    case = manifest["cases"][1]
    case.update(
        expected_status="BLOCKED",
        expected_coverage=None,
        expected_analysis_sha256=None,
        expected_evidence_sha256=None,
    )
    with journal.tx() as db:
        db.execute(
            "UPDATE authorized_research_runs SET state='FAILED',result=NULL,failure='research_execution_failed' WHERE id=?",
            (run_map[case["case_id"]],),
        )
    result = evaluate_managed(manifest, run_map, journal.path, model_id=model, image_id=image)
    assert result["status"] == "FAILED"
    assert result["splits"]["development"]["passed"] == 1
    assert "managed_block_reason_not_independently_classified" in result["cases"][1]["reasons"]


@pytest.mark.parametrize("failure,passed", [("no_admission_history", True), ("research_execution_failed", False)])
def test_managed_eval_credits_only_independently_labelled_empty_history(tmp_path, failure, passed):
    from benchmarks.authorized_research_eval import evaluate_managed

    manifest, run_map, journal, model, image = managed_fixture(tmp_path)
    case = manifest["cases"][1]
    case.update(
        expected_status="BLOCKED",
        expected_block_reason="no_admission_history",
        expected_coverage=None,
        expected_analysis_sha256=None,
        expected_evidence_sha256=None,
    )
    with journal.tx() as db:
        db.execute(
            "UPDATE authorized_research_runs SET state='FAILED',result=NULL,failure=? WHERE id=?",
            (failure, run_map[case["case_id"]]),
        )
    result = evaluate_managed(manifest, run_map, journal.path, model_id=model, image_id=image)
    assert result["cases"][1]["passed"] is passed
    assert result["status"] == ("REVIEW_REQUIRED" if passed else "FAILED")


def test_managed_eval_detects_wrong_independent_gold(tmp_path):
    from benchmarks.authorized_research_eval import evaluate_managed

    manifest, run_map, journal, model, image = managed_fixture(tmp_path)
    manifest["cases"][2]["expected_evidence_sha256"] = "d" * 64
    result = evaluate_managed(manifest, run_map, journal.path, model_id=model, image_id=image)
    assert result["status"] == "FAILED"
    assert "gold_evidence_mismatch" in result["cases"][2]["reasons"]


def test_managed_cli_preserves_journal_and_fails_closed_on_missing_schema(tmp_path):
    import json
    import stat

    from benchmarks.authorized_research_eval import main

    manifest, run_map, journal, model, image = managed_fixture(tmp_path)
    gold = tmp_path / "gold.json"
    runs = tmp_path / "runs.json"
    output = tmp_path / "eval.json"
    gold.write_text(json.dumps(manifest), encoding="utf-8")
    runs.write_text(json.dumps(run_map), encoding="utf-8")
    args = [
        "--gold",
        str(gold),
        "--managed-runs",
        str(runs),
        "--expected-model",
        model,
        "--expected-image",
        image,
        "--authorize-private-eval",
    ]
    assert main(args + ["--journal", str(journal.path), "--out", str(output)]) == 0
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "REVIEW_REQUIRED"

    invalid = tmp_path / "no-schema.sqlite"
    invalid.touch(mode=0o600)
    failed = tmp_path / "failed.json"
    with pytest.raises(SystemExit) as exc:
        main(args + ["--journal", str(invalid), "--out", str(failed)])
    assert exc.value.code == 2
    assert not failed.exists()
    assert invalid.stat().st_size == 0
