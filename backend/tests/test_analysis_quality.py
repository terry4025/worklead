"""Source/body compensation and explicit delivery request regressions."""
from datetime import UTC, datetime

import pytest

from worklead.analysis.pay import parse_pay
from worklead.analysis.service import analyze
from worklead.analysis.types import PayInfo, PostInput, Profile

NOW = datetime(2026, 9, 30, tzinfo=UTC)


def check(body: str, pay_hint: PayInfo | None = None, title: str = "홈페이지 제작 의뢰"):
    post = PostInput(title=title, body=body, observed_at=NOW, source_status="open",
                     first_seen_at=NOW, last_checked_at=NOW, pay_hint=pay_hint)
    return analyze(post, Profile(services=["website", "shop"], target_hourly=40_000), NOW)


@pytest.mark.parametrize("hint", [None, PayInfo(), PayInfo(raw="  ")])
def test_empty_source_pay_recovers_explicit_body_project_amount(hint):
    a = check("홈페이지 제작 의뢰합니다. 전 과정 비대면 진행. 예산 80만원.", hint)
    assert (a.pay.unit, a.pay.min, a.pay.max) == ("project", 800_000, 800_000)
    assert a.recommendation == "recommended"
    assert a.pay.evidence[0].start is not None
    assert a.profitability.status == "calculated"


def test_different_source_and_body_amounts_require_review_with_both_quotes():
    hint = parse_pay("건당 1,000원")
    original_evidence = list(hint.evidence)
    a = check("홈페이지 제작 의뢰합니다. 전 과정 비대면 진행.\n비용: 40만원~50만원 내외", hint)
    assert (a.pay.raw, a.pay.unit, a.pay.min, a.pay.max) == ("건당 1,000원", "project", 1_000, 1_000)
    assert a.recommendation == "needs_review"
    assert "보수 금액 불일치" in a.reasons[0].text
    assert a.profitability.status == "not_calculated" and not a.profitability.scenarios
    assert any(ev.quote == "건당 1,000원" and ev.start is None for ev in a.pay.evidence)
    assert any("40만원~50만원" in ev.quote and ev.start is not None for ev in a.pay.evidence)
    assert "실제 건당 계약 금액" in a.questions[0]
    assert hint.evidence == original_evidence, "Analysis must not mutate the source hint."


@pytest.mark.parametrize(("source", "body_pay"), [
    ("건당 45만원", "예산 40~50만원"),
    ("건당 50만원", "예산 50만원 이상"),
    ("건당 협의", "예산 80만원"),
    ("건당 80만원", "예산 협의"),
])
def test_compatible_or_incomplete_prices_do_not_invent_a_conflict(source, body_pay):
    a = check(f"홈페이지 제작 의뢰합니다. 전 과정 비대면 진행.\n{body_pay}", parse_pay(source))
    assert a.recommendation == "recommended"
    assert not any("불일치" in r.text for r in a.reasons)
    assert a.pay.raw == source


@pytest.mark.parametrize("source", ["시급 2만원", "일급 15만원", "월급 400만원", "연봉 4200만원"])
def test_body_project_amount_never_overrides_a_source_wage(source):
    a = check("홈페이지 제작 의뢰합니다. 전 과정 비대면 진행.\n예산 80만원", parse_pay(source))
    assert a.recommendation == "excluded"
    assert a.pay.raw == source
    assert any("지급" in r.text for r in a.reasons)


@pytest.mark.parametrize("body_pay", ["시급 2만원", "일급 15만원", "월급 400만원"])
def test_price_conflict_never_reincludes_a_body_wage(body_pay):
    a = check(f"홈페이지 제작 의뢰합니다. 전 과정 비대면 진행.\n{body_pay}", parse_pay("건당 80만원"))
    assert a.recommendation == "excluded"
    assert a.pay.unit == "project" and a.pay.min == 800_000


@pytest.mark.parametrize("wording", [
    "홈페이지 제작가능하신분 계실까요?",
    "카페24 쇼핑몰을 구축하실 분 찾습니다.",
    "웹사이트 개발 가능한 프리랜서 구합니다.",
])
def test_named_deliverable_person_requests_are_recognized(wording):
    a = check(f"{wording}\n건당 80만원, 전 과정 비대면 진행.", title=wording)
    assert a.judgements["demand_intent"].value == "buyer_project"
    assert a.judgements["demand_intent"].basis == "explicit"
    assert a.recommendation == "recommended"


@pytest.mark.parametrize(("title", "wording"), [
    ("홈페이지 제작 강의", "홈페이지 제작가능하신분 계실까요? 제작을 가르쳐 주실 과외 선생님을 구합니다."),
    ("쇼핑몰 관리 직원 모집", "쇼핑몰을 구축하실 분 찾습니다. 주 5일 근무하는 직원 모집."),
    ("쇼핑몰 관리", "쇼핑몰 관리를 하실 분을 구합니다. 상세페이지 제작 가능한 분 모십니다."),
    ("웹사이트 개발 프리랜서", "웹사이트 개발 가능한 프리랜서 구합니다. 시급 2만원, 하루 4시간."),
])
def test_expanded_wording_keeps_education_operations_and_wages_excluded(title, wording):
    a = check(f"{wording}\n건당 80만원, 전 과정 비대면 진행.", title=title)
    assert a.recommendation == "excluded"


def test_generic_person_recruitment_is_still_unknown_and_excluded():
    a = check("개발 가능한 프리랜서 구합니다. 건당 80만원, 전 과정 비대면 진행.", title="개발자 구합니다")
    assert a.judgements["demand_intent"].value == "unknown"
    assert a.recommendation == "excluded"


def test_seller_wording_does_not_become_a_buyer_from_delivery_request_quotes():
    a = check("홈페이지 제작가능하신분 계실까요? 홈페이지 제작해드립니다. 건당 80만원, 전 과정 비대면 진행.")
    assert a.judgements["demand_intent"].value == "seller_service"
    assert a.recommendation == "excluded"
