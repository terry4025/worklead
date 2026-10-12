"""Separate paid advice from a request to deliver software changes."""

from datetime import UTC, datetime

import pytest

from worklead.analysis.service import analyze
from worklead.analysis.types import PostInput, Profile


NOW = datetime(2026, 9, 30, 12, tzinfo=UTC)


def analysis(title: str, body: str):
    return analyze(
        PostInput(
            title=title,
            body=body + "\n건당 5만원. 전 과정 비대면 진행.",
            observed_at=NOW,
            source_status="open",
            last_checked_at=NOW,
            first_seen_at=NOW,
        ),
        Profile(services=["website", "software", "fullstack"]),
        NOW,
    )


@pytest.mark.parametrize(("title", "body"), [
    (
        "개발자 포트폴리오 수정",
        "앱, 웹개발 포트폴리오는 완성했지만 수정이 필요할 것 같습니다.\n"
        "포트폴리오만 수정요청부탁드립니다.\n"
        "좋은 방향으로 수정방향 제시 해주실 현직자 부탁드립니다.\n"
        "1-2시간 이내로 예상합니다.",
    ),
    (
        "React 개발자 포트폴리오 첨삭",
        "React로 만든 포트폴리오입니다. 수정 의뢰합니다.\n"
        "코드는 제가 수정하며 현직자에게 내용 첨삭과 피드백만 요청합니다.",
    ),
    (
        "웹 개발 포트폴리오 조언 요청",
        "웹개발 포트폴리오 수정 요청합니다. 구현은 제가 합니다.\n"
        "코드 수정 방향을 제시해 주실 분과 화면 구성 조언만 구합니다.",
    ),
    (
        "개발 포트폴리오 피드백 요청",
        "앱 개발 의뢰가 아니라 완성한 포트폴리오의 피드백만 요청합니다.",
    ),
    (
        "개발 포트폴리오 피드백 요청",
        "앱 개발 의뢰는 아닙니다. 포트폴리오 피드백만 요청합니다.",
    ),
    (
        "개발 포트폴리오 피드백 요청",
        "앱 개발 의뢰가 아닌 포트폴리오 피드백만 요청합니다.",
    ),
])
def test_explicit_advice_is_excluded_without_inventing_a_delivery(title, body):
    result = analysis(title, body)
    assert result.pay.unit == "project"
    assert result.recommendation == "excluded"
    intent = result.judgements["demand_intent"]
    assert intent.value == "unknown"
    assert any("첨삭·조언" in reason.text for reason in result.reasons)
    assert intent.evidence
    evidence = intent.evidence[0]
    if evidence.start is not None:
        assert body[evidence.start:evidence.end] == evidence.quote


@pytest.mark.parametrize(("title", "body"), [
    (
        "포트폴리오 웹사이트 수정 의뢰",
        "포트폴리오 웹사이트 수정 의뢰합니다. 디자인 피드백도 부탁드립니다.",
    ),
    (
        "포트폴리오 HTML/CSS 수정",
        "포트폴리오 HTML/CSS 수정해 주실 분을 구합니다. 수정 방향 조언도 요청합니다.",
    ),
    (
        "React 포트폴리오 코드 수정",
        "React 코드 오류 수정 요청합니다. 완성 코드를 납품하고 피드백도 부탁드립니다.",
    ),
    (
        "포트폴리오 첨삭 플랫폼 개발 의뢰",
        "포트폴리오 첨삭과 피드백을 제공하는 웹사이트 제작 의뢰합니다.",
    ),
    (
        "컨설팅 업체 홈페이지 제작 의뢰",
        "조언과 피드백을 제공하는 컨설팅 업체의 홈페이지 제작 의뢰합니다.",
    ),
    (
        "포트폴리오 홈페이지 제작 의뢰",
        "홈페이지 제작 의뢰합니다. 납품 후 수정 방향 조언과 인수인계도 부탁드립니다.",
    ),
])
def test_explicit_software_delivery_with_advice_remains_eligible(title, body):
    result = analysis(title, body)
    assert result.judgements["demand_intent"].value == "buyer_project"
    assert result.recommendation == "recommended"
    assert not any("첨삭·조언" in reason.text for reason in result.reasons)


@pytest.mark.parametrize(("title", "body"), [
    ("홈페이지 제작좀 도와주실분", "홈페이지 제작 능숙하신분 도와주세요"),
    ("홈페이지 제작좀 도와주실분", "건당으로 부탁드립니다."),
    ("홈페이지 작업", "홈페이지 제작 능숙하신분 도와주세요"),
    ("홈페이지 제작좀 도와주실분", "홈페이지 제작 능숙하신분 도와주세요. 디자인 피드백도 부탁드립니다."),
    ("홈페이지 제작좀 도와주실분", "제작한 화면에 대해 피드백도 부탁드립니다."),
    ("홈페이지 작업", "홈페이지 제작 능숙하신분 도와주세요. 디자인 피드백도 부탁드립니다."),
])
def test_request_for_help_building_website_does_not_invent_remote_status(title, body):
    result = analyze(
        PostInput(
            title=title, body=body + "\n건당 30만원.", observed_at=NOW,
            source_status="open", last_checked_at=NOW, first_seen_at=NOW,
        ),
        Profile(services=["website"]), NOW,
    )
    assert result.judgements["demand_intent"].value == "buyer_project"
    assert result.judgements["work_mode"].value == "unknown"
    assert result.recommendation == "needs_review"


@pytest.mark.parametrize(("title", "body", "intent"), [
    ("홈페이지 제작 전문가", "홈페이지 제작 능숙합니다. 제작 도와드려요.", "seller_service"),
    ("홈페이지 제작 전문가 광고", "홈페이지 제작 능숙한 전문가입니다.", "unknown"),
    ("홈페이지 제작 직원 모집", "홈페이지 제작 능숙하신분 도와주세요. 직원 채용합니다.", "employee_hiring"),
])
def test_skill_statements_and_staff_recruitment_are_not_delivery_orders(title, body, intent):
    result = analysis(title, body)
    assert result.judgements["demand_intent"].value == intent
    assert result.recommendation == "excluded"
