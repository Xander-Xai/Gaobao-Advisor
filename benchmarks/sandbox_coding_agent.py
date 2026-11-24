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
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from benchmarks.sandbox_workspace import run_sandbox_probe


@dataclass(frozen=True)
class CodingAction:
    kind: Literal["inspect", "edit", "test", "finish"]
    content: str = ""
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


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


_ALLOWED_EXPR_NODES = (
    ast.BinOp,
    ast.Add,
    ast.Sub,
    ast.Name,
    ast.Load,
    ast.Constant,
    ast.Call,
)


def validate_synthetic_patch(source: str, task_id: str) -> None:
    """Accept only a tiny arithmetic function, not arbitrary executable Python.

    This is intentionally a constrained coding *exercise*, not a general
    untrusted-repository executor. The sandbox is defense in depth.
    """
    if task_id not in _TASKS:
        raise ValueError("Unsupported synthetic task")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise SyntaxError("Patch syntax invalid") from exc
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef):
        raise ValueError("Patch must be one function")
    function = tree.body[0]
    signatures = {"addition": ("add", ("a", "b")), "clamp": ("clamp", ("value", "low", "high"))}
    name, parameters = signatures[task_id]
    if (
        function.name != name
        or function.decorator_list
        or function.returns is not None
        or len(function.body) != 1
        or not isinstance(function.body[0], ast.Return)
        or function.args.posonlyargs
        or function.args.kwonlyargs
        or function.args.vararg is not None
        or function.args.kwarg is not None
        or function.args.defaults
        or function.args.kw_defaults
        or tuple(arg.arg for arg in function.args.args) != parameters
        or any(arg.annotation is not None for arg in function.args.args)
    ):
        raise ValueError("Patch signature or body is not allowlisted")
    expr = function.body[0].value
    for node in ast.walk(expr):
        if not isinstance(node, _ALLOWED_EXPR_NODES):
            raise ValueError("Patch contains disallowed expression")
        if isinstance(node, ast.BinOp) and not isinstance(node.op, (ast.Add, ast.Sub)):
            raise ValueError("Patch contains disallowed binary operation")
        if isinstance(node, ast.Name) and node.id not in {*parameters, "min", "max"}:
            raise ValueError("Patch references an unapproved name")
        if isinstance(node, ast.Call):
            if (
                not isinstance(node.func, ast.Name)
                or node.func.id not in {"min", "max"}
                or len(node.args) != 2
                or node.keywords
            ):
                raise ValueError("Patch attempts an unapproved function call")
        if isinstance(node, ast.Constant) and (type(node.value) is not int or abs(node.value) > 1000):
            raise ValueError("Patch has an unapproved constant")


def _bounded_regular_bytes(path: Path, max_bytes: int) -> bytes:
    """Read only bounded, regular, single-link scratch artifacts.

    The private scratch parent and completed Docker invocation are assumed;
    this is not a concurrent hostile-host filesystem security boundary.
    """
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or metadata.st_size > max_bytes:
        raise ValueError("Unsafe scratch artifact")
    with path.open("rb") as stream:
        data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("Oversized scratch artifact")
    return data


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
    max_provider_tokens: int = 6000,
    require_provider_usage: bool = False,
) -> dict[str, Any]:
    """Require a *real sandbox test pass after the last patch* before completion."""
    if task_id not in _TASKS or type(max_steps) is not int or not 1 <= max_steps <= 12:
        raise ValueError("Unknown synthetic task or invalid model step budget")
    if type(max_provider_tokens) is not int or not 1 <= max_provider_tokens <= 200000:
        raise ValueError("Invalid provider Token budget")
    spec = _TASKS[task_id]
    actions: list[str] = []
    trace: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    last_patch_hash = ""
    last_test_pass = False
    tested_source_sha256: str | None = None
    tool_calls = test_runs = model_steps = 0
    prompt_tokens = completion_tokens = 0
    usage_known = True
    status = "blocked"
    reason = "step_budget_exhausted"

    with tempfile.TemporaryDirectory(prefix="gaobao-synthetic-parent-") as parent:
        work = Path(parent) / ("gaobao-synthetic-" + secrets.token_hex(6))
        work.mkdir(mode=0o777)
        work.chmod(0o777)
        (work / ".synthetic_workspace").write_text("SYNTHETIC_ONLY\n", encoding="utf-8")
        solution = work / "solution.py"
        solution.write_text(spec["broken"], encoding="utf-8")
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
            if (
                type(action.prompt_tokens) is not int
                or type(action.completion_tokens) is not int
                or action.prompt_tokens < 0
                or action.completion_tokens < 0
            ):
                usage_known = False
                if require_provider_usage:
                    reason = "provider_usage_missing"
                    break
            else:
                prompt_tokens += action.prompt_tokens
                completion_tokens += action.completion_tokens
            if prompt_tokens + completion_tokens > max_provider_tokens:
                reason = "provider_token_budget_exhausted"
                break
            if action.kind == "inspect":
                tool_calls += 1
                actions.append("inspect")
                try:
                    source = _bounded_regular_bytes(solution, 8000).decode("utf-8")
                except (OSError, ValueError):
                    reason = "unsafe_workspace_path"
                    break
                observations.append({"kind": "inspect", "source": source[:2000]})
                trace.append({"step": index, "tool": "inspect", "result": "ok"})
            elif action.kind == "edit":
                if not isinstance(action.content, str) or not 1 <= len(action.content) <= 2000:
                    reason = "invalid_patch"
                    break
                try:
                    validate_synthetic_patch(action.content, task_id)
                except SyntaxError:
                    reason = "patch_syntax_error"
                    break
                except ValueError:
                    reason = "unsafe_patch_ast"
                    break
                new_digest = hashlib.sha256(action.content.encode("utf-8")).hexdigest()
                if new_digest == last_patch_hash:
                    reason = "duplicate_patch"
                    break
                try:
                    _bounded_regular_bytes(solution, 8000)
                except (OSError, ValueError):
                    reason = "unsafe_workspace_path"
                    break
                solution.write_text(action.content, encoding="utf-8")
                last_patch_hash = new_digest
                last_test_pass = False
                tested_source_sha256 = None
                (work / "artifact.txt").unlink(missing_ok=True)
                tool_calls += 1
                actions.append("edit")
                observations.append({"kind": "edit", "result": "applied"})
                trace.append({"step": index, "tool": "edit", "result": "ok", "patch_sha256": new_digest})
            elif action.kind == "test":
                test_runs += 1
                tool_calls += 1
                actions.append("test")
                last_test_pass = False
                tested_source_sha256 = None
                artifact = work / "artifact.txt"
                try:
                    before = _bounded_regular_bytes(solution, 8000)
                    validate_synthetic_patch(before.decode("utf-8"), task_id)
                    artifact.unlink(missing_ok=True)
                    result = run_sandbox_probe(
                        image_id,
                        work,
                        oracle_source=_TEST_RUNNER.format(oracle=spec["oracle"]),
                    )
                    after = _bounded_regular_bytes(solution, 8000)
                    if result.get("passed") is True and before == after:
                        last_test_pass = _bounded_regular_bytes(artifact, 256) == b"synthetic tests passed"
                    if last_test_pass:
                        tested_source_sha256 = hashlib.sha256(after).hexdigest()
                except (OSError, ValueError, SyntaxError):
                    result = {"error": "invalid_sandbox_artifact"}
                if result.get("passed") is True and not last_test_pass:
                    result = {"error": "sandbox_evidence_mismatch"}
                observations.append(
                    {"kind": "test", "passed": last_test_pass, "reason": result.get("error", "completed")}
                )
                trace.append({"step": index, "tool": "test", "result": "pass" if last_test_pass else "fail"})
            elif action.kind == "finish":
                try:
                    current = hashlib.sha256(_bounded_regular_bytes(solution, 8000)).hexdigest()
                except (OSError, ValueError):
                    current = None
                if last_test_pass and tested_source_sha256 is not None and current == tested_source_sha256:
                    status, reason = "completed", "sandbox_tests_passed"
                else:
                    reason = "unverified_finish"
                    tested_source_sha256 = None
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
        "prompt_tokens": prompt_tokens if usage_known else None,
        "completion_tokens": completion_tokens if usage_known else None,
        "tool_calls": tool_calls,
        "test_runs": test_runs,
        "accepted_source_sha256": tested_source_sha256 if status == "completed" else None,
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
    parser.add_argument("--model", help="Explicit OpenAI-compatible model ID (optional)")
    parser.add_argument("--permit-external-model", action="store_true")
    args = parser.parse_args(argv)
    if args.model and not args.permit_external_model:
        parser.error("Explicit --permit-external-model required before external API calls")
    if not args.model and args.permit_external_model:
        parser.error("--model is required with --permit-external-model")
    if args.model:
        import os

        if not os.getenv("OPENAI_API_KEY"):
            parser.error("OPENAI_API_KEY is required for explicit model calls")
        from openai import OpenAI

        from benchmarks.sandbox_model_planner import JSONCodingPlanner

        client = OpenAI(
            api_key=os.environ["OPENAI_API_KEY"],
            base_url=os.getenv("OPENAI_API_BASE") or None,
            timeout=20.0,
            max_retries=0,
        )
        reports = [
            run_repair(
                name,
                args.image,
                JSONCodingPlanner(client, args.model),
                require_provider_usage=True,
            )
            for name in _TASKS
        ]
        controller = "REAL_MODEL_NOT_SCRIPTED"
    else:
        reports = [
            run_repair(name, args.image, ScriptedRepairPlanner(task["correct"])) for name, task in _TASKS.items()
        ]
        controller = "SCRIPTED_NOT_LLM"
    for row in reports:
        row["controller"] = controller
    tokens_known = all(r["prompt_tokens"] is not None and r["completion_tokens"] is not None for r in reports)
    passed = sum(r["status"] == "completed" for r in reports)
    summary = {
        "suite": "synthetic_sandbox_code_repair_contract",
        "controller": controller,
        "model_id": args.model if args.model else None,
        "cases": len(reports),
        "passed": passed,
        "synthetic_model_task_pass_rate": round(passed / len(reports), 4) if args.model else None,
        "provider_prompt_tokens": (sum(r["prompt_tokens"] for r in reports) if tokens_known and args.model else None),
        "provider_completion_tokens": (
            sum(r["completion_tokens"] for r in reports) if tokens_known and args.model else None
        ),
        "total_tool_calls": sum(r["tool_calls"] for r in reports),
        "total_model_steps": sum(r["model_steps"] for r in reports),
        "real_admissions_accuracy": "NOT_MEASURED",
        "results": reports,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"code_repair_sandbox_passed={summary['passed']}/{summary['cases']}")
    return 0 if summary["passed"] == summary["cases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
