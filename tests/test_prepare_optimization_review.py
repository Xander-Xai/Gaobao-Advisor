import json

import pytest

from benchmarks.prepare_optimization_review import load_pairs, main
from benchmarks.synthetic_optimization_review import build_optimization_review
from tests.test_repeated_model_experiment import pairs


def write_runs(directory):
    for index, pair in enumerate(pairs(), 1):
        for arm, report in zip(("baseline", "evidence_first"), pair, strict=True):
            (directory / f"trial-{index}-{arm}.json").write_text(json.dumps(report), encoding="utf-8")


def test_export_matches_server_recomputation_and_never_overwrites(tmp_path, capsys):
    write_runs(tmp_path)
    output = tmp_path / "proposal.json"
    args = ["--run-dir", str(tmp_path), "--trials", "2", "--submission-out", str(output)]
    assert main(args) == 0
    analysis = json.loads(capsys.readouterr().out)
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert set(payload) == {"pairs"}
    assert analysis == build_optimization_review(payload["pairs"])
    assert analysis["production_deployment_authorized"] is False
    previous = output.read_bytes()
    with pytest.raises(SystemExit):
        main(args)
    assert output.read_bytes() == previous


def test_rejects_missing_extra_and_duplicate_reports(tmp_path):
    write_runs(tmp_path)
    path = tmp_path / "trial-2-evidence_first.json"
    report = path.read_text(encoding="utf-8")
    path.unlink()
    with pytest.raises(ValueError):
        load_pairs(tmp_path, 2)
    path.write_text(report, encoding="utf-8")
    extra = tmp_path / "trial-3-baseline.json"
    extra.write_text(report, encoding="utf-8")
    with pytest.raises(ValueError):
        load_pairs(tmp_path, 2)
    extra.unlink()
    path.write_text('{"model_id":"a","model_id":"b"}', encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate"):
        load_pairs(tmp_path, 2)
