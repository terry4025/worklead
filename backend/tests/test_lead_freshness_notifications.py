"""Recent uncertain buyer work is alerted without relaxing its classification."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select

from worklead.analysis.types import DateInfo, PayInfo
from worklead.engine.pipeline import ingest
from worklead.jobs.auto_apply import condition
from worklead.models import EventOutbox, Lead, SourceRecord
from worklead.services import native_notifications as native
from worklead.services.settings import put_setting
from worklead.sources.base import ParsedPost

NOW = datetime(2026, 10, 16, 3, tzinfo=UTC)


def post(**changes):
    base = ParsedPost(
        title="온라인 상세페이지 만들어주실수 있는분이요",
        body="업소 홍보에 쓰일 매니저 소개 페이지 제작이 필요합니다. 매니저 홍보 상세페이지입니다. 관련 작업이 가능하신 분이면 좋겠습니다.",
        original_url="https://jobs.daangn.com/job-posts/freshness001",
        canonical_url="https://jobs.daangn.com/job-posts/freshness001", source_post_id="freshness001",
        published=DateInfo(NOW - timedelta(hours=1), "exact", "2026-10-16T02:00:00Z"),
        source_status="open", pay=PayInfo(raw="건당 150,000원", unit="project", min=150_000, max=150_000),
    )
    return replace(base, **changes)


def save(ctx, p=None, *, now=NOW, source_kind="site"):
    with ctx.db.session() as s:
        return ingest(s, "daangn-alba", p or post(), parser_version="test", now=now, source_kind=source_kind)


def alerts(ctx):
    with ctx.db.session() as s:
        return list(s.scalars(select(EventOutbox).where(EventOutbox.type == "notification.created").order_by(EventOutbox.seq)))


def test_new_review_alert_does_not_promote_and_duplicate_or_reanalysis_does_not_repeat(ctx):
    ctx.clock = lambda: NOW
    first = save(ctx)
    assert first.recommendation == "needs_review"
    assert [row.payload["kind"] for row in alerts(ctx)] == ["new_review_lead"]
    assert alerts(ctx)[0].created_at == NOW
    assert native.list_pending(ctx)["backlog_count"] == 1
    again = save(ctx, now=NOW + timedelta(minutes=1))
    assert again.kind == "unchanged" and again.lead_id == first.lead_id
    assert len(alerts(ctx)) == 1
    with ctx.db.session() as s:
        lead = s.get(Lead, first.lead_id)
        assert lead.recommendation == "needs_review" and lead.work_mode == "unknown"
        assert any(reason["text"] == "재택 미확인" for reason in lead.reasons)


@pytest.mark.parametrize("age,precision,expected", [
    (timedelta(hours=24), "exact", True),
    (timedelta(hours=24, microseconds=1), "exact", False),
    (-timedelta(seconds=1), "exact", False),
    (timedelta(hours=1), "day", False),
    (timedelta(hours=1), "approximate", False),
    (timedelta(hours=1), "unknown", False),
])
def test_review_freshness_requires_valid_precise_publication(ctx, age, precision, expected):
    save(ctx, post(published=DateInfo(NOW - age, precision)))
    assert bool(alerts(ctx)) is expected


@pytest.mark.parametrize("changes,source_kind", [
    ({"source_status": "closed"}, "site"),
    ({"source_status": "unknown"}, "site"),
    ({"published": DateInfo()}, "site"),
    ({"title": "홈페이지 제작해 드립니다", "body": "홈페이지 제작해 드립니다. 고객님 문의 주세요."}, "site"),
    ({}, "manual"),
])
def test_closed_unknown_seller_or_manual_are_not_new_review_alerts(ctx, changes, source_kind):
    save(ctx, post(**changes), source_kind=source_kind)
    assert not any(row.payload["kind"] == "new_review_lead" for row in alerts(ctx))


def test_non_onsite_buyer_request_with_hourly_pay_is_a_review_alert(ctx):
    # 2026-11-07: a development buyer request (not onsite) posted with 시급 is a
    # review lead with a caution, so it now raises a review alert; it never auto-applies.
    result = save(ctx, post(pay=PayInfo(raw="시급 15,000원", unit="hour", min=15_000, max=15_000)))
    assert result.recommendation == "needs_review"
    with ctx.db.session() as s:
        lead = s.get(Lead, result.lead_id)
        assert lead.pay_unit == "hour" and lead.work_mode != "onsite"
        assert any("시급 표기" in reason["text"] for reason in lead.reasons)
        assert condition(s, lead, NOW, 0, queue=True) == "condition_changed"
    assert [row.payload["kind"] for row in alerts(ctx)] == ["new_review_lead"]


def test_review_and_recommended_toggles_are_independent(ctx):
    with ctx.db.session() as s:
        put_setting(s, "notifications", {"new_recommended": False, "new_review": True})
    save(ctx)
    assert [row.payload["kind"] for row in alerts(ctx)] == ["new_review_lead"]
    with ctx.db.session() as s:
        put_setting(s, "notifications", {"new_recommended": True, "new_review": False})
    save(ctx, post(source_post_id="freshness002"))
    assert len(alerts(ctx)) == 1


def test_low_pay_review_stays_caution_and_does_not_become_recommended(ctx):
    result = save(ctx, post(pay=PayInfo(raw="건당 10,000원", unit="project", min=10_000, max=10_000)))
    with ctx.db.session() as s:
        lead = s.get(Lead, result.lead_id)
        assert lead.recommendation == "needs_review"
        assert any("저가" in reason["text"] or "낮" in reason["text"] for reason in lead.reasons)
    assert [row.payload["kind"] for row in alerts(ctx)] == ["new_review_lead"]


def test_quiet_end_is_exact_and_disabled_type_never_releases_deferred_alert(ctx):
    night = NOW.replace(hour=14)  # 23:00 KST
    with ctx.db.session() as s:
        put_setting(s, "notifications", {"quiet_hours": {"enabled": True, "start": "22:00", "end": "08:00"}})
    save(ctx, post(published=DateInfo(night - timedelta(minutes=1), "exact")), now=night)
    id_ = alerts(ctx)[0].id
    ctx.clock = lambda: NOW.replace(hour=22, minute=59, second=59)
    assert native.list_pending(ctx)["items"] == []
    assert native.claim(ctx, id_)["state"] == "pending"
    ctx.db.dispose()
    ctx.clock = lambda: NOW.replace(hour=23)
    assert native.list_pending(ctx)["backlog_count"] == 1
    with ctx.db.session() as s:
        put_setting(s, "notifications", {"new_review": False})
    assert native.list_pending(ctx)["items"] == []
    assert native.claim(ctx, id_)["reason"] == "type_disabled"
    with ctx.db.session() as s:
        put_setting(s, "notifications", {"new_review": True})
    assert native.list_pending(ctx)["items"] == []  # Terminal suppression is not revived.


@pytest.mark.parametrize("change", ["expired_publication", "excluded", "closed", "inaccessible", "dismissed", "contacted", "stale_check"])
def test_current_review_eligibility_is_rechecked_at_get_and_claim(ctx, change):
    result = save(ctx)
    id_ = alerts(ctx)[0].id
    ctx.clock = lambda: NOW
    with ctx.db.session() as s:
        lead, rec = s.get(Lead, result.lead_id), s.get(SourceRecord, result.record_id)
        if change == "expired_publication": rec.published_at = NOW - timedelta(days=2)
        if change == "excluded": lead.recommendation = "excluded"
        if change == "closed": rec.source_status = "closed"
        if change == "inaccessible": rec.access_status = "error"
        if change == "dismissed": lead.user_mark = "dismissed"
        if change == "contacted": lead.sales_stage = "contacted"
        if change == "stale_check": rec.last_checked_at = NOW - timedelta(days=2)
    assert native.list_pending(ctx)["items"] == []
    result = native.claim(ctx, id_)
    assert not result["allowed"] and result["reason"] == "lead_not_eligible"


def night_posts(ctx, *, recommended=False):
    night = NOW.replace(hour=14)
    with ctx.db.session() as s:
        put_setting(s, "notifications", {"quiet_hours": {"enabled": True, "start": "22:00", "end": "08:00"}})
    p = post(published=DateInfo(night - timedelta(minutes=1), "exact"))
    if recommended:
        p = replace(p, source_post_id="freshness002", title="홈페이지 제작 의뢰",
                    body="홈페이지 제작 의뢰합니다. 원격으로 진행합니다.",
                    pay=PayInfo(raw="건당 100만원", unit="project", min=1_000_000, max=1_000_000))
    result = save(ctx, p, now=night)
    ctx.clock = lambda: night
    return result


def test_morning_app_summary_with_os_off_is_persistent_private_and_leaves_native_pending(ctx):
    night_posts(ctx)
    first_id = alerts(ctx)[0].id
    ctx.clock = lambda: NOW.replace(hour=22, minute=59, second=59)
    assert native.release_quiet_summary(ctx) == 0
    ctx.clock = lambda: NOW.replace(hour=23)  # Exact 08:00 KST boundary
    assert native.release_quiet_summary(ctx) == 1
    original, summary = alerts(ctx)
    assert original.id == first_id and original.notify is True
    assert original.payload["native_delivery"]["state"] == "pending"
    assert original.payload["native_delivery"]["app_summary_released"] == summary.id
    assert summary.notify is False and summary.suppressed_reason is None
    assert summary.created_at == NOW.replace(hour=23)
    assert summary.payload == {
        "kind": "new_review_lead", "title": "밤사이 새 제작 의뢰 1건 · 조건을 확인하세요",
        "lead_id": None, "source_id": None,
    }
    # No Windows preference or native claim is needed for the app-only event.
    assert [item["id"] for item in native.list_pending(ctx)["items"]] == [first_id]
    ctx.db.dispose()
    assert native.release_quiet_summary(ctx) == 0
    assert len(alerts(ctx)) == 2
    claimed = native.claim(ctx, first_id)
    assert claimed["allowed"]
    assert native.release_quiet_summary(ctx) == 0
    assert len(alerts(ctx)) == 2


def test_morning_summary_groups_multiple_leads_without_public_identifiers_or_titles(ctx):
    night_posts(ctx)
    save(ctx, post(source_post_id="freshness003", published=DateInfo(NOW.replace(hour=14), "exact")), now=NOW.replace(hour=14))
    night_posts(ctx, recommended=True)
    originals = alerts(ctx)
    assert [row.payload["kind"] for row in originals] == ["new_review_lead", "new_review_lead", "new_lead"]
    ctx.clock = lambda: NOW.replace(hour=23)
    assert native.release_quiet_summary(ctx) == 3
    summaries = [row for row in alerts(ctx) if not row.notify]
    assert len(summaries) == 2
    assert {row.payload["title"] for row in summaries} == {"밤사이 새 제작 의뢰 1건 · 조건을 확인하세요", "밤사이 새 제작 의뢰 2건 · 조건을 확인하세요"}
    assert native.list_pending(ctx)["backlog_count"] == 3  # Summaries never add native work.


def test_disabled_review_type_is_not_included_in_morning_app_summary(ctx):
    night_posts(ctx)
    night_posts(ctx, recommended=True)
    ctx.clock = lambda: NOW.replace(hour=23)
    with ctx.db.session() as s:
        put_setting(s, "notifications", {"new_review": False})
    assert native.release_quiet_summary(ctx) == 1
    summaries = [row for row in alerts(ctx) if not row.notify]
    assert [row.payload["kind"] for row in summaries] == ["new_lead"]
    original_review = next(row for row in alerts(ctx) if row.payload["kind"] == "new_review_lead")
    assert "app_summary_released" not in original_review.payload["native_delivery"]
    assert native.release_quiet_summary(ctx) == 0


def test_concurrent_morning_release_emits_one_app_summary(ctx):
    night_posts(ctx)
    ctx.clock = lambda: NOW.replace(hour=23)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: native.release_quiet_summary(ctx), range(4)))
    assert sorted(results) == [0, 0, 0, 1]
    assert len([row for row in alerts(ctx) if not row.notify]) == 1


def test_app_summary_marker_survives_another_quiet_claim_before_os_delivery(ctx):
    night_posts(ctx)
    id_ = alerts(ctx)[0].id
    ctx.clock = lambda: NOW.replace(hour=23)
    assert native.release_quiet_summary(ctx) == 1
    with ctx.db.session() as s:
        put_setting(s, "notifications", {"quiet_hours": {"enabled": True, "start": "08:00", "end": "10:00"}})
    assert native.claim(ctx, id_)["state"] == "pending"
    ctx.clock = lambda: NOW + timedelta(days=1, hours=-2)  # 10:00 KST next morning
    assert native.release_quiet_summary(ctx) == 0
    assert len(alerts(ctx)) == 2


@pytest.mark.parametrize("change", ["legacy", "expired_publication", "excluded", "closed", "inaccessible", "dismissed", "contacted", "stale_check", "attempted"])
def test_morning_summary_never_releases_legacy_or_no_longer_eligible_leads(ctx, change):
    result = night_posts(ctx)
    original_id = alerts(ctx)[0].id
    ctx.clock = lambda: NOW.replace(hour=23)
    with ctx.db.session() as s:
        lead, rec = s.get(Lead, result.lead_id), s.get(SourceRecord, result.record_id)
        if change == "expired_publication": rec.published_at = NOW - timedelta(days=2)
        if change == "excluded": lead.recommendation = "excluded"
        if change == "closed": rec.source_status = "closed"
        if change == "inaccessible": rec.access_status = "error"
        if change == "dismissed": lead.user_mark = "dismissed"
        if change == "contacted": lead.sales_stage = "contacted"
        if change == "stale_check": rec.last_checked_at = NOW - timedelta(days=2)
        if change == "legacy":
            row = s.scalar(select(EventOutbox).where(EventOutbox.id == original_id))
            row.payload = {key: value for key, value in row.payload.items() if key != "native_delivery"}
    if change == "attempted":
        assert native.claim(ctx, original_id)["allowed"]
    assert native.release_quiet_summary(ctx) == 0
    assert len(alerts(ctx)) == 1
