"""Personal minimum contract eligibility and profile reanalysis regressions."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from tests.conftest import client_for
from tests.test_api import drain
from worklead.analysis.pay import parse_pay
from worklead.analysis.service import analyze
from worklead.analysis.types import PayInfo, PostInput, Profile
from worklead.engine.pipeline import ingest
from worklead.models import AnalysisResult, Lead, SourceRecord
from worklead.services.settings import get_profile_row
from worklead.sources.base import ParsedPost

NOW = datetime(2026, 9, 30, tzinfo=UTC)
BODY = "홈페이지 제작 의뢰합니다. 전 과정 비대면 진행."
FLOOR = 1_000_000


def check(pay: PayInfo, floor: int | None = FLOOR, **post_kwargs):
    post = PostInput(title="홈페이지 제작 의뢰", body=BODY, observed_at=NOW,
                     source_status="open", first_seen_at=NOW, last_checked_at=NOW, pay_hint=pay)
    for key, value in post_kwargs.items():
        setattr(post, key, value)
    return analyze(post, Profile(services=["website"], min_contract=floor), NOW)


@pytest.mark.parametrize(("pay_text", "caution"), [
    ("건당 60만원", "최소 계약금액 미달 — 협의 필요"),
    ("예산 60~90만원", "최소 계약금액 미달 — 협의 필요"),
    ("건당 0원", "최소 계약금액 미달 — 협의 필요"),
    ("예산 60~100만원", "예산 범위가 최소 계약금액에 걸침"),
    ("예산 60~150만원", "예산 범위가 최소 계약금액에 걸침"),
])
def test_lower_budget_requires_review_without_excluding_negotiation(pay_text, caution):
    pay = parse_pay(pay_text)
    original = deepcopy(pay)
    a = check(pay)
    assert a.recommendation == "needs_review"
    assert any(r.tone == "caution" and caution in r.text and "내 최소 계약금액 100만원" in r.text for r in a.reasons)
    assert any(caution in text for text in a.uncertain)
    assert any("100만원 이상" in question for question in a.questions)
    assert a.pay == check(pay, None).pay, "The setting must not replace the original amount or evidence."
    assert pay == original, "Analysis must not mutate source compensation."
    assert len(a.reasons) <= 3
    if pay.min == 0:
        assert a.pay.min == 0 and a.pay.max == 0
        assert "0원" in a.reasons[0].text
        assert a.profitability.scenarios[0].contract_value == 0


@pytest.mark.parametrize(("pay", "caution"), [
    (PayInfo(raw="건당 60만원 이상", min=600_000, unit="project"), "게시 하한이 최소 계약금액 미달"),
    (PayInfo(raw="건당 최대 90만원", max=900_000, unit="project"), "최소 계약금액 미달 — 협의 필요"),
    (PayInfo(raw="건당 최대 150만원", max=1_500_000, unit="project"), "계약금액 하한 미확인"),
    (PayInfo(raw="건당 협의", unit="project", negotiable=True), "계약금액 협의"),
    (PayInfo(raw="프로젝트 단위 지급", unit="project"), "계약금액 미확인"),
])
def test_partial_or_missing_prices_distinguish_what_needs_confirmation(pay, caution):
    a = check(pay)
    assert a.recommendation == "needs_review"
    assert any(r.tone == "caution" and caution in r.text for r in a.reasons)
    assert a.pay == check(pay, None).pay
    assert len(a.reasons) <= 3
    if pay.min is None and pay.max is None:
        assert a.profitability.status == "not_calculated" and not a.profitability.scenarios


@pytest.mark.parametrize("pay", [
    parse_pay("건당 100만원"), parse_pay("예산 100~150만원"), parse_pay("건당 120만원"),
    PayInfo(raw="건당 100만원 이상", min=1_000_000, unit="project"),
])
def test_confirmed_lower_bound_at_or_above_floor_keeps_current_recommendation(pay):
    a, baseline = check(pay), check(pay, None)
    assert a.recommendation == baseline.recommendation == "recommended"
    assert a.reasons == baseline.reasons and a.uncertain == baseline.uncertain
    assert a.pay == baseline.pay and a.profitability == baseline.profitability


@pytest.mark.parametrize("floor", [None, 0])
@pytest.mark.parametrize("pay", [parse_pay("건당 60만원"), parse_pay("건당 0원"), parse_pay("건당 협의")])
def test_disabled_floor_does_not_disable_low_pay_priority(floor, pay):
    a = check(pay, floor)
    if pay.max == 0:
        assert a.recommendation == "needs_review"
        assert a.total <= 39
        assert any("보수가 매우 낮음" in r.text for r in a.reasons)
    else:
        assert a.recommendation == "recommended"
    assert not any("최소 계약금액" in r.text for r in a.reasons)


@pytest.mark.parametrize("pay", [parse_pay("시급 2만원"), parse_pay("일급 15만원"), parse_pay("월급 400만원")])
def test_remote_time_wage_review_does_not_add_contract_floor_negotiation(pay):
    # 2026-11-07: remote dev request with a time wage → review with a caution and a
    # time_pay penalty (never auto-apply). A wage is not a contract amount, so the
    # minimum-contract caution must not appear.
    a = check(pay)
    assert a.recommendation == "needs_review"
    assert any(r.tone == "caution" and "표기" in r.text for r in a.reasons)
    assert any(f.key == "time_pay" for f in a.factors)
    assert not any("최소 계약금액" in r.text for r in a.reasons)
    assert not any("최소 계약금액" in question for question in a.questions)
    assert (a.pay.raw, a.pay.min, a.pay.max, a.pay.unit) == (pay.raw, pay.min, pay.max, pay.unit)


@pytest.mark.parametrize(("pay", "body"), [
    (parse_pay("건당 60만원"), BODY + " 시급 2만원."),
    (parse_pay("건당 60만원"), BODY + " 알바 구합니다."),
    (parse_pay("건당 협의"), "홈페이지 담당 직원 모집. 전 과정 비대면 진행."),
])
def test_wage_or_employee_exclusion_wins_over_contract_negotiation(pay, body):
    a = check(pay, body=body)
    assert a.recommendation == "excluded"
    assert a.reasons and all(r.tone == "negative" for r in a.reasons)
    assert not any("최소 계약금액" in r.text for r in a.reasons)
    assert not any("최소 계약금액" in question for question in a.questions)
    assert (a.pay.raw, a.pay.min, a.pay.max, a.pay.unit) == (pay.raw, pay.min, pay.max, pay.unit)


def test_floor_caution_remains_visible_with_conflict_and_multiple_reviews():
    a = check(parse_pay("건당 60만원"), body="홈페이지 제작 의뢰합니다. 예산 150만원.",
              last_checked_at=NOW - timedelta(hours=25))
    assert a.recommendation == "needs_review"
    assert len(a.reasons) == 3
    assert "보수 금액 불일치" in a.reasons[0].text
    assert "최소 계약금액 미달 — 협의 필요" in a.reasons[1].text
    assert a.profitability.status == "not_calculated"
    assert len(a.questions) <= 4
    assert any("최소 계약금액" in question for question in a.questions)


def test_profile_floor_change_reanalyzes_leads_and_preserves_user_state(ctx, monkeypatch):
    ctx.clock = lambda: NOW
    # The list/detail freshness gate uses its own clock; keep it on the same
    # scenario date so this regression does not expire as calendar time passes.
    monkeypatch.setattr("worklead.services.leads.utcnow", lambda: NOW)
    post = ParsedPost(title="홈페이지 제작 의뢰", body=BODY,
                      original_url="https://jobs.daangn.com/job-posts/fixture00001",
                      canonical_url="https://jobs.daangn.com/job-posts/fixture00001", source_post_id="fixture00001",
                      pay=parse_pay("건당 60만원"), source_status="open")
    with ctx.db.session() as s:
        first = ingest(s, "daangn-alba", post, parser_version="test", now=NOW)
        lead = s.get(Lead, first.lead_id)
        assert lead.recommendation == "recommended"
        lead.memo, lead.user_mark, lead.sales_stage = "계약 금액 확인 메모", "interested", "contacted"
        lead.draft_text, lead.draft_edited = "내가 작성한 문의 초안", True
        original_record = (post.body, post.pay.raw, post.pay.min, post.pay.max, post.pay.unit)

    with client_for(ctx) as client:
        for floor, expected in [(FLOOR, "needs_review"), (600_000, "recommended"), (FLOOR, "needs_review"), (0, "recommended")]:
            profile = client.get("/v1/settings").json()["profile"]
            with ctx.db.session() as s:
                old_version = get_profile_row(s).version
            response = client.patch("/v1/settings", json={"profile": {**profile, "min_contract": floor}})
            assert response.status_code == 200, response.text
            assert response.json()["profile"]["min_contract"] == floor
            assert any(run["kind"] == "reanalyze_all" and run["state"] == "queued"
                       for run in client.get("/v1/runs").json()["items"])
            drain(ctx)
            detail = client.get(f"/v1/leads/{first.lead_id}").json()
            assert detail["recommendation"] == expected
            assert (detail["memo"], detail["user_mark"], detail["sales_stage"]) == (
                "계약 금액 확인 메모", "interested", "contacted")
            if expected == "needs_review":
                assert any("최소 계약금액 미달" in reason["text"] for reason in detail["reasons"])
            with ctx.db.session() as s:
                assert get_profile_row(s).version == old_version + 1
                lead, record = s.get(Lead, first.lead_id), s.get(SourceRecord, first.record_id)
                assert (lead.draft_text, lead.draft_edited) == ("내가 작성한 문의 초안", True)
                assert (record.body_text, record.pay_raw, record.pay_min, record.pay_max, record.pay_unit) == original_record
        with ctx.db.session() as s:
            assert s.scalar(select(func.count()).select_from(AnalysisResult).where(AnalysisResult.lead_id == first.lead_id)) == 5
