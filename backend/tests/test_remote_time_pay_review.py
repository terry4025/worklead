"""2026-11-07 user decision: remote development requests posted with hourly,
daily or monthly pay are review candidates, never auto-apply candidates.

Daangn makes posters pick a pay type, so short website jobs often show "시급".
Such posts stay visible with a score penalty and an explicit caution; onsite,
part-time, employment and non-development posts remain excluded.
"""
from datetime import UTC, datetime, timedelta

import pytest

from worklead.analysis.service import TIME_PAY_REVIEW_PENALTY, analyze
from worklead.analysis.types import DateInfo, PayInfo, PostInput, Profile
from worklead.engine.pipeline import ingest
from worklead.jobs.auto_apply import condition
from worklead.models import Lead, SourcePolicy
from worklead.services.settings import update_profile
from worklead.sources.base import ParsedPost

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
PROFILE = Profile(services=["website", "landing", "shop", "fullstack", "software", "vba", "automation"])
TITLE = "쇼핑몰 홈페이지 수정 의뢰"
BODY = ("운영 중인 쇼핑몰 홈페이지 수정 의뢰합니다. 재택으로 진행 가능하며 전 과정 비대면입니다. "
        "작업 범위는 메인 화면 배너 교체와 상품 상세 레이아웃 수정입니다.")


def pay(unit: str) -> PayInfo:
    if unit == "project":
        return PayInfo(raw="건당 2,000,000원", min=2_000_000, max=2_000_000, unit="project")
    label = {"hour": "시급 15,000원", "day": "일급 120,000원", "month": "월급 2,500,000원"}[unit]
    amount = {"hour": 15_000, "day": 120_000, "month": 2_500_000}[unit]
    return PayInfo(raw=label, min=amount, max=amount, unit=unit)


def run(title: str, body: str, unit: str):
    post = PostInput(title=title, body=body, observed_at=NOW, source_status="open", last_checked_at=NOW,
                     published=DateInfo(NOW - timedelta(hours=1), "exact"), first_seen_at=NOW,
                     pay_hint=pay(unit), collection_allowed=True)
    return analyze(post, PROFILE, NOW, 24, None)


@pytest.mark.parametrize("unit,label", [("hour", "시급"), ("day", "일급"), ("month", "월급")])
def test_remote_dev_request_with_time_pay_is_review_with_penalty(unit: str, label: str) -> None:
    project, timed = run(TITLE, BODY, "project"), run(TITLE, BODY, unit)
    assert project.recommendation != "excluded"
    assert timed.recommendation == "needs_review", [r.text for r in timed.reasons]
    assert any(label in r.text and r.tone == "caution" for r in timed.reasons), [r.text for r in timed.reasons]
    assert timed.total == project.total - TIME_PAY_REVIEW_PENALTY
    assert any(f.key == "time_pay" and f.score == -TIME_PAY_REVIEW_PENALTY for f in timed.factors)


@pytest.mark.parametrize("title,body", [
    (TITLE, BODY.replace("재택으로 진행 가능하며 전 과정 비대면입니다.", "매장으로 출근해서 작업해야 합니다.")),
    ("홈페이지 관리 아르바이트", "쇼핑몰 홈페이지 상품 등록 아르바이트 구합니다. 재택 가능, 주 3일 시간제 근무입니다."),
    ("카페 서빙 구합니다", "주말 카페 서빙 도와주실 분 구합니다. 재택 불가."),
])
def test_onsite_part_time_and_non_dev_time_pay_stay_excluded(title: str, body: str) -> None:
    assert run(title, body, "hour").recommendation == "excluded"


def test_annual_salary_stays_excluded() -> None:
    post = PostInput(title=TITLE, body=BODY, observed_at=NOW, source_status="open", last_checked_at=NOW,
                     published=DateInfo(NOW - timedelta(hours=1), "exact"), first_seen_at=NOW, collection_allowed=True,
                     pay_hint=PayInfo(raw="연봉 42,000,000원", min=42_000_000, max=42_000_000, unit="year"))
    assert analyze(post, PROFILE, NOW, 24, None).recommendation == "excluded"


@pytest.mark.parametrize("body,unit,expected", [
    (BODY, "hour", "needs_review"),
    # A wage written in the body reads as staff hiring and stays excluded,
    # even when the source shows per-project pay.
    (BODY + " 시급 2만원.", "project", "excluded"),
])
def test_time_pay_review_never_becomes_auto_apply_candidate(ctx, body: str, unit: str, expected: str) -> None:
    url = "https://jobs.daangn.com/job-posts/time-pay-TIMEPAY00001"
    post = ParsedPost(title=TITLE, body=body, original_url=url, canonical_url=url, source_post_id="TIMEPAY00001",
                      source_status="open", published=DateInfo(at=NOW - timedelta(hours=1), precision="exact"),
                      pay=pay(unit))
    with ctx.db.session() as session:
        session.get(SourcePolicy, "daangn-alba").status = "allowed"
        update_profile(session, {"services": PROFILE.services})
        lead = session.get(Lead, ingest(session, "daangn-alba", post, parser_version="test", now=NOW).lead_id)
        assert lead.recommendation == expected
        assert condition(session, lead, NOW, 0) == "condition_changed"
        assert condition(session, lead, NOW, 0, queue=True) == "condition_changed"
