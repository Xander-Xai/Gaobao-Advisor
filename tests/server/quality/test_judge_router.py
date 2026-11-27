"""Regression tests for the LLM judge's no-key path.

The offline demo promises that no API key is required. A placeholder key must
never be used to issue an outbound request carrying the user's text: when no
``LLM_API_KEY`` is configured the judge must skip evaluation entirely.
"""

from __future__ import annotations

import pytest

from quality.judge import judge_router
from quality.judge.scorecard import DIMENSION_WEIGHTS


class TestJudgeNoKeyFailClosed:
    @pytest.mark.asyncio
    async def test_evaluate_all_skips_without_key(self, monkeypatch) -> None:
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        monkeypatch.delenv("LLM_PROVIDER", raising=False)

        def _must_not_construct(*args, **kwargs):
            raise AssertionError("judge must not construct an LLM client without an API key")

        monkeypatch.setattr(judge_router, "governed_async_client", _must_not_construct)

        router = judge_router.JudgeRouter()
        scores = await router.evaluate_all("query", "reply")

        assert len(scores) == len(DIMENSION_WEIGHTS)
        assert all(s.unscored for s in scores)
        assert all("LLM_API_KEY" in s.reason for s in scores)

    @pytest.mark.asyncio
    async def test_evaluate_dimension_skips_without_key(self, monkeypatch) -> None:
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        monkeypatch.delenv("LLM_PROVIDER", raising=False)

        def _must_not_construct(*args, **kwargs):
            raise AssertionError("judge must not construct an LLM client without an API key")

        monkeypatch.setattr(judge_router, "governed_async_client", _must_not_construct)

        score = await judge_router.JudgeRouter().evaluate_dimension("factual", "query", "reply")

        assert score.unscored is True
        assert score.reason
