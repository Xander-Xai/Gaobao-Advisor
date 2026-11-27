#!/usr/bin/env python3
"""
轻量知识检索引擎 — 向量 + 关键词混合检索。

支持 6 个知识组的按需检索和语录语义匹配，
通过环境变量 ENABLE_RAG_KB 控制开关。
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from math import log
from typing import Any

logger = logging.getLogger(__name__)

import numpy as np  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_ASCII_WORD_RE = re.compile(r"[a-z0-9]+")

# Function-word characters that make Chinese bigrams noisy for retrieval.
# Bigrams containing any of these are dropped (e.g. "的关", "与暗", "什么").
_STOP_CHARS = set("的了着过吗呢吧啊呀哦嗯是在和与及或我你他她它这那什么怎请就都也还又很太更最把被让给对")


def tokenize(text: str) -> list[str]:
    """Tokenize mixed Chinese/ASCII text for BM25.

    Chinese text is tokenized into adjacent character bigrams (which behave far
    better than unigrams for retrieval while staying dependency-free), and
    ASCII runs are lowercased word tokens. Bigrams made of function-word
    characters are dropped so that generic strings such as "的关系" do not
    masquerade as evidence.
    """
    text = (text or "").lower()
    tokens: list[str] = _ASCII_WORD_RE.findall(text)
    chars = _CJK_RE.findall(text)
    for i in range(len(chars) - 1):
        a, b = chars[i], chars[i + 1]
        if a in _STOP_CHARS or b in _STOP_CHARS:
            continue
        tokens.append(a + b)
    return tokens


class BM25:
    """Minimal BM25 (Okapi) scorer over a small in-memory corpus.

    Used as the always-available keyword retrieval path. It requires no API
    key, no network and no model download, which is what makes the default
    synthetic demo work offline.
    """

    def __init__(self, documents: list[str], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self._tf: list[Counter[str]] = [Counter(tokenize(d)) for d in documents]
        self._len = [sum(c.values()) for c in self._tf]
        self._avgdl = (sum(self._len) / len(self._len)) if self._len else 0.0
        self._df: Counter[str] = Counter()
        for counter in self._tf:
            self._df.update(counter.keys())
        self._n = len(documents)

    @property
    def size(self) -> int:
        return self._n

    def idf(self, term: str) -> float:
        df = self._df.get(term, 0)
        return log(1 + (self._n - df + 0.5) / (df + 0.5))

    def score(self, query: str) -> list[float]:
        """Return a BM25 score per document, in corpus order."""
        scores = [0.0] * self._n
        if self._n == 0 or not self._avgdl:
            return scores
        for term in set(tokenize(query)):
            df = self._df.get(term, 0)
            if df == 0:
                continue
            idf = self.idf(term)
            for i, counter in enumerate(self._tf):
                f = counter.get(term, 0)
                if not f:
                    continue
                denom = f + self.k1 * (1 - self.b + self.b * self._len[i] / self._avgdl)
                scores[i] += idf * (f * (self.k1 + 1)) / denom
        return scores

    def matched_terms(self, query: str, doc_index: int) -> int:
        """Number of distinct query terms that occur in a given document."""
        return len(self.matched_set(query, doc_index))

    def matched_set(self, query: str, doc_index: int) -> set[str]:
        """Distinct query terms that occur in a given document."""
        terms = set(tokenize(query))
        counter = self._tf[doc_index]
        return {t for t in terms if t in counter}

    def query_idf_mass(self, query: str) -> float:
        """Sum of idf over distinct *in-vocabulary* query terms.

        Out-of-vocabulary bigrams are excluded: natural-language queries almost
        always contain segmentation bigrams absent from any small corpus, and
        counting them would cap coverage below any usable threshold. Coverage
        therefore measures "of the query terms the corpus knows, how much mass
        does this document match".
        """
        return sum(self.idf(t) for t in set(tokenize(query)) if self._df.get(t, 0) > 0)


@dataclass
class Chunk:
    """一个语义完整的知识片段。"""

    text: str
    group_id: str
    embedding: np.ndarray | None = None
    start_line: int = 0
    section_title: str = ""
    chunk_id: str = ""

    @property
    def citation_label(self) -> str:
        """Stable, human-readable source marker for citation in answers."""
        section = self.section_title or self.group_id
        return f"{self.group_id} § {section}"


@dataclass
class QuoteEntry:
    """一条语录及其元数据。"""

    id: str
    text: str
    major: str  # 所属专业关键词（JSON key）
    tags: list[str] = field(default_factory=list)
    category: str = ""
    sentiment: str = ""
    embedding: np.ndarray | None = None
    source: str = ""  # 出处（节目/直播/讲座）
    year: int = 0  # 发表年份（0=长期/未知）


@dataclass
class RetrievalResult:
    """检索结果。"""

    groups: list[str]  # 选中的知识组 ID 列表
    group_chunks: list[Chunk] = field(default_factory=list)
    quotes: list[QuoteEntry] = field(default_factory=list)
    has_evidence: bool = False
    citations: list[dict[str, Any]] = field(default_factory=list)


GROUP_TRIGGERS: dict[str, list[str]] = {
    "G1_core_method": [
        "志愿",
        "填报",
        "冲稳保",
        "冲一冲",
        "稳一稳",
        "保一保",
        "位次",
        "投档",
        "滑档",
        "退档",
        "灵魂拷问",
        "咨询风格",
    ],
    "G2_major_school": [
        "专业",
        "学校",
        "985",
        "211",
        "双一流",
        "院校",
        "学科评估",
        "天坑",
        "推荐专业",
        "就业率",
        "薪资",
        "转专业",
        "选专业",
        "报学校",
    ],
    "G3_career_future": [
        "就业",
        "前景",
        "AI",
        "人工智能",
        "大模型",
        "毕业",
        "出路",
        "行业",
        "趋势",
        "市场",
        "薪资",
        "体制内",
        "考公",
        "考编",
    ],
    "G4_life_planning": [
        "城市",
        "地域",
        "北上广",
        "专科",
        "高中规划",
        "选科",
        "新高考",
        "实习",
        "发展空间",
    ],
    "G5_data_format": [
        "数据",
        "可信度",
        "格式",
        "模板",
    ],
    "G6_quick_ref": [
        "速查",
        "一览",
        "对照",
        "快速",
    ],
    "G7_employment_paths": [
        "教师",
        "医生",
        "公务员",
        "考公",
        "考编",
        "国企",
        "央企",
        "电网",
        "铁路",
        "烟草",
        "军工",
        "航天",
        "银行",
        "编制",
        "稳定就业",
        "铁饭碗",
        "央国企",
        "石油",
        "就业路径",
    ],
    "G8_graduate_and_vocational": [
        "考研",
        "研究生",
        "专硕",
        "学硕",
        "研招",
        "二战",
        "读研",
        "专科",
        "双高",
        "高职",
        "专升本",
        "升本率",
    ],
    # PUBLIC DEMO: the G9 persona-methodology group is NOT redistributed here
    # (it reproduces a named individual's methodology and speech patterns).
    # Its trigger list is removed along with it so the name is not retained in
    # source. With no corpus shipped this map is documentation only.
}


# PUBLIC DEMO: the persona-quote trigger list is not redistributed here.
# It only ever boosted quotes from an excluded corpus, so it is removed
# together with that corpus.
_ZX_TRIGGERS: list[str] = []


def split_group(content: str, group_id: str) -> list[Chunk]:
    """按 ## 标题切分知识组文件为 chunks。"""
    chunks: list[Chunk] = []
    current_text = ""
    current_title = ""
    start_line = 1

    def _flush(text: str, title: str, line: int) -> None:
        stripped = text.strip()
        if not stripped:
            return
        chunks.append(
            Chunk(
                text=stripped,
                group_id=group_id,
                start_line=line,
                section_title=title,
                chunk_id=f"{group_id}#L{line}",
            )
        )

    for i, line in enumerate(content.split("\n"), start=1):
        if line.startswith("## "):
            _flush(current_text, current_title, start_line)
            current_title = line[3:].strip()
            current_text = line + "\n"
            start_line = i
        else:
            current_text += line + "\n"

    _flush(current_text, current_title, start_line)

    return chunks


def load_all_groups(groups_dir: str) -> dict[str, list[Chunk]]:
    """加载所有知识组文件，切分为 chunks。

    A missing or unreadable directory yields an empty mapping rather than
    raising: construction must not crash a request path. Callers that need to
    distinguish "no corpus" from "nothing matched" should consult
    ``server.services.rag.status()``, which is the authoritative signal.
    """
    all_groups: dict[str, list[Chunk]] = {}
    if not groups_dir or not os.path.isdir(groups_dir):
        return all_groups
    for filename in sorted(os.listdir(groups_dir)):
        if not filename.endswith(".md"):
            continue
        group_id = filename.removesuffix(".md")
        filepath = os.path.join(groups_dir, filename)
        with open(filepath, encoding="utf-8") as f:
            content = f.read()
        all_groups[group_id] = split_group(content, group_id)
    return all_groups


def keyword_match_score(user_msg: str, triggers: list[str]) -> float:
    """计算用户消息与触发词列表的匹配分数（0.0 ~ 1.0）。"""
    if not triggers:
        return 0.0
    hits = sum(1 for t in triggers if t.lower() in user_msg.lower())
    if hits == 0:
        return 0.0
    elif hits == 1:
        return 0.4
    elif hits == 2:
        return 0.7
    else:
        return 1.0


def keyword_exact_match(user_msg: str, keyword: str) -> bool:
    """精确子串匹配。"""
    return keyword.lower() in user_msg.lower()


class EmbeddingProvider:
    """Embedding API 的抽象接口。"""

    def embed(self, texts: list[str]) -> list[np.ndarray]:
        raise NotImplementedError


class OpenAIEmbedding(EmbeddingProvider):
    """Embedding API 的抽象接口 (OpenAI-compatible, governed)."""

    def __init__(
        self,
        model: str = "text-embedding-3-small",
        api_key: str | None = None,
        base_url: str | None = None,
        provider: str = "openai",
    ):
        from server.services.llm_gateway import governed_client

        self._provider = provider
        self._model = model
        self._client = governed_client(
            provider,
            model=model,
            operation="embedding",
            config_override={
                provider: {
                    "api_key": api_key,
                    "base_url": base_url,
                    "model": model,
                }
            },
        )

    def embed(self, texts: list[str]) -> list[np.ndarray]:
        if not texts:
            return []
        all_embeddings: list[np.ndarray] = []
        # Batch size 32: SiliconFlow (and most OpenAI-compatible APIs) cap
        # the input array length at 32 per request. Every batch is a separate
        # provider request and therefore a separate governor admission.
        batch_size = 32
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            resp = self._client.embeddings.create(model=self._model, input=batch)
            all_embeddings.extend(np.array(item.embedding, dtype=np.float32) for item in resp.data)
        return all_embeddings


class OllamaEmbedding(EmbeddingProvider):
    """通过 Ollama 本地 API 计算 embedding。"""

    def __init__(self, model: str = "bge-m3", base_url: str = "http://localhost:11434"):
        import urllib.request as _req

        self._base_url = base_url.rstrip("/")
        self._model = model
        self._req = _req

    def embed(self, texts: list[str]) -> list[np.ndarray]:
        if not texts:
            return []
        results: list[np.ndarray] = []
        batch_size = 200
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            payload = json.dumps({"model": self._model, "input": batch}).encode()
            req = self._req.Request(
                f"{self._base_url}/api/embed",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with self._req.urlopen(req) as resp:
                data = json.loads(resp.read())
            for emb in data["embeddings"]:
                results.append(np.array(emb, dtype=np.float32))
        return results


class KeywordOnlyEmbedding(EmbeddingProvider):
    """纯关键词模式（降级用）— 返回全零向量。"""

    def __init__(self, dim: int = 1536):
        self._dim = dim

    def embed(self, texts: list[str]) -> list[np.ndarray]:
        return [np.zeros(self._dim, dtype=np.float32) for _ in range(len(texts))]


def create_embedding_provider(provider: str = "openai", model: str | None = None, **kwargs: Any) -> EmbeddingProvider:
    """工厂函数：根据 provider 名称创建 embedding 提供者。"""
    if provider == "openai":
        return OpenAIEmbedding(
            model=model or "text-embedding-3-small",
            api_key=kwargs.get("api_key") or os.getenv("OPENAI_API_KEY"),
            base_url=kwargs.get("base_url"),
            provider="openai",
        )
    elif provider == "dashscope":
        return OpenAIEmbedding(
            model=model or "text-embedding-v3",
            api_key=kwargs.get("api_key") or os.getenv("DASHSCOPE_API_KEY"),
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            provider="dashscope",
        )
    elif provider == "siliconflow":
        return OpenAIEmbedding(
            model=model or "BAAI/bge-large-zh-v1.5",
            api_key=kwargs.get("api_key") or os.getenv("SILICONFLOW_API_KEY"),
            base_url=kwargs.get("base_url", "https://api.siliconflow.cn/v1"),
            provider="siliconflow",
        )
    elif provider == "ollama":
        return OllamaEmbedding(
            model=model or "bge-m3",
            base_url=kwargs.get("base_url", "http://localhost:11434"),
        )
    else:
        return KeywordOnlyEmbedding()


class KbRetriever:
    """轻量知识检索引擎。"""

    def __init__(
        self,
        groups_dir: str,
        quotes_path: str,
        embedding_provider: EmbeddingProvider | None = None,
        embedding_model: str = "",
        vector_weight: float = 0.6,
        keyword_weight: float = 0.4,
        group_threshold: float = 0.3,
        max_groups: int = 2,
        max_quotes: int = 3,
        max_chunks: int = 5,
        min_keyword_score: float = 0.8,
        min_dense_score: float = 0.35,
        min_coverage: float = 0.2,
    ):
        self._vector_weight = vector_weight
        self._keyword_weight = keyword_weight
        self._group_threshold = group_threshold
        self._max_groups = max_groups
        self._max_quotes = max_quotes
        self._max_chunks = max_chunks
        self._min_keyword_score = min_keyword_score
        self._min_dense_score = min_dense_score
        self._min_coverage = min_coverage
        self._embedder = embedding_provider or KeywordOnlyEmbedding()
        self._groups = load_all_groups(groups_dir)
        self._quotes = self._load_quotes(quotes_path)

        # Flatten chunks into a single index so BM25 operates over one corpus,
        # and remember which flat indices belong to each group.
        self._flat_chunks: list[Chunk] = []
        self._group_indices: dict[str, list[int]] = {}
        for group_id, chunks in self._groups.items():
            self._group_indices[group_id] = []
            for chunk in chunks:
                self._group_indices[group_id].append(len(self._flat_chunks))
                self._flat_chunks.append(chunk)
        self._bm25 = BM25([c.text for c in self._flat_chunks])

        self._init_embeddings()

    def _load_quotes(self, quotes_path: str) -> list[QuoteEntry]:
        """Load the attributed-quote index, if one is configured.

        PUBLIC DEMO: the upstream quote corpus was quotations attributed to a
        named individual and is not redistributed. Any caller supplying a
        ``quotes_path`` must supply a corpus they have the right to ship; the
        caller is responsible for that, and ``rag.status()`` is what makes a
        missing corpus visible rather than silent.
        """
        quotes: list[QuoteEntry] = []
        if not quotes_path or not os.path.isdir(quotes_path):
            return quotes

        # 主语录库（按专业索引）
        index_path = os.path.join(quotes_path, "_by_major.json")
        if os.path.exists(index_path):
            with open(index_path, encoding="utf-8") as f:
                raw_index: dict = json.load(f)
            for major_key, quote_list in raw_index.items():
                for q in quote_list:
                    quotes.append(
                        QuoteEntry(
                            id=q.get("id", ""),
                            text=q["text"],
                            major=major_key,
                            tags=q.get("tags", []),
                            category=q.get("category", ""),
                            sentiment=q.get("sentiment", ""),
                        )
                    )
        return quotes

    def _init_embeddings(self) -> None:
        all_texts: list[str] = []
        all_refs: list[tuple[str, int]] = []
        for group_id, chunks in self._groups.items():
            for i, chunk in enumerate(chunks):
                all_texts.append(chunk.text)
                all_refs.append((group_id, i))
        quote_texts: list[str] = [q.text for q in self._quotes]
        combined_texts = all_texts + quote_texts
        if combined_texts:
            # Truncate each text to ~500 chars to stay under the
            # per-text token limit (bge-large-zh-v1.5 via SiliconFlow: ~512 tokens,
            # but some 500-char Chinese strings still exceed it).
            truncated = [t[:500] for t in combined_texts]
            try:
                embeddings = self._embedder.embed(truncated)
            except Exception as e:
                logger.warning(
                    "Embedding init failed (%s: %s) — falling back to keyword search only",
                    type(e).__name__,
                    e,
                )
                embeddings = [None] * len(combined_texts)
            for idx, (group_id, chunk_idx) in enumerate(all_refs):
                if embeddings[idx] is not None:
                    self._groups[group_id][chunk_idx].embedding = embeddings[idx]
            for i, q in enumerate(self._quotes):
                emb_idx = len(all_texts) + i
                if embeddings[emb_idx] is not None:
                    q.embedding = embeddings[emb_idx]

    def _cosine_similarity(self, a: np.ndarray, b: np.ndarray) -> float:
        if a is None or b is None:
            return 0.0
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(np.dot(a, b) / (norm_a * norm_b))

    def _embed_query(self, text: str) -> np.ndarray | None:
        try:
            # Truncate to ~500 chars to stay under embedding token limits
            results = self._embedder.embed([text[:500]])
            return results[0] if results else None
        except Exception as e:
            logger.debug("Query embedding failed: %s", e)
            return None

    def _score_groups(
        self,
        user_msg: str,
        query_emb: np.ndarray | None,
        bm25_scores: list[float],
    ) -> list[tuple[str, float]]:
        max_bm = max(bm25_scores) if bm25_scores else 0.0
        scores: list[tuple[str, float]] = []
        for group_id, chunks in self._groups.items():
            vec_score = 0.0
            if query_emb is not None:
                for chunk in chunks:
                    if chunk.embedding is not None:
                        sim = self._cosine_similarity(query_emb, chunk.embedding)
                        vec_score = max(vec_score, sim)
            indices = self._group_indices.get(group_id, [])
            bm_group = max((bm25_scores[i] for i in indices), default=0.0)
            bm_norm = (bm_group / max_bm) if max_bm > 0 else 0.0
            triggers = GROUP_TRIGGERS.get(group_id, [])
            kw_score = keyword_match_score(user_msg, triggers)
            hybrid = self._vector_weight * vec_score + self._keyword_weight * kw_score + 0.5 * bm_norm
            scores.append((group_id, hybrid))
        scores.sort(key=lambda x: -x[1])
        return scores

    def _select_top_groups(self, scores: list[tuple[str, float]]) -> list[str]:
        selected = [g for g, s in scores if s > self._group_threshold][: self._max_groups]
        if not selected:
            selected = [g for g, _ in scores[: self._max_groups]]
        return selected

    def _chunk_dense_score(self, chunk: Chunk, query_emb: np.ndarray | None) -> float:
        if query_emb is None or chunk.embedding is None:
            return 0.0
        return self._cosine_similarity(query_emb, chunk.embedding)

    def _select_evidence(
        self,
        user_msg: str,
        query_emb: np.ndarray | None,
        group_ids: list[str],
        bm25_scores: list[float],
    ) -> tuple[list[Chunk], list[dict[str, Any]]]:
        """Return only chunks that genuinely match the query.

        A chunk counts as evidence when it passes *either* the keyword path
        (BM25 above ``min_keyword_score`` with enough distinct matched terms)
        *or* the dense path (cosine similarity above ``min_dense_score``). When
        nothing passes, the result is empty — callers must treat that as "no
        evidence", never as "answer freely".
        """
        q_terms = set(tokenize(user_msg))
        min_match = 1 if len(q_terms) <= 2 else 2
        idf_mass = self._bm25.query_idf_mass(user_msg)

        candidates: list[tuple[Chunk, float]] = []
        for group_id in group_ids:
            for idx in self._group_indices.get(group_id, []):
                chunk = self._flat_chunks[idx]
                dense = self._chunk_dense_score(chunk, query_emb)
                kw = bm25_scores[idx] if idx < len(bm25_scores) else 0.0
                matched = self._bm25.matched_set(user_msg, idx)
                coverage = (sum(self._bm25.idf(t) for t in matched) / idf_mass) if idf_mass > 0 else 0.0
                kw_ok = kw >= self._min_keyword_score and len(matched) >= min_match and coverage >= self._min_coverage
                dense_ok = dense >= self._min_dense_score
                if kw_ok or dense_ok:
                    candidates.append((chunk, dense + kw))

        candidates.sort(key=lambda x: -x[1])
        selected = [c for c, _ in candidates[: self._max_chunks]]
        citations = [
            {
                "chunk_id": c.chunk_id,
                "group_id": c.group_id,
                "section_title": c.section_title,
                "source": c.citation_label,
                "start_line": c.start_line,
            }
            for c in selected
        ]
        return selected, citations

    def _select_top_quotes(self, user_msg: str, query_emb: np.ndarray | None) -> list[QuoteEntry]:
        scored: list[tuple[int, float]] = []
        zx_triggered = any(t.lower() in user_msg.lower() for t in _ZX_TRIGGERS)
        for i, q in enumerate(self._quotes):
            vec_score = 0.0
            if query_emb is not None and q.embedding is not None:
                vec_score = self._cosine_similarity(query_emb, q.embedding)
            kw_bonus = 0.3 if keyword_exact_match(user_msg, q.major) else 0.0
            # Persona-quote boost removed with the excluded corpus.
            zx_bonus = 0.4 if zx_triggered and q.id.startswith("zx_") else 0.0
            final = vec_score + kw_bonus + zx_bonus
            scored.append((i, final))
        scored.sort(key=lambda x: -x[1])
        selected: list[QuoteEntry] = []
        seen_majors: set[str] = set()
        for idx, score in scored:
            if score <= 0:
                break
            q = self._quotes[idx]
            if q.major not in seen_majors or len(selected) < 2:
                selected.append(q)
                seen_majors.add(q.major)
                if len(selected) >= self._max_quotes:
                    break
        return selected

    def search(self, user_msg: str, slots: dict) -> RetrievalResult:
        """主检索方法：BM25 关键词 + 可选向量（dense）混合。

        The keyword/BM25 path is always available (no API key, no network).
        The dense path only contributes when the configured embedding provider
        actually returns vectors.
        """
        query_emb = self._embed_query(user_msg)
        bm25_scores = self._bm25.score(user_msg)
        group_scores = self._score_groups(user_msg, query_emb, bm25_scores)
        selected_groups = self._select_top_groups(group_scores)
        selected_chunks, citations = self._select_evidence(user_msg, query_emb, selected_groups, bm25_scores)
        selected_quotes = self._select_top_quotes(user_msg, query_emb)
        result = RetrievalResult(
            groups=selected_groups,
            group_chunks=selected_chunks,
            quotes=selected_quotes,
            has_evidence=bool(selected_chunks),
            citations=citations,
        )
        logger.info(
            "RAG retrieval: query=%s, groups=%d, chunks=%d, quotes=%d",
            user_msg[:50],
            len(result.groups),
            len(result.group_chunks),
            len(result.quotes),
        )
        return result
