"""Offline final-answer policy regression, never an admissions accuracy metric.

This is a frozen set of *synthetic* examples for the static source/quality
gate. No provider calls, real school records, or network traffic are used.
The known semantic failure is recorded separately, not counted as a success.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from server.routes.chat import _deterministic_gate_issues

_CONTRACT = "SYNTHETIC_CHAT_GATE_V1"

CASES = [
    {
        "case_id": "clean_explanation",
        "state": {"input_text": "请解释志愿规划"},
        "candidate": "志愿填报需要核对官方规则。",
        "expected": [],
    },
    {
        "case_id": "user_provided_score",
        "state": {"input_text": "我考了600分", "slots": {"score": 600}},
        "candidate": "根据你提供的600分，还需核实位次与选科。",
        "expected": [],
    },
    {
        "case_id": "invented_numeric_unattributed",
        "state": {"input_text": "请问分数线是多少？"},
        "candidate": "该校最低录取分为666分。",
        "expected": ["UNATTRIBUTED_NUMBER", "UNVERIFIED_NUMERIC_CLAIM"],
    },
    {
        "case_id": "forged_source_marker",
        "state": {"input_text": "请问分数线是多少？"},
        "candidate": "根据最新可靠数据，该校分数线为666分。",
        "expected": ["UNVERIFIED_NUMERIC_CLAIM"],
    },
    {
        "case_id": "evidence_backed_sql_number",
        "state": {
            "input_text": "最低分数？",
            "data_query_results": {"match_schools": [{"school_name": "示例甲（合成）", "min_score": 600}]},
        },
        "candidate": "根据合成数据记录，示例甲的最低分为600分。",
        "expected": [],
    },
    {
        "case_id": "evidence_backed_rag_percentage",
        "state": {
            "input_text": "就业率呢？",
            "rag_chunks": [{"text": "在本合成样本中，例子就业率为95%。", "source": "synthetic:example"}],
        },
        "candidate": "根据合成材料，样本就业率为95%。",
        "expected": [],
    },
    {
        "case_id": "education_identifier_not_data",
        "state": {"input_text": "985高校是什么意思"},
        "candidate": "985高校是一种历史教育建设项目的常用称呼。",
        "expected": [],
    },
]

# This deliberately FAILS semantic attribution: the number exists in SQL
# but the candidate assigns it to the wrong fictional school. We disclose
# it as a known limitation and never claim the gate proves factual support.
KNOWN_LIMITATION = {
    "case_id": "numeric_wrong_school",
    "state": {
        "input_text": "比较两所学校",
        "data_query_results": {"match_schools": [{"school_name": "示例甲（合成）", "min_score": 600}]},
    },
    "candidate": "根据合成数据，示例乙的最低分为600分。",
    "expected_unsafe": True,
}


def evaluate_chat_gate() -> dict:
    results = []
    for case in CASES:
        actual = _deterministic_gate_issues(case["state"], case["candidate"])
        expected = case["expected"]
        results.append({
            "case_id": case["case_id"],
            "passed": actual == expected,
            "observed_reason_codes": actual,
            "expected_reason_codes": expected,
        })
    limitation_accepted = not _deterministic_gate_issues(
        KNOWN_LIMITATION["state"], KNOWN_LIMITATION["candidate"]
    )
    return {
        "contract": _CONTRACT,
        "data_profile": "SYNTHETIC_DEMO",
        "case_digest": hashlib.sha256(
            json.dumps(CASES, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest(),
        "cases": len(results),
        "contract_passed": sum(item["passed"] for item in results),
        "all_contract_cases_passed": all(item["passed"] for item in results),
        "case_results": results,
        "known_semantic_attribution_failure": {
            "case_id": KNOWN_LIMITATION["case_id"],
            "wrong_school_claim_accepted": limitation_accepted,
            "requires_claim_level_verification": True,
        },
        "real_model_task_success": "NOT_MEASURED",
        "human_reviewed_ground_truth": False,
        "admissions_accuracy": "NOT_MEASURED",
        "provider_tokens": None,
        "provider_cost": None,
        "production_eligible": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate_chat_gate()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if not result["all_contract_cases_passed"]:
        raise SystemExit("Synthetic chat gate contract failed")
    print(f"chat gate: {result['contract_passed']}/{result['cases']} synthetic checks passed")


if __name__ == "__main__":
    main()
