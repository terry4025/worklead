"""규칙 판정 검증. 요구 검증 사례 5, 6, 7, 8, 12 를 다룬다."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from worklead.analysis.pay import parse_pay
from worklead.analysis.service import analyze
from worklead.analysis.types import PostInput, Profile
from worklead.engine.robots import RobotsRules

NOW = datetime(2026, 9, 26, 3, 0, tzinfo=UTC)
PROFILE = Profile(services=["website", "landing", "shop", "fullstack", "software", "vba", "automation"], target_hourly=40_000)


def run(title: str, body: str, status: str = "open", checked_h: float = 1, **kw):
    post = PostInput(
        title=title,
        body=body,
        observed_at=NOW,
        source_status=status,
        last_checked_at=NOW - timedelta(hours=checked_h),
        first_seen_at=NOW - timedelta(hours=5),
        **kw,
    )
    return analyze(post, PROFILE, NOW)


# ── 사례 5: 재택 판정 ────────────────────────────────────────────────
def test_remote_explicit() -> None:
    a = run("랜딩페이지 제작 의뢰", "랜딩페이지 제작 의뢰합니다. 전 과정 원격으로 진행합니다. 예산 80만원.")
    assert a.judgements["work_mode"].value == "fully_remote"
    assert a.judgements["work_mode"].basis == "explicit"
    ev = a.judgements["work_mode"].evidence[0]
    assert ev.start is not None and "원격" in ev.quote


def test_first_day_visit_is_onsite_required() -> None:
    a = run("랜딩페이지 제작", "랜딩페이지 제작 의뢰합니다. 재택 가능하지만 첫날 방문 교육이 있습니다.")
    assert a.judgements["work_mode"].value == "fully_remote"
    assert a.judgements["collaboration_mode"].value == "onsite_required"
    assert a.recommendation != "recommended"


def test_online_shop_staff_is_not_remote() -> None:
    a = run("온라인 쇼핑몰 직원", "온라인 쇼핑몰 상품 등록 업무입니다. 근무지: 김포 사무실 (주 5일 출근). 월급 230만원.")
    assert a.judgements["work_mode"].value == "onsite"
    assert a.recommendation == "excluded"


def test_remote_full_time_is_not_outsourcing() -> None:
    a = run("C# 개발자 재택 정규직", "재고관리 프로그램 개발 정규직 채용합니다. 완전 재택 근무. 연봉 4,200만원 협의.")
    assert a.judgements["work_mode"].value == "fully_remote"
    assert a.judgements["engagement_type"].value == "full_time"
    assert a.recommendation == "excluded"
    assert any("정규직" in r.text for r in a.reasons)


def test_no_remote_mention_is_unknown() -> None:
    a = run("예약 페이지 만들어주세요", "네일샵 예약 페이지를 만들고 싶어요. 날짜랑 시간 선택하면 예약되면 됩니다.")
    assert a.judgements["work_mode"].value == "unknown"
    assert a.recommendation == "needs_review"
    remote = next(f for f in a.factors if f.key == "remote_fit")
    assert remote.score is None, "모르는 요소는 0점이 아니라 미평가"


def test_freelancer_word_alone_is_not_remote() -> None:
    a = run("앱 개발자 구합니다 (프리랜서)", "앱·웹 개발 가능한 프리랜서 구합니다. React 경험자 우대.")
    assert a.judgements["work_mode"].value == "unknown"
    # 프리랜서 구인은 '프리랜서·단기 작업'(추정)으로 보지만 재택 근거는 아니다
    assert a.judgements["demand_intent"].value == "short_gig"
    assert a.judgements["demand_intent"].basis == "inferred"


def test_address_alone_does_not_mean_onsite() -> None:
    a = run("홈페이지 제작 의뢰", "회사 주소: 서울 강남구 테헤란로 1. 홈페이지 제작 의뢰합니다.")
    assert a.judgements["work_mode"].value == "unknown"


def test_form_workplace_is_inferred_onsite_unless_body_says_remote() -> None:
    cond = "\n\n— 구인 양식 표시 조건 —\n근무지 유형: 사업장 (서울특별시 강남구)\n고용 형태: 알바"
    a = run("웹개발 알바", "쇼핑몰 상세페이지 코딩 가능한 분 구합니다." + cond)
    w = a.judgements["work_mode"]
    assert (w.value, w.basis) == ("onsite", "inferred")
    assert a.recommendation == "excluded" and any("출근 근무로 보임" in r.text for r in a.reasons)
    # 본문에 재택이 명시되면 양식 근무지보다 본문을 따른다
    b = run("웹개발 알바", "쇼핑몰 상세페이지 코딩. 재택 가능합니다." + cond)
    assert b.judgements["work_mode"].value == "fully_remote"


# ── 건당·1회성 작업만 ────────────────────────────────────────────────
def test_gig_only_excludes_time_based_pay_and_prefers_one_off() -> None:
    hourly = run("쇼핑몰 상세페이지 코딩 알바 (재택)", "재택근무로 쇼핑몰 상세페이지 HTML 코딩해 주실 분. 프리랜서. 시급 15,000원")
    assert hourly.recommendation == "excluded"
    assert any(r.text == "시급 보수 — 건당·1회성 작업 아님" for r in hourly.reasons)
    monthly = run("웹 개발자 (재택, 3개월)", "재택근무 웹 개발 프리랜서. 월급 300만원")
    assert monthly.recommendation == "excluded" and any("월 단위 보수" in r.text for r in monthly.reasons)
    yearly = run("웹 개발자 채용", "재택근무 웹 개발. 연봉 4,000만원")
    assert yearly.recommendation == "excluded" and any("연봉 보수" in r.text for r in yearly.reasons)

    gig = run("홈페이지 수정 건당 작업 (재택)", "회사 홈페이지 문구·이미지 수정 한 번만 작업해 주실 분. 미팅 없이 온라인으로만 진행. 건당 20만원")
    assert gig.recommendation == "recommended", gig.reasons
    assert gig.reasons[0].text == "1회성·건당 작업"

    # 매일 하는 일을 자동화해 달라는 요청은 장기 근무가 아니다
    daily = run("엑셀 자동화 (재택)", "매일 하는 엑셀 정리 작업을 매크로로 자동화해 주실 분. 미팅 없이 온라인으로만 진행. 예산 30만원")
    assert not any("장기·정기" in r.text for r in daily.reasons)

    ongoing = run("쇼핑몰 관리 (재택)", "쇼핑몰 상품 등록 페이지 수정을 장기로 맡아 주실 분. 미팅 없이 온라인으로만 진행. 예산 50만원")
    assert ongoing.recommendation == "needs_review"
    assert any(r.text == "장기·정기 작업 — 1회성 아님" for r in ongoing.reasons)


def test_gig_only_can_be_turned_off() -> None:
    body = "재택근무로 쇼핑몰 상세페이지 HTML 코딩해 주실 분. 프리랜서. 시급 15,000원"
    off = analyze(
        PostInput(title="쇼핑몰 상세페이지 코딩 알바 (재택)", body=body, observed_at=NOW, source_status="open", last_checked_at=NOW, first_seen_at=NOW),
        replace(PROFILE, gig_only=False),
        NOW,
    )
    assert not any("시급 보수" in r.text for r in off.reasons)


# ── 사례 6: 판매자 홍보 vs 의뢰 ─────────────────────────────────────
def test_seller_vs_buyer() -> None:
    seller = run("홈페이지 만들어드립니다", "홈페이지 만들어드립니다. 저렴하게 빠르게.")
    buyer = run("홈페이지 만들어주실 분 구합니다", "병원 홈페이지 만들어주실 분 구합니다. 재택 가능.")
    assert seller.judgements["demand_intent"].value == "seller_service"
    assert seller.recommendation == "excluded"
    assert buyer.judgements["demand_intent"].value == "buyer_project"


def test_excel_office_job_is_not_vba_request() -> None:
    a = run("엑셀 가능한 사무직", "사무실 근무, 엑셀 가능한 사무직 구합니다. 주 5일 출근.")
    assert "vba" not in a.categories
    assert a.judgements["demand_intent"].value == "employee_hiring"


# ── 사례 7: 보수 보존 ────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("body", "unit", "lo", "hi", "negotiable"),
    [
        ("시급 12,000원, 하루 4시간", "hour", 12_000, 12_000, False),
        ("월급 230만원, 4대보험", "month", 2_300_000, 2_300_000, False),
        ("건당 30만원", "project", 300_000, 300_000, False),
        ("예산: 50~80만원 (범위 확인 후 조정 가능)", "project", 500_000, 800_000, True),
        ("금액은 작업 범위 보고 협의하고 싶습니다.", "negotiable", None, None, True),
        ("예산은 아직 정하지 못했어요.", "unknown", None, None, False),
        ("연봉 4,200만원 협의", "unknown", 42_000_000, 42_000_000, True),
        ("1억 5천만원 규모 프로젝트 예산", "project", 150_000_000, 150_000_000, False),
    ],
)
def test_pay_preserved(body: str, unit: str, lo: int | None, hi: int | None, negotiable: bool) -> None:
    p = parse_pay(body)
    assert (p.unit, p.min, p.max, p.negotiable) == (unit, lo, hi, negotiable)
    if lo is None:
        assert p.min is None, "모르는 금액은 0 이 아니라 null"


def test_no_budget_is_not_zero_and_not_excluded() -> None:
    a = run("홈페이지 만들어주실 분", "홈페이지 만들어주실 분 구합니다. 전 과정 비대면 진행. 지역 무관.")
    assert a.pay.min is None and a.pay.max is None
    assert a.recommendation == "recommended"
    assert any("예산" in r.text for r in a.reasons)
    assert a.profitability.status == "hypothesis"
    assert all(sc.contribution is None for sc in a.profitability.scenarios), "예산이 없으면 예상 기여액은 null"


def test_hourly_pay_not_converted_to_project_revenue() -> None:
    a = run("데이터 정리 알바 (재택)", "엑셀 데이터 정리 작업. 재택 가능. 시급 12,000원, 하루 4시간.")
    assert a.profitability.status == "not_calculated"
    assert a.profitability.scenarios == []


# ── 사례 8: 날짜 구분 ────────────────────────────────────────────────
def test_deadline_vs_work_start() -> None:
    a = run("랜딩 제작 의뢰", "랜딩페이지 제작 의뢰합니다. 10월 3일까지 지원 받습니다. 10월 10일부터 시작합니다.")
    assert a.deadline.raw and "10월 3일" in a.deadline.raw
    assert a.work_start.raw and "10월 10일" in a.work_start.raw
    assert a.deadline.at != a.work_start.at


def test_closed_marker_and_passed_deadline() -> None:
    a = run("[마감] 랜딩 제작", "[마감] 랜딩페이지 제작 의뢰합니다.")
    assert a.closed_marker is not None


# ── 사례 12: 적격성 ─────────────────────────────────────────────────
def test_high_score_but_ineligible_is_not_recommended() -> None:
    body = "쇼핑몰 홈페이지 제작 의뢰합니다. 전 과정 원격으로 진행, 지역 무관. 예산 300만원. 페이지 10개, 관리자 기능, 결제 연동 필요."
    open_ = run("쇼핑몰 홈페이지 제작 의뢰", body)
    closed = run("쇼핑몰 홈페이지 제작 의뢰", body, status="closed")
    stale = run("쇼핑몰 홈페이지 제작 의뢰", body, checked_h=72)
    blocked = run("쇼핑몰 홈페이지 제작 의뢰", body, access_status="blocked")
    assert open_.recommendation == "recommended"
    assert closed.recommendation == "excluded"
    assert stale.recommendation == "needs_review"
    assert blocked.recommendation == "needs_review"
    assert (stale.total or 0) >= 60


def test_risk_signals_with_quotes() -> None:
    a = run("재택 부업", "월 300만원 이상 고수익 보장! 교육 자료비 5만원 입금 후 안내드립니다.")
    ids = {r.id for r in a.risks}
    assert {"upfront_payment", "income_guarantee"} <= ids
    assert all(r.quote for r in a.risks)
    assert a.recommendation == "excluded"


def test_developer_down_payment_is_not_risk() -> None:
    a = run("웹 개발 외주", "웹 서비스 개발 외주 맡기려고 합니다. 계약 후 착수금 30% 입금해 드립니다.")
    assert not a.risks


def test_user_feedback_overlay() -> None:
    body = "홈페이지 만들어주실 분 구합니다. 예산 200만원."
    post = PostInput(title="홈페이지 제작", body=body, observed_at=NOW, source_status="open", last_checked_at=NOW, first_seen_at=NOW)
    auto = analyze(post, PROFILE, NOW)
    mine = analyze(post, PROFILE, NOW, feedback={"remote": "confirmed"})
    assert auto.judgements["work_mode"].value == "unknown"
    assert mine.judgements["work_mode"].basis == "user_confirmed"
    assert mine.recommendation == "recommended"


# ── robots.txt ─────────────────────────────────────────────────────
def test_robots_longest_match() -> None:
    r = RobotsRules("User-agent: *\nDisallow: /jobs\nAllow: /jobs/public\n\nUser-agent: WorkleadLocal\nDisallow: /private$\n")
    assert r.allowed("https://x/jobs/public/1", "OtherBot/1.0")
    assert not r.allowed("https://x/jobs/1", "OtherBot/1.0")
    assert r.allowed("https://x/jobs/1", "WorkleadLocal/0.1")
    assert not r.allowed("https://x/private", "WorkleadLocal/0.1")
    assert r.allowed("https://x/private/x", "WorkleadLocal/0.1")


# ── 실제 공고(2026-09 당근알바)에서 드러난 오탐·누락 — 문구는 합성 ─────────────
@pytest.mark.parametrize(
    "title,body",
    [
        ("자동화물기사 구합니다", "화물차 운전 가능하신 분 구합니다. 월 320만원."),
        ("물류자동화 설비 개조 인원 모집", "설비 개조 작업, 주 5일."),
        ("자동화장비 배선작업 보조", "배선 보조 작업입니다."),
        ("자동화 안전담당자 모집", "현장 안전 관리 업무입니다."),
    ],
)
def test_non_software_automation_is_not_dev(title: str, body: str) -> None:
    a = run(title, body)
    assert "automation" not in a.categories
    assert a.recommendation == "excluded"


def test_app_development_is_dev_work() -> None:
    a = run("앱 개발해주실 분 구합니다", "서비스 앱 개발을 도와주실 분을 찾고 있어요. 크로스 플랫폼으로 만들 예정입니다.")
    assert "software" in a.categories
    assert a.judgements["demand_intent"].value == "buyer_project"
    assert all("개발·자동화 업무 아님" not in r.text for r in a.reasons)


def test_teaching_request_is_flagged_for_review() -> None:
    a = run("홈페이지 제작 가르쳐 주실 분", "홈페이지 제작을 하고 싶은데 만들어 주시고 가르쳐 주실 분 구해요. 예산 20만원.")
    assert a.recommendation == "needs_review"
    assert a.reasons[0].text.startswith("교육·과외 요청")


def test_come_to_office_is_onsite() -> None:
    a = run("쇼핑몰 오픈 도와주실 분", "카페24 쇼핑몰 오픈을 도와주실 분. 사무실로 오셔서 근무 가능하신 분만 지원해 주세요.")
    assert a.judgements["work_mode"].value == "onsite"
    assert a.recommendation == "excluded"


def test_illegal_work_request_is_risk() -> None:
    a = run("웹 개발 강의 구해요", "웹 개발, 소프트웨어 크랙 등 알려주실 분 구합니다. 예산 50만원.")
    assert any(r.id == "illegal_work" for r in a.risks)
    assert a.recommendation == "excluded"


def test_site_build_without_web_prefix_is_website() -> None:
    a = run("사이트 제작 및 구글시트 연동 가능하신분", "사이트 제작 및 구글 시트 연동 가능하신 분 구합니다.")
    assert "website" in a.categories and "automation" in a.categories


# ── 건당·단기 작업 구인 (크몽·숨고 밖 틈새 수요) ─────────────────────────
def test_short_gig_hiring_becomes_gig_and_can_be_recommended() -> None:
    a = run("홈페이지 수정 재택 알바 (건당)", "쇼핑몰 홈페이지 배너·상품 페이지 수정 업무 알바 모집. 재택근무 가능, 건당 5만원입니다.")
    assert a.judgements["demand_intent"].value == "short_gig"
    assert a.recommendation == "recommended", [r.text for r in a.reasons]


def test_short_term_dev_parttime_is_short_gig_not_employment() -> None:
    a = run("웹개발 코딩 가능한 알바", "행정보조로 웹개발 코딩 가능한 분 구합니다. 시급 16,000원, 근무기간 1개월~3개월, 재택근무 가능.")
    assert a.judgements["demand_intent"].value == "short_gig"
    assert all("단기 고용 — 개발 외주 아님" != r.text for r in a.reasons)


def test_full_time_hiring_is_not_short_gig() -> None:
    a = run("웹 개발자 정규직", "웹 개발자 정규직 채용합니다. 4대보험, 재택근무 병행.")
    assert a.judgements["demand_intent"].value == "employee_hiring"
    assert a.recommendation == "excluded"


def test_trainee_recruitment_ad_is_excluded() -> None:
    a = run("[국비지원] AI 풀스택 개발 교육생 모집", "무료 숙식 제공, 풀스택 개발 과정 교육생을 모집합니다.")
    assert a.recommendation == "excluded"
    assert any("교육생" in r.text for r in a.reasons)


def test_short_gig_disabled_by_profile_setting() -> None:
    from worklead.analysis.service import analyze
    from worklead.analysis.types import PostInput, Profile

    post = PostInput(title="웹개발 코딩 가능한 알바", body="웹개발 코딩 가능한 분. 시급 16,000원, 1개월~3개월, 재택근무.", observed_at=NOW, source_status="open", last_checked_at=NOW, first_seen_at=NOW)
    a = analyze(post, Profile(services=PROFILE.services, allow_short_term_employment=False), NOW)
    assert a.judgements["demand_intent"].value == "employee_hiring"
