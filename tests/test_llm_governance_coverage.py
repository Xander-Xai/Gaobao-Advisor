"""Coverage regression for the model call paths.

Every production model call has to pass the LLM Resource Governor. A new
call site that talks to a provider SDK directly would silently bypass the
provider budget, so the repository is scanned for such a bypass.

The scan is a guard, not a proof: it finds the obvious escape hatches. The
per-path coverage matrix in
``docs/architecture/llm-resource-governance.md`` is the authoritative list.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

#: The only module that is allowed to build a provider SDK client.
SDK_OWNER = Path("server/services/llm_gateway.py")

#: The Deep Agents modules build a LangChain chat model; the framework makes
#: the provider request, so the call is intercepted instead.
LANGCHAIN_OWNERS = (
    Path("server/agent/admissions_deepagents.py"),
    Path("server/agent/deepagents_adapter.py"),
)

#: Deprecated Streamlit mode. It is not part of the FastAPI service and it is
#: not started by any deployment entry point. It is listed as an uncovered
#: path in the coverage matrix.
EXCLUDED = (Path("legacy/agent.py"),)

#: Modules that receive their client from the caller. The caller has to
#: build a governed client, which the second test below checks.
INJECTED_CLIENT_MODULES = (
    Path("server/agent/readonly_model_planner.py"),
    Path("server/agent/decision_card_model_planner.py"),
    Path("server/agent/synthetic_replanning.py"),
    Path("benchmarks/sandbox_model_planner.py"),
)

#: Modules that build the client themselves and therefore have to use the
#: governed gateway.
SELF_BUILT_CLIENT_MODULES = (
    Path("server/graph/nodes/llm_node.py"),
    Path("server/agent/memory_manager.py"),
    Path("server/quality/chat_repair.py"),
    Path("quality/judge/judge_router.py"),
    Path("server/services/kb_retriever.py"),
    Path("server/services/voice.py"),
    Path("server/agent/admissions_analysis_sandbox.py"),
    Path("server/agent/native_taskrun_harness.py"),
    Path("benchmarks/context_ab_eval.py"),
    Path("benchmarks/decision_card_business_eval.py"),
    Path("benchmarks/readonly_model_eval.py"),
    Path("benchmarks/sandbox_coding_agent.py"),
    Path("benchmarks/synthetic_replanning_eval.py"),
)

SCANNED_ROOTS = ("server", "quality", "benchmarks", "analytics", "skills", "slots", "scripts", "db")

_SDK_IMPORT = re.compile(r"^\s*(from\s+openai\s+import|import\s+openai\b)")
_SDK_CONSTRUCTOR = re.compile(r"(?<![\w.])(AsyncOpenAI|OpenAI)\s*\(")
_GOVERNED_CLIENT = re.compile(r"governed_(async_)?(openai_)?client\(")


def _python_files() -> list[Path]:
    files: list[Path] = []
    for root in SCANNED_ROOTS:
        base = ROOT / root
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            files.append(path)
    return sorted(files)


def _relative(path: Path) -> Path:
    return path.relative_to(ROOT)


@pytest.mark.parametrize("path", _python_files(), ids=lambda p: str(_relative(p)))
def test_only_the_gateway_builds_a_provider_sdk_client(path: Path):
    """No production module may import or construct a provider SDK client."""
    relative = _relative(path)
    if relative == SDK_OWNER or relative in EXCLUDED:
        return
    source = path.read_text(encoding="utf-8")
    for line_number, line in enumerate(source.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if _SDK_IMPORT.search(line):
            pytest.fail(f"{relative}:{line_number} imports the provider SDK outside the gateway: {stripped}")
        if _SDK_CONSTRUCTOR.search(line):
            # The Deep Agents modules build a LangChain chat model, which the
            # governed chat model intercepts.
            if relative in LANGCHAIN_OWNERS and "ChatOpenAI(" in line:
                continue
            pytest.fail(f"{relative}:{line_number} builds a provider client outside the gateway: {stripped}")


@pytest.mark.parametrize("relative", SELF_BUILT_CLIENT_MODULES, ids=lambda p: str(p))
def test_a_self_built_client_comes_from_the_governed_gateway(relative: Path):
    source = (ROOT / relative).read_text(encoding="utf-8")
    assert _GOVERNED_CLIENT.search(source), f"{relative} does not use a governed client"


@pytest.mark.parametrize("relative", LANGCHAIN_OWNERS, ids=lambda p: str(p))
def test_the_framework_internal_calls_are_governed(relative: Path):
    """A LangChain chat model must be routed through the governor."""
    source = (ROOT / relative).read_text(encoding="utf-8")
    assert "govern_chat_model(" in source, f"{relative} builds a chat model that is not governed"


@pytest.mark.parametrize("relative", INJECTED_CLIENT_MODULES, ids=lambda p: str(p))
def test_an_injected_client_module_documents_its_caller(relative: Path):
    """An injected client module must not build a client itself."""
    source = (ROOT / relative).read_text(encoding="utf-8")
    assert "chat.completions.create(" in source or "embeddings.create(" in source
    assert not _SDK_CONSTRUCTOR.search(source), f"{relative} must receive its client from the caller"


def test_the_chat_node_does_not_import_the_provider_sdk():
    source = (ROOT / "server/graph/nodes/llm_node.py").read_text(encoding="utf-8")
    assert "from openai import" not in source
    assert "import openai" not in source


def test_the_governed_gateway_is_the_only_sdk_owner():
    source = (ROOT / SDK_OWNER).read_text(encoding="utf-8")
    assert "from openai import OpenAI" in source
    assert "from openai import AsyncOpenAI" in source


def test_the_shipped_coverage_matrix_lists_every_governed_path():
    matrix = ROOT / "docs/architecture/llm-resource-governance.md"
    assert matrix.is_file(), "the model call coverage matrix must exist"
    text = matrix.read_text(encoding="utf-8")
    for module in (
        "server/graph/nodes/llm_node.py",
        "server/agent/memory_manager.py",
        "server/quality/chat_repair.py",
        "quality/judge/judge_router.py",
        "server/services/kb_retriever.py",
        "server/services/voice.py",
        "server/agent/admissions_deepagents.py",
        "server/agent/deepagents_adapter.py",
        "server/agent/admissions_analysis_sandbox.py",
        "server/agent/native_taskrun_harness.py",
    ):
        assert module in text, f"{module} is missing from the coverage matrix"
    # The uncovered paths have to be stated explicitly.
    assert "legacy/agent.py" in text
