"""Harness guardrails: tool allowlist, evidence, repeat, budgets and usage."""

from types import SimpleNamespace

import pytest

from server.agent.readonly_harness import Decision, HarnessLimits, ReadOnlyTool, run_readonly_agent
from server.agent.readonly_model_planner import OpenAIJsonPlanner

SOURCE = "synthetic:school:0123456789ab"


class SequencePlanner:
    def __init__(self, decisions):
        self._decisions = iter(decisions)
        self.seen_histories = []

    def decide(self, task, history, tool_schemas):
        self.seen_histories.append(history)
        return next(self._decisions)


def demo_tool(arguments):
    return {"data_profile": "SYNTHETIC_DEMO", "source_ids": [SOURCE], "school_name": arguments["school_name"]}


def test_model_selects_two_tools_and_finishes_with_real_evidence():
    planner = SequencePlanner(
        [
            Decision("tool", "school_lookup", {"school_name": "合成 A"}, prompt_tokens=10, completion_tokens=5),
            Decision("tool", "school_lookup", {"school_name": "合成 B"}, prompt_tokens=11, completion_tokens=6),
            Decision(
                "finish",
                answer="合成演示，非真实招生数据",
                evidence_ids=[SOURCE],
                prompt_tokens=12,
                completion_tokens=7,
            ),
        ]
    )
    result = run_readonly_agent(
        "比较两所虚构学校", planner, {"school_lookup": ReadOnlyTool("school_lookup", {"school_name": str}, demo_tool)}
    )
    assert result.status == "completed"
    assert result.tool_calls == 2
    assert result.model_steps == 3
    assert result.prompt_tokens == 33
    assert result.completion_tokens == 18
    assert len(planner.seen_histories[-1]) == 2
    assert result.public_report()["tool_calls"] == 2
    assert "合成演示" not in str(result.public_report())


@pytest.mark.parametrize(
    ("decision", "reason"),
    [
        (Decision("finish", answer="凭空编造", evidence_ids=[SOURCE]), "unverified_finish"),
        (Decision("tool", "os_shell", {"command": "rm -rf /"}), "tool_not_allowlisted"),
        (Decision("tool", "school_lookup", {"school_name": "A", "extra": "bad"}), "tool_schema_invalid"),
        (Decision("tool", "school_lookup", {"school_name": ""}), "tool_schema_invalid"),
    ],
)
def test_unsafe_planner_actions_fail_closed(decision, reason):
    tools = {"school_lookup": ReadOnlyTool("school_lookup", {"school_name": str}, demo_tool)}
    result = run_readonly_agent("虚构数据", SequencePlanner([decision]), tools)
    assert result.status == "blocked"
    assert result.reason == reason
    assert result.tool_calls == 0


def test_repeated_tool_loop_stops_before_third_execution():
    planner = SequencePlanner([Decision("tool", "school_lookup", {"school_name": "A"})] * 3)
    result = run_readonly_agent(
        "虚构数据", planner, {"school_lookup": ReadOnlyTool("school_lookup", {"school_name": str}, demo_tool)}
    )
    assert result.reason == "tool_repeat_loop"
    assert result.tool_calls == 2
    assert result.repeated_calls == 2


def test_tool_budget_stops_before_second_execution():
    planner = SequencePlanner(
        [
            Decision("tool", "school_lookup", {"school_name": "A"}),
            Decision("tool", "school_lookup", {"school_name": "B"}),
        ]
    )
    result = run_readonly_agent(
        "虚构数据",
        planner,
        {"school_lookup": ReadOnlyTool("school_lookup", {"school_name": str}, demo_tool)},
        HarnessLimits(max_tool_calls=1),
    )
    assert result.reason == "tool_budget_exhausted"
    assert result.tool_calls == 1


def test_tool_exception_and_untrusted_provenance_block():
    def explode(_arguments):
        raise RuntimeError("private provider token")

    for handler, reason in [
        (explode, "tool_execution_failed"),
        (lambda _args: {"data_profile": "REAL_DATA", "source_ids": [SOURCE]}, "tool_provenance_invalid"),
        (lambda _args: {"data_profile": "SYNTHETIC_DEMO", "source_ids": ["../../escape"]}, "tool_evidence_invalid"),
    ]:
        result = run_readonly_agent(
            "虚构数据",
            SequencePlanner([Decision("tool", "school_lookup", {"school_name": "A"})]),
            {"school_lookup": ReadOnlyTool("school_lookup", {"school_name": str}, handler)},
        )
        assert result.reason == reason
        assert result.tool_failures == 1
        assert "private" not in str(result.public_report())


def test_context_compaction_maintains_bounded_history_and_unknown_tokens():
    def large_result(args):
        return {"data_profile": "SYNTHETIC_DEMO", "source_ids": [SOURCE], "content": args["school_name"] * 400}

    planner = SequencePlanner(
        [
            Decision("tool", "school_lookup", {"school_name": "A"}),
            Decision("tool", "school_lookup", {"school_name": "B"}),
            Decision("tool", "school_lookup", {"school_name": "C"}),
            Decision("finish", answer="安全结束", evidence_ids=[SOURCE]),
        ]
    )
    result = run_readonly_agent(
        "虚构数据",
        planner,
        {"school_lookup": ReadOnlyTool("school_lookup", {"school_name": str}, large_result)},
        HarnessLimits(max_context_chars=1000, max_observation_chars=450),
    )
    assert result.status == "completed"
    assert result.history_evictions >= 1
    assert result.max_history_chars <= 1000
    assert result.prompt_tokens is None
    assert result.completion_tokens is None


def test_no_tools_except_allowlisted_and_stop_at_step_budget():
    planner = SequencePlanner([Decision("tool", "school_lookup", {"school_name": str(i)}) for i in range(8)])
    result = run_readonly_agent(
        "虚构数据",
        planner,
        {"school_lookup": ReadOnlyTool("school_lookup", {"school_name": str}, demo_tool)},
        HarnessLimits(max_model_steps=2, max_tool_calls=5),
    )
    assert result.status == "blocked"
    assert result.reason == "step_budget_exhausted"
    assert result.model_steps == 2


def test_opt_in_model_planner_parses_action_and_provider_usage():
    class Chat:
        def __init__(self):
            self.kwargs = None

        def create(self, **kwargs):
            self.kwargs = kwargs
            choice = SimpleNamespace(
                message=SimpleNamespace(
                    content='{"kind":"tool","tool":"school_lookup","arguments":{"school_name":"合成 A"}}'
                )
            )
            return SimpleNamespace(choices=[choice], usage=SimpleNamespace(prompt_tokens=8, completion_tokens=4))

    chat = Chat()
    client = SimpleNamespace(chat=SimpleNamespace(completions=chat))
    selected = OpenAIJsonPlanner(client, "synthetic-test-model").decide(
        "只读查询", [], {"school_lookup": {"school_name": "str"}}
    )
    assert selected.tool == "school_lookup"
    assert selected.prompt_tokens == 8
    assert selected.completion_tokens == 4
    assert chat.kwargs["max_tokens"] == 384
    assert chat.kwargs["response_format"] == {"type": "json_object"}
