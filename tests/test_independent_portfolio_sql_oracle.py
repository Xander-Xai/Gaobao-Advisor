"""Independent direct-SQL synthetic oracle: completeness, provenance and forgery.

Runs in a clean subprocess because the admissions SQLAlchemy database URL is
initialized at module import. All entities and identities are fabricated.
"""

from __future__ import annotations

import os
import subprocess
import sys

_SCRIPT = r"""
import copy
import os
import sqlite3
import sys
from pathlib import Path

root = Path(sys.argv[1])
os.environ["DATABASE_URL"] = "sqlite:///" + str(root / "synthetic.sqlite")
os.environ["ENABLE_OUTBOUND_FETCH"] = "false"
os.environ["ENABLE_SYNTHETIC_AGENT_EXPERIMENT"] = "true"
os.environ["APP_ENV"] = "development"
from scripts.seed_demo_data import seed_database, DEMO_MAJORS, DEMO_SCHOOLS, DEMO_YEAR
seed_database(db_path=root / "synthetic.sqlite", reset=True)
from server.agent.synthetic_portfolio import PortfolioRequest, build_synthetic_portfolio
from server.agent.portfolio_sql_oracle import UnverifiedPortfolioReceipt, verify_portfolio_sql_truth

candidates = (
    (DEMO_SCHOOLS[0][0], DEMO_MAJORS[0][0]),
    (DEMO_SCHOOLS[6][0], DEMO_MAJORS[0][0]),
    (DEMO_SCHOOLS[2][0], DEMO_MAJORS[4][0]),
    (DEMO_SCHOOLS[4][0], DEMO_MAJORS[8][0]),
    (DEMO_SCHOOLS[1][0], DEMO_MAJORS[0][0]),
)
profile = PortfolioRequest("示例省·甲", 550, "物理类", DEMO_YEAR, candidates)
receipt = build_synthetic_portfolio(profile)
assert verify_portfolio_sql_truth(profile, receipt) == {
    "evaluated": 5, "historically_supported": 4, "excluded": 1,
}
bad = []
def forged(edit):
    result = copy.deepcopy(receipt)
    edit(result)
    try:
        verify_portfolio_sql_truth(profile, result)
    except UnverifiedPortfolioReceipt:
        bad.append("rejected")
    else:
        raise AssertionError("Forged receipt was accepted")

forged(lambda r: r["accepted_historical_comparisons"][0].update(historical_min_score=1))
forged(lambda r: r["accepted_historical_comparisons"][0].update(source_ids=["fake"]))
forged(lambda r: r.update(eligible_history_count=5))
forged(lambda r: r.update(excluded_candidates=[]))
forged(lambda r: r.update(human_review_complete=True))
forged(lambda r: r.update(application_ranking=["unverified decision"]))
forged(lambda r: r["request"].update(score=500))
forged(lambda r: r.update(debug_credentials="no"))
forged(lambda r: r["accepted_historical_comparisons"].reverse())
assert len(bad) == 9

negative = PortfolioRequest("示例省·甲", 550, "历史类", DEMO_YEAR, candidates)
refusal = build_synthetic_portfolio(negative)
assert verify_portfolio_sql_truth(negative, refusal)["historically_supported"] == 0
assert refusal["status"] == "needs_review"

# Independent truth invalidates an already-calculated receipt if its frozen
# record was changed before verification.
with sqlite3.connect(root / "synthetic.sqlite") as db:
    school_id = db.execute("SELECT id FROM schools WHERE name=?", (DEMO_SCHOOLS[0][0],)).fetchone()[0]
    major_id = db.execute("SELECT id FROM majors WHERE name=?", (DEMO_MAJORS[0][0],)).fetchone()[0]
    db.execute(
        "UPDATE admission_scores SET min_score=min_score+1 "
        "WHERE school_id=? AND major_id=? AND province=? AND year=?",
        (school_id, major_id, "示例省·甲", DEMO_YEAR),
    )
try:
    verify_portfolio_sql_truth(profile, receipt)
except UnverifiedPortfolioReceipt:
    pass
else:
    raise AssertionError("Stale source row was trusted")

# A deliberately compromised first verifier must not bypass the second,
# independent SQL gate in the durable worker.
from server.agent.taskrun_store import SyntheticTaskRunStore
from server.agent.taskrun_worker import execute_approved_run
import server.agent.synthetic_portfolio as portfolio_module

store = SyntheticTaskRunStore(root / "journal.sqlite")
portfolio_payload = {
    "province": profile.province,
    "score": profile.score,
    "subject_type": profile.subject_type,
    "year": profile.year,
    "candidates": [{"school": school, "major": major} for school, major in profile.candidates],
}
created = store.create(
    {"data_profile": "SYNTHETIC_DEMO", "portfolio": portfolio_payload},
    "portfolio", "oracle-operator", "oracle-poisoned-2026",
)
store.approve(created["run_id"], "oracle-checker")
evil = copy.deepcopy(receipt)
evil["accepted_historical_comparisons"][0]["historical_min_score"] = 1
portfolio_module.build_synthetic_portfolio = lambda p: evil
portfolio_module.verify_synthetic_portfolio = lambda p, c: True
result = execute_approved_run(store, created["run_id"])
assert result["status"] == "FAILED"
assert result["result"] is None
assert result["failure_code"] == "agent_execution_failed"
assert result["attempts"] == 1
print("independent_portfolio_oracle_cases=12/12")
"""


def test_direct_sql_oracle_and_compromised_worker_receipts(tmp_path):
    env = dict(os.environ)
    env.pop("OPENAI_API_KEY", None)
    result = subprocess.run(
        [sys.executable, "-c", _SCRIPT, str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-3500:] + result.stdout[-1000:]
    assert "independent_portfolio_oracle_cases=12/12" in result.stdout
