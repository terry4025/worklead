"""보수 원문 해석. 모르는 금액은 None (0원 아님). 원문 표현을 항상 보존한다."""

from __future__ import annotations

import re

from .types import Ev, PayInfo

_NUM = r"\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?"
_MUL = r"억|천만|백만|만|천"

# "50~80만원", "300 ~ 500만 원", "12,000~15,000원"
_RANGE = re.compile(
    rf"(?P<a>{_NUM})\s*(?P<amul>{_MUL})?\s*(?:원)?\s*[~\-–〜]\s*(?P<b>{_NUM})\s*(?P<bmul>{_MUL})?\s*(?P<won>원)?"
)
# "1억 5천만원", "4,200만원", "12,000원", "150만원", "1.5만원"
_SINGLE = re.compile(rf"(?P<a>{_NUM})\s*(?P<amul>{_MUL})?(?:\s*(?P<c>\d+)\s*(?P<cmul>천만|백만|만|천))?\s*(?P<won>원)?")

_MULT = {"억": 100_000_000, "천만": 10_000_000, "백만": 1_000_000, "만": 10_000, "천": 1_000, None: 1}

_UNIT_BEFORE = [
    (re.compile(r"시급|시간당|시간\s*당"), "hour"),
    (re.compile(r"일급|일당|하루\s*(?:에)?\s*$"), "day"),
    (re.compile(r"주급"), "week"),
    (re.compile(r"월급|월\s*$|매월|월\s*고정|한\s*달|월\s*급여|월\s*보수"), "month"),
    (re.compile(r"연봉|년\s*$|연\s*$"), "year"),
    (re.compile(r"건당|건\s*당|프로젝트|예산|총\s*$|견적|제작비|개발비|비용은?\s*$|금액은?\s*$"), "project"),
]
_UNIT_AFTER = [
    (re.compile(r"^\s*(?:/|\()\s*(?:시간|시)"), "hour"),
    (re.compile(r"^\s*(?:/|\()\s*(?:월|달)"), "month"),
    (re.compile(r"^\s*(?:/|\()\s*(?:일)"), "day"),
    (re.compile(r"^\s*(?:/|\()\s*(?:건)"), "project"),
    (re.compile(r"^\s*고정"), None),
]
_NEGOTIABLE = re.compile(r"협의|조정\s*가능|조율|네고")
_NEGOTIABLE_ONLY = re.compile(
    r"(?:금액|예산|보수|급여|페이|비용|단가|견적)[^.\n]{0,20}?(?:협의|상의|조율)|(?:급여|보수|금액)\s*[:：]?\s*협의"
)
_NOT_PAY_BEFORE = re.compile(r"(?:교육비|자료비|가입비|등록비|보증금|수수료|매출|회비|입금)[^.\n]{0,6}$")
_PAY_CONTEXT = re.compile(r"예산|시급|일급|주급|월급|연봉|보수|급여|금액|비용|페이|만원|고정|건당|견적|제작비|개발비|수익")
_LOWER = re.compile(r"^\s*(?:부터|이상|~\s*$)")
_UPPER = re.compile(r"^\s*(?:까지|이하|이내)")
_UPPER_BEFORE = re.compile(r"(?:최대|최고)\s*$")


def _to_int(num: str, mul: str | None) -> int | None:
    try:
        v = float(num.replace(",", "")) * _MULT[mul]
    except (ValueError, KeyError):
        return None
    return int(round(v))


def _unit(before: str, after: str) -> str | None:
    tail = before[-14:]
    for pat, unit in _UNIT_BEFORE:
        if pat.search(tail):
            return unit
    for pat, unit in _UNIT_AFTER:
        if pat.search(after[:8]):
            return unit
    return None


_LINE_PROJECT = re.compile(r"예산|프로젝트|제작비|개발비|견적|건당|외주")


def _unit_in_line(line: str) -> str | None:
    """금액 앞뒤에서 단위를 못 찾으면 같은 줄의 예산·프로젝트 표현으로 건 단위를 판단한다."""
    return "project" if _LINE_PROJECT.search(line) else None


def _line_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    s = text.rfind("\n", 0, start) + 1
    e = text.find("\n", end)
    return s, (len(text) if e < 0 else e)


def parse_pay(body: str, title: str = "") -> PayInfo:
    """본문에서 보수 표현을 찾아 해석한다. 여러 금액이 있으면 보수 맥락이 있는 첫 금액을 쓴다."""
    candidates: list[tuple[int, int, int | None, int | None, str | None]] = []

    for m in _RANGE.finditer(body):
        amul = m.group("amul") or m.group("bmul")
        if not (m.group("bmul") or m.group("won") or amul):
            continue
        lo = _to_int(m.group("a"), amul)
        hi = _to_int(m.group("b"), m.group("bmul") or amul)
        candidates.append((m.start(), m.end(), lo, hi, None))

    for m in _SINGLE.finditer(body):
        if not (m.group("amul") or m.group("won")):
            continue
        if any(s <= m.start() < e for s, e, *_ in candidates):
            continue
        v = _to_int(m.group("a"), m.group("amul"))
        if v is not None and m.group("c"):
            extra = _to_int(m.group("c"), m.group("cmul"))
            v = v + (extra or 0)
        candidates.append((m.start(), m.end(), v, v, "single"))

    candidates.sort(key=lambda c: c[0])
    chosen = None
    for cand in candidates:
        start, end = cand[0], cand[1]
        before = body[max(0, start - 20) : start]
        if _NOT_PAY_BEFORE.search(before):
            continue
        ls, le = _line_bounds(body, start, end)
        if _PAY_CONTEXT.search(body[ls:le]):
            chosen = cand
            break
    if chosen is None:
        # 금액 없이 협의만 있는 경우
        m = _NEGOTIABLE_ONLY.search(body)
        if m:
            ls, le = _line_bounds(body, m.start(), m.end())
            quote = body[ls:le].strip()
            return PayInfo(
                raw=quote,
                unit="negotiable",
                negotiable=True,
                evidence=[Ev(quote, ls + (len(body[ls:le]) - len(body[ls:le].lstrip())), le, "explicit", "high")],
            )
        return PayInfo()

    start, end, lo, hi, kind = chosen
    before = body[max(0, start - 20) : start]
    after = body[end : end + 12]
    ls, le = _line_bounds(body, start, end)
    line = body[ls:le]
    unit = _unit(body[:start], after) or _unit_in_line(line) or "unknown"
    negotiable = bool(_NEGOTIABLE.search(line))

    if kind == "single":
        if _LOWER.search(after):
            hi = None
        elif _UPPER.search(after) or _UPPER_BEFORE.search(before):
            lo = None

    # 원문 표현: 금액이 포함된 문장 조각 (줄이 길면 마침표·쉼표 경계까지만)
    cut = max(body.rfind(". ", ls, start), body.rfind("! ", ls, start), body.rfind("? ", ls, start))
    raw_start = cut + 1 if cut >= 0 else ls
    nxt = [i for i in (body.find(". ", end, le), body.find(".\n", end, le)) if i >= 0]
    raw_end = min(nxt) + 1 if nxt else le
    seg = body[raw_start:raw_end]
    raw_start += len(seg) - len(seg.lstrip(" -•*\t"))
    raw = body[raw_start:raw_end].strip()
    ev = Ev(raw, raw_start, raw_start + len(raw), "explicit", "high")
    notes = []
    if unit == "year":
        notes.append("연 단위 — 지급 단위 목록(project/hour/day/week/month)에 없어 unknown 으로 보존")
        unit = "unknown"
    if notes:
        ev.note = "; ".join(notes)
    return PayInfo(raw=raw, min=lo, max=hi, unit=unit, negotiable=negotiable, evidence=[ev])
