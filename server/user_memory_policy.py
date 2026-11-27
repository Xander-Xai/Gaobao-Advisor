"""Field-level policy for cross-task user memory.

This module is the *only* place that decides what may be written into the
preference vault and what may be injected back into a model prompt.

Three independent rules are enforced:

1. **Field allowlist** — a preference must use one of the supported keys.
   Each key carries a data class so downstream code can reason about how
   sensitive a stored value is without inspecting its content.
2. **Sensitive-value screen** — values that look like exam scores, identity
   documents, contact details, family background, external links or
   instructions are refused. The default product stance is that Gaobao does
   not store 高考分数, 身份证, 联系方式 or 家庭信息 unless a separate,
   explicit authorization exists. No such authorization exists today, so the
   screen refuses them.
3. **Prompt neutralization** — on the way back into a prompt, values are
   stripped of control characters, de-identified of instruction-like phrasing
   and rendered as inert JSON. Memory is a *preference hint*, never an
   instruction and never an admissions fact.
"""

from __future__ import annotations

import re
from typing import Any

MAX_VALUE_LENGTH = 120
MAX_ITEMS_IN_PROMPT = 5
MAX_PROMPT_CHARS = 900

# Data classes for the stored fields. They are intentionally coarse: the
# vault stores presentation and preference hints only.
PRESENTATION = "PRESENTATION"
PREFERENCE_CATEGORICAL = "PREFERENCE_CATEGORICAL"
PREFERENCE_GOAL = "PREFERENCE_GOAL"

MEMORY_KEY_CLASS: dict[str, str] = {
    "preferred_regions": PREFERENCE_CATEGORICAL,
    "interested_majors": PREFERENCE_CATEGORICAL,
    "career_goal": PREFERENCE_GOAL,
    "study_goal": PREFERENCE_GOAL,
    "response_style": PRESENTATION,
}

ALLOWED_KEYS = frozenset(MEMORY_KEY_CLASS)
ALLOWED_STYLES = frozenset({"concise", "detailed", "comparison"})

# Reason codes returned by the screen. The caller must not echo the offending
# value back to the client or into a log line.
REASON_IDENTITY_DOCUMENT = "IDENTITY_DOCUMENT"
REASON_CONTACT = "CONTACT_DETAIL"
REASON_EXAM_SCORE = "EXAM_SCORE"
REASON_FAMILY = "FAMILY_CONTEXT"
REASON_FINANCIAL = "FINANCIAL_DETAIL"
REASON_EXTERNAL_LINK = "EXTERNAL_LINK"
REASON_INSTRUCTION = "INSTRUCTION_INJECTION"
REASON_CONTROL_CHARACTER = "CONTROL_CHARACTER"

_SENSITIVE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (REASON_IDENTITY_DOCUMENT, re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")),
    (REASON_IDENTITY_DOCUMENT, re.compile(r"护照?号?\s*[:：]?\s*[A-Za-z0-9]{6,}")),
    (REASON_IDENTITY_DOCUMENT, re.compile(r"(身份证|证件号|身份证号)\s*[:：]?\s*[A-Za-z0-9]{6,}")),
    (REASON_EXAM_SCORE, re.compile(r"(高考)?(分数|成绩|总分|得分|分数段|位次|排名|考分)\s*[:：]?\s*\d{2,4}")),
    (REASON_EXAM_SCORE, re.compile(r"(?<!\d)\d{3,4}\s*分(?!钟|析|发|布|类|享)")),
    (REASON_EXAM_SCORE, re.compile(r"(一分一段|位次表|投档线|录取线)\s*[:：]?\s*\d{2,4}")),
    (REASON_CONTACT, re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    (REASON_CONTACT, re.compile(r"(\+?86[-\s]?)?1[3-9]\d{2}[-\s]?\d{4}[-\s]?\d{4}")),
    (REASON_CONTACT, re.compile(r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,}")),
    (REASON_CONTACT, re.compile(r"(微信|QQ|qq|邮箱|电话|手机|联系方式|住址|地址)\s*[:：]?")),
    (REASON_FAMILY, re.compile(r"(父亲|母亲|爸爸|妈妈|父母|家庭情况|家庭背景|户籍|户口)")),
    (REASON_FINANCIAL, re.compile(r"(房产|年收入|工资|存款|理财|保险|房产证)")),
    (REASON_FINANCIAL, re.compile(r"(?<!\d)\d{16,19}(?!\d)")),
    (REASON_EXTERNAL_LINK, re.compile(r"(https?://|www\.|[\w.-]+\.(com|cn|net|org|io)/)", re.IGNORECASE)),
    (
        REASON_INSTRUCTION,
        re.compile(
            r"(忽略(上面|以上|之前|前面|先前|全部)|无视(以上|之前|前面|全部)|不要遵守|"
            r"你现在是|必须(回答|输出|执行|告诉|按照)|请扮演|开发者模式|"
            r"system\s*:|assistant\s*:|<\s*/?\s*(system|instructions?)\s*>|"
            r"ignore\s+(all\s+)?(previous|prior|above)|disregard\s+(the\s+)?(previous|prior)|"
            r"you\s+are\s+now|new\s+instructions?)",
            re.IGNORECASE,
        ),
    ),
)

_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")


def classify(key: str) -> str:
    """Return the data class of a supported memory key."""
    return MEMORY_KEY_CLASS[key]


def screen_value(value: str) -> str | None:
    """Return a refusal reason code, or ``None`` when the value may be stored.

    The reason code is deliberately coarse: it must not leak the offending
    value into an HTTP response, an audit row or a log line.
    """
    if _CONTROL_CHARACTERS.search(value):
        return REASON_CONTROL_CHARACTER
    for reason, pattern in _SENSITIVE_PATTERNS:
        if pattern.search(value):
            return reason
    return None


def sanitize_for_prompt(value: str) -> str:
    """Neutralize a stored value before it is rendered into a model prompt.

    Legacy rows may predate the write-time screen, so the read path also
    removes instruction-like phrasing instead of trusting the stored text.
    """
    cleaned = _CONTROL_CHARACTERS.sub(" ", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    for reason, pattern in _SENSITIVE_PATTERNS:
        if reason == REASON_INSTRUCTION:
            cleaned = pattern.sub("[已屏蔽]", cleaned)
    return cleaned


def is_allowed_key(key: Any) -> bool:
    return isinstance(key, str) and key in ALLOWED_KEYS


def prompt_items(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Reduce stored entries to the small, safe set that may reach a prompt."""
    if not isinstance(items, list):
        return []
    safe: list[dict[str, str]] = []
    for item in items[:MAX_ITEMS_IN_PROMPT]:
        if not isinstance(item, dict):
            continue
        key = item.get("key")
        value = item.get("value")
        if not is_allowed_key(key) or not isinstance(value, str):
            continue
        cleaned = sanitize_for_prompt(value)[:MAX_VALUE_LENGTH]
        if not cleaned:
            continue
        safe.append({"key": key, "value": cleaned, "data_class": MEMORY_KEY_CLASS[key]})
    return safe
