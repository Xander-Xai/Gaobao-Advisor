"""Sandboxed synthetic code-repair Harness: inspect → edit → test → finish.

The only writable artifact is a newly generated fictional scratch workspace.
The test runner executes inside the restricted Docker executor, not on the host.
Default controller is SCRIPTED; it does not demonstrate real LLM coding skill.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import secrets
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from benchmarks.sandbox_workspace import run_sandbox_probe


@dataclass(frozen=True)
class CodingAction:
    kind: Literal["inspect", "edit", "test", "finish"]
    content: str = ""


class CodingPlanner(Protocol):
    def decide(self, task: str, observations: list[dict[str, Any]]) -> CodingAction:
        """Choose the next coding operation; never choose a host command."""


_TASKS = {
    "addition": {
        "task": "Repair the fictional add(a, b) helper to pass both tests",
        "broken": "def add(a, b):\n    return a - b\n",
        "correct": "def add(a, b):\n    return a + b\n",
        "oracle": "assert module.add(2, 3) == 5\nassert module.add(-4, 2) == -2",
    },
    "clamp": {
        "task": "Repair fictional clamp(value, low, high) to enforce numeric bounds",
        "broken": "def clamp(value, low, high):\n    return max(high, min(low, value))\n",
        "correct": "def clamp(value, low, high):\n    return max(low, min(high, value))\n",
        "oracle": "assert module.clamp(15, 0, 10) == 10\nassert module.clamp(-4, 0, 10) == 0\nassert module.clamp(7, 0, 10) == 7",
    },
}

_TEST_RUNNER = """import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("synthetic_solution", "/workspace/solution.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
{oracle}
Path("/workspace/artifact.txt").write_text("synthetic tests passed")
print("SYNTHETIC_WORKSPACE_PASS")
"""


class ScriptedRepairPlanner:
    """Deterministic test fixture. No model is being evaluated."""

    def __init__(self, correct: str) -> None:
        self._actions = iter(
            [
                CodingAction("inspect"),
                CodingAction("test"),
                CodingAction("edit", correct),
                CodingAction("test"),
                CodingAction("finish"),
            ]
        )

    def decide(self, task: str, observations: list[dict[str, Any]]) -> CodingAction:
        return next(self._actions)


def run_repair(
    task_id: str,
    image_id: str,
    planner: CodingPlanner,
    *,
    max_steps: int = 8,
) -> dict[str, Any]:
    """Require a *real sandbox test pass after the last patch* before completion."""
    if task_id not in _TASKS or type(max_steps) is not int or not 1 <= max_steps <= 12:
        raise ValueError("Unknown synthetic task or invalid model step budget")
    spec = _TASKS[task_id]
    actions: list[str] = []
    trace: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    last_patch_hash = ""
    last_test_pass = False
    tool_calls = test_runs = model_steps = 0
    status = "blocked"
    reason = "step_budget_exhausted"

    with tempfile.TemporaryDirectory(prefix="gaobao-synthetic-parent-") as parent:
        work = Path(parent) / ("gaobao-synthetic-" + secrets.token_hex(6))
        work.mkdir(mode=0o777)
        work.chmod(0o777)
        (work / ".synthetic_workspace").write_text("SYNTHETIC_ONLY\n", encoding="utf-8")
        solution = work / "solution.py"
        solution.write_text(spec["broken"], encoding="utf-8")
        (work / "probe.py").write_text(_TEST_RUNNER.format(oracle=spec["oracle"]), encoding="utf-8")
        for index in range(1, max_steps + 1):
            model_steps = index
            try:
                action = planner.decide(spec["task"], [dict(event) for event in observations])
            except Exception:  # noqa: BLE001 - never log model/credential content
                reason = "planner_failed"
                break
            if not isinstance(action, CodingAction):
                reason = "planner_contract_invalid"
                break
            if action.kind == "inspect":
                tool_calls += 1
                actions.append("inspect")
                observations.append({"kind": "inspect", "source": solution.read_text(encoding="utf-8")[:2000]})
                trace.append({"step": index, "tool": "inspect", "result": "ok"})
            elif action.kind == "edit":
                if not isinstance(action.content, str) or not 1 <= len(action.content) <= 2000:
                    reason = "invalid_patch"
                    break
                try:
                    ast.parse(action.content)
                except SyntaxError:
                    reason = "patch_syntax_error"
                    break
                new_digest = hashlib.sha256(action.content.encode("utf-8")).hexdigest()
                if new_digest == last_patch_hash:
                    reason = "duplicate_patch"
                    break
                if solution.is_symlink():
                    reason = "unsafe_workspace_path"
                    break
                solution.write_text(action.content, encoding="utf-8")
                last_patch_hash = new_digest
                last_test_pass = False
                (work / "artifact.txt").unlink(missing_ok=True)
                tool_calls += 1
                actions.append("edit")
                observations.append({"kind": "edit", "result": "applied"})
                trace.append({"step": index, "tool": "edit", "result": "ok", "patch_sha256": new_digest})
            elif action.kind == "test":
                test_runs += 1
                tool_calls += 1
                actions.append("test")
                result = run_sandbox_probe(image_id, work)
                last_test_pass = result["passed"] and (work / "artifact.txt").is_file()
                observations.append(
                    {"kind": "test", "passed": last_test_pass, "reason": result.get("error", "completed")}
                )
                trace.append({"step": index, "tool": "test", "result": "pass" if last_test_pass else "fail"})
            elif action.kind == "finish":
                if last_test_pass:
                    status, reason = "completed", "sandbox_tests_passed"
                else:
                    reason = "unverified_finish"
                trace.append({"step": index, "tool": "finish", "result": reason})
                break
            else:
                reason = "unsupported_action"
                break
        # tempfile removes the only writable mount even on failed execution.
    return {
        "task_id": task_id,
        "controller": "UNSPECIFIED",
        "status": status,
        "reason": reason,
        "model_steps": model_steps,
        "tool_calls": tool_calls,
        "test_runs": test_runs,
        "tool_sequence": actions,
        "trace": trace,
        "sandbox": "DOCKER_RESTRICTED_SYNTHETIC",
        "real_model_accuracy": "NOT_MEASURED",
        "persistent_memory": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    reports = [run_repair(name, args.image, ScriptedRepairPlanner(task["correct"])) for name, task in _TASKS.items()]
    for row in reports:
        row["controller"] = "SCRIPTED_NOT_LLM"
    summary = {
        "suite": "synthetic_sandbox_code_repair_contract",
        "controller": "SCRIPTED_NOT_LLM",
        "cases": len(reports),
        "passed": sum(r["status"] == "completed" for r in reports),
        "real_model_accuracy": "NOT_MEASURED",
        "results": reports,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"code_repair_sandbox_passed={summary['passed']}/{summary['cases']}")
    return 0 if summary["passed"] == summary["cases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
