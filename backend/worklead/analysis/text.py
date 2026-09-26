"""원문 검색 도우미. 인용 구간은 문장 경계까지 넓혀 근거로 남긴다."""

from __future__ import annotations

import re
from collections.abc import Iterable

from .types import Basis, Ev

_BOUNDARY = re.compile(r"[\n.!?。]")


def _expand(text: str, start: int, end: int, limit: int = 90) -> tuple[int, int]:
    s = start
    while s > 0 and not _BOUNDARY.match(text[s - 1]) and start - s < limit:
        s -= 1
    e = end
    while e < len(text) and not _BOUNDARY.match(text[e]) and e - end < limit:
        e += 1
    if e < len(text) and text[e] in ".!?。":
        e += 1
    # 앞뒤 공백·목록 기호 제거
    while s < e and text[s] in " \t-•*·":
        s += 1
    while e > s and text[e - 1] in " \t":
        e -= 1
    return s, e


def find(
    patterns: Iterable[re.Pattern[str]],
    body: str,
    title: str,
    basis: Basis,
    note: str | None = None,
    expand: bool = True,
) -> Ev | None:
    """본문 → 제목 순으로 첫 일치를 찾는다."""
    pats = list(patterns)
    for p in pats:
        m = p.search(body)
        if m:
            s, e = _expand(body, m.start(), m.end()) if expand else (m.start(), m.end())
            return Ev(body[s:e], s, e, basis, "high" if basis == "explicit" else "medium", note)
    for p in pats:
        m = p.search(title)
        if m:
            return Ev(title.strip(), None, None, basis, "high" if basis == "explicit" else "medium", note or "제목에서 확인")
    return None


def any_match(patterns: Iterable[re.Pattern[str]], *texts: str) -> bool:
    return any(p.search(t) for p in patterns for t in texts)


def c(*patterns: str) -> list[re.Pattern[str]]:
    return [re.compile(p) for p in patterns]


def first_sentence(text: str, limit: int = 90) -> str | None:
    for line in text.splitlines():
        line = line.strip(" -•*\t")
        if len(line) >= 6:
            m = re.match(r"(.+?[.!?])(\s|$)", line)
            s = m.group(1) if m else line
            return s if len(s) <= limit else s[: limit - 1] + "…"
    return None
