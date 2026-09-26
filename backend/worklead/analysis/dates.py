"""날짜 표현 해석. 정밀도를 함께 남기고, 부정확한 표현을 정확한 시각처럼 만들지 않는다."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta, timezone

from .types import DateInfo, Ev

KST = timezone(timedelta(hours=9))

_DEADLINE = [
    re.compile(r"(?P<m>\d{1,2})\s*월\s*(?P<d>\d{1,2})\s*일\s*(?:\([^)]{1,3}\))?\s*(?:까지|마감|전까지)"),
    re.compile(r"(?P<m>\d{1,2})\s*/\s*(?P<d>\d{1,2})\s*(?:\([^)]{1,3}\))?\s*(?:까지|마감)"),
    re.compile(r"마감\s*(?:일)?\s*[:：]?\s*(?P<m>\d{1,2})\s*월\s*(?P<d>\d{1,2})\s*일"),
]
_ALWAYS = re.compile(r"상시\s*(?:모집|채용)|채용\s*시\s*마감|구해지면\s*마감")
_CLOSED = re.compile(r"\[\s*마감\s*\]|모집\s*(?:이\s*)?마감(?:되었|됐|했|합니다|입니다)?|마감\s*되었|채용\s*완료|구인\s*완료|마감했습니다|모집\s*종료")
_WORK_START = [
    (re.compile(r"(?P<m>\d{1,2})\s*월\s*(?P<d>\d{1,2})\s*일\s*(?:부터|시작|착수)"), "day"),
]
_WORK_START_VAGUE = re.compile(r"(이번\s*(?:달|주)\s*(?:안에|내|중)?\s*시작|다음\s*(?:달|주)\s*(?:부터|시작|중)|바로\s*시작|즉시\s*(?:시작|투입))")
_RELATIVE = re.compile(r"(?P<n>\d+)\s*(?P<u>분|시간|일|주)\s*전|어제|방금")


def _resolve_month_day(month: int, day: int, observed: datetime) -> datetime | None:
    obs = observed.astimezone(KST)
    try:
        cand = datetime(obs.year, month, day, tzinfo=KST)
    except ValueError:
        return None
    # 60일 이상 지난 날짜면 다음 해로 본다 (연말 게시 대비)
    if (obs - cand).days > 60:
        try:
            cand = cand.replace(year=obs.year + 1)
        except ValueError:
            return None
    return cand.astimezone(UTC)


def _ev(text: str, m: re.Match[str], note: str | None = None) -> Ev:
    return Ev(m.group(0), m.start(), m.end(), "explicit", "high", note)


def parse_deadline(body: str, observed: datetime) -> DateInfo:
    for pat in _DEADLINE:
        m = pat.search(body)
        if m:
            at = _resolve_month_day(int(m.group("m")), int(m.group("d")), observed)
            if at is None:
                continue
            return DateInfo(at=at, precision="day", raw=m.group(0), evidence=[_ev(body, m, "구인 마감일 (근무일·납기와 다름)")])
    m = _ALWAYS.search(body)
    if m:
        return DateInfo(at=None, precision="unknown", raw=m.group(0), evidence=[_ev(body, m)])
    return DateInfo()


def closed_marker(body: str, title: str) -> Ev | None:
    m = _CLOSED.search(body)
    if m:
        return Ev(m.group(0), m.start(), m.end(), "explicit", "high", "원문 마감 표시")
    m = _CLOSED.search(title)
    if m:
        return Ev(title, None, None, "explicit", "high", "제목의 마감 표시")
    return None


def parse_work_start(body: str, observed: datetime) -> DateInfo:
    for pat, precision in _WORK_START:
        m = pat.search(body)
        if m:
            at = _resolve_month_day(int(m.group("m")), int(m.group("d")), observed)
            if at:
                return DateInfo(at=at, precision=precision, raw=m.group(0), evidence=[_ev(body, m, "작업 시작일 (구인 마감과 다름)")])  # type: ignore[arg-type]
    m = _WORK_START_VAGUE.search(body)
    if m:
        return DateInfo(at=None, precision="unknown", raw=m.group(0), evidence=[_ev(body, m)])
    return DateInfo()


def parse_relative_published(raw: str, observed: datetime) -> DateInfo:
    """'3일 전' 같은 표현. 관찰 시각 기준 근사값이며 precision=approximate."""
    m = _RELATIVE.search(raw)
    if not m:
        return DateInfo(raw=raw or None)
    text = m.group(0)
    if text == "방금":
        delta = timedelta(0)
    elif text == "어제":
        delta = timedelta(days=1)
    else:
        n = int(m.group("n"))
        unit = m.group("u")
        delta = {
            "분": timedelta(minutes=n),
            "시간": timedelta(hours=n),
            "일": timedelta(days=n),
            "주": timedelta(weeks=n),
        }[unit]
    return DateInfo(at=observed - delta, precision="approximate", raw=raw)
