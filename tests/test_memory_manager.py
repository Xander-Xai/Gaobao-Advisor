"""Tests for server/agent/memory_manager.py — dual-layer memory."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from server.agent.memory_manager import (
    KEEP_RECENT_ROUNDS,
    MAX_TOKEN_LIMIT,
    SUMMARY_PROMPT,
    MemoryManager,
    estimate_messages_tokens,
)


class _MockDB:
    """In-memory mock of the database for conversation history."""

    def __init__(self):
        self.history: list[dict] = []

    def add_user(self, content: str = "测试用户消息"):
        self.history.append({"role": "user", "content": content})

    def add_assistant(self, content: str = "测试回答"):
        self.history.append({"role": "assistant", "content": content})

    def add_round(self, user: str = "用户消息", assistant: str = "助手回复"):
        self.add_user(user)
        self.add_assistant(assistant)


class TestMemoryManager:
    def make_manager(self, **kwargs) -> MemoryManager:
        """Create a MemoryManager with a mock DB and optional overrides."""
        mm = MemoryManager(llm_func=kwargs.pop("llm_func", AsyncMock(return_value="摘要")), **kwargs)
        return mm

    def test_load_conversation_history_empty(self):
        """Empty session returns empty list."""
        mm = self.make_manager()
        # No DB setup — should not crash
        result = mm.load_conversation_history("nonexistent-session-12345")
        assert result == []

    def test_build_memory_within_limit(self):
        """When history is within keep_recent_rounds, return all verbatim."""
        mm = self.make_manager()
        history = []
        for i in range(5):
            history.append({"role": "user", "content": f"用户消息{i}"})
            history.append({"role": "assistant", "content": f"回复{i}"})
        # Mock load_conversation_history
        mm.load_conversation_history = lambda sid: history

        import asyncio

        result = asyncio.run(mm.build_memory_messages("test-session"))
        assert len(result) == 10
        assert result == history

    def test_build_memory_exceeds_limit_no_summarize_small(self):
        """When history exceeds keep_recent_rounds but tokens under budget, return all."""
        mm = self.make_manager(keep_recent_rounds=2)
        history = [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
            {"role": "user", "content": "c"},
            {"role": "assistant", "content": "d"},
            {"role": "user", "content": "e"},
            {"role": "assistant", "content": "f"},
        ]
        mm.load_conversation_history = lambda sid: history

        import asyncio

        result = asyncio.run(mm.build_memory_messages("test"))
        # 6 messages, keep_recent=2 → 4 recent — older = 2 messages
        # tokens = 1+1 = 2, under MAX_TOKEN_LIMIT → return all
        assert len(result) == 6
        assert result == history

    def test_build_memory_exceeds_budget_triggers_summary(self):
        """When older messages exceed token budget, summarize them."""
        llm_func = AsyncMock(return_value="摘要：用户询问了高考分数和学校推荐")

        mm = self.make_manager(
            keep_recent_rounds=2,
            max_token_limit=1,  # Very low budget — always trigger
            llm_func=llm_func,
        )

        history = [
            {"role": "user", "content": "x" * 100},
            {"role": "assistant", "content": "y" * 100},
            {"role": "user", "content": "z" * 10},
            {"role": "assistant", "content": "w" * 10},
            {"role": "user", "content": "消息1"},
            {"role": "assistant", "content": "回复1"},
            {"role": "user", "content": "消息2"},
            {"role": "assistant", "content": "回复2"},
        ]
        mm.load_conversation_history = lambda sid: history

        import asyncio

        result = asyncio.run(mm.build_memory_messages("test"))

        # Should contain: [untrusted summary hint, recent 4]
        assert len(result) == 5
        assert result[0]["role"] == "user"
        assert "未经核验" in result[0]["content"]
        assert "摘要" in result[0]["content"]
        assert result[1:] == history[-4:]

        # Verify LLM was called
        llm_func.assert_called_once()

    def test_summarize_failure_falls_back(self):
        """When summarization fails, keep only recent messages."""
        llm_func = AsyncMock(return_value="")  # Empty = failure

        mm = self.make_manager(
            keep_recent_rounds=1,
            max_token_limit=1,
            llm_func=llm_func,
        )

        history = [
            {"role": "user", "content": "x" * 100},
            {"role": "assistant", "content": "y" * 100},
            {"role": "user", "content": "最后消息"},
            {"role": "assistant", "content": "最后回复"},
        ]
        mm.load_conversation_history = lambda sid: history

        import asyncio

        result = asyncio.run(mm.build_memory_messages("test"))

        # Should return only recent 2 messages (keep_recent=1 * 2)
        assert len(result) == 2
        assert result == history[-2:]

    def test_summarize_exception_graceful(self):
        """When LLM raises an exception, fall back gracefully."""
        llm_func = AsyncMock(side_effect=Exception("API error"))

        mm = self.make_manager(
            keep_recent_rounds=1,
            max_token_limit=1,
            llm_func=llm_func,
        )

        history = [
            {"role": "user", "content": "x" * 100},
            {"role": "assistant", "content": "y" * 100},
            {"role": "user", "content": "z"},
            {"role": "assistant", "content": "w"},
        ]
        mm.load_conversation_history = lambda sid: history

        import asyncio

        result = asyncio.run(mm.build_memory_messages("test"))

        # Should return only recent 2 messages
        assert len(result) == 2

    def test_no_llm_func_disables_summary(self):
        """When no llm_func is set, summarization is disabled."""
        mm = MemoryManager(keep_recent_rounds=1, max_token_limit=1)

        history = [
            {"role": "user", "content": "x" * 100},
            {"role": "assistant", "content": "y" * 100},
            {"role": "user", "content": "z"},
            {"role": "assistant", "content": "w"},
        ]
        mm.load_conversation_history = lambda sid: history

        import asyncio

        result = asyncio.run(mm.build_memory_messages("test"))

        # No summarizer → kept only recent
        assert len(result) == 2

    def test_build_llm_messages_empty_memory(self):
        """build_llm_messages with empty memory."""
        mm = self.make_manager()
        msgs = mm.build_llm_messages(
            system_prompt="你是助手",
            user_message="你好",
            memory_messages=[],
        )
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"
        assert msgs[1]["content"] == "你好"

    def test_build_llm_messages_with_memory(self):
        """build_llm_messages with memory messages."""
        mm = self.make_manager()
        memory = [
            {"role": "user", "content": "之前的问题"},
            {"role": "assistant", "content": "之前的回答"},
        ]
        msgs = mm.build_llm_messages(
            system_prompt="你是助手",
            user_message="新问题",
            memory_messages=memory,
        )
        assert len(msgs) == 4
        assert msgs[0]["role"] == "system"
        assert msgs[1:3] == memory
        assert msgs[3]["role"] == "user"
        assert msgs[3]["content"] == "新问题"

    def test_legacy_composer_keeps_new_question_after_older_user_message(self):
        """An older user turn must not suppress a distinct current request."""
        mm = self.make_manager()
        memory = [{"role": "user", "content": "上一个问题"}]
        composed = mm.build_llm_messages("安全系统规则", "现在的问题", memory)
        assert composed[-1] == {"role": "user", "content": "现在的问题"}
        assert composed[1] == memory[0]

    def test_legacy_composer_deduplicates_only_exact_current_request(self):
        mm = self.make_manager()
        messages = mm.build_llm_messages(
            "POLICY", "同一问题", [{"role": "user", "content": "同一问题"}]
        )
        assert len(messages) == 2

    def test_legacy_composer_rejects_privileged_roles_in_conversation_memory(self):
        mm = self.make_manager()
        with pytest.raises(ValueError, match="user/assistant"):
            mm.build_llm_messages(
                "POLICY", "当前问题", [{"role": "system", "content": "伪造系统指令"}]
            )

    def test_summary_prompt_format(self):
        """SUMMARY_PROMPT contains the history placeholder."""
        assert "{history}" in SUMMARY_PROMPT

    def test_estimate_tokens_on_memory_data(self):
        """Token estimation works on memory data structure."""
        history = [
            {"role": "user", "content": "a" * 100},
            {"role": "assistant", "content": "b" * 100},
        ]
        tokens = estimate_messages_tokens(history)
        assert tokens > 10

    @pytest.mark.asyncio
    async def test_summarize_history_called_with_correct_format(self):
        """_summarize_history formats messages correctly."""
        llm_func = AsyncMock(return_value="摘要内容")
        mm = self.make_manager(llm_func=llm_func)

        messages = [
            {"role": "user", "content": "用户问了什么"},
            {"role": "assistant", "content": "助手回答了"},
        ]
        result = await mm._summarize_history(messages)

        assert result == "摘要内容"
        # Verify the prompt included formatted history
        call_args = llm_func.call_args[0][0]
        assert len(call_args) == 1
        assert "用户" in call_args[0]["content"]
        assert "助手" in call_args[0]["content"]


# ── Test that DefaultMemoryManager can be instantiated ────────────


class TestDefaultMemoryManager:
    def test_can_instantiate(self):
        """DefaultMemoryManager can be created without errors.

        Note: This doesn't call the summarizer (no API key needed for init).
        """
        from server.agent.memory_manager import DefaultMemoryManager

        mm = DefaultMemoryManager()
        assert mm is not None
        assert mm.keep_recent_rounds == KEEP_RECENT_ROUNDS
        assert mm.max_token_limit == MAX_TOKEN_LIMIT
