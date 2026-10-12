"""Eligibility regressions for project-paid freelance delivery only."""
from datetime import UTC, datetime

import pytest

from tests.conftest import make_ctx
from worklead.analysis.pay import parse_pay
from worklead.analysis.service import analyze
from worklead.analysis.types import PostInput, Profile
from worklead.engine.pipeline import apply_analysis, ingest
from worklead.models import Lead, SourceRecord
from worklead.sources.base import ParsedPost

NOW = datetime(2026, 9, 30, tzinfo=UTC)


def analysis(body, pay_hint=None, feedback=None, title="홈페이지 제작 의뢰"):
    post = PostInput(title=title, body=body, observed_at=NOW,
                     source_status="open", last_checked_at=NOW, first_seen_at=NOW, pay_hint=pay_hint)
    return analyze(post, Profile(services=["website"]), NOW, feedback=feedback)


@pytest.mark.parametrize("pay", ["시급 2만원", "일급 15만원", "주급 50만원", "월급 400만원", "연봉 4200만원"])
def test_wages_in_source_metadata_are_excluded_even_for_remote_buyers(pay):
    a = analysis("홈페이지 제작 의뢰합니다. 전 과정 비대면 진행.", parse_pay(pay),
                 {"remote": "confirmed", "real_request": "yes"})
    assert a.recommendation == "excluded"
    assert a.pay.raw == pay


@pytest.mark.parametrize("pay", ["시급 2만원", "일급 15만원", "월급 400만원", "월급 협의", "월 단위 정산, 금액 협의", "프로젝트 20,000원/시간"])
def test_wage_alternative_in_body_cannot_be_hidden_by_project_metadata(pay):
    a = analysis(f"홈페이지 제작 의뢰합니다. 전 과정 비대면 진행.\n예산 100만원\n{pay}", parse_pay("건당 100만원"))
    assert a.pay.unit == "project"  # source display is preserved
    assert a.recommendation == "excluded"
    assert any("지급" in r.text for r in a.reasons)


@pytest.mark.parametrize("pay", ["건당 100만원", "예산 100만원", "개발비 100만원", "건 당 협의", "프로젝트 단위 정산, 금액 협의", "제작 기간 한 달, 건당 100만원"])
def test_project_payments_and_negotiated_project_prices_remain_eligible(pay):
    a = analysis(f"홈페이지 제작 의뢰합니다. 전 과정 비대면 진행.\n{pay}\n예상 작업 시간은 20시간입니다.")
    assert a.pay.unit == "project"
    assert a.recommendation == "recommended"


@pytest.mark.parametrize("pay", ["", "급여 협의", "금액 100만원", "보수 100만원"])
def test_unknown_payment_unit_is_excluded_without_inventing_project_pay(pay):
    a = analysis(f"홈페이지 제작 의뢰합니다. 전 과정 비대면 진행.\n{pay}")
    assert a.pay.unit != "project"
    assert a.recommendation == "excluded"
    assert any("지급 미확인" in r.text for r in a.reasons)


@pytest.mark.parametrize("body", [
    "홈페이지 제작 의뢰합니다. 알바 구합니다. 전 과정 비대면 진행.",
    "홈페이지 담당 직원 모집. 전 과정 비대면 진행.",
    "프리랜서 구합니다. 전 과정 비대면 진행.",
])
def test_project_price_alone_does_not_make_staff_hiring_a_freelance_delivery(body):
    assert analysis(body, parse_pay("건당 100만원"), title="웹사이트 개발자 구합니다").recommendation == "excluded"


def test_explicit_freelancer_delivery_recruitment_is_recognized():
    a = analysis("웹사이트 개발 프리랜서 구합니다. 건당 100만원, 전 과정 비대면 진행.")
    assert a.judgements["demand_intent"].value == "buyer_project"
    assert a.recommendation == "recommended"


def test_portfolio_modification_request_is_recognized():
    a = analysis("포트폴리오 수정요청부탁드립니다. 건당 5만원. 전 과정 비대면 진행.")
    assert a.judgements["demand_intent"].value == "buyer_project"
    assert a.recommendation == "recommended"


def test_wage_refetch_and_reanalysis_preserve_user_state(tmp_path):
    ctx = make_ctx(tmp_path)
    p = ParsedPost(title="홈페이지 제작 의뢰", body="홈페이지 제작 의뢰합니다. 전 과정 비대면 진행.",
                   original_url="https://jobs.daangn.com/job-posts/fixture00001", canonical_url="https://jobs.daangn.com/job-posts/fixture00001", source_post_id="fixture00001",
                   pay=parse_pay("건당 100만원"), source_status="open")
    with ctx.db.session() as s:
        first = ingest(s, "daangn-alba", p, parser_version="test", now=NOW)
        lead = s.get(Lead, first.lead_id)
        assert lead.recommendation == "recommended"
        lead.memo, lead.user_mark, lead.sales_stage = "견적 메모", "interested", "contacted"
        lead.draft_text, lead.draft_edited = "수동 초안", True
    p.pay = parse_pay("월급 400만원")
    with ctx.db.session() as s:
        ingest(s, "daangn-alba", p, parser_version="test", now=NOW)
        lead, rec = s.get(Lead, first.lead_id), s.get(SourceRecord, first.record_id)
        assert lead.recommendation == "excluded" and lead.pay_unit == "month"
        apply_analysis(s, lead, rec, now=NOW)
        assert lead.recommendation == "excluded" and rec.pay_raw == "월급 400만원"
        assert (lead.memo, lead.user_mark, lead.sales_stage, lead.draft_text, lead.draft_edited) == (
            "견적 메모", "interested", "contacted", "수동 초안", True)
