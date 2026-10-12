"""Financial metrics preserve currency, unknown values, and occurrence dates."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from tests.conftest import client_for
from tests.test_source_fields import sample
from worklead.api import schemas as S
from worklead.engine.pipeline import apply_analysis, ingest
from worklead.models import Lead, LeadActivity, Outcome, SourceRecord, UserFeedback, new_id
from worklead.services import metrics as metrics_service

NOW = datetime(2026, 9, 30, 15, 10, tzinfo=UTC)  # October 1 in Korea.


@pytest.fixture
def lead_id(ctx, monkeypatch):
    monkeypatch.setattr(metrics_service, "utcnow", lambda: NOW)
    with ctx.db.session() as session:
        return ingest(session, "daangn-alba", sample(), parser_version="test", now=NOW).lead_id


def record(client, lead_id, kind, amount, *, currency="KRW", occurred_on="2026-10-01"):
    response = client.post(f"/v1/leads/{lead_id}/outcomes",
                           json={"kind": kind, "amount": amount, "currency": currency, "occurred_on": occurred_on})
    assert response.status_code == 200, response.text
    return response.json()


def totals(amount, known, records, missing=0):
    return {"amount": None if amount is None else str(amount), "known_amount": str(known),
            "records": records, "missing_amounts": missing}


def test_api_currency_groups_do_not_convert_or_combine_minor_units(ctx, lead_id):
    with client_for(ctx) as client:
        for currency, amount in (("KRW", 100_000), ("USD", 100), ("JPY", 500)):
            record(client, lead_id, "payment_received", amount, currency=currency)
        record(client, lead_id, "contract_confirmed", 120_000)
        record(client, lead_id, "contract_confirmed", 200, currency="USD")
        record(client, lead_id, "refund", 20, currency="USD")
        record(client, lead_id, "direct_cost", 70, currency="JPY")
        sales = client.get("/v1/metrics").json()["sales"]
        groups = {group["currency"]: group for group in sales["by_currency"]}
        assert list(groups) == ["JPY", "KRW", "USD"]
        assert sales["collected"] == 100_000 and sales["contract_amount"] == 120_000
        assert sales["refunded"] == 0 and sales["direct_cost"] == 0
        assert sales["contracts_confirmed"] == 2
        assert groups["KRW"]["collected"] == totals(100_000, 100_000, 1)
        assert groups["USD"]["collected"] == totals(100, 100, 1)
        assert groups["JPY"]["collected"] == totals(500, 500, 1)
        assert groups["USD"]["contract"] == totals(200, 200, 1)
        assert groups["USD"]["refunded"] == totals(20, 20, 1)
        assert groups["JPY"]["direct_cost"] == totals(70, 70, 1)
        detail = client.get(f"/v1/leads/{lead_id}").json()
        assert {(row["currency"], row["amount"]) for row in detail["outcomes"]} >= {("USD", 100), ("JPY", 500)}


def test_outcome_activity_preserves_currency_without_rewriting_old_user_records(ctx, lead_id):
    with ctx.db.session() as session:
        session.add(LeadActivity(id="old-user-activity", lead_id=lead_id, at=NOW - timedelta(days=1),
                                 kind="memo", text="이전 사용자 메모 100원 그대로"))
    with client_for(ctx) as client:
        record(client, lead_id, "payment_received", 1_000)
        record(client, lead_id, "refund", 100, currency="USD")
        record(client, lead_id, "direct_cost", None, currency="JPY")
    with ctx.db.session() as session:
        texts = list(session.scalars(select(LeadActivity.text).where(LeadActivity.lead_id == lead_id,
                                                                   LeadActivity.kind == "outcome")))
        assert "수금 기록 1,000원" in texts
        assert "환불 기록 USD 100 (최소 통화 단위)" in texts
        assert "직접 비용 기록" in texts
        assert session.get(LeadActivity, "old-user-activity").text == "이전 사용자 메모 100원 그대로"


@pytest.mark.parametrize("kind,field,legacy", [
    ("contract_confirmed", "contract", "contract_amount"),
    ("payment_received", "collected", "collected"),
    ("refund", "refunded", "refunded"),
    ("direct_cost", "direct_cost", "direct_cost"),
])
def test_unknown_amount_keeps_known_subtotal_without_claiming_zero(ctx, lead_id, kind, field, legacy):
    with client_for(ctx) as client:
        record(client, lead_id, kind, None)
        record(client, lead_id, kind, 250)
        record(client, lead_id, kind, 0)
        sales = client.get("/v1/metrics").json()["sales"]
        assert sales[legacy] is None
        assert sales["by_currency"][0][field] == totals(None, 250, 3, 1)


def test_real_zero_no_records_and_non_krw_unknown_remain_distinct(ctx, lead_id):
    with client_for(ctx) as client:
        empty = client.get("/v1/metrics").json()["sales"]
        assert empty["by_currency"] == [] and empty["collected"] == 0
        record(client, lead_id, "payment_received", 0)
        record(client, lead_id, "payment_received", None, currency="USD")
        sales = client.get("/v1/metrics").json()["sales"]
        groups = {group["currency"]: group for group in sales["by_currency"]}
        assert sales["collected"] == 0
        assert groups["KRW"]["collected"] == totals(0, 0, 1)
        assert groups["KRW"]["refunded"] == totals(0, 0, 0)
        assert groups["USD"]["collected"] == totals(None, 0, 1, 1)


def test_financial_window_is_kst_calendar_occurrence_and_excludes_old_future(ctx, lead_id):
    with client_for(ctx) as client:
        for date, amount in (("2026-09-01", 10), ("2026-09-02", 20),
                             ("2026-10-01", 30), ("2026-10-02", 40), ("2020-01-01", 50)):
            record(client, lead_id, "payment_received", amount, occurred_on=date)
        record(client, lead_id, "refund", 2, occurred_on="2026-09-02")
        record(client, lead_id, "direct_cost", 3, occurred_on="2026-10-01")
        with ctx.db.session() as session:
            # A very late backfill is included by occurrence, not by input time.
            session.add(Outcome(id=new_id("out"), lead_id=lead_id, kind="contract_confirmed", amount=80,
                                currency="KRW", occurred_on="2026-09-02", created_at=NOW - timedelta(days=90)))
        metrics = client.get("/v1/metrics").json()
        sales = metrics["sales"]
        assert (sales["period_start"], sales["period_end"], sales["period_days"], sales["time_zone"]) == (
            "2026-09-02", "2026-10-01", 30, "Asia/Seoul")
        assert (sales["collected"], sales["refunded"], sales["direct_cost"], sales["contract_amount"]) == (50, 2, 3, 80)
        assert sales["contracts_confirmed"] == 1
        assert "발생일 기준" in metrics["note"]


def test_kst_midnight_advances_the_occurrence_window(ctx, lead_id):
    with ctx.db.session() as session:
        session.add(Outcome(id=new_id("out"), lead_id=lead_id, kind="payment_received", amount=900,
                            currency="KRW", occurred_on="2026-09-01"))
    with ctx.db.session() as session:
        before = metrics_service.metrics(session, now=datetime(2026, 9, 30, 14, 59, 59, tzinfo=UTC)).sales
        after = metrics_service.metrics(session, now=datetime(2026, 9, 30, 15, 0, 0, tzinfo=UTC)).sales
        assert before.period_start.isoformat() == "2026-09-01" and before.collected == 900
        assert after.period_start.isoformat() == "2026-09-02" and after.collected == 0


def test_crm_stage_counts_are_explicit_current_inventory_and_won_is_not_payment(ctx, lead_id):
    with ctx.db.session() as session:
        lead = session.get(Lead, lead_id)
        lead.sales_stage = "contacted"
        lead.updated_at = NOW - timedelta(days=60)
    with client_for(ctx) as client:
        before = client.get("/v1/metrics").json()["sales"]
        assert before["reviewed"] == 0 and before["contacted"] == 1
        assert before["stage_scope"] == "current_all"
        assert client.patch(f"/v1/leads/{lead_id}", json={"sales_stage": "won"}).status_code == 200
        after = client.get("/v1/metrics").json()["sales"]
        assert after["won"] == 1 and after["collected"] == before["collected"] == 0


def test_legacy_currency_codes_stay_separate_and_do_not_become_krw(ctx, lead_id):
    codes = ("KRW", "krw", " usd ", "", "OLD-CODE")
    with ctx.db.session() as session:
        for code in codes:
            session.add(Outcome(id=new_id("out"), lead_id=lead_id, kind="payment_received", amount=10,
                                currency=code, occurred_on="2026-10-01"))
    with client_for(ctx) as client:
        sales = client.get("/v1/metrics").json()["sales"]
        assert sales["collected"] == 10
        assert {group["currency"] for group in sales["by_currency"]} == set(codes)
        assert all(group["collected"] == totals(10, 10, 1) for group in sales["by_currency"])


@pytest.mark.parametrize("kind,field,legacy", [
    ("contract_confirmed", "contract", "contract_amount"),
    ("payment_received", "collected", "collected"),
    ("refund", "refunded", "refunded"),
    ("direct_cost", "direct_cost", "direct_cost"),
])
def test_aggregate_larger_than_js_safe_integer_remains_exact_decimal_string(ctx, lead_id, kind, field, legacy):
    maximum = 9_007_199_254_740_991
    with client_for(ctx) as client:
        record(client, lead_id, kind, maximum)
        record(client, lead_id, kind, maximum)
        record(client, lead_id, kind, 3)
        sales = client.get("/v1/metrics").json()["sales"]
        assert sales[legacy] is None
        exact = str(maximum * 2 + 3)
        assert sales["by_currency"][0][field] == {
            "amount": exact, "known_amount": exact, "records": 3, "missing_amounts": 0}


@pytest.mark.parametrize("amount", [True, False, 1.0, 1.5, "100", -1, 9_007_199_254_740_992])
def test_outcome_amount_rejects_non_integer_or_unsafe_values(ctx, lead_id, amount):
    with client_for(ctx) as client:
        response = client.post(f"/v1/leads/{lead_id}/outcomes",
                               json={"kind": "payment_received", "amount": amount, "occurred_on": "2026-10-01"})
        assert response.status_code == 422
        assert client.get(f"/v1/leads/{lead_id}").json()["outcomes"] == []


@pytest.mark.parametrize("amount", [None, 0, 9_007_199_254_740_991])
def test_outcome_amount_preserves_null_zero_and_max_safe_integer(ctx, lead_id, amount):
    with client_for(ctx) as client:
        detail = record(client, lead_id, "payment_received", amount)
        assert detail["outcomes"][0]["amount"] == amount


@pytest.mark.parametrize("currency", ["usd", " USD", "USD ", "US", "USDD", "123", "원", "", None, 123])
def test_new_currency_requires_three_uppercase_ascii_letters(ctx, lead_id, currency):
    with client_for(ctx) as client:
        response = client.post(f"/v1/leads/{lead_id}/outcomes",
                               json={"kind": "payment_received", "amount": 10, "currency": currency, "occurred_on": "2026-10-01"})
        assert response.status_code == 422


def test_old_metrics_fixture_without_added_fields_remains_readable():
    metrics = S.SalesMetrics(period_days=30, reviewed=0, contacted=0, negotiating=0, won=0,
                             contracts_confirmed=0, contract_amount=0, collected=0, refunded=0, direct_cost=0)
    assert metrics.by_currency == [] and metrics.period_start is None and metrics.period_end is None
    assert metrics.stage_scope == "current_all" and metrics.time_zone == "Asia/Seoul"


def test_reanalysis_and_changed_body_do_not_turn_old_user_judgment_into_recent_review(ctx, lead_id):
    with ctx.db.session() as session:
        lead = session.get(Lead, lead_id)
        lead.user_mark = "interested"
        session.add(LeadActivity(id=new_id("act"), lead_id=lead_id, at=NOW - timedelta(days=60),
                                 kind="user_mark", text="Old interest"))
    with ctx.db.session() as session:
        assert metrics_service.metrics(session, now=NOW).quality.reviewed == 0
        lead = session.get(Lead, lead_id)
        rec = session.get(SourceRecord, lead.primary_record_id)
        apply_analysis(session, lead, rec, now=NOW, source_kind="site")
    changed = sample()
    changed.body += " 추가 제작 요구사항을 기록합니다."
    with ctx.db.session() as session:
        ingest(session, "daangn-alba", changed, parser_version="test", now=NOW)
    with ctx.db.session() as session:
        metrics = metrics_service.metrics(session, now=NOW)
        assert metrics.quality.reviewed == 0 and metrics.sales.reviewed == 0
        assert metrics.quality.confirmed_fit == 0 and metrics.quality.median_hours_to_review is None


def test_actual_manual_review_counts_once_and_cleared_interest_still_counts(ctx, lead_id, monkeypatch):
    from worklead.api import app as app_module
    monkeypatch.setattr(app_module, "utcnow", lambda: NOW)
    with client_for(ctx) as client:
        assert client.patch(f"/v1/leads/{lead_id}", json={"memo": "실제 검토 메모"}).status_code == 200
        assert client.patch(f"/v1/leads/{lead_id}", json={"user_mark": "interested"}).status_code == 200
        first = client.get("/v1/metrics").json()
        assert first["quality"]["reviewed"] == first["sales"]["reviewed"] == 1
        assert first["quality"]["confirmed_fit"] == 1
        assert client.patch(f"/v1/leads/{lead_id}", json={"user_mark": None}).status_code == 200
        cleared = client.get("/v1/metrics").json()
        assert cleared["quality"]["reviewed"] == cleared["sales"]["reviewed"] == 1
        assert cleared["quality"]["confirmed_fit"] == 0


def test_feedback_only_is_user_review_but_money_and_generated_draft_are_not(ctx, lead_id, monkeypatch):
    from worklead.api import app as app_module
    monkeypatch.setattr(app_module, "utcnow", lambda: NOW)
    with client_for(ctx) as client:
        record(client, lead_id, "payment_received", 100)
        assert client.post(f"/v1/leads/{lead_id}/draft").status_code == 200
        assert client.get("/v1/metrics").json()["quality"]["reviewed"] == 0
        response = client.post(f"/v1/leads/{lead_id}/feedback", json={"kind": "remote", "value": "denied"})
        assert response.status_code == 200, response.text
        metrics = client.get("/v1/metrics").json()
        assert metrics["quality"]["reviewed"] == metrics["sales"]["reviewed"] == 1
        assert metrics["quality"]["remote_false_positive"] == 1


@pytest.mark.parametrize("kind", ["user_mark", "sales_stage", "memo", "feedback"])
def test_review_window_includes_boundaries_excludes_future_and_is_distinct(ctx, lead_id, kind):
    since = NOW - timedelta(days=30)
    with ctx.db.session() as session:
        for at in (since - timedelta(microseconds=1), NOW + timedelta(microseconds=1)):
            session.add(LeadActivity(id=new_id("act"), lead_id=lead_id, at=at, kind=kind, text="Outside window"))
    with ctx.db.session() as session:
        assert metrics_service.metrics(session, now=NOW).quality.reviewed == 0
        for at in (since, NOW, NOW - timedelta(hours=1)):
            session.add(LeadActivity(id=new_id("act"), lead_id=lead_id, at=at, kind=kind, text="Inside window"))
    with ctx.db.session() as session:
        metrics = metrics_service.metrics(session, now=NOW)
        assert metrics.quality.reviewed == metrics.sales.reviewed == 1


def test_current_judgment_without_activity_is_not_guessed_as_review(ctx, lead_id):
    with ctx.db.session() as session:
        lead = session.get(Lead, lead_id)
        lead.user_mark, lead.sales_stage, lead.updated_at = "interested", "won", NOW
    with ctx.db.session() as session:
        metrics = metrics_service.metrics(session, now=NOW)
        assert metrics.quality.reviewed == metrics.sales.reviewed == 0
        assert metrics.quality.confirmed_fit == 0
        assert metrics.sales.won == 1 and metrics.sales.stage_scope == "current_all"


def test_review_latency_uses_first_valid_user_action_not_future_or_before_discovery(ctx, lead_id):
    with ctx.db.session() as session:
        lead = session.get(Lead, lead_id)
        lead.first_seen_at = NOW - timedelta(hours=10)
        for at, kind in ((NOW - timedelta(hours=11), "memo"),
                         (NOW - timedelta(hours=7), "feedback"),
                         (NOW - timedelta(hours=1), "user_mark"),
                         (NOW + timedelta(hours=1), "sales_stage")):
            session.add(LeadActivity(id=new_id("act"), lead_id=lead_id, at=at, kind=kind, text="Synthetic user action"))
    with ctx.db.session() as session:
        quality = metrics_service.metrics(session, now=NOW).quality
        assert quality.reviewed == 1 and quality.median_hours_to_review == 3.0


def test_review_latency_missing_valid_activity_is_unknown_and_future_feedback_is_excluded(ctx, lead_id):
    with ctx.db.session() as session:
        lead = session.get(Lead, lead_id)
        lead.first_seen_at = NOW
        session.add(LeadActivity(id=new_id("act"), lead_id=lead_id, at=NOW - timedelta(hours=1),
                                 kind="memo", text="Invalid timestamp before discovery"))
        session.add(UserFeedback(id=new_id("fb"), lead_id=lead_id, kind="remote", value="denied",
                                 created_at=NOW + timedelta(seconds=1)))
        session.add(UserFeedback(id=new_id("fb"), lead_id=lead_id, kind="real_request", value="no",
                                 created_at=NOW + timedelta(seconds=1)))
    with ctx.db.session() as session:
        quality = metrics_service.metrics(session, now=NOW).quality
        assert quality.reviewed == 1 and quality.median_hours_to_review is None
        assert quality.remote_false_positive == quality.buyer_false_positive == 0


@pytest.mark.parametrize("kind", ["created", "outcome", "draft", "relation", "source_changed", "analysis"])
def test_non_review_activity_does_not_increase_reviewed(ctx, lead_id, kind):
    with ctx.db.session() as session:
        session.add(LeadActivity(id=new_id("act"), lead_id=lead_id, at=NOW, kind=kind, text="Not a review action"))
    with ctx.db.session() as session:
        metrics = metrics_service.metrics(session, now=NOW)
        assert metrics.quality.reviewed == metrics.sales.reviewed == 0
