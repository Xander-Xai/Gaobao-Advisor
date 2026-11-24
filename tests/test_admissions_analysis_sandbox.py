"""Business sandbox contract tests; real Docker is exercised separately in CI."""

import json
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from db.models import AdmissionScore
from server.agent import admissions_analysis_sandbox as sandbox

IMAGE = "sha256:" + "a" * 64
SCOPE = sandbox.Scope("广东", "物理", "本科批", 2022, 2025)
ROWS = [
    {"id": 1, "school_id": 5, "major_id": None, "year": 2024, "min_score": 600, "max_score": 620, "min_rank": 8000},
    {"id": 2, "school_id": 6, "major_id": None, "year": 2024, "min_score": 580, "max_score": 600, "min_rank": 10000},
]
RESULT = {"groups": [{
    "key": [2024], "records": 2, "missing_min_scores": 0, "median_min_score": 590.0,
    "lowest_min_score": 580, "highest_min_score": 600, "best_min_rank": 8000, "worst_min_rank": 10000,
}]}


def test_queries_actual_admissions_table_and_filters_cohort():
    engine = create_engine("sqlite://")
    AdmissionScore.__table__.create(engine)
    with Session(engine) as session:
        for i, province, subject, major in ((1, "广东", "物理", None), (2, "广西", "物理", None),
                                           (3, "广东", "历史", None), (4, "广东", "物理", 9)):
            session.add(AdmissionScore(id=i, school_id=i, major_id=major, province=province,
                                       year=2024, subject_type=subject, batch="本科批", min_score=600))
        session.commit()
        rows = sandbox.collect_admissions_rows(session, SCOPE)
        assert [row["id"] for row in rows] == [1]
        assert set(rows[0]) == set(sandbox.FIELDS)
        assert sandbox.collect_admissions_rows(
            session, sandbox.Scope("广东", "物理", "本科批", 2022, 2025, "major")
        )[0]["id"] == 4
    engine.dispose()


def test_requires_authorization_before_execution(monkeypatch):
    monkeypatch.setattr(sandbox, "_bounded_docker_run", lambda *a, **k: pytest.fail("unexpected execution"))
    with pytest.raises(ValueError, match="authorization"):
        sandbox.execute_admissions_analysis(
            ROWS, SCOPE, ("year",), b"print('x')", image_id=IMAGE, approval_ref="approved"
        )


@pytest.mark.parametrize("output,cleanup,success", [
    (RESULT, True, True),
    ({"groups": []}, True, False),
    (RESULT, False, False),
])
def test_bounded_read_only_mount_and_independent_oracle(monkeypatch, output, cleanup, success):
    def run(command, **kwargs):
        assert command[0:2] == ["docker", "run"]
        assert command[command.index("--network") + 1] == "none"
        assert command[command.index("--user") + 1] == "65534:65534"
        assert "--read-only" in command and "--pull=never" in command
        mounts = [command[i + 1] for i, value in enumerate(command) if value == "--mount"]
        assert len(mounts) == 1 and mounts[0].endswith(",dst=/input,readonly")
        root = Path(mounts[0].split(",")[1].removeprefix("src="))
        assert set(p.name for p in root.iterdir()) == {"rows.json", "analysis.py"}
        assert json.loads((root / "rows.json").read_bytes())["rows"] == ROWS
        assert (root / "analysis.py").read_bytes() == b"print('test')"
        return subprocess.CompletedProcess(command, 0, json.dumps(output), "private stderr")

    monkeypatch.setattr(sandbox, "_bounded_docker_run", run)
    monkeypatch.setattr(sandbox, "_remove_and_verify_container", lambda name: cleanup)
    result = sandbox.execute_admissions_analysis(
        ROWS, SCOPE, ("year",), b"print('test')", image_id=IMAGE, approval_ref="approved", authorized=True
    )
    assert (result["status"] == "VERIFIED_DESCRIPTIVE_ANALYSIS") is success
    assert ("result" in result) is success
    assert result["admissions_recommendation_verified"] is False
    assert "private stderr" not in json.dumps(result)
    assert "approved" not in json.dumps(result)


def test_no_rows_or_too_many_rows_never_run(monkeypatch):
    monkeypatch.setattr(sandbox, "_bounded_docker_run", lambda *a, **k: pytest.fail("unexpected execution"))
    for rows in ([], ROWS * 1001):
        with pytest.raises(ValueError, match="cohort size"):
            sandbox.execute_admissions_analysis(
                rows, SCOPE, ("year",), b"print('x')", image_id=IMAGE, approval_ref="approved", authorized=True
            )


def test_model_loop_only_sends_schema_and_retries_on_oracle_mismatch(monkeypatch):
    from types import SimpleNamespace

    prompts = []
    calls = []

    def generate(**kwargs):
        prompts.append(kwargs)
        return SimpleNamespace(
            model="test-model", usage=SimpleNamespace(prompt_tokens=20, completion_tokens=40),
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({"code": "print(1)"})))],
        )

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=generate)))

    def execute(*args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            return {"status": "BLOCKED", "error": "oracle_mismatch"}
        return {"status": "VERIFIED_DESCRIPTIVE_ANALYSIS", "result": RESULT}

    monkeypatch.setattr(sandbox, "execute_admissions_analysis", execute)
    receipt = sandbox.run_analysis_agent(
        ROWS, SCOPE, ("year",), client=client, model="test-model", image_id=IMAGE,
        approval_ref="internal-approval", authorized=True, model_authorized=True,
    )
    assert receipt["status"] == "VERIFIED_DESCRIPTIVE_ANALYSIS"
    assert len(prompts) == len(calls) == 2
    assert all(p["max_tokens"] == 1800 for p in prompts)
    for prompt in prompts:
        text = json.dumps(prompt)
        assert "8000" not in text and "10000" not in text and "internal-approval" not in text
    assert "oracle_mismatch" in json.dumps(prompts[-1])
    assert receipt["model_execution"]["billing_verified"] is False
