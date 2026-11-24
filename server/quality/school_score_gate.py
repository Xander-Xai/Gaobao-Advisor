"""Conservative row-bound check for explicit school minimum-score claims.

Only a narrow Chinese statement grammar is accepted. This is not a general
semantic verifier; year, province, major, rank and policy claims need separate
structured evidence. Do not treat user input, slots or RAG text as SQL rows.
"""

from __future__ import annotations

import re

_METRIC = r"(?:最低录取分(?:数)?|最低分(?:数)?|录取分数线|录取线|分数线|参考线)"
_SCORE_CLAIM = re.compile(_METRIC + r"[^。！？；;\n]{0,32}?\d+\s*分")
_EXACT = re.compile(
    r"(?:根据合成数据(?:记录)?[，,]\s*)?"
    r"(?P<school>[^，,。！？；;\n]+?)(?:的)?"
    + _METRIC
    + r"(?:为|是|[:：])\s*(?P<score>[0-9]{1,3})\s*分"
)


def has_unbound_school_score(state: dict, candidate: str) -> bool:
    """Reject recognized score assertions that lack an unambiguous SQL row."""
    results = state.get("data_query_results")
    rows = results.get("match_schools", []) if isinstance(results, dict) else []
    aliases: dict[str, set[tuple[str, int | None]]] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        name = row.get("school_name")
        if not isinstance(name, str) or not name.strip():
            continue
        name = name.strip()
        value = row.get("min_score")
        score = value if type(value) is int and 0 <= value <= 750 else None
        for alias in {name, name.removesuffix("（合成）")}:
            if alias:
                aliases.setdefault(alias, set()).add((name, score))

    for sentence in re.split(r"[。！？；;\n]", candidate):
        if not _SCORE_CLAIM.search(sentence):
            continue
        match = _EXACT.fullmatch(sentence.strip())
        if match is None:
            return True
        school = match.group("school").strip()
        matches = aliases.get(school, set())
        if len(matches) != 1 or next(iter(matches))[1] != int(match.group("score")):
            return True
    return False
