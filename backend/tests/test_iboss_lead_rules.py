"""Project eligibility for i-boss requests; fixtures contain no contact data.

The first example paraphrases the public scope of ab-1958-88584, observed
2026-09-30. Other examples are synthetic boundary regressions, not live posts.
"""
from datetime import UTC, datetime

import pytest

from worklead.analysis.service import analyze
from worklead.analysis.types import DateInfo, Ev, PayInfo, PostInput, Profile


NOW = datetime(2026, 9, 30, 12, tzinfo=UTC)
PROFILE = Profile(services=["website", "landing", "shop", "fullstack", "software", "automation"])
PROJECT_PAY = PayInfo(
    raw="건당 최소 10만원 ~ 최대 300만원", min=100_000, max=3_000_000,
    unit="project", evidence=[Ev("건당 최소 10만원 ~ 최대 300만원", None, None, "explicit")],
)
PUBLIC_SCOPE = (
    "운영 중인 중고 휴대폰 거래 웹사이트를 개선할 개발자 또는 업체를 찾습니다. "
    "고객 화면과 관리자 화면의 기존 HTML 및 기획 자료가 있습니다. "
    "PC와 모바일 화면 개선, 매입 신청 기능, 서버 및 데이터베이스 연결, "
    "관리자 기능 추가와 오류 수정 및 테스트를 요청합니다. "
    "자료 검토 후 견적과 단계별 개발 일정을 협의합니다. "
    "유사 작업 경험과 개발 언어, 착수 가능 일정, 하자 보수 조건을 쪽지로 알려 주세요."
)


def check(title, body, *, pay=PROJECT_PAY, source_status="open", deadline=None):
    return analyze(
        PostInput(
            title=title, body=body, observed_at=NOW, source_status=source_status,
            first_seen_at=NOW, last_checked_at=NOW, pay_hint=pay, deadline_hint=deadline,
            published=DateInfo(at=datetime(2026, 9, 22, 2, 43, tzinfo=UTC), precision="exact"),
        ),
        PROFILE, NOW,
    )


def test_public_html_admin_request_keeps_source_range_and_unknown_remote():
    result = check("기존 HTML 기반 웹사이트 기능 보완 및 관리자 페이지 개발 의뢰", PUBLIC_SCOPE)
    assert result.recommendation == "needs_review"
    assert result.dev_relevant
    assert {"website", "fullstack"} <= set(result.categories)
    assert result.judgements["demand_intent"].value == "buyer_project"
    assert result.judgements["work_mode"].value == "unknown"
    assert result.judgements["collaboration_mode"].value == "unknown"
    assert (result.pay.unit, result.pay.min, result.pay.max) == ("project", 100_000, 3_000_000)
    assert result.pay.raw == PROJECT_PAY.raw
    assert any("재택 미확인" in reason.text for reason in result.reasons)


@pytest.mark.parametrize("status", ["closed", "deleted"])
def test_expired_or_deleted_development_request_is_excluded(status):
    result = check("웹사이트 관리자 페이지 개발 의뢰", PUBLIC_SCOPE, source_status=status)
    assert result.recommendation == "excluded"


@pytest.mark.parametrize("title,body", [
    ("블로그 원고 제작 의뢰", "브랜드 홍보 블로그 원고와 SNS 이미지 제작 의뢰합니다."),
    ("쇼핑몰 광고 소재 제작 의뢰", "쇼핑몰 상품 홍보 이미지와 광고 문구 제작 의뢰합니다."),
    ("랜딩페이지 유입 광고 운영 의뢰", "기존 랜딩페이지에 고객을 유입시키는 광고 소재 제작 의뢰합니다."),
    ("홈페이지 검색광고 소재 제작 의뢰", "기존 홈페이지를 홍보할 검색광고 문구와 이미지 제작 의뢰합니다."),
    ("광고 자동화 도구를 활용한 콘텐츠 제작 의뢰", "기존 광고 자동화 도구를 이용해 SNS 이미지 제작 의뢰합니다."),
    ("쇼핑몰 바이럴 콘텐츠 수정 외주", "쇼핑몰 홍보용 블로그 원고 수정 의뢰합니다."),
    ("CPA 상담 신청 유치 의뢰", "상담 신청 고객을 모집하는 CPA 광고 대행 의뢰합니다. 유효 상담 건당 지급합니다."),
    ("영업 계약 성사 건당 지급", "상품 판매 계약을 성사시킬 영업 대행 의뢰합니다. 성사 건당 수수료 지급합니다."),
])
def test_marketing_creative_and_sales_outcomes_are_not_software_delivery(title, body):
    result = check(title, body + " 전 과정 비대면 진행.")
    assert result.recommendation == "excluded"
    assert not result.dev_relevant


def test_monthly_advertising_budget_does_not_qualify_as_project_payment():
    result = check(
        "쇼핑몰 월간 광고 운영 의뢰",
        "쇼핑몰 광고 이미지 제작과 캠페인 운영을 의뢰합니다. 전 과정 비대면 진행.",
        pay=PayInfo(raw="월 최소 100만원 ~ 최대 300만원", min=1_000_000, max=3_000_000, unit="month"),
    )
    assert result.recommendation == "excluded"
    assert result.pay.unit == "month"


@pytest.mark.parametrize("title,body,category", [
    ("광고 캠페인용 랜딩페이지 개발 의뢰", "광고 유입 고객용 랜딩페이지를 개발해 주세요. 신청 폼과 관리 기능이 필요합니다.", "landing"),
    ("쇼핑몰 광고 성과 API 연동 개발 의뢰", "쇼핑몰 광고 성과 데이터를 API로 가져오는 자동화 프로그램 개발 의뢰합니다.", "automation"),
    ("브랜드 홈페이지 제작 의뢰", "마케팅 목적의 브랜드 홈페이지를 제작해 주세요. 반응형 화면과 문의 폼이 필요합니다.", "website"),
    ("랜딩페이지 제작 및 광고 운영 의뢰", "랜딩페이지 제작과 광고 캠페인 운영을 의뢰합니다. 신청 폼과 저장 기능이 필요합니다.", "landing"),
    ("광고 캠페인용 홈페이지 제작 견적", "홈페이지 제작 견적 부탁드립니다. 광고 문구 작성도 함께 요청합니다.", "website"),
])
def test_marketing_business_context_keeps_actual_software_deliverables(title, body, category):
    result = check(title, body + " 광고 소재 제작은 별도 업체가 담당합니다. 전 과정 비대면 진행.")
    assert result.recommendation == "recommended"
    assert result.dev_relevant
    assert category in result.categories


def test_service_seller_is_not_mistaken_for_buyer():
    result = check("홈페이지 제작 서비스", "홈페이지와 관리자 페이지를 제작해 드립니다. 전 과정 비대면 진행.")
    assert result.recommendation == "excluded"
    assert result.judgements["demand_intent"].value == "seller_service"
