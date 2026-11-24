"""judge_router — 维度评分裁判路由。

通过 LLM 对各维度进行评分，支持异步并行评估和超时回退。
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from typing import Any

from openai import AsyncOpenAI

from config.loader import load_llm_config
from quality.judge.dimensions import build_dimension_messages
from quality.judge.scorecard import DIMENSION_WEIGHTS, DimensionScore

logger = logging.getLogger(__name__)

# ── 超时和重试常量 ──────────────────────────────────────────────────
EVALUATE_TIMEOUT_SECONDS = 15
MAX_RETRIES = 1
RETRY_DELAY_SECONDS = 1.0

# ── JSON 解析回退 ───────────────────────────────────────────────────
_DEFAULT_SCORE = 50.0
_DEFAULT_REASON = "评分解析失败"


def _parse_llm_json(text: str) -> dict[str, Any]:
    """从 LLM 回复中解析 JSON 评分结果。

    LLM 可能返回带 ```json 标记的代码块，需要提取其中的 JSON。
    """
    text = text.strip()

    # 尝试提取 ```json ... ``` 代码块
    if "```" in text:
        start = text.find("```")
        end = text.find("```", start + 3)
        if end > start:
            block = text[start + 3 : end].strip()
            # 去除可能的语言标记
            if block.startswith("json"):
                block = block[4:].strip()
            text = block

    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    # 尝试提取花括号内的内容
    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start >= 0 and brace_end > brace_start:
        try:
            result = json.loads(text[brace_start : brace_end + 1])
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

    return {}


class JudgeRouter:
    """维度评分路由器 — 调用 LLM 对各维度进行评分。"""

    def __init__(self, model_name: str | None = None) -> None:
        """初始化路由器。

        Args:
            model_name: 使用的 LLM 模型名称。为 None 时从配置加载。
        """
        self._model_name = model_name
        self._client: AsyncOpenAI | None = None

    @property
    def model_name(self) -> str:
        """当前使用的模型名称。"""
        if self._model_name:
            return self._model_name
        cfg = load_llm_config()
        return cfg.get("model", "agnes-2.0-flash")

    def _api_key_configured(self) -> bool:
        """Return True only when a real LLM API key is configured.

        The offline demo must not send user text to any LLM endpoint. Using a
        placeholder key would still issue an outbound request (and fail), so
        the judge is skipped entirely when no key is present.
        """
        return bool(str(load_llm_config().get("api_key", "")).strip())

    def _get_client(self) -> AsyncOpenAI:
        """获取或创建异步 OpenAI 客户端。"""
        if self._client is None:
            cfg = load_llm_config()
            api_key = str(cfg.get("api_key", "")).strip()
            if not api_key:
                raise RuntimeError("LLM_API_KEY is not configured; LLM judge skipped")
            self._client = AsyncOpenAI(
                api_key=api_key,
                base_url=cfg.get("base_url", "https://api.agnes.ai/v1"),
                timeout=EVALUATE_TIMEOUT_SECONDS,
                max_retries=MAX_RETRIES,
            )
        return self._client

    async def _call_llm(self, messages: list[dict[str, str]]) -> str:
        """调用 LLM 并返回回复文本。"""
        client = self._get_client()

        response = await client.chat.completions.create(
            model=self.model_name,
            messages=messages,
            temperature=0.1,  # 评分需要确定性输出
            max_tokens=500,
        )
        content = response.choices[0].message.content
        return (content or "").strip()

    async def evaluate_dimension(
        self,
        dimension: str,
        query: str,
        reply: str,
        knowledge_chunks: str | None = None,
        conversation_history: list[dict[str, str]] | None = None,
    ) -> DimensionScore:
        """评估单个维度。

        Args:
            dimension: 评估维度名称。
            query: 用户原始问题。
            reply: AI 生成的回答。
            knowledge_chunks: 知识库参考片段。
            conversation_history: 对话历史。

        Returns:
            DimensionScore 评估结果。超时或错误时返回 unscored=True。
        """
        if dimension not in DIMENSION_WEIGHTS:
            return DimensionScore(
                dimension=dimension,
                score=_DEFAULT_SCORE,
                reason=f"未知维度: {dimension}",
                unscored=True,
            )

        messages = build_dimension_messages(
            dimension=dimension,
            query=query,
            reply=reply,
            knowledge_chunks=knowledge_chunks,
            conversation_history=conversation_history,
        )

        try:
            raw = await asyncio.wait_for(
                self._call_llm(messages),
                timeout=EVALUATE_TIMEOUT_SECONDS,
            )
            parsed = _parse_llm_json(raw)

            # Invalid/missing provider output is UNGRADED, not a valid 50.
            # Also reject NaN/Inf so bad JSON cannot poison metrics.
            if "score" not in parsed:
                raise ValueError("judge returned no score")
            score = float(parsed["score"])
            if not math.isfinite(score):
                raise ValueError("judge returned non-finite score")
            score = max(0.0, min(100.0, score))  # 钳制到 0-100

            reason = str(parsed.get("reason", ""))
            issues = parsed.get("issues", [])
            if not isinstance(issues, list):
                issues = [str(issues)] if issues else []

            return DimensionScore(
                dimension=dimension,
                score=score,
                reason=reason,
                issues=[str(i) for i in issues],
            )

        except asyncio.TimeoutError:
            logger.warning("维度 %s 评估超时 (%ds)", dimension, EVALUATE_TIMEOUT_SECONDS)
            return DimensionScore(
                dimension=dimension,
                score=_DEFAULT_SCORE,
                reason=f"评估超时 ({EVALUATE_TIMEOUT_SECONDS}s)",
                unscored=True,
            )
        except Exception as exc:
            logger.warning("维度 %s 评估失败: %s", dimension, exc)
            return DimensionScore(
                dimension=dimension,
                score=_DEFAULT_SCORE,
                reason=f"评估失败: {exc}",
                unscored=True,
            )

    async def evaluate_all(
        self,
        query: str,
        reply: str,
        knowledge_chunks: str | None = None,
        conversation_history: list[dict[str, str]] | None = None,
    ) -> list[DimensionScore]:
        """并行评估所有维度。

        Args:
            query: 用户原始问题。
            reply: AI 生成的回答。
            knowledge_chunks: 知识库参考片段。
            conversation_history: 对话历史。

        Returns:
            所有维度的评估结果列表。未配置 ``LLM_API_KEY`` 时全部标记为
            ``unscored``，且不会发起任何网络请求（离线演示的 fail-closed 路径）。
        """
        if not self._api_key_configured():
            logger.info("LLM_API_KEY not configured — skipping LLM judge (no outbound request)")
            return [
                DimensionScore(
                    dimension=dim,
                    score=_DEFAULT_SCORE,
                    reason="LLM_API_KEY 未配置，跳过 LLM 评分",
                    unscored=True,
                )
                for dim in DIMENSION_WEIGHTS
            ]

        tasks = [
            self.evaluate_dimension(
                dimension=dim,
                query=query,
                reply=reply,
                knowledge_chunks=knowledge_chunks,
                conversation_history=conversation_history,
            )
            for dim in DIMENSION_WEIGHTS
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        scores: list[DimensionScore] = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                dim = list(DIMENSION_WEIGHTS.keys())[i]
                logger.warning("维度 %s 评估异常: %s", dim, result)
                scores.append(
                    DimensionScore(
                        dimension=dim,
                        score=_DEFAULT_SCORE,
                        reason=f"评估异常: {result}",
                        unscored=True,
                    )
                )
            else:
                scores.append(result)

        return scores
