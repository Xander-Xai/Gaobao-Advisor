"""Evidence anchors and fact quarantine for session-level context.

Two rules protect the working/session memory layer:

1. **Evidence priority** — the current task and the source markers that were
   actually cited in this conversation survive every compaction step. When
   older turns are summarized or dropped, the server keeps their citation
   markers verbatim in a protected block that is never truncated. The markers
   are pointers, not facts: the facts still have to come from the current
   verified SQL retrieval.

2. **Fact quarantine** — a generated summary is untrusted dialogue prose. It
   must never be able to look like verified admissions data. Any sentence
   that asserts a score line, an admission rate or a fabricated citation is
   replaced by an explicit redaction marker before the summary is stored or
   rendered.
"""

from __future__ import annotations

import re
from typing import Any

MAX_ANCHORS = 8
MAX_ANCHOR_LENGTH = 80

_CITATION = re.compile(r"（\s*来源[:：]\s*([^）]{1,120})）")
_SOURCE_LABEL = re.compile(r"(?:^|\n)\s*(?:数据来源|来源|引用)\s*[:：]\s*([^\n]{1,120})")

# Admissions-fact shaped claims. A summary is not allowed to state them.
_FACT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(分数线|投档线|录取线|录取率|位次|最低分|最高分|平均分|参考分|一分一段|专业组)"),
    re.compile(r"(必上|稳上|保上|一定能?录|保证录取|录取概率)"),
    re.compile(r"\d{2,4}\s*分"),
    re.compile(r"\d{1,3}\s*%"),
)

_REDACTION = "[已屏蔽：历史摘要中的招生数据未经核验，请以当前已核验数据为准]"


def extract_evidence_anchors(messages: list[dict[str, Any]], *, limit: int = MAX_ANCHORS) -> list[str]:
    """Return the distinct source markers cited by the conversation so far.

    Only assistant turns are inspected: a user turn may mention a school or a
    score, but that is a request, not verified provenance.
    """
    anchors: list[str] = []
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        content = message.get("content")
        if not isinstance(content, str) or not content:
            continue
        found = [match.strip() for match in _CITATION.findall(content)]
        found.extend(match.strip() for match in _SOURCE_LABEL.findall(content))
        for marker in found:
            marker = marker.strip(" ：:、,，。;；")
            if not marker or len(marker) > MAX_ANCHOR_LENGTH:
                continue
            if marker not in anchors:
                anchors.append(marker)
            if len(anchors) >= limit:
                return anchors
    return anchors


def render_evidence_block(anchors: list[str]) -> str:
    """Render the protected evidence block appended to the current task."""
    if not anchors:
        return ""
    listed = "、".join(anchors[:MAX_ANCHORS])
    return f"[本会话此前已引用的来源标记（仅为出处指针，不是招生数据；相关事实必须重新以当前已核验数据为准）：{listed}]"


def _looks_like_admissions_fact(line: str) -> bool:
    return any(pattern.search(line) for pattern in _FACT_PATTERNS)


def quarantine_summary(summary: str) -> tuple[str, int]:
    """Remove admissions-fact shaped claims from an untrusted summary.

    Returns ``(clean_summary, redaction_count)``. Fabricated citations are
    dropped as well: a summary may not claim a source it did not verify.
    """
    if not isinstance(summary, str) or not summary.strip():
        return "", 0
    lines = summary.splitlines()
    cleaned: list[str] = []
    redactions = 0
    for line in lines:
        stripped = line.strip()
        if not stripped:
            cleaned.append(line)
            continue
        without_citation = _CITATION.sub("", line)
        if _looks_like_admissions_fact(without_citation):
            cleaned.append(_REDACTION)
            redactions += 1
            continue
        cleaned.append(without_citation)
    return "\n".join(cleaned).strip(), redactions
