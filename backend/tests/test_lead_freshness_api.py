"""Fresh review candidates, observable timings and schedule gaps on disposable DBs."""
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from tests.conftest import client_for
from tests.test_source_fields import sample
from worklead.api.app import _event_data
from worklead.analysis.types import DateInfo
from worklead.engine.pipeline import ingest
from worklead.engine.events import emit
from worklead.models import DiscoveryListing, Lead, Run, Source, SourcePolicy, SourceRecord
from worklead.services import leads, metrics, sources

NOW = datetime(2026, 10, 16, 7, tzinfo=UTC)


def add(ctx, suffix, *, minutes=20, precision="exact", **changes):
    post = sample()
    post.source_post_id = suffix
    post.original_url = post.canonical_url = f"https://jobs.daangn.com/job-posts/{suffix}"
    post.title += " " + suffix
    post.published = DateInfo(NOW - timedelta(minutes=minutes), precision, "합성 게시 시각")
    post.deadline = None
    with ctx.db.session() as s:
        item = ingest(s, "daangn-alba", post, parser_version="test", now=NOW - timedelta(minutes=5))
        lead = s.get(Lead, item.lead_id)
        for key, value in changes.items():
            setattr(lead, key, value)
        return item.lead_id, item.record_id


def test_fresh_queue_counts_and_cursor_share_eligibility_and_default_latest_sort(ctx, monkeypatch):
    monkeypatch.setattr(leads, "utcnow", lambda: NOW)
    older, _ = add(ctx, "older", priority_total=99, recommendation="recommended")
    newer, _ = add(ctx, "newer", minutes=10, priority_total=39, recommendation="needs_review")
    for suffix, kwargs in [
        ("expired", {"minutes": 24 * 60 + 1}),
        ("approximate", {"precision": "approximate"}),
        ("future", {"minutes": -10}),
        ("dismissed", {"user_mark": "dismissed"}),
        ("contacted", {"sales_stage": "contacted"}),
        ("closed", {"source_status": "closed"}),
        ("access", {"access_status": "unavailable"}),
        ("excluded", {"recommendation": "excluded"}),
        ("hiring", {"demand_intent": "employee_hiring"}),
        ("wage", {"engagement_type": "hourly_contract"}),
        ("stale", {"last_checked_at": NOW - timedelta(hours=25)}),
    ]:
        add(ctx, suffix, **kwargs)
    with client_for(ctx) as c:
        first = c.get("/v1/leads", params={"queue": "fresh", "limit": 1}).json()
        assert [item["id"] for item in first["items"]] == [newer]
        second = c.get("/v1/leads", params={"queue": "fresh", "limit": 1, "cursor": first["next_cursor"]}).json()
        assert [item["id"] for item in second["items"]] == [older]
        assert second["has_more"] is False
        assert c.get("/v1/leads/queue-counts").json()["counts"]["fresh"] == 2
        assert c.get("/v1/leads", params={"queue": "fresh", "sort": "priority"}).json()["items"][0]["id"] == older
        assert first["items"][0]["recommendation"] == "needs_review"


def test_summary_distinguishes_publication_list_observation_and_detail_time(ctx, monkeypatch):
    monkeypatch.setattr(leads, "utcnow", lambda: NOW)
    lead_id, record_id = add(ctx, "observed")
    listed = NOW - timedelta(minutes=9)
    with ctx.db.session() as s:
        rec = s.get(SourceRecord, record_id)
        s.add(DiscoveryListing(source_id=rec.source_id, ref_key="fixture-key", source_post_id=rec.source_post_id,
                               url=rec.original_url, title_hint=rec.title, title_digest="0" * 64,
                               first_listed_at=listed, last_listed_at=NOW, selection="core"))
    with client_for(ctx) as c:
        detail = c.get(f"/v1/leads/{lead_id}").json()
        row = c.get("/v1/leads", params={"queue": "fresh"}).json()["items"][0]
        assert row["freshness"] == detail["freshness"]
        assert row["freshness"]["discovery_delay_seconds"] == 15 * 60
        assert row["freshness"]["listing_to_detail_seconds"] == 4 * 60
        assert row["freshness"]["first_listed_at"] == listed.isoformat().replace("+00:00", "Z")
    with ctx.db.session() as s:
        observation = s.scalar(select(DiscoveryListing))
        observation.first_listed_at = NOW
    with client_for(ctx) as c:
        # Existing records first observed by the new feature get no invented historical list latency.
        assert c.get(f"/v1/leads/{lead_id}").json()["freshness"]["listing_to_detail_seconds"] is None


@pytest.mark.parametrize("precision,minutes", [("approximate", 20), ("unknown", 20), ("exact", -5)])
def test_imprecise_or_republished_dates_do_not_show_negative_or_fake_delay(ctx, monkeypatch, precision, minutes):
    monkeypatch.setattr(leads, "utcnow", lambda: NOW)
    lead_id, _ = add(ctx, "uncertain", precision=precision, minutes=minutes)
    with client_for(ctx) as c:
        assert c.get(f"/v1/leads/{lead_id}").json()["freshness"]["discovery_delay_seconds"] is None


def test_freshness_metrics_keep_denominators_and_unknown_dates(ctx):
    add(ctx, "ten", minutes=15)
    add(ctx, "fifty", minutes=55)
    add(ctx, "approx", precision="approximate")
    add(ctx, "republished", minutes=-5)
    with ctx.db.session() as s:
        result = metrics.freshness_metrics(s, NOW)[0]
    assert result.records == 4 and result.samples == 2 and result.unavailable_samples == 2
    assert result.median_discovery_seconds == 30 * 60
    assert result.p95_discovery_seconds == 50 * 60
    assert result.within_15_minutes == 1 and result.within_1_hour == 2
    assert "백필" in result.note


def test_schedule_monitoring_distinguishes_overdue_disabled_active_and_cooldown(ctx):
    with ctx.db.session() as s:
        source = s.get(Source, "daangn-alba")
        source.auto_collect_enabled = True
        source.interval_minutes = 30
        s.get(SourcePolicy, source.id).status = "allowed"
        run = Run(id="gap", source_id=source.id, kind="discovery", state="succeeded", trigger="schedule",
                  created_at=NOW - timedelta(hours=4), started_at=NOW - timedelta(hours=4),
                  finished_at=NOW - timedelta(hours=4))
        s.add(run); s.flush()
        adapter = ctx.adapters[source.id]
        gap = sources.monitoring(s, source, adapter, now=NOW)
        assert gap.state == "overdue" and gap.overdue_seconds == 3.5 * 3600
        run.retry_after = NOW + timedelta(hours=1)
        assert sources.monitoring(s, source, adapter, now=NOW).state == "waiting"
        run.state = "queued"
        run.result = {"collection_timing": {"gap_seconds": 14 * 3600}}
        assert sources.monitoring(s, source, adapter, now=NOW).state == "running"
        assert sources.monitoring(s, source, adapter, now=NOW).previous_gap_seconds == 14 * 3600
        source.auto_collect_enabled = False
        assert sources.monitoring(s, source, adapter, now=NOW).state == "disabled"
        source.auto_collect_enabled = True
        source.stopped_reason = "차단 확인 필요"
        assert sources.monitoring(s, source, adapter, now=NOW).state == "stopped"


def test_five_minute_daangn_watch_is_allowed_but_faster_setting_rejected(client):
    assert client.patch("/v1/sources/daangn-alba", json={"interval_minutes": 15}).status_code == 200
    assert client.patch("/v1/sources/daangn-alba", json={"interval_minutes": 5}).status_code == 200
    assert client.patch("/v1/sources/daangn-alba", json={"interval_minutes": 4}).status_code == 422


def test_sse_replay_never_exposes_native_claim_tokens_or_mutates_delivery():
    payload = {"kind": "new_review_lead", "lead_id": "synthetic", "native_delivery": {"claim_token": "private-claim"}}
    result = _event_data(1, "event", "notification.created", payload, NOW, "quiet_hours")
    assert "native_delivery" not in result and "private-claim" not in str(result)
    assert result["kind"] == "new_review_lead" and result["suppressed_reason"] == "quiet_hours"
    assert payload["native_delivery"]["claim_token"] == "private-claim"


def test_bootstrap_persists_recent_app_morning_summary_for_startup_before_sse(ctx, monkeypatch):
    import worklead.api.app as api
    monkeypatch.setattr(api, "utcnow", lambda: NOW)
    with ctx.db.session() as s:
        row = emit(s, "notification.created", {"kind": "new_review_lead", "title": "밤사이 제작 의뢰 2건"},
                   dedupe_key="notify:quiet_summary:new_review_lead:synthetic", notify=False)
        row.created_at = NOW - timedelta(hours=1)
        wanted = row.id
        old = emit(s, "notification.created", {"kind": "new_lead", "title": "이전 요약"},
                   dedupe_key="notify:quiet_summary:new_lead:old", notify=False)
        old.created_at = NOW - timedelta(days=2)
        emit(s, "notification.created", {"kind": "new_lead", "title": "요약 아님"}, notify=False)
    with client_for(ctx) as c:
        first = c.get("/v1/bootstrap").json()["notices"]
        second = c.get("/v1/bootstrap").json()["notices"]
    assert first == second and len(first) == 1
    assert first[0]["id"] == wanted and first[0]["title"] == "밤사이 제작 의뢰 2건"
    assert "native_delivery" not in first[0]


def test_previous_revision_remains_valid_but_current_revision_requires_listing_metadata(ctx):
    import sqlite3
    from alembic import command
    from worklead import storage
    ctx.db.dispose()
    command.downgrade(storage.alembic_config(ctx.config.db_path), "0006_daangn_cafe")
    assert storage.validate_database(ctx.config.db_path) == ("0006_daangn_cafe", 0)
    with sqlite3.connect(ctx.config.db_path) as connection:
        connection.execute("UPDATE alembic_version SET version_num='0007_discovery_listings'")
    with pytest.raises(ValueError, match="필수 테이블"):
        storage.validate_database(ctx.config.db_path)
