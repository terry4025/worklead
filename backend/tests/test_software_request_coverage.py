"""Delivery aliases and explicit required visits found in public discovery."""
from datetime import UTC, datetime

import pytest

from worklead.analysis.service import analyze
from worklead.analysis.types import PostInput, Profile

NOW = datetime(2026, 9, 30, 12, tzinfo=UTC)


def check(title, body, pay="건당 30만원"):
    return analyze(PostInput(title=title, body=body + "\n" + pay,
                             observed_at=NOW, source_status="open",
                             last_checked_at=NOW, first_seen_at=NOW),
                   Profile(services=["website", "software", "automation"]), NOW)


@pytest.mark.parametrize("title,body,category", [
    ("사이트 만들어주실분 구해요", "회원가입과 결제 기능이 있는 사이트 제작 의뢰합니다.", "website"),
    ("사이트 만들어 주실 분", "게시판 기능을 넣어 주세요.", "website"),
    ("AI 에이전트 만들어 주실분", "반복 업무를 처리하는 AI 에이전트 제작 의뢰합니다.", "software"),
    ("챗봇 개발 의뢰", "고객 문의용 챗봇 개발 의뢰합니다.", "software"),
])
def test_explicit_delivery_aliases_remain_project_candidates(title, body, category):
    result = check(title, body + " 전 과정 비대면 진행.")
    assert category in result.categories
    assert result.recommendation == "recommended"
    hourly = check(title, body + " 전 과정 비대면 진행.", "시급 2만원")
    assert hourly.recommendation == "excluded"


def test_unknown_remote_status_is_not_invented_for_site_request():
    result = check("사이트 만들어주실분 구해요", "회원가입과 게시판 기능이 필요합니다.")
    assert result.recommendation == "needs_review"
    assert result.judgements["work_mode"].value == "unknown"


def test_required_home_visit_is_excluded_with_original_evidence():
    body = "반복 작업용 AI 에이전트를 만들어 주세요. 작업은 저희집으로 방문 하셔야해요."
    result = check("AI 에이전트 만들어 주실분", body)
    assert "software" in result.categories
    assert result.recommendation == "excluded"
    assert result.judgements["work_mode"].value == "onsite"
    judgement = result.judgements["collaboration_mode"]
    assert judgement.value == "onsite_required"
    assert judgement.basis == "explicit"
    evidence = judgement.evidence[0]
    assert body[evidence.start:evidence.end] == evidence.quote


@pytest.mark.parametrize("visit", [
    "작업은 저희집으로 방문하지 않아도 됩니다.",
    "작업은 사무실로 방문하셔도 됩니다.",
    "작업은 저희집으로 방문 하셔야 하는 것은 아닙니다.",
])
def test_optional_or_negated_visit_does_not_force_onsite(visit):
    result = check("사이트 제작 의뢰", visit + " 전 과정 비대면 진행.")
    assert result.judgements["collaboration_mode"].value == "online_only"
    assert result.recommendation == "recommended"


@pytest.mark.parametrize("title,body", [
    ("AI 에이전트 사용법 조언", "AI 에이전트 활용 방법 조언 부탁드립니다."),
    ("사이트 링크 소개", "구인 사이트를 통해 알바 지원서를 만들어 주세요."),
    ("웹사이트 제작 전문가", "사이트 만들어드립니다. 전 과정 비대면 진행."),
    ("챗봇 개발 직원 모집", "챗봇 개발 직원 채용합니다. 전 과정 비대면 진행."),
])
def test_non_delivery_advice_seller_and_staff_stay_excluded(title, body):
    assert check(title, body).recommendation == "excluded"
