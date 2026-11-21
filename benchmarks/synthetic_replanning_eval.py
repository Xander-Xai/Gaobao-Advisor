"""Synthetic decision-card planning/replanning evaluation.

Default runs scripted (not LLM) SQL+oracle tests with an injected transient
tool failure. External model calls are explicitly opt-in and never CI-default.

python -m benchmarks.synthetic_replanning_eval --out /tmp/replan.json --inject-transient
python -m benchmarks.synthetic_replanning_eval --mode model --model MODEL \\
  --permit-external-model --max-cases 8 --inject-transient --out /tmp/model-replan.json
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from benchmarks.decision_card_business_eval import frozen_cases, grade_business_case
from server.agent.synthetic_decision_card import DecisionCardProfile
from server.agent.synthetic_replanning import (
    DeterministicPlanPolicy,
    OpenAIReplanningPolicy,
    execute_synthetic_replanning,
)


def run_cases(
    cases: list[dict[str, Any]],
    policy_factory: Callable[[], Any],
    *,
    inject_transient: bool = True,
    model_mode: bool = False,
) -> dict[str, Any]:
    """Independent SQL oracle grades final card, not the model's plan text."""
    rows: list[dict[str, Any]] = []
    for case in cases:
        profile = DecisionCardProfile(**case["profile"])
        injection = {("admission_lookup", profile.school_a)} if inject_transient else set()
        card = execute_synthetic_replanning(
            profile,
            policy_factory(),
            require_provider_usage=model_mode,
            inject_transient_once=injection,
        )
        grade = grade_business_case(case, card)
        observed = card["planning"]
        rows.append(
            {
                "case_id": case["case_id"],
                "split": case["split"],
                "passed": grade["passed"],
                "failure_codes": grade["failure_codes"],
                "card_status": card["status"],
                "tool_attempts": observed["tool_attempts"],
                "plan_rounds": observed["rounds"],
                "replans": observed["replans"],
                "transient_tool_failures": observed["transient_tool_failures"],
                "provider_prompt_tokens": card["execution"]["prompt_tokens"],
                "provider_completion_tokens": card["execution"]["completion_tokens"],
                "trace": observed["trace"],
            }
        )
    return {
        "kind": "SYNTHETIC_REPLANNING_EVAL_V1",
        "dataset_profile": "SYNTHETIC_DEMO",
        "controller": "OPT_IN_REAL_MODEL" if model_mode else "SCRIPTED_NOT_MODEL",
        "oracle": "INDEPENDENT_SYNTHETIC_SQL",
        "human_expert_ground_truth": False,
        "real_admissions_quality": "NOT_MEASURED",
        "real_user_delivery": "NOT_MEASURED",
        "provider_billing_verified": False,
        "cases": len(rows),
        "passed": sum(row["passed"] for row in rows),
        "replanned_cases": sum(row["replans"] > 0 for row in rows),
        "replanned_passed": sum(row["passed"] and row["replans"] > 0 for row in rows),
        "total_tool_attempts": sum(row["tool_attempts"] for row in rows),
        "results": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("scripted", "model"), default="scripted")
    parser.add_argument("--model", help="Explicit model ID for real-model experiment")
    parser.add_argument("--permit-external-model", action="store_true")
    parser.add_argument("--max-cases", type=int, default=8)
    parser.add_argument("--inject-transient", action="store_true")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    if not 1 <= args.max_cases <= 8:
        parser.error("--max-cases must be 1..8 for the frozen synthetic suite")
    if args.mode == "scripted":
        if args.permit_external_model or args.model:
            parser.error("Scripted mode must not accept provider configuration")
        factory: Callable[[], Any] = DeterministicPlanPolicy
    else:
        if (
            not args.permit_external_model
            or not args.model
            or not os.environ.get("OPENAI_API_KEY")
            or os.environ.get("APP_ENV", "development").lower() == "production"
        ):
            parser.error("Model mode requires explicit permit, key, model and nonproduction environment")
        from openai import OpenAI

        client = OpenAI(
            api_key=os.environ["OPENAI_API_KEY"],
            base_url=os.environ.get("OPENAI_API_BASE") or None,
            timeout=25.0,
            max_retries=0,
        )

        def factory() -> OpenAIReplanningPolicy:
            return OpenAIReplanningPolicy(client, args.model)

    # No external scraping: only verified seeded fictional records are used.
    if os.environ.get("DATABASE_URL", "").startswith(("mysql:", "postgresql:")):
        parser.error("Live DB is not permitted for replanning Eval")
    with tempfile.TemporaryDirectory(prefix="gaobao-replanning-eval-") as directory:
        dbpath = Path(directory) / "synthetic.db"
        os.environ["DATABASE_URL"] = "sqlite:///" + str(dbpath)
        os.environ["ENABLE_OUTBOUND_FETCH"] = "false"
        from scripts.seed_demo_data import seed_database

        seed_database(db_path=dbpath, reset=True)
        report = run_cases(
            frozen_cases()[: args.max_cases],
            factory,
            inject_transient=args.inject_transient,
            model_mode=args.mode == "model",
        )
        from db.database import engine

        engine.dispose()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"replanning_verified={report['passed']}/{report['cases']}; "
        f"replanned_cases={report['replanned_cases']}; "
        f"controller={report['controller']}"
    )
    return 0 if report["passed"] == report["cases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
