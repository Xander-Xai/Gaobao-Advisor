"""Business research uses actual SQL and compiled subgraphs, no provider calls."""

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from db.models import AdmissionScore, EnrollmentPlan
from server.agent import admissions_deepagents as research

SCOPE = research.ResearchScope((1, 2), 3, "广东", "物理", "本科批", 2024, 2024)


@pytest.fixture
def sessions():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    AdmissionScore.__table__.create(engine)
    EnrollmentPlan.__table__.create(engine)
    with Session(engine) as session:
        for school in (1, 2, 9):
            session.add(
                AdmissionScore(
                    school_id=school,
                    major_id=3,
                    province="广东",
                    subject_type="物理",
                    batch="本科批",
                    year=2024,
                    min_score=600,
                    min_rank=12345,
                )
            )
            session.add(
                EnrollmentPlan(
                    school_id=school,
                    major_id=3,
                    province="广东",
                    batch="本科批",
                    year=2024,
                    plan_count=10,
                    subject_requirement="物理必选",
                )
            )
        session.commit()
    yield lambda: Session(engine)
    engine.dispose()


def controller(*, submit=True, mutate=None):
    def factory(model_id, tools, subagents):
        assert model_id == "openai:test-model"
        assert len(tools) == 1 and tools[0].__name__ == "submit_research"
        assert {a["name"] for a in subagents} == {"admission-history", "enrollment-plans"}

        class Agent:
            def invoke(self, state, **kwargs):
                assert "广东" not in json.dumps(state, ensure_ascii=False)
                received = {}
                for a in subagents:
                    outcome = a["runnable"].invoke({"messages": [{"role": "user", "content": "research"}]})
                    value = json.loads(outcome["messages"][-1].content)
                    assert set(value) == {
                        "kind",
                        "receipt",
                        "row_count",
                        "missing_count",
                        "ambiguous_count",
                        "incomplete_count",
                    }
                    assert "12345" not in json.dumps(value)
                    received[a["name"]] = value["receipt"]
                if submit:
                    tools[0](received["admission-history"], received["enrollment-plans"])
                if mutate:
                    mutate()
                return {"messages": []}

        return Agent()

    return factory


def run(sessions, factory):
    return research.run_admissions_research(
        SCOPE,
        sessions,
        "openai:test-model",
        approval_ref="local-approval",
        authorized=True,
        model_authorized=True,
        agent_factory=factory,
    )


def test_two_real_sql_subgraphs_and_verified_host_report(sessions):
    receipt = run(sessions, controller())
    assert receipt["status"] == "VERIFIED_RESEARCH"
    assert receipt["engine"] == "injected_test_controller"
    assert receipt["admission_recommendation_verified"] is False
    for records in receipt["evidence"].values():
        assert {r["school_id"] for r in records} == {1, 2}


def test_no_submitted_receipt_cannot_publish(sessions):
    receipt = run(sessions, controller(submit=False))
    assert receipt["status"] == "BLOCKED"
    assert "evidence" not in receipt


def test_source_change_after_delegation_blocks(sessions):
    def mutate():
        with sessions() as session:
            row = session.query(AdmissionScore).filter_by(school_id=1).one()
            row.min_score = 610
            session.commit()

    receipt = run(sessions, controller(mutate=mutate))
    assert receipt["error"] == "source_changed"
    assert "evidence" not in receipt


def test_missing_plan_is_explicit_partial(sessions):
    with sessions() as session:
        session.query(EnrollmentPlan).filter_by(school_id=2).delete()
        session.commit()
    receipt = run(sessions, controller())
    assert receipt["status"] == "VERIFIED_PARTIAL_RESEARCH"
    assert receipt["coverage"]["enrollment-plans"]["missing"] == [[2, 2024]]


def test_requires_model_consent(sessions):
    with pytest.raises(ValueError, match="authorization"):
        research.run_admissions_research(
            SCOPE,
            sessions,
            "openai:test-model",
            approval_ref="local-approval",
            authorized=True,
        )


def test_actual_sdk_builds_specialized_delegation_graph(monkeypatch):
    pytest.importorskip("deepagents")
    pytest.importorskip("langchain_openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sdk-construction-test-only")

    def submit_research(history_receipt: str, plans_receipt: str) -> str:
        """Submit research receipts."""
        return "ok"

    specs = [
        {
            "name": name,
            "description": f"Research {name}",
            "runnable": research.build_research_subgraph(name, lambda kind: kind),
        }
        for name in ("admission-history", "enrollment-plans")
    ]
    graph = research.build_deep_agent("openai:research-sdk-no-call", [submit_research], specs)
    assert callable(graph.invoke)
    assert "tools" in graph.get_graph().nodes


def test_actual_sdk_task_and_todos_with_scripted_model(sessions, monkeypatch):
    """Real Deep Agents tool dispatch; only the provider transport is stubbed."""
    import re

    pytest.importorskip("deepagents")
    pytest.importorskip("langchain_openai")
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langchain_openai import ChatOpenAI

    monkeypatch.setenv("OPENAI_API_KEY", "sdk-dispatch-test-only")
    requested = []

    def generate(self, messages, stop=None, run_manager=None, **kwargs):
        index = len(requested)
        requested.append(index)
        if index == 0:
            name = "write_todos"
            args = {"todos": [{"content": "Research history and plans", "status": "in_progress"}]}
        elif index in (1, 2):
            name = "task"
            args = {
                "subagent_type": ("admission-history", "enrollment-plans")[index - 1],
                "description": "Read the approved SQL scope and return the receipt.",
            }
        elif index == 3:
            name = "submit_research"
            ids = []
            for message in messages:
                if getattr(message, "type", "") == "tool" and getattr(message, "name", "") == "task":
                    matches = re.findall(r'"receipt"\s*:\s*"([0-9a-f]{64})"', str(message.content))
                    ids.extend(matches)
            assert len(ids) == 2
            args = {"history_receipt": ids[0], "plans_receipt": ids[1]}
        else:
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="Research finished"))])
        message = AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"call_{index}"}])
        return ChatResult(generations=[ChatGeneration(message=message)])

    monkeypatch.setattr(ChatOpenAI, "_generate", generate)
    receipt = research.run_admissions_research(
        SCOPE,
        sessions,
        "openai:research-sdk-dispatch-no-call",
        approval_ref="local-approval",
        authorized=True,
        model_authorized=True,
    )
    assert receipt["status"] == "VERIFIED_RESEARCH", receipt
    assert receipt["delegated_dimensions"] == ["admission-history", "enrollment-plans"]
    assert len(requested) == 5
    assert receipt["provider_billing_verified"] is False
