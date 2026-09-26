"""규칙 분석 전체 흐름: 분류 → 적격성 → 설명 가능한 우선순위 → 수익성 시나리오 → 확인 질문.

우선순위 점수는 제품 초기 설정 가중치이며 수주 확률이 아니다.
모르는 요소는 score=None 으로 두고, 남은 요소만으로 재정규화하지 않는다.
점수가 높아도 필수 적격성 조건을 통과하지 못하면 recommended 가 되지 않는다.
"""

from __future__ import annotations

import re

from datetime import datetime, timedelta

from . import dates, rules
from .pay import parse_pay
from .text import first_sentence
from .types import Ev, Factor, Judgement, PayInfo, PostInput, Profile, Profitability, Reason, RuleAnalysis, Scenario

WEIGHTS = {
    "work_fit": ("업무 적합성", 30),
    "buyer_intent": ("구매 의도", 25),
    "remote_fit": ("원격 적합성", 20),
    "freshness": ("최신성", 15),
    "scope_clarity": ("범위 명확성", 10),
}
RISK_PENALTY_EACH = 15

CATEGORY_LABEL = {
    "website": "웹사이트",
    "landing": "랜딩페이지",
    "shop": "쇼핑몰",
    "fullstack": "풀스택·웹 서비스",
    "software": "프로그램·앱",
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
        return Factor("work_fit", label, mx, min(mx, 24 + 3 * len(matched)), f"{names} — 제공 서비스와 일치")
    if profile.services:
        return Factor("work_fit", label, mx, 16, f"{names} — 제공 서비스 목록에 없음")
    return Factor("work_fit", label, mx, 20, f"{names} — 제공 서비스 미설정")


def _score_intent(intent: Judgement, engagement: Judgement) -> Factor:
    label, mx = WEIGHTS["buyer_intent"]
    v, b = intent.value, intent.basis
    if v == "buyer_project":
        return Factor("buyer_intent", label, mx, 23 if b == "explicit" else 18, "구매 표현 " + ("명시" if b == "explicit" else "추정"))
    if v == "buyer_ongoing":
        return Factor("buyer_intent", label, mx, 21 if b == "explicit" else 16, "지속 의뢰 " + ("명시" if b == "explicit" else "추정"))
    if v == "short_gig":
        return Factor("buyer_intent", label, mx, 18, "프리랜서·단기 작업 구인 (고용 형식)")
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
            pts, why = (20, "재택·온라인 진행 명시") if collab.value == "online_only" else (17, "재택 명시, 진행 방식 일부 불명확")
        else:
            pts, why = 8, "재택 명시 없음 — 추정"
    elif v == "hybrid":
        pts, why = 8, "일부 재택"
    else:
        pts, why = 10, "재택 협의"
    if collab.value == "onsite_required":
        cap = {"no": 8, "first_meeting": 15, "yes": 18}[profile.onsite]
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


TIME_PAY_LABEL = {"hour": "시급", "day": "일급", "week": "주급", "month": "월 단위"}


def _time_based_pay(pay: PayInfo) -> str | None:
    """시간·기간 단위 보수면 그 이름 (건당·1회성 작업만 볼 때 제외 근거). 연봉은 단위 목록에 없어 원문 표현으로 확인한다."""
    if pay.unit in TIME_PAY_LABEL:
        return TIME_PAY_LABEL[pay.unit]
    if pay.unit == "unknown" and pay.raw and re.search(r"연봉", pay.raw):
        return "연봉"
    return None


def _pay_short(pay: PayInfo) -> str | None:
    if pay.min is None and pay.max is None:
        return None
    unit = {"project": "예산 ", "hour": "시급 ", "day": "일급 ", "week": "주급 ", "month": "월 "}.get(pay.unit, "")
    if pay.min is not None and pay.max is not None and pay.min != pay.max:
        return f"{unit}{_fmt_krw(pay.min)}~{_fmt_krw(pay.max)}"
    return f"{unit}{_fmt_krw(pay.min if pay.min is not None else pay.max)}"  # type: ignore[arg-type]


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
    engagement = rules.classify_engagement(title, body, intent.value)
    if profile.allow_short_term_employment:
        intent = rules.classify_short_gig(title, body, intent, engagement, dev)
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
    pay = post.pay_hint or parse_pay(body, title)
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
    unknown = sum(1 for f in factors if f.score is None)

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
    time_pay = _time_based_pay(pay) if profile.gig_only else None
    if time_pay:
        exclude.append(f"{time_pay} 보수 — 건당·1회성 작업 아님")
    if intent.value == "seller_service":
        exclude.append("판매자 홍보 — 구매 의뢰 아님")
    if intent.value == "job_seeker":
        exclude.append("구직 글 — 의뢰 아님")
    if work.value == "onsite":
        exclude.append("출근 근무로 보임 (구인 양식 근무지가 사업장)" if work.basis == "inferred" else "출근 필수")
    if engagement.value == "full_time":
        exclude.append("정규직 채용 — 외주 아님" + (" (재택이어도 구분)" if work.value == "fully_remote" else ""))
    if risks:
        exclude.append("위험 신호: " + " · ".join(r.label for r in risks))
    if excluded_hit:
        exclude.append(f"제외 업무 포함 ({excluded_hit})")
    trainee = rules.find(rules.TRAINEE_AD, "", title, "explicit")
    if trainee:
        exclude.append("교육생·수강생 모집 광고 — 일거리 아님")
    if feedback.get("real_request") == "no":
        exclude.append("내가 실제 의뢰 아님으로 표시")
    if not dev and "data" not in categories:
        exclude.append("개발·자동화 업무 아님")

    one_off = rules.find(rules.ONE_OFF, body, title, "explicit") if profile.gig_only else None
    long_term = rules.find(rules.LONG_TERM, body, title, "explicit") if profile.gig_only else None
    if long_term and not one_off:
        review.append("장기·정기 작업 — 1회성 아님")
    teaching = rules.find(rules.TEACHING, body, title, "explicit")
    if teaching and not exclude:
        review.insert(0, "교육·과외 요청 — 제작 의뢰인지 확인")
    if intent.value not in ("buyer_project", "buyer_ongoing"):
        if intent.value == "employee_hiring":
            review.append("단기 고용 — 개발 외주 아님")
        elif intent.value == "unknown":
            review.append("의뢰·채용 구분 불명확")
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
    elif work.value == "fully_remote" and collab.value not in ("online_only",) and work.basis in ("explicit", "user_confirmed") and intent.value != "short_gig":
        # 짧은 재택 작업 구인은 대면 여부까지 적는 경우가 드물어 확인 필요로 돌리지 않는다
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
    if intent.value == "short_gig":
        positives.append("프리랜서·단기 작업")
    if one_off or (profile.gig_only and pay.unit == "project" and not long_term):
        positives.insert(0, "1회성·건당 작업")
    if post.published.at is not None and status == "open" and (now - post.published.at) <= timedelta(hours=24):
        hrs = max(1, int((now - post.published.at).total_seconds() // 3600))
        positives.insert(0, f"{hrs}시간 전 게시 — 빠른 연락 유리")
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
    if dev:
        fit.append(", ".join(CATEGORY_LABEL.get(c, c) for c in categories if c != "other") + " 업무")
    if intent.value.startswith("buyer"):
        fit.append("구매 수요")
    elif intent.value == "short_gig":
        fit.append("프리랜서·단기 작업 수요")
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
    if deadline.at is None and (intent.value.startswith("buyer") or intent.value == "short_gig"):
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


def quick_message(title: str, questions: list[str], intro: str = "", portfolio_url: str | None = None) -> str:
    """구인 글에 가볍게 보내는 짧은 메시지. 경력·가격을 지어내지 않고, 비어 있는 값은 채울 자리로 남긴다."""
    lines = [f"안녕하세요, 올려주신 '{title.strip()}' 글 보고 연락드립니다."]
    lines.append(intro.strip() if intro.strip() else "[한 줄 소개 — 설정 > 프로필에서 입력하면 자동으로 들어갑니다]")
    lines.append(f"비슷한 작업 예시: {portfolio_url}" if portfolio_url else "[포트폴리오 링크 — 설정 > 프로필에서 입력]")
    lines.append("바로 시작할 수 있고, 내용 확인 후 금액과 일정을 먼저 알려드리겠습니다.")
    if questions:
        lines.append(f"혹시 {questions[0]}")
    return "\n".join(lines)


def inquiry_draft(title: str, analysis: RuleAnalysis) -> str:
    """수정 가능한 문의 초안. 경력·포트폴리오·가격을 만들어내지 않고 사용자가 채울 자리를 남긴다."""
    lines = [
        "안녕하세요, 올려주신 글 보고 연락드립니다.",
        f"'{title.strip()}' 관련해 원격으로 작업 가능한지 여쭙고 싶습니다.",
        "",
        "[간단한 소개와 비슷한 작업 경험 — 직접 작성]",
        "",
    ]
    if analysis.questions:
        lines.append("진행 전에 몇 가지 확인하고 싶습니다.")
        for i, q in enumerate(analysis.questions, 1):
            lines.append(f"{i}) {q}")
        lines.append("")
    lines.append("답변 주시면 범위와 일정, 견적을 정리해 드리겠습니다.")
    return "\n".join(lines)
