"""Offline synthetic five-option portfolio regression: historical evidence, not advice.

python -m benchmarks.synthetic_portfolio_eval --out /tmp/portfolio-eval.json
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from scripts.seed_demo_data import DEMO_MAJORS, DEMO_SCHOOLS, DEMO_YEAR, seed_database
from server.agent.synthetic_portfolio import (
    PortfolioRequest,
    build_synthetic_portfolio,
    verify_synthetic_portfolio,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    if os.getenv("APP_ENV", "development").lower() == "production":
        parser.error("Synthetic portfolio benchmark is disabled in production")
    with tempfile.TemporaryDirectory(prefix="gaobao-portfolio-eval-") as temp:
        path = Path(temp) / "synthetic.sqlite"
        os.environ["DATABASE_URL"] = "sqlite:///" + str(path)
        os.environ["ENABLE_OUTBOUND_FETCH"] = "false"
        seed_database(db_path=path, reset=True)
        options = (
            (DEMO_SCHOOLS[0][0], DEMO_MAJORS[0][0]),
            (DEMO_SCHOOLS[6][0], DEMO_MAJORS[0][0]),
            (DEMO_SCHOOLS[2][0], DEMO_MAJORS[4][0]),
            (DEMO_SCHOOLS[4][0], DEMO_MAJORS[8][0]),
            (DEMO_SCHOOLS[1][0], DEMO_MAJORS[0][0]),
        )
        request = PortfolioRequest("示例省·甲", 550, "物理类", DEMO_YEAR, options)
        receipt = build_synthetic_portfolio(request)
        verified = verify_synthetic_portfolio(request, receipt)
        from db.database import engine

        engine.dispose()
    report = {
        "controller": "DETERMINISTIC_SQL_NO_LLM",
        "data_profile": "SYNTHETIC_DEMO",
        "independent_human_ground_truth": False,
        "sql_reread_verified": verified,
        "evaluated_candidates": receipt["evaluated_count"],
        "historical_comparisons": receipt["eligible_history_count"],
        "evidence_exclusions": receipt["excluded_count"],
        "sql_tool_calls": receipt["sql_tool_calls"],
        "real_admissions_accuracy": "NOT_MEASURED",
        "real_user_delivery_rate": "NOT_MEASURED",
        "model_tokens": None,
        "provider_cost_usd": None,
        "receipt": receipt,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        "synthetic_portfolio_sql_verified="
        + str(verified)
        + " historical_comparisons="
        + str(report["historical_comparisons"])
        + "/"
        + str(report["evaluated_candidates"])
    )
    return 0 if verified and report["historical_comparisons"] == 4 and report["evidence_exclusions"] == 1 else 1


if __name__ == "__main__":
    raise SystemExit(main())
