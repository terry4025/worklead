"""날짜 표현 해석. 정밀도를 함께 남기고, 부정확한 표현을 정확한 시각처럼 만들지 않는다."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta, timezone

from .types import DateInfo, Ev

KST = timezone(timedelta(hours=9))

_DEADLINE = [
    re.compile(r"(?P<m>\d{1,2})\s*월\s*(?P<d>\d{1,2})\s*일\s*(?:\([^)]{1,3}\))?\s*(?:까지|마감|전까지)"),
    re.compile(r"(?P<m>\d{1,2})\s*/\s*(?P<d>\d{1,2})\s*(?:\([^)]{1,3}\))?\s*(?:까지|마감)"),
    re.compile(r"마감\s*(?:일)?\s*(?:은|는)?\s*[:：]?\s*(?P<m>\d{1,2})\s*월\s*(?P<d>\d{1,2})\s*일"),
]
_ALWAYS = re.compile(r"상시\s*(?:모집|채용)|채용\s*시\s*마감|구해지면\s*마감")
_CLOSED = re.compile(
    r"\[\s*마감\s*\]|(?:모집\s*(?:이\s*)?)?마감\s*(?:이\s*|가\s*)?(?:되었|됐|했|하였|완료|합니다|입니다)|"
    r"(?:채용|구인)\s*완료|모집\s*종료"
)
_RECRUITMENT = re.compile(r"모집|구인|채용|지원|접수")
_WORK_DATE = re.compile(r"(?:작업|제작|개발|프로젝트|근무)\s*(?:은|는|을|를|이|가)?\s*(?:완료|마감|기간|일정)|납품|납기|완성|오픈|배포|착수")
_CLAUSE_BOUNDARY = re.compile(r"[\n.!?。;，,]")
_FUTURE_CLOSE = re.compile(r"예정|내일|모레|다음\s*(?:주|달)|(?:까지|되면|(?:완료|종료)\s*(?:시|후))")
_NEGATED_CLOSE = re.compile(r"마감[^.\n]{0,10}(?:아니|아님|않|취소)|완료[^.\n]{0,10}(?:아니|아님|않)")
_DATED_CLOSE = re.compile(r"\d{1,2}\s*(?:월\s*\d{1,2}\s*일|/\s*\d{1,2})")
_CONDITIONAL_CLOSE = re.compile(r"(?:바뀌|변경되|표시되|처리되|마감되)면")
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


def _clause(text: str, match: re.Match[str]) -> tuple[str, int]:
    start = 0
    for boundary in _CLAUSE_BOUNDARY.finditer(text, 0, match.start()):
        start = boundary.end()
    end = next((b.start() for b in _CLAUSE_BOUNDARY.finditer(text, match.end())), len(text))
    return text[start:end], start


def _is_recruitment_context(body: str, match: re.Match[str]) -> bool:
    clause, offset = _clause(body, match)
    start, end = match.start() - offset, match.end() - offset

    def distance(candidate: re.Match[str]) -> int:
        return max(start - candidate.end(), candidate.start() - end, 0)

    recruit = min((distance(m) for m in _RECRUITMENT.finditer(clause)), default=None)
    work = min((distance(m) for m in _WORK_DATE.finditer(clause)), default=None)
    if recruit is not None:
        return work is None or recruit < work
    # A bare "마감일: 10월 3일" label is retained, but a delivery/work
    # date and an unqualified "10월 3일까지" are not recruitment evidence.
    return work is None and "마감" in match.group(0)


def parse_deadline(body: str, observed: datetime) -> DateInfo:
    for pat in _DEADLINE:
        for m in pat.finditer(body):
            if not _is_recruitment_context(body, m):
                continue
            at = _resolve_month_day(int(m.group("m")), int(m.group("d")), observed)
            if at is None:
                continue
            return DateInfo(at=at, precision="day", raw=m.group(0), evidence=[_ev(body, m, "구인 마감일 (근무일·납기와 다름)")])
    m = _ALWAYS.search(body)
    if m:
        return DateInfo(at=None, precision="unknown", raw=m.group(0), evidence=[_ev(body, m)])
    return DateInfo()


def closed_marker(body: str, title: str) -> Ev | None:
    for text, is_title in ((body, False), (title, True)):
        for m in _CLOSED.finditer(text):
            clause, _ = _clause(text, m)
            if not _is_recruitment_context(text, m) or _CONDITIONAL_CLOSE.search(clause):
                continue
            if not m.group(0).lstrip().startswith("["):
                past = re.search(r"되었|됐|했|하였", m.group(0))
                if _NEGATED_CLOSE.search(clause) or not past and (_FUTURE_CLOSE.search(clause) or _DATED_CLOSE.search(clause)):
                    continue
            return Ev(title if is_title else m.group(0), None if is_title else m.start(), None if is_title else m.end(),
                      "explicit", "high", "제목의 마감 표시" if is_title else "원문 마감 표시")
        # Plain current-state labels need their own complete clause so a
        # future deadline label ("모집 마감은 ...") does not close the post.
        for m in re.finditer(r"(?:모집\s*마감|구인\s*완료|채용\s*완료|모집\s*종료)", text):
            clause, _ = _clause(text, m)
            if clause.strip() == m.group(0):
                return Ev(title if is_title else m.group(0), None if is_title else m.start(), None if is_title else m.end(),
                          "explicit", "high", "제목의 마감 표시" if is_title else "원문 마감 표시")
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
