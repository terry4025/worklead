"""Low compensation must lower priority without erasing condition matches."""
from datetime import UTC, datetime

import pytest

from worklead.analysis.pay import parse_pay
from worklead.analysis.service import analyze
from worklead.analysis.types import DateInfo, PostInput, Profile

NOW = datetime(2026, 10, 12, tzinfo=UTC)
BODY = "웹앱 목업 제작해주실 분 구해요. 전 과정 비대면 진행. 지역 무관. 기능: 로그인, 예약 화면. React 샘플 자료 제공. 일정은 이번 달까지."


def check(pay, target=40_000, **changes):
    post = PostInput(title="웹앱 목업 제작해주실 분 구해요", body=BODY,
                     observed_at=NOW, source_status="open", last_checked_at=NOW,
                     first_seen_at=NOW, published=DateInfo(at=NOW), pay_hint=parse_pay(pay))
    for key, value in changes.items():
        setattr(post, key, value)
    return analyze(post, Profile(services=["fullstack", "website"], target_hourly=target), NOW)


def test_two_thousand_won_cannot_be_a_high_priority_recommendation():
    low, adequate = check("건당 2,000원"), check("건당 500만원")
    assert low.total <= 39 < adequate.total
    assert low.recommendation == "needs_review"
    assert low.judgements == adequate.judgements
    assert low.factors[:5] == adequate.factors[:5]
    assert low.pay.min == low.pay.max == 2_000
    assert "보수가 매우 낮음" in low.reasons[0].text
    assert any("전체 작업의 계약금액" in q for q in low.questions)
    assert low.total == sum(f.score or 0 for f in low.factors) - low.risk_penalty


@pytest.mark.parametrize("target", [None, 0])
def test_obviously_small_budget_does_not_need_profile_configuration(target):
    a = check("건당 2,000원", target=target)
    assert a.total <= 39 and a.recommendation == "needs_review"
    assert "시장 적정가 아님" in a.factors[-1].reason


def test_pay_priority_increases_with_budget_and_preserves_fit():
    rows = [check(f"건당 {amount}원") for amount in (2_000, 100_000, 800_000, 2_000_000, 5_000_000)]
    assert [a.total for a in rows] == sorted(a.total for a in rows)
    assert all(a.factors[:5] == rows[0].factors[:5] for a in rows)


@pytest.mark.parametrize("pay", ["건당 협의", "건당 2,000원 이상", "건당 2,000~500만원"])
def test_unknown_upper_or_wide_negotiable_range_is_not_treated_as_fixed_low_pay(pay):
    a = check(pay)
    assert a.recommendation == "recommended"
    assert not any("보수가 매우 낮음" in r.text for r in a.reasons)
    assert a.factors[-1].score in (None, 0)


def test_conflicting_source_and_body_pay_does_not_invent_profitability_penalty():
    a = check("건당 2,000원", body=BODY + " 예산 500만원.")
    assert a.recommendation == "needs_review"
    assert "불일치" in a.reasons[0].text
    assert a.factors[-1].score is None


def test_exclusion_still_wins_and_zero_budget_remains_zero():
    a = check("건당 0원", source_status="closed")
    assert a.recommendation == "excluded" and a.total <= 39
    assert a.pay.min == a.pay.max == 0
