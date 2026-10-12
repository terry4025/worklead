"""New wage-unit and non-compensation context regressions using synthetic HTML."""
from copy import deepcopy
from datetime import UTC, datetime
from html import escape
import json

import pytest

from worklead.analysis.pay import parse_pay, time_pay_unit
from worklead.analysis.service import analyze
from worklead.analysis.types import PostInput, Profile
from worklead.engine.http import FetchResult
from worklead.sources.base import PostRef
from worklead.sources.daangn import create

NOW = datetime(2026, 9, 30, tzinfo=UTC)
TITLE = "홈페이지 제작 의뢰"
BASE = "홈페이지 제작 의뢰합니다. 전 과정 비대면 진행.\n"
URL = "https://jobs.daangn.com/job-posts/fixture00001"


def analysis(tail, source=None, title=TITLE, feedback=None):
    hint = parse_pay(source) if source else None
    before = deepcopy(hint)
    post = PostInput(title=title, body=BASE + tail, observed_at=NOW, first_seen_at=NOW,
                     last_checked_at=NOW, source_status="open", pay_hint=hint)
    result = analyze(post, Profile(services=["website"]), NOW, feedback=feedback)
    assert hint == before, "Source compensation must not be mutated."
    assert post.body == BASE + tail
    return result


@pytest.mark.parametrize("wage", [
    "매달 200만원 지급", "건당 20,000원/1시간", "건당 40,000원/2시간", "건당 150,000원(1일)",
    "건당 100만원/1개월", "건당 50만원/1주", "건당 1000만원/1년",
])
@pytest.mark.parametrize("source", [None, "건당 80만원"])
def test_explicit_time_rates_never_enter_review_or_recommended(wage, source):
    result = analysis(wage, source)
    assert result.recommendation == "excluded"
    assert result.reasons and all(reason.tone == "negative" for reason in result.reasons)
    if source:
        assert result.pay.raw == source and (result.pay.min, result.pay.max) == (800_000, 800_000)


@pytest.mark.parametrize("cost", [
    "호스팅 비용 월 2만원", "서버 이용료 시간당 500원",
    "도메인 갱신비 매년 3만원", "호스팅 요금은 매월 정산합니다",
])
@pytest.mark.parametrize("source", [None, "건당 80만원"])
def test_explicit_external_costs_do_not_become_worker_wages(cost, source):
    body = f"{cost}. 제작 예산 80만원."
    result = analysis(body, source)
    assert result.recommendation == "recommended"
    assert result.pay.unit == "project" and (result.pay.min, result.pay.max) == (800_000, 800_000)
    assert time_pay_unit(BASE + body) is None
    assert not any("불일치" in reason.text for reason in result.reasons)
    if source is None:
        assert result.pay.evidence and result.pay.evidence[0].start is not None
        for evidence in result.pay.evidence:
            assert (BASE + body)[evidence.start:evidence.end] == evidence.quote


@pytest.mark.parametrize("duration", [
    "제작 기간은 한 달 예상, 80만원 예산입니다.",
    "개발 일정은 한 달, 80만원 견적입니다.",
    "건당 80만원(1일 소요).",
    "건당 80만원(20시간 예상).",
])
@pytest.mark.parametrize("source", [None, "건당 80만원"])
def test_explicit_delivery_duration_is_not_monthly_payment(duration, source):
    result = analysis(duration, source)
    assert result.recommendation == "recommended"
    assert result.pay.unit == "project" and result.pay.min == 800_000
    assert time_pay_unit(BASE + duration) is None


@pytest.mark.parametrize("tail", [
    "호스팅 비용 월 2만원. 제작 예산 80만원. 월급 200만원 지급.",
    "호스팅비 외 월급 200만원 지급. 제작 예산 80만원.",
    "제작 기간은 한 달 예상. 시급 2만원 지급. 제작 예산 80만원.",
    "웹사이트 유지보수 의뢰합니다. 매달 200만원 지급. 제작 예산 80만원.",
    "쇼핑몰 관리를 하실 분을 구합니다. 상세페이지 제작. 건당 80만원.",
    "Salary: USD 2000 per month. 제작 예산 80만원.",
    "Salary: 200만원. 제작 예산 80만원.",
])
def test_non_wage_context_exemptions_never_hide_real_wages_or_operations(tail):
    result = analysis(tail, "건당 80만원")
    assert result.recommendation == "excluded"
    assert len(result.reasons) <= 3


def synthetic_page(body, pay):
    store = {
        "client:root": {'permalinkPairByPublicId(publicId:"fixture00001")': {"__ref": "pair"}},
        "pair": {"daangnPermalink": {"__ref": "permalink"}},
        "permalink": {"publicId": "fixture00001", "model": {"__ref": "job"}},
        "job": {"__typename": "JobPost", "title(masking:true)": TITLE, "content(masking:true)": body,
                "closed": False, "hidden": False, "deleted": False, "user": {"__ref": "user"}},
        "user": {"webCrawlNotAllowed": False},
    }
    embedded = json.dumps(json.dumps(store, ensure_ascii=False), ensure_ascii=False)
    return (f'<html><head><meta name="karrot:embed_view_type:title" content="{escape(pay, quote=True)}"></head>'
            f'<body><h1>{TITLE}</h1><p class="whitespace-pre-wrap">{escape(body)}</p>'
            f'<script>window.__RELAY_STORE__ = {embedded};</script></body></html>')


@pytest.mark.parametrize("source", ["건당 20,000원/1시간", "건당 150,000원(1일)", "매달 200만원 지급", "Salary: USD 2000 per month"])
def test_daangn_source_rate_remains_excluded_despite_body_project_amount(source):
    body = BASE + "제작 예산 80만원."
    html = synthetic_page(body, source)
    parsed = create().parse(PostRef(URL, "fixture00001"), FetchResult(URL, URL, 200, "ok", html), NOW)
    result = analyze(PostInput(title=parsed.title, body=parsed.body, observed_at=NOW, first_seen_at=NOW,
                               last_checked_at=NOW, source_status=parsed.source_status, pay_hint=parsed.pay),
                     Profile(services=["website"]), NOW)
    assert result.recommendation == "excluded"
    assert result.pay.raw == source
    assert result.pay.min == parse_pay(source).min, "Keep the displayed rate; do not convert it to project revenue."
    assert parsed.body == body


def test_salary_software_field_is_not_an_employment_salary():
    result = analysis("salary 컬럼을 포함하는 급여 계산 프로그램을 개발해 주세요. 제작 예산 80만원.", "건당 80만원")
    assert result.recommendation == "recommended"


@pytest.mark.parametrize("salary", ["Salary: USD 2000 per month", "Salary: 200만원"])
def test_user_confirmation_never_overrides_explicit_salary(salary):
    result = analysis(salary + ". 제작 예산 80만원.", "건당 80만원",
                      feedback={"remote": "confirmed", "real_request": "yes"})
    assert result.recommendation == "excluded"


@pytest.mark.parametrize("wage", ["월급 200만원", "일급 15만원", "시급 2만원", "매달 200만원 지급", "건당 20,000원/1시간"])
def test_title_only_time_wage_is_excluded_despite_project_metadata(wage):
    result = analysis("제작 예산 80만원.", "건당 80만원", title=TITLE + " " + wage,
                      feedback={"remote": "confirmed", "real_request": "yes"})
    assert result.recommendation == "excluded"
    assert result.pay.raw == "건당 80만원" and result.pay.min == 800_000


def test_title_hosting_cost_does_not_exclude_project_request():
    result = analysis("제작 예산 80만원.", "건당 80만원", title=TITLE + " 호스팅 비용 월 2만원")
    assert result.recommendation == "recommended"
