"""Five-option synthetic business review: positive, missing, subject and tamper gates."""

from __future__ import annotations

import subprocess
import sys

from scripts.seed_demo_data import DEMO_MAJORS, DEMO_SCHOOLS, DEMO_YEAR
from server.agent.synthetic_portfolio import PortfolioRequest


def test_input_contract_rejects_real_or_ambiguous_portfolios():
    good = (
        (DEMO_SCHOOLS[0][0], DEMO_MAJORS[0][0]),
        (DEMO_SCHOOLS[6][0], DEMO_MAJORS[0][0]),
        (DEMO_SCHOOLS[2][0], DEMO_MAJORS[4][0]),
    )
    assert PortfolioRequest("示例省·甲", 550, "物理类", DEMO_YEAR, good).candidates == good
    import pytest

    with pytest.raises(ValueError):
        PortfolioRequest("广东", 550, "物理类", DEMO_YEAR, good)
    with pytest.raises(ValueError):
        PortfolioRequest("示例省·甲", 550, "物理类", DEMO_YEAR, (good[0], good[0], good[1]))
    with pytest.raises(ValueError):
        PortfolioRequest("示例省·甲", 550, "物理类", DEMO_YEAR, good[:2])
    with pytest.raises(ValueError):
        PortfolioRequest("示例省·甲", 550, "物理类", DEMO_YEAR, (("真实大学", good[0][1]), *good[1:]))


_SCRIPT = r"""
import copy
import json
import os
import sys
from pathlib import Path
root = Path(sys.argv[1])
os.environ["DATABASE_URL"] = "sqlite:///" + str(root / "synthetic.db")
os.environ["ENABLE_OUTBOUND_FETCH"] = "false"
from scripts.seed_demo_data import seed_database, DEMO_YEAR, DEMO_MAJORS, DEMO_SCHOOLS
seed_database(db_path=root / "synthetic.db", reset=True)
from server.agent.synthetic_portfolio import PortfolioRequest, build_synthetic_portfolio, verify_synthetic_portfolio

options = (
    (DEMO_SCHOOLS[0][0], DEMO_MAJORS[0][0]),
    (DEMO_SCHOOLS[6][0], DEMO_MAJORS[0][0]),
    (DEMO_SCHOOLS[2][0], DEMO_MAJORS[4][0]),
    (DEMO_SCHOOLS[4][0], DEMO_MAJORS[8][0]),
    (DEMO_SCHOOLS[1][0], DEMO_MAJORS[0][0]),
)
profile = PortfolioRequest("示例省·甲", 550, "物理类", DEMO_YEAR, options)
result = build_synthetic_portfolio(profile)
assert result["evaluated_count"] == 5
assert result["eligible_history_count"] == 4, result
assert result["excluded_count"] == 1
assert result["sql_tool_calls"] == 13
assert result["status"] == "historical_candidates_reviewed"
assert result["application_ranking"] is None
assert result["admission_probability"] is None
assert result["applicant_rank"] is None
assert result["human_review_complete"] is False
assert all(len(x["source_ids"]) == 3 for x in result["accepted_historical_comparisons"])
assert "admission_evidence_missing_or_ambiguous" in result["excluded_candidates"][0]["reason_codes"]
assert verify_synthetic_portfolio(profile, result)
faked = copy.deepcopy(result)
faked["accepted_historical_comparisons"][0]["historical_min_score"] = 1
assert not verify_synthetic_portfolio(profile, faked)
faked = copy.deepcopy(result)
faked["accepted_historical_comparisons"][0]["source_ids"] = ["fake"]
assert not verify_synthetic_portfolio(profile, faked)
faked = copy.deepcopy(result)
faked["excluded_candidates"] = []
assert not verify_synthetic_portfolio(profile, faked)

history_subject = PortfolioRequest("示例省·甲", 550, "历史类", DEMO_YEAR, options)
negative = build_synthetic_portfolio(history_subject)
assert negative["eligible_history_count"] == 0
assert negative["status"] == "needs_review"
assert negative["excluded_count"] == 5
assert all(item["reason_codes"] for item in negative["excluded_candidates"])
print(json.dumps({"status":"PASS","valid_candidates":4,"exclusions":1,"tool_calls":13}))
"""


def test_multicandidate_real_sql_evidence_negative_cases_and_forgery(tmp_path):
    result = subprocess.run(
        [sys.executable, "-c", _SCRIPT, str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2300:] + result.stdout[-900:]
    assert '"valid_candidates": 4' in result.stdout
