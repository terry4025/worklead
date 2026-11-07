"""규칙 분석 전체 흐름: 분류 → 적격성 → 설명 가능한 우선순위 → 수익성 시나리오 → 확인 질문.

우선순위 점수는 제품 초기 설정 가중치이며 수주 확률이 아니다.
모르는 요소는 score=None 으로 두고, 남은 요소만으로 재정규화하지 않는다.
점수가 높아도 필수 적격성 조건을 통과하지 못하면 recommended 가 되지 않는다.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

from . import dates, rules
from .pay import TIME_PAY_LABELS, parse_pay, time_pay_unit
from .text import first_sentence
from .types import Ev, Factor, Judgement, PayInfo, PostInput, Profile, Profitability, Reason, RuleAnalysis, Scenario

WEIGHTS = {
    "work_fit": ("업무 적합성", 35),
    "buyer_intent": ("구매 의도", 30),
    "remote_fit": ("원격 적합성", 10),
    "freshness": ("최신성", 15),
    "scope_clarity": ("범위 명확성", 10),
}
RISK_PENALTY_EACH = 15
# 시간 단위 보수로 올라온 재택 개발 의뢰의 우선순위 감점. 건당 의뢰보다 뒤에 오도록 하는 정렬용 값.
TIME_PAY_REVIEW_PENALTY = 10

CATEGORY_LABEL = {
    "website": "웹사이트",
    "landing": "랜딩페이지",
    "shop": "쇼핑몰",
    "fullstack": "풀스택·웹 서비스",
    "software": "프로그램",
    "vba": "엑셀·VBA·매크로",
    "automation": "업무 자동화·연동",
    "data": "데이터 정리",
    "other": "기타",
}

#: 카테고리별 기준 투입 시간 (요구 확인·개발·테스트·배포·소통·수정·지원 포함) — 규칙 추정치
BASE_HOURS = {"landing": 20, "website": 28, "shop": 24, "fullstack": 80, "software": 40, "vba": 16, "automation": 16, "data": 10, "other": 20}

_CLARITY_SIGNALS = [
    r"페이지|화면|게시판|폼|양식|시트|기능|메뉴|지도|예약|로그인|관리자|알림",
    r"\d+\s*(?:건|명|개|종|행|페이지|회)",
    r"엑셀\s*\d{4}|카페24|워드프레스|React|Node|C#|파이썬|API|알림톡|WinForms",
    r"샘플|예시|자료|문구|사진",
    r"일정|까지|오픈|마감|주\s*안|이번\s*달",
]


def _score_work_fit(categories: list[str], dev: bool, profile: Profile, excluded_hit: str | None) -> Factor:
    label, mx = WEIGHTS["work_fit"]
    if excluded_hit:
        return Factor("work_fit", label, mx, 0, f"제외 업무 키워드 '{excluded_hit}' 포함")
    names = ", ".join(CATEGORY_LABEL.get(c, c) for c in categories)
    if not dev:
        if "data" in categories:
            return Factor("work_fit", label, mx, 12, "데이터 정리 — 개발 업무는 아님")
        return Factor("work_fit", label, mx, 3, "개발·자동화 업무 근거 없음")
    matched = [c for c in categories if c in profile.services]
    if profile.services and matched:
        return Factor("work_fit", label, mx, min(mx, 29 + 3 * len(matched)), f"{names} — 제공 서비스와 일치")
    if profile.services:
        return Factor("work_fit", label, mx, 16, f"{names} — 제공 서비스 목록에 없음")
    return Factor("work_fit", label, mx, 25, f"{names} — 개발 업무 근거 있음, 제공 서비스 미설정")


def _score_intent(intent: Judgement, engagement: Judgement) -> Factor:
    label, mx = WEIGHTS["buyer_intent"]
    v, b = intent.value, intent.basis
    if v == "buyer_project":
        return Factor("buyer_intent", label, mx, 28 if b in ("explicit", "user_confirmed") else 20, "구매 표현 " + ("확인" if b == "user_confirmed" else "명시" if b == "explicit" else "추정"))
    if v == "buyer_ongoing":
        return Factor("buyer_intent", label, mx, 28 if b in ("explicit", "user_confirmed") else 20, "지속 의뢰 " + ("확인" if b == "user_confirmed" else "명시" if b == "explicit" else "추정"))
    if v == "employee_hiring":
        pts = 4 if engagement.value == "full_time" else 8
        return Factor("buyer_intent", label, mx, pts, "고용 공고 — 외주 구매 아님")
    if v in ("seller_service", "job_seeker", "information"):
        return Factor("buyer_intent", label, mx, 0, "구매 수요 없음")
    return Factor("buyer_intent", label, mx, None, "근거 부족: 의뢰/채용 구분 불가")


def _score_remote(work: Judgement, collab: Judgement, profile: Profile) -> Factor:
    label, mx = WEIGHTS["remote_fit"]
    v, b = work.value, work.basis
    if v == "unknown":
        return Factor("remote_fit", label, mx, None, "근거 부족: 재택·온라인 언급 없음")
    if v == "onsite":
        return Factor("remote_fit", label, mx, 0, "출근 필요")
    if v == "fully_remote":
        if b in ("explicit", "user_confirmed"):
            pts, why = (10, "재택·온라인 진행 명시") if collab.value == "online_only" else (8, "재택 명시, 진행 방식 일부 불명확")
        else:
            pts, why = 4, "재택 명시 없음 — 추정"
    elif v == "hybrid":
        pts, why = 4, "일부 재택"
    else:
        pts, why = 5, "재택 협의"
    if collab.value == "onsite_required":
        cap = {"no": 4, "first_meeting": 7, "yes": 9}[profile.onsite]
        pts, why = min(pts, cap), why + ", 대면 필요"
    return Factor("remote_fit", label, mx, pts, why)


def _score_freshness(post: PostInput, now: datetime, ttl_hours: int) -> Factor:
    label, mx = WEIGHTS["freshness"]
    if post.source_status in ("closed", "deleted"):
        return Factor("freshness", label, mx, 0, "마감" if post.source_status == "closed" else "삭제됨")
    ref = post.published.at
    basis = "게시"
    if ref is None:
        if post.source_kind == "manual":
            return Factor("freshness", label, mx, None, "근거 부족: 수동 입력이라 게시·모집 시각 없음")
        ref = post.first_seen_at
        basis = "게시일 미확인, 최초 발견"
    if ref is None:
        return Factor("freshness", label, mx, None, "근거 부족: 시각 정보 없음")
    age_h = max(0.0, (now - ref).total_seconds() / 3600)
    pts = 15 if age_h <= 24 else 12 if age_h <= 72 else 8 if age_h <= 168 else 4
    if post.published.at is None:
        pts = min(pts, 8)
    stale = post.last_checked_at is None or (now - post.last_checked_at) > timedelta(hours=ttl_hours)
    if stale:
        pts = max(0, pts - 6)
    age = f"{int(age_h)}시간" if age_h < 48 else f"{int(age_h // 24)}일"
    return Factor("freshness", label, mx, pts, f"{basis} {age} 전" + (" · 모집 재확인 필요" if stale else ""))


def _score_clarity(body: str, pay: PayInfo) -> Factor:
    import re

    label, mx = WEIGHTS["scope_clarity"]
    hits = sum(1 for p in _CLARITY_SIGNALS if re.search(p, body))
    pts = min(mx, hits * 2 + (1 if pay.min is not None or pay.max is not None else 0) + (1 if len(body) > 120 else 0))
    why = "요구 범위 구체적" if pts >= 7 else "범위 일부만 명시" if pts >= 4 else "업무 내용이 거의 없음"
    return Factor("scope_clarity", label, mx, pts, why)


def _excluded_hit(text: str, profile: Profile) -> str | None:
    for kw in [k.strip() for k in profile.excluded_work.replace("\n", ",").split(",")]:
        if len(kw) >= 2 and kw in text:
            return kw
    return None


def _fmt_krw(v: int) -> str:
    if v >= 10_000 and v % 10_000 == 0:
        return f"{v // 10_000:,}만원"
    return f"{v:,}원"


def _pay_short(pay: PayInfo) -> str | None:
    if pay.min is None and pay.max is None:
        return None
    unit = {"project": "예산 ", "hour": "시급 ", "day": "일급 ", "week": "주급 ", "month": "월 "}.get(pay.unit, "")
    if pay.min is not None and pay.max is not None and pay.min != pay.max:
        return f"{unit}{_fmt_krw(pay.min)}~{_fmt_krw(pay.max)}"
    return f"{unit}{_fmt_krw(pay.min if pay.min is not None else pay.max)}"  # type: ignore[arg-type]


def _contract_floor_caution(pay: PayInfo, floor: int | None) -> str | None:
    """A personal floor needs a confirmed lower bound, without changing pay."""
    if not floor or floor <= 0 or pay.unit != "project" or (pay.min is not None and pay.min >= floor):
        return None
    setting = f"내 최소 계약금액 {_fmt_krw(floor)}"
    if pay.max is not None and pay.max < floor:
        return f"최소 계약금액 미달 — 협의 필요 ({_pay_short(pay)}, {setting})"
    if pay.min is not None:
        if pay.max is not None:
            return f"예산 범위가 최소 계약금액에 걸침 — 확정 금액 확인 ({_pay_short(pay)}, {setting})"
        return f"게시 하한이 최소 계약금액 미달 — 최종 금액 확인 ({_pay_short(pay)}, {setting})"
    if pay.max is not None:
        return f"계약금액 하한 미확인 — 최소 계약금액 충족 확인 (게시 상한 {_fmt_krw(pay.max)}, {setting})"
    label = "계약금액 협의" if pay.negotiable else "계약금액 미확인"
    return f"{label} — 최소 계약금액 충족 확인 ({setting})"


def _pay_priority(pay: PayInfo, categories: list[str], profile: Profile, clarity: int,
                  total: int | None, conflict: str | None) -> tuple[Factor, str | None]:
    """Discount priority, preserving fit scores and the source payment.

    Use the most optimistic existing time estimate and the posted upper bound
    so negotiable ranges are not condemned on their lowest possible price.
    The small-budget fallback is a screening rule, not a market price estimate.
    """
    reason = "보수 단위·상한 또는 실제 계약금액 미확인 — 감점 보류"
    if conflict or pay.currency != "KRW" or pay.unit != "project" or pay.max is None:
        return Factor("pay_priority", "보수 감점", 0, None, reason), None
    reference = max(100_000, profile.min_contract or 0)
    basis = f"소액 의뢰 검토 기준 {_fmt_krw(reference)} (시장 적정가 아님)"
    if profile.target_hourly and profile.target_hourly > 0:
        cat = next((c for c in categories if c in BASE_HOURS), "other")
        hours = max(1, round(BASE_HOURS[cat] * (1.2 if clarity < 4 else 1.0) * 0.75))
        reference = max(reference, hours * profile.target_hourly)
        basis = (f"규칙상 낙관적 추정 {hours}시간 × 내 목표 시간가치 "
                 f"{_fmt_krw(profile.target_hourly)}; 실제 투입 시간 확인 필요")
    ratio = pay.max / reference
    cap = 39 if ratio < 0.25 else 59 if ratio < 0.5 else 74 if ratio < 1 else 100
    discount = max(0, (total or 0) - cap)
    reason = f"게시 상한 {_fmt_krw(pay.max)} / 비교 기준 {_fmt_krw(reference)} · {basis}"
    if cap < 100:
        reason += f" · 우선순위 최대 {cap}점"
    else:
        reason += " · 추가 감점 없음; 수익성 보장 아님"
    caution = "보수가 매우 낮음 — 작업 범위·실제 계약금액 협의 필요" if ratio < 0.25 else None
    return Factor("pay_priority", "보수 감점", 0, -discount, reason), caution


def _resolve_pay(hint: PayInfo | None, body_pay: PayInfo) -> tuple[PayInfo, str | None]:
    """Use body pay when source metadata is empty, retaining meaningful hints.

    Different non-overlapping amounts are contradictory. Overlapping ranges
    can describe the same negotiable price and are not treated as a conflict.
    """
    if hint is None or not (hint.raw and hint.raw.strip() or hint.min is not None or hint.max is not None
                            or hint.unit != "unknown" or hint.negotiable):
        return body_pay, None
    source_evidence = [replace(ev, start=None, end=None, note="사이트 보수" + (f" · {ev.note}" if ev.note else ""))
                       for ev in hint.evidence]
    if hint.raw and not source_evidence:
        source_evidence = [Ev(hint.raw, None, None, "explicit", "high", "사이트 보수")]
    pay = replace(hint, evidence=source_evidence)
    source_lo, source_hi = hint.min, hint.max
    body_lo, body_hi = body_pay.min, body_pay.max
    if (source_lo is None and source_hi is None) or (body_lo is None and body_hi is None):
        return pay, None
    if hint.currency != body_pay.currency:
        return pay, None
    disjoint = (source_hi is not None and body_lo is not None and source_hi < body_lo
                or body_hi is not None and source_lo is not None and body_hi < source_lo)
    if not disjoint:
        return pay, None
    conflict = f"보수 금액 불일치 — 사이트 {_pay_short(hint)}, 본문 {_pay_short(body_pay)}"
    pay.evidence.extend(replace(ev, note="본문 보수 · 사이트 표시 금액과 불일치") for ev in body_pay.evidence)
    return pay, conflict


def profitability(categories: list[str], pay: PayInfo, intent: str, profile: Profile, clarity: int) -> Profitability:
    target = profile.target_hourly
    cat = next((c for c in categories if c in BASE_HOURS), "other")
    base_h = BASE_HOURS[cat] * (1.2 if clarity < 4 else 1.0)
    hours = {"conservative": round(base_h * 1.35), "base": round(base_h), "optimistic": max(1, round(base_h * 0.75))}
    assumptions_common = [
        f"투입 시간은 규칙 추정치 ({CATEGORY_LABEL.get(cat, cat)} 기준 {round(base_h)}시간, 요구 확인·개발·테스트·배포·소통·수정·지원 포함)",
        "확인된 직접 비용 없음 — 확인되지 않은 세금·수수료는 반영하지 않음",
    ]
    if target:
        assumptions_common.append(f"목표 시간당 가치 {target:,}원 (내 설정)")
    recurring = "recurring" if intent == "buyer_ongoing" or pay.unit == "month" else "one_time" if pay.unit == "project" else "unknown"

    if pay.unit in ("hour", "day", "week", "month"):
        label = {"hour": "시급", "day": "일급", "week": "주급", "month": "월 단위"}[pay.unit]
        return Profitability(
            "not_calculated",
            None,
            f"{label} 보수입니다. 원문 그대로 표시하고 프로젝트 매출로 환산하지 않았습니다.",
            [],
            [],
            recurring,  # type: ignore[arg-type]
            target,
        )
    if pay.unit == "project" and (pay.min is not None or pay.max is not None):
        lo = pay.min if pay.min is not None else pay.max
        hi = pay.max if pay.max is not None else pay.min
        assert lo is not None and hi is not None
        values = {"conservative": lo, "base": (lo + hi) // 2, "optimistic": hi}
        scenarios = []
        for key in ("conservative", "base", "optimistic"):
            v, h = values[key], hours[key]
            contribution = v - 0
            scenarios.append(
                Scenario(
                    key,  # type: ignore[arg-type]
                    v,
                    0,
                    h,
                    contribution,
                    round(contribution / h) if h > 0 else None,
                    (contribution - round(h * target)) if (target and h > 0) else None,
                )
            )
        basis = f"게시 금액 {_pay_short(pay)}" + (" (하한만 기재)" if pay.max is None else " (상한만 기재)" if pay.min is None else "")
        return Profitability("calculated", basis, None, assumptions_common, scenarios, "one_time", target)
    if pay.unit in ("project", "negotiable", "unknown") and pay.min is None and pay.max is None and intent.startswith("buyer"):
        if not target:
            return Profitability(
                "not_calculated",
                None,
                "예산 미기재이고 목표 시간당 가치가 설정되지 않아 견적 가설도 만들지 않았습니다.",
                [],
                [],
                recurring,  # type: ignore[arg-type]
                None,
            )
        scenarios = [
            Scenario(k, round(hours[k] * target, -4), None, hours[k], None, None, None)  # type: ignore[arg-type]
            for k in ("conservative", "base", "optimistic")
        ]
        return Profitability(
            "hypothesis",
            "제안 견적 가설 — 게시 예산 아님",
            "예산이 기재되지 않아 예상 기여액은 계산하지 않았습니다. 아래는 예상 시간 × 목표 시간가치로 만든 조정 가능한 견적 가설입니다.",
            assumptions_common[:1] + [f"목표 시간당 가치 {target:,}원 (내 설정)"],
            scenarios,
            recurring,  # type: ignore[arg-type]
            target,
        )
    reason = "지급 단위를 확인할 수 없어 계산하지 않았습니다." if pay.min is not None else "예산 미기재 — 예상 기여액을 계산하지 않았습니다."
    return Profitability("not_calculated", None, reason, [], [], recurring, target)  # type: ignore[arg-type]


def analyze(
    post: PostInput,
    profile: Profile,
    now: datetime,
    ttl_hours: int = 24,
    feedback: dict[str, str] | None = None,
) -> RuleAnalysis:
    """feedback: 사용자 확인 {'remote': 'confirmed'|'denied', 'real_request': 'yes'|'no'} — 자동 판정과 별도 저장된 값을 근거로 덧붙인다."""
    title, body = post.title, post.body
    feedback = feedback or {}
    categories, dev = rules.classify_categories(title, body)
    intent = rules.classify_intent(title, body)
    non_delivery = rules.non_delivery_reason(title, body)
    if non_delivery:
        intent = Judgement("demand_intent", "unknown", None, [non_delivery[1]])
    engagement = rules.classify_engagement(title, body, intent.value)
    work = rules.classify_work_mode(title, body)
    collab = rules.classify_collaboration(title, body, work.value)
    scope = rules.classify_scope(title, body)
    if feedback.get("remote") == "confirmed":
        mine = Ev("내가 재택·온라인 진행 가능으로 확인", None, None, "user_confirmed", None, "사용자 확인")
        work = Judgement("work_mode", "fully_remote", "user_confirmed", [mine, *work.evidence])
        if collab.value == "unknown":
            collab = Judgement("collaboration_mode", "online_only", "user_confirmed", [mine])
    elif feedback.get("remote") == "denied":
        mine = Ev("내가 재택 불가로 확인", None, None, "user_confirmed", None, "사용자 확인")
        work = Judgement("work_mode", "onsite", "user_confirmed", [mine, *work.evidence])
    if feedback.get("real_request") == "yes" and not intent.value.startswith("buyer"):
        mine = Ev("내가 실제 의뢰로 확인", None, None, "user_confirmed", None, "사용자 확인")
        intent = Judgement("demand_intent", "buyer_project", "user_confirmed", [mine, *intent.evidence])
    pay, pay_conflict = _resolve_pay(post.pay_hint, parse_pay(body, title))
    deadline = post.deadline_hint or dates.parse_deadline(body, post.observed_at)
    work_start = dates.parse_work_start(body, post.observed_at)
    closed = dates.closed_marker(body, title)
    risks = rules.detect_risks(body)
    excluded_hit = _excluded_hit(f"{title}\n{body}", profile)

    factors = [
        _score_work_fit(categories, dev, profile, excluded_hit),
        _score_intent(intent, engagement),
        _score_remote(work, collab, profile),
        _score_freshness(post, now, ttl_hours),
        _score_clarity(body, pay),
    ]
    penalty = RISK_PENALTY_EACH * len(risks)
    known = [f.score for f in factors if f.score is not None]
    total = max(0, sum(known) - penalty) if known else None
    pay_factor, low_pay_review = _pay_priority(pay, categories, profile, factors[4].score or 0, total, pay_conflict)
    factors.append(pay_factor)
    if total is not None:
        total = max(0, total + (pay_factor.score or 0))
    unknown = sum(1 for f in factors if f.score is None)

    # Source metadata can contain wages absent from the body; a body wage also counts.
    time_unit = pay.unit if pay.unit in TIME_PAY_LABELS else time_pay_unit(pay.raw or "") or time_pay_unit(body) or time_pay_unit(title)
    # 2026-11-07 사용자 결정: 당근은 글쓴이가 보수 유형을 고르게 해서 짧은 개발 의뢰도 시급·월급으로 올라온다.
    # 재택 가능한 개발 구매 의뢰는 시간 단위 보수여도 제외하지 않고 감점·확인 표시와 함께 검토에 올린다.
    # 자동 지원은 계속 건당 보수만 대상이다 (jobs.auto_apply.condition).
    # 연봉은 정의상 고용 조건이라 제외를 유지한다.
    time_pay_review = (time_unit in ("hour", "day", "week", "month") and dev
                       and intent.value in ("buyer_project", "buyer_ongoing") and work.value != "onsite")
    if time_pay_review and time_unit:
        factors.append(Factor("time_pay", "시간 단위 보수 감점", 0, -TIME_PAY_REVIEW_PENALTY,
                              f"{TIME_PAY_LABELS[time_unit]} 표기 · 실제 작업 단위와 총액을 확인하기 전까지 감점"))
        if total is not None:
            total = max(0, total - TIME_PAY_REVIEW_PENALTY)

    # ── 적격성 → 추천 ────────────────────────────────────────────────
    status = post.source_status
    access_ok = post.access_status == "accessible"
    fresh = post.last_checked_at is not None and (now - post.last_checked_at) <= timedelta(hours=ttl_hours)
    reasons: list[Reason] = []
    exclude: list[str] = []
    review: list[str] = []

    if status == "closed":
        exclude.append("모집 마감" + (" (원문 표시)" if closed else ""))
    if status == "deleted":
        exclude.append("원문 삭제 확인")
    if post.collection_allowed is False:
        exclude.append("공개 수집 허용 불가·미확인 공고")
    if rules.salary_evidence(title, body) or rules.salary_evidence("", pay.raw or ""):
        exclude.append("급여·salary 지급 — 건당 프리랜서 의뢰 아님")
    # Deliverable/project payments are eligible; time wages only for remote
    # development requests, which go to review (time_pay_review above).
    if time_unit and not time_pay_review:
        exclude.append(f"{TIME_PAY_LABELS[time_unit]} 지급 — 건당 프리랜서 의뢰 아님")
    elif time_unit:
        review.insert(0, f"{TIME_PAY_LABELS[time_unit]} 표기 — 실제 작업 단위·총액 확인")
    elif pay.unit != "project":
        exclude.append("건당·프로젝트 지급 미확인")
    if intent.value == "seller_service":
        exclude.append("판매자 홍보 — 구매 의뢰 아님")
    if intent.value == "job_seeker":
        exclude.append("구직 글 — 의뢰 아님")
    if work.value == "onsite":
        exclude.append("출근 필수")
    if engagement.value == "full_time":
        exclude.append("정규직 채용 — 외주 아님" + (" (재택이어도 구분)" if work.value == "fully_remote" else ""))
    elif engagement.value in ("part_time", "hourly_contract"):
        exclude.append("시간제·아르바이트 고용 — 건당 프리랜서 의뢰 아님")
    if risks:
        exclude.append("위험 신호: " + " · ".join(r.label for r in risks))
    if excluded_hit:
        exclude.append(f"제외 업무 포함 ({excluded_hit})")
    if feedback.get("real_request") == "no":
        exclude.append("내가 실제 의뢰 아님으로 표시")
    if intent.value == "employee_hiring" and rules.operational_staff_evidence(title, body) and feedback.get("real_request") != "yes":
        exclude.append("쇼핑몰 운영 인력 모집 — 제작 납품 의뢰 아님")
    if non_delivery and feedback.get("real_request") != "yes":
        exclude.append(non_delivery[0])
    if not dev:
        exclude.append("단순 데이터 작업 — 제작 의뢰 아님" if "data" in categories else "개발·자동화 업무 아님")

    if intent.value == "employee_hiring":
        exclude.append("인력 채용 — 제작 납품 의뢰 아님")
    elif intent.value not in ("buyer_project", "buyer_ongoing", "seller_service", "job_seeker"):
        exclude.append("프리랜서 제작 의뢰 미확인")

    contract_floor_review = _contract_floor_caution(pay, profile.min_contract)
    if pay_conflict:
        review.append(pay_conflict)
    if contract_floor_review:
        review.append(contract_floor_review)
    if low_pay_review:
        review.append(low_pay_review)
    if not dev:
        review.append("개발 업무 여부 불명확")
    if not access_ok:
        review.append({"blocked": "접근 차단 — 모집 상태 확인 불가", "login_required": "로그인 필요 — 모집 상태 확인 불가"}.get(post.access_status, "접근 오류 — 모집 상태 확인 불가"))
    elif status == "unknown":
        review.append("모집 상태 미확인" + (" — 수동 입력" if post.source_kind == "manual" else " — 마감 표시 없음만으로 판단 안 함"))
    elif status == "open" and not fresh:
        review.append("모집 재확인 필요")
    if work.value == "unknown":
        review.append("재택 미확인")
    elif work.value == "fully_remote" and work.basis == "inferred":
        review.append("재택 추정 — 명시 없음")
    elif work.value in ("hybrid", "negotiable"):
        review.append("재택 조건 확인 필요")
    if collab.value == "onsite_required" and profile.onsite != "yes":
        q = collab.evidence[0].quote if collab.evidence else ""
        review.append("대면 필요" + (" (첫 미팅)" if "첫" in q else ""))
    elif work.value == "fully_remote" and collab.value not in ("online_only",) and work.basis in ("explicit", "user_confirmed"):
        review.append("진행 방식(대면 여부) 미확인")
    if scope.value == "regional_restriction":
        review.append("지원 지역 제한 — 자격 확인")

    if exclude:
        recommendation = "excluded"
        reasons = [Reason("negative", t) for t in exclude[:3]]
    elif review:
        recommendation = "needs_review"
        reasons = [Reason("caution", t) for t in review[:2]]
    else:
        recommendation = "recommended"

    positives: list[str] = []
    if intent.value in ("buyer_project", "buyer_ongoing") and intent.basis in ("explicit", "user_confirmed"):
        positives.append("지속 의뢰 명시" if intent.value == "buyer_ongoing" else "구매 의뢰 명시")
    if work.value == "fully_remote" and work.basis in ("explicit", "user_confirmed"):
        positives.append("재택·온라인 명시" if collab.value == "online_only" else "재택 명시")
    if scope.value == "nationwide":
        positives.append("전국 지원")
    ps = _pay_short(pay)
    if ps:
        positives.append(ps)
    if recommendation != "excluded":
        missing_pay = pay.min is None and pay.max is None
        room = 3 - len(reasons) - (1 if missing_pay else 0)
        reasons.extend(Reason("positive", t) for t in positives[: max(0, room)])
        if missing_pay and len(reasons) < 3:
            # 예산 없음은 0원·부적합이 아니라 확인 필요 사항
            reasons.append(Reason("caution", "금액 협의 — 견적 필요" if pay.unit == "negotiable" else "예산 미기재 — 확인 필요"))

    # ── 설명 ──────────────────────────────────────────────────────────
    fit, unfit, uncertain, questions = [], [], [], []
    if pay_conflict:
        uncertain.append(pay_conflict)
        questions.append("사이트 표시 보수와 본문 금액이 다릅니다. 실제 건당 계약 금액은 얼마인가요?")
    if contract_floor_review and not exclude and profile.min_contract is not None:
        uncertain.append(contract_floor_review)
        questions.append(f"프로젝트당 확정 계약금액을 내 최소 계약금액인 {_fmt_krw(profile.min_contract)} 이상으로 협의할 수 있을까요?")
    if low_pay_review and not exclude:
        uncertain.append(low_pay_review)
        questions.append("게시된 보수가 전체 작업의 계약금액인가요? 작업 범위에 맞춰 금액을 협의할 수 있을까요?")
    if dev:
        fit.append(", ".join(CATEGORY_LABEL.get(c, c) for c in categories if c != "other") + " 업무")
    if intent.value.startswith("buyer"):
        fit.append("구매 수요")
    if work.value == "fully_remote":
        fit.append("재택 " + ("명시" if work.basis == "explicit" else "추정"))
    unfit.extend(exclude)
    if work.value in ("unknown",) or (work.value == "fully_remote" and work.basis == "inferred"):
        uncertain.append("원격 진행 가능 여부")
        questions.append("원격으로만 진행해도 괜찮을까요?")
    if collab.value == "onsite_required":
        uncertain.append("대면 일정")
        questions.append("대면 미팅을 화상으로 대체할 수 있을까요?")
    if pay.min is None and pay.max is None:
        uncertain.append("예산")
        questions.append("생각하시는 예산 범위가 있으신가요?")
    if status in ("unknown",) or not access_ok or (status == "open" and not fresh):
        uncertain.append("현재 모집 여부")
        questions.append("아직 작업자를 찾고 계신가요?")
    if intent.value == "unknown":
        uncertain.append("외주 프로젝트인지 고용인지")
        questions.append("프로젝트 단위 외주인가요, 기간제 고용인가요?")
    if factors[4].score is not None and factors[4].score < 6:
        uncertain.append("세부 요구 범위")
        questions.append("필요한 기능이나 페이지 목록을 받을 수 있을까요?")
    if deadline.at is None and intent.value.startswith("buyer"):
        questions.append("희망하시는 완료 일정이 있으신가요?")

    conversion = None
    if intent.value == "employee_hiring" and "data" in categories:
        conversion = "반복 정리 작업이라 자동화 도구 납품을 제안할 여지가 있습니다. 이미 존재하는 개발 의뢰로 집계하지 않습니다."
        review_note = "자동화 제안 기회 (의뢰 아님)"
        if recommendation == "needs_review" and all(r.text != review_note for r in reasons):
            reasons = reasons[:2] + [Reason("caution", review_note)]

    next_action = None
    if recommendation == "excluded":
        next_action = "검토 불필요" + (" — 작성자를 단정하지 않고 위험 신호만 표시" if risks else "")
    elif "현재 모집 여부" in uncertain and not access_ok:
        next_action = "원문을 직접 열어 모집 여부 확인"
    elif questions:
        next_action = "핵심 질문 확인 후 범위·일정 포함 견적 제시"

    prof = profitability(categories, pay, intent.value, profile, factors[4].score or 0)
    if pay_conflict:
        prof = Profitability("not_calculated", None, pay_conflict + " — 실제 계약 금액 확인 후 계산", [], [],
                             prof.revenue_type, profile.target_hourly)

    return RuleAnalysis(
        rule_version=rules.RULE_VERSION,
        categories=categories,
        dev_relevant=dev,
        judgements={
            "demand_intent": intent,
            "engagement_type": engagement,
            "work_mode": work,
            "collaboration_mode": collab,
            "applicant_scope": scope,
        },
        pay=pay,
        deadline=deadline,
        work_start=work_start,
        closed_marker=closed,
        risks=risks,
        factors=factors,
        risk_penalty=penalty,
        risk_reasons=[r.label for r in risks],
        total=total,
        unknown_factors=unknown,
        recommendation=recommendation,  # type: ignore[arg-type]
        reasons=reasons[:3],
        summary=first_sentence(body),
        fit=fit,
        unfit=unfit,
        uncertain=uncertain,
        deliverables=[],
        tech=[],
        questions=questions[:4],
        next_action=next_action,
        conversion_opportunity=conversion,
        profitability=prof,
    )


def inquiry_draft(title: str, analysis: RuleAnalysis, *, reply: bool = False) -> str:
    """수정 가능한 문의 초안. 경력·포트폴리오·가격을 만들어내지 않고 사용자가 채울 자리를 남긴다."""
    lines = [
        "안녕하세요, 올려주신 글 보고 연락드립니다.",
        f"'{title.strip()}' 관련해 원격으로 작업 가능한지 여쭙고 싶습니다.",
        "",
        "[간단한 소개와 비슷한 작업 경험 — 직접 작성]",
        "",
    ]
    if reply:
        lines = ['문의 남겨주셔서 감사합니다.', '원하시는 작업의 목적과 기능, 희망 일정·예산을 알려주시면 범위를 확인하겠습니다.', '']
    if analysis.questions:
        lines.append("진행 전에 몇 가지 확인하고 싶습니다.")
        for i, q in enumerate(analysis.questions, 1):
            lines.append(f"{i}) {q}")
        lines.append("")
    lines.append("답변 주시면 범위와 일정, 견적을 정리해 드리겠습니다.")
    return "\n".join(lines)
