"""Synthetic SQL regressions for exact cohort, year and evidence rendering."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from db.database import Base
from db.models import AdmissionScore, School, YiFenYiDuan
from server.services.industrial_admissions import build_history_reply, exact_score, query_history


@pytest.fixture
def history_db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all(
            [
                School(id=1, name="fixture correct school", province="广东"),
                School(id=2, name="fixture incorrect school", province="广东"),
            ]
        )
        db.add_all(
            [
                YiFenYiDuan(
                    id=1,
                    province="广东",
                    year=2025,
                    subject_type="物理类",
                    score=600,
                    cumulative_count=1000,
                ),
                YiFenYiDuan(
                    id=2,
                    province="广东",
                    year=2025,
                    subject_type="历史类",
                    score=600,
                    cumulative_count=2000,
                ),
                YiFenYiDuan(
                    id=3,
                    province="广东",
                    year=2024,
                    subject_type="物理类",
                    score=600,
                    cumulative_count=3000,
                ),
            ]
        )
        db.add_all(
            [
                AdmissionScore(
                    id=1,
                    school_id=1,
                    province="广东",
                    year=2025,
                    subject_type="物理类",
                    min_score=599,
                    min_rank=1020,
                ),
                AdmissionScore(
                    id=2,
                    school_id=2,
                    province="广东",
                    year=2025,
                    subject_type="历史类",
                    min_score=600,
                    min_rank=1000,
                ),
                AdmissionScore(
                    id=3,
                    school_id=2,
                    province="广东",
                    year=2024,
                    subject_type="物理类",
                    min_score=600,
                    min_rank=1000,
                ),
            ]
        )
        db.commit()
        yield db
    engine.dispose()


def test_history_never_crosses_year_or_cohort(history_db):
    evidence = query_history(history_db, province="广东", score=600, subject="物理", year=2025)
    assert evidence["rank"] == 1000
    assert evidence["status"] == "HISTORICAL_ROWS"
    assert [row["id"] for row in evidence["rows"]] == [1]
    assert (
        query_history(history_db, province="广东", score=601, subject="物理", year=2025)["status"]
        == "NO_UNAMBIGUOUS_RANK"
    )
    assert (
        query_history(history_db, province="广东", score=600, subject="综合", year=2025)["status"]
        == "NO_UNAMBIGUOUS_RANK"
    )


def test_ambiguous_alias_rows_refuse(history_db):
    history_db.add(YiFenYiDuan(province="广东", year=2025, subject_type="物理", score=600, cumulative_count=1100))
    history_db.commit()
    assert (
        query_history(history_db, province="广东", score=600, subject="物理", year=2025)["status"]
        == "NO_UNAMBIGUOUS_RANK"
    )


def test_industrial_text_and_card_share_exact_receipts(history_db, monkeypatch):
    monkeypatch.setenv("GAOBAO_ADMISSIONS_YEAR", "2025")
    monkeypatch.setenv("GAOBAO_DATA_APPROVAL_REF", "fixture-approval")
    evidence = query_history(history_db, province="广东", score=600, subject="物理", year=2025)
    reply, card = build_history_reply({"scene": "gaokao", "data_query_results": {"industrial_history": evidence}})
    assert "fixture correct school" in reply
    assert "fixture incorrect school" not in reply
    assert "admission_scores:1" in card["sources"]
    assert not card["suggestions"]
    assert card["confidence"] == 0
    refused, card = build_history_reply({"scene": "gaokao", "knowledge_query": True})
    assert "尚未获准发布" in refused
    assert not card["facts"]


@pytest.mark.parametrize(
    ("slots", "expected"),
    [
        ({"score": 600}, 600),
        ({"score_rank": "600分 / 位次3000"}, 600),
        ({"score": "600", "score_rank": "600分"}, 600),
        ({"score": 600, "score_rank": "601分"}, None),
        ({"score_rank": "位次600"}, None),
        ({"score_rank": "600"}, None),
        ({"score_rank": "6000分"}, None),
        ({"score_rank": "一本线上100分"}, None),
        ({"score": 600.5}, None),
        ({"score": True}, None),
    ],
)
def test_industrial_score_requires_exact_unambiguous_value(slots, expected):
    assert exact_score(slots) == expected


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("广东600分物理类", 600),
        ("我改成620分", 620),
        ("广东1600分物理类", None),
        ("广东600.5分物理类", None),
        ("广东-600分物理类", None),
        ("广东600分左右", None),
        ("一本线上100分", None),
        ("不到600分", None),
        ("600分还是610分", None),
        ("考了600", None),
    ],
)
def test_industrial_current_turn_score_precedes_legacy_extraction(monkeypatch, message, expected):
    from server.graph.nodes.extract import slot_extract_node

    monkeypatch.setenv("GAOBAO_RUNTIME_PROFILE", "industrial")
    state = {"input_text": message, "slots": {"province": "广东", "subject": "物理类", "score_rank": "590分"}}
    result = slot_extract_node(state)
    assert result["industrial_score_invalid"] is (expected is None)
    assert exact_score(result["slots"]) == expected


def test_industrial_invalid_score_never_queries_sql(monkeypatch):
    from unittest.mock import Mock

    from server.graph.nodes.data_nodes import data_query_node
    from server.graph.nodes.extract import slot_extract_node

    monkeypatch.setenv("GAOBAO_RUNTIME_PROFILE", "industrial")
    monkeypatch.setenv("GAOBAO_ADMISSIONS_YEAR", "2025")
    session = Mock(side_effect=AssertionError("invalid input reached SQL"))
    monkeypatch.setattr("db.database.get_session", session)
    state = {
        "input_text": "广东1600分物理类",
        "scene": "gaokao",
        "slots": {"province": "广东", "subject": "物理类", "score_rank": "600分"},
    }
    state.update(slot_extract_node(state))
    state.update(data_query_node(state))
    reply, card = build_history_reply(state)
    session.assert_not_called()
    assert "明确的高考整数总分" in reply
    assert not card["facts"]


def test_history_fingerprint_changes_with_row_values_and_hides_approval(history_db, monkeypatch):
    monkeypatch.setenv("GAOBAO_ADMISSIONS_YEAR", "2025")
    monkeypatch.setenv("GAOBAO_DATA_APPROVAL_REF", "internal-fixture-reference")
    before = query_history(history_db, province="广东", score=600, subject="物理", year=2025)
    row = history_db.get(AdmissionScore, 1)
    row.min_score = 598
    history_db.commit()
    after = query_history(history_db, province="广东", score=600, subject="物理", year=2025)
    assert before["rows"][0]["id"] == after["rows"][0]["id"]
    assert before["evidence_sha256"] != after["evidence_sha256"]
    reply, card = build_history_reply({"scene": "gaokao", "data_query_results": {"industrial_history": after}})
    assert after["evidence_sha256"] in reply
    assert f"historical_sql_sha256:{after['evidence_sha256']}" in card["sources"]
    assert "internal-fixture-reference" not in str(card)
    assert "internal-fixture-reference" not in reply


@pytest.mark.parametrize("mutation", ["row", "year", "digest"])
def test_history_refuses_inconsistent_evidence(history_db, monkeypatch, mutation):
    monkeypatch.setenv("GAOBAO_ADMISSIONS_YEAR", "2025")
    evidence = query_history(history_db, province="广东", score=600, subject="物理", year=2025)
    if mutation == "row":
        evidence["rows"][0]["min_score"] = 700
    elif mutation == "year":
        evidence["year"] = 2024
    else:
        evidence.pop("evidence_sha256")
    reply, card = build_history_reply({"scene": "gaokao", "data_query_results": {"industrial_history": evidence}})
    assert "证据不一致" in reply
    assert not card["facts"]
    assert not card["sources"]


@pytest.fixture
def reviewed_manifest(history_db, monkeypatch, tmp_path):
    import hashlib
    import json

    from server.services.admissions_lineage import canonical_digest, row_values

    reference = query_history(history_db, province="广东", score=600, subject="物理", year=2025)
    doc = {
        "schema": "admissions-lineage/v1",
        "dataset_version": "synthetic-fixture-v1",
        "year": 2025,
        "approval_ref": "internal-fixture-approval",
        "sources": {"fixture": {"url": "https://example.invalid/fixture", "file_sha256": "a" * 64}},
        "rows": {
            key: {"source_id": "fixture", "locator": "fixture row", "row_sha256": canonical_digest(values)}
            for key, values in row_values(reference).items()
        },
    }
    monkeypatch.setenv("GAOBAO_RUNTIME_PROFILE", "industrial")
    monkeypatch.setenv("GAOBAO_ADMISSIONS_YEAR", "2025")
    monkeypatch.setenv("GAOBAO_DATA_APPROVAL_REF", doc["approval_ref"])

    def install(document):
        raw = json.dumps(document, ensure_ascii=False).encode()
        path = tmp_path / "reviewed.json"
        path.write_bytes(raw)
        monkeypatch.setenv("GAOBAO_ADMISSIONS_MANIFEST_PATH", str(path))
        monkeypatch.setenv("GAOBAO_ADMISSIONS_MANIFEST_SHA256", hashlib.sha256(raw).hexdigest())

    install(doc)
    return doc, install


def _industrial_graph_history(history_db, monkeypatch):
    from server.graph.nodes.data_nodes import data_query_node

    monkeypatch.setattr("db.database.get_session", lambda: Session(history_db.bind))
    state = {
        "scene": "gaokao",
        "input_text": "广东600分物理类",
        "slots": {"province": "广东", "subject": "物理类", "score": 600},
    }
    state.update(data_query_node(state))
    return state


def test_reviewed_rows_bind_actual_graph_and_public_card(history_db, reviewed_manifest, monkeypatch):
    state = _industrial_graph_history(history_db, monkeypatch)
    evidence = state["data_query_results"]["industrial_history"]
    assert evidence["status"] == "HISTORICAL_ROWS"
    assert len(evidence["lineage"]["records"]) == 2
    reply, card = build_history_reply(state)
    assert "fixture correct school" in reply
    assert "synthetic-fixture-v1" in reply
    assert evidence["lineage"]["manifest_sha256"] in reply
    assert "https://example.invalid/fixture" in reply
    assert "internal-fixture-approval" not in str(card)
    assert "internal-fixture-approval" not in reply


@pytest.mark.parametrize("changed", ["rank", "school", "name", "duplicate"])
def test_changed_or_ambiguous_sql_fails_closed(history_db, reviewed_manifest, monkeypatch, changed):
    if changed == "rank":
        history_db.get(YiFenYiDuan, 1).cumulative_count = 1001
    elif changed == "school":
        history_db.get(AdmissionScore, 1).min_score = 598
    elif changed == "name":
        history_db.get(School, 1).name = "changed name"
    else:
        history_db.add(
            AdmissionScore(school_id=1, province="广东", year=2025, subject_type="物理类", min_score=599, min_rank=1020)
        )
    history_db.commit()
    state = _industrial_graph_history(history_db, monkeypatch)
    assert state["data_query_results"]["industrial_history"]["status"] == "UNAPPROVED_LINEAGE"
    reply, card = build_history_reply(state)
    assert "已停止发布" in reply
    assert not card["facts"]
    assert not card["sources"]


def test_missing_manifest_does_not_query_sql(history_db, reviewed_manifest, monkeypatch):
    from unittest.mock import Mock

    from server.graph.nodes.data_nodes import data_query_node

    monkeypatch.delenv("GAOBAO_ADMISSIONS_MANIFEST_PATH")
    session = Mock(side_effect=AssertionError("unapproved query"))
    monkeypatch.setattr("db.database.get_session", session)
    result = data_query_node({"scene": "gaokao", "slots": {"province": "广东", "subject": "物理类", "score": 600}})
    session.assert_not_called()
    assert result["data_query_results"]["industrial_history"]["status"] == "UNAPPROVED_LINEAGE"


@pytest.mark.parametrize("mutation", ["pin", "approval", "source", "year"])
def test_manifest_refuses_unreviewed_bytes(history_db, reviewed_manifest, monkeypatch, mutation):
    from server.services.admissions_lineage import ManifestError, approved_manifest

    doc, install = reviewed_manifest
    if mutation == "pin":
        monkeypatch.setenv("GAOBAO_ADMISSIONS_MANIFEST_SHA256", "b" * 64)
    elif mutation == "approval":
        monkeypatch.setenv("GAOBAO_DATA_APPROVAL_REF", "different-review")
    elif mutation == "source":
        doc["sources"]["fixture"]["url"] = "http://example.invalid"
        install(doc)
    else:
        doc["year"] = 2024
        install(doc)
    with pytest.raises(ManifestError):
        approved_manifest(2025)


def test_render_rechecks_manifest_even_if_query_fingerprint_recomputed(history_db, reviewed_manifest, monkeypatch):
    from server.services.industrial_admissions import evidence_digest

    state = _industrial_graph_history(history_db, monkeypatch)
    evidence = state["data_query_results"]["industrial_history"]
    evidence["rows"][0]["min_score"] = 700
    evidence["evidence_sha256"] = evidence_digest(evidence)
    reply, card = build_history_reply(state)
    assert "证据不一致" in reply
    assert not card["facts"]


def test_keyed_chat_receipt_digest_changes_with_release_pin(monkeypatch):
    from server.services.industrial_chat_receipt import make_turn_key

    monkeypatch.setenv("GAOBAO_ADMISSIONS_MANIFEST_SHA256", "a" * 64)
    first = make_turn_key("fixture-key-00000001", "gaokao", "600分", {})
    monkeypatch.setenv("GAOBAO_ADMISSIONS_MANIFEST_SHA256", "b" * 64)
    second = make_turn_key("fixture-key-00000001", "gaokao", "600分", {})
    assert first.key_digest == second.key_digest
    assert first.request_digest != second.request_digest


@pytest.fixture
def audited_release(history_db, reviewed_manifest, tmp_path):
    import hashlib

    from server.services.admissions_lineage import approved_manifest, canonical_digest, row_values

    doc, install = reviewed_manifest
    # Synthetic fixture deliberately uses two cohorts; the live audit must
    # detect missing rows even if a single chat query would not select them.
    history_db.get(AdmissionScore, 2).min_rank = 2020
    history_db.commit()
    history = query_history(history_db, province="广东", score=600, subject="历史", year=2025)
    assert len(history["rows"]) == 1
    doc["rows"].update(
        {
            key: {"source_id": "fixture", "locator": "fixture row", "row_sha256": canonical_digest(values)}
            for key, values in row_values(history).items()
        }
    )
    raw = b"synthetic source fixture, not official data"
    digest = hashlib.sha256(raw).hexdigest()
    (tmp_path / digest).write_bytes(raw)
    doc["sources"]["fixture"]["file_sha256"] = digest
    install(doc)
    return approved_manifest(2025), tmp_path


def test_release_audit_full_supported_inventory(history_db, audited_release):
    from server.services.admissions_release_audit import audit_release

    manifest, path = audited_release
    result = audit_release(history_db, manifest, path)
    assert result["passed"]
    assert result["counts"] == {"rank": 2, "school": 2, "rank_out_of_scope": 0, "school_out_of_scope": 0}
    assert not any(result["issues"].values())
    assert "internal-fixture-approval" not in str(result)


@pytest.mark.parametrize(
    ("mutation", "issue"),
    [
        ("file_changed", "source_missing_or_changed"),
        ("file_missing", "source_missing_or_changed"),
        ("row_changed", "changed_rows"),
        ("row_missing", "missing_rows"),
        ("row_added", "unregistered_rows"),
        ("alias_added", "ambiguous_rows"),
    ],
)
def test_release_audit_detects_drift(history_db, audited_release, mutation, issue):
    from server.services.admissions_release_audit import audit_release

    manifest, path = audited_release
    digest = manifest[1]["sources"]["fixture"]["file_sha256"]
    if mutation == "file_changed":
        (path / digest).write_bytes(b"changed")
    elif mutation == "file_missing":
        (path / digest).unlink()
    elif mutation == "row_changed":
        history_db.get(AdmissionScore, 2).min_score = 601
    elif mutation == "row_missing":
        history_db.delete(history_db.get(AdmissionScore, 2))
    elif mutation == "row_added":
        history_db.add(YiFenYiDuan(province="广东", year=2025, subject_type="物理类", score=601, cumulative_count=900))
    else:
        history_db.add(YiFenYiDuan(province="广东", year=2025, subject_type="物理", score=600, cumulative_count=1000))
    history_db.commit()
    result = audit_release(history_db, manifest, path)
    assert not result["passed"]
    assert result["issues"][issue] == 1


def test_release_audit_mysql_entry_rejects_sqlite(history_db, audited_release):
    from server.services.admissions_lineage import ManifestError
    from server.services.admissions_release_audit import audit_mysql_snapshot

    manifest, path = audited_release
    with pytest.raises(ManifestError, match="requires MySQL"):
        audit_mysql_snapshot(history_db.get_bind(), manifest, path)
