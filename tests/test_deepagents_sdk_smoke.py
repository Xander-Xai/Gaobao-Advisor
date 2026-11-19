"""Actual installed Deep Agents SDK construction, without model API calls."""

from __future__ import annotations

import pytest

pytest.importorskip("deepagents")
pytest.importorskip("langchain_openai")


def test_official_sdk_can_construct_restricted_graph_without_shell_or_subagent(monkeypatch):
    from deepagents import (
        GeneralPurposeSubagentProfile,
        HarnessProfile,
        create_deep_agent,
        register_harness_profile,
    )
    from deepagents.backends import StateBackend
    from deepagents.middleware import FilesystemMiddleware
    from langchain.agents.middleware import ModelCallLimitMiddleware
    from langgraph.checkpoint.memory import InMemorySaver

    # A synthetically invented CI key is used only to construct the SDK client,
    # never to invoke a network API request.
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-sdk-configuration-only-" + "z" * 24)
    model = "openai:synthetic-ci-no-call"
    register_harness_profile(
        model,
        HarnessProfile(
            excluded_tools=frozenset(
                {"ls", "glob", "grep", "write_file", "edit_file", "delete", "execute", "task", "write_todos"}
            ),
            general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
        ),
    )

    def research_approved_school(school_name: str) -> str:
        """Lookup only a fictitious school."""
        return "synthetic:school:0123456789ab"

    backend = StateBackend()
    graph = create_deep_agent(
        model=model,
        tools=[research_approved_school],
        system_prompt="Test-only: no network, filesystem writes or model calls.",
        backend=backend,
        middleware=[
            FilesystemMiddleware(backend=backend, tools=["read_file"]),
            ModelCallLimitMiddleware(run_limit=9, exit_behavior="end"),
        ],
        skills=["/skills/"],
        checkpointer=InMemorySaver(),
        subagents=[],
    )
    assert callable(graph.invoke)
    assert "tools" in graph.get_graph().nodes
