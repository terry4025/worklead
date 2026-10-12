"""Durable full-body collection without title filtering or silent depth truncation."""
from datetime import timedelta
import json

import httpx
from sqlalchemy import select

from tests import fixture_site as fx
from tests.conftest import client_for, make_ctx
from tests.test_engine import run_discovery
from worklead.db import utcnow
from worklead.engine import runs
from worklead.engine.schedule import next_due
from worklead.engine.worker import schedule_due
from worklead.models import CollectionTask, Lead, Run, Source, SourcePolicy, SourceRecord
from worklead.sources.base import DiscoverResult, DiscoveryPlan, PostRef, TaskSpec


def paged_context(tmp_path, monkeypatch, pages, *, limit=120, detail_mode=None):
    adapter = fx.adapter()
    adapter.persist_discovery_pages = True
    adapter.discovery_request_limit = limit
    adapter.batch_resume_seconds = 60
    adapter.plan_discovery = lambda groups, now: DiscoveryPlan(
        [TaskSpec("all", "public", "공개 의뢰")], "공개 의뢰 전체", "not_applicable", "공개 목록", 0,
        target_units_total=1, target_units_covered=1)
    requests = []
    site = fx.transport(fx.SiteBehavior(detail_mode=detail_mode or {}))

    def discover(task, fetcher):
        page = task.cursor or "1"
        response = fetcher.get(f"https://{fx.HOST}/pages/{page}")
        data = json.loads(response.text)
        return DiscoverResult([PostRef(f"https://{fx.HOST}/jobs/{pid}", pid, "키워드 없는 의뢰") for pid in data["ids"]],
                              data.get("next"), {"strategy": "public_board_fulltext", "page": int(page),
                                                "listed_posts": len(data["ids"]), "has_next": bool(data.get("next"))})

    adapter.discover = discover

    def handler(request):
        requests.append(request.url.path)
        if request.url.path.startswith("/pages/"):
            return httpx.Response(200, json=pages[request.url.path.rsplit("/", 1)[1]])
        return site.handle_request(request)

    ctx = make_ctx(tmp_path, adapters={adapter.source_id: adapter})
    ctx.transport = httpx.MockTransport(handler)
    clock = [utcnow()]
    ctx.clock = lambda: clock[0]
    monkeypatch.setattr(runs, "utcnow", ctx.clock)
    with ctx.db.session() as s:
        policy = s.get(SourcePolicy, adapter.source_id)
        policy.status, policy.basis = "restricted", "합성 공개 사이트 테스트"
    return ctx, clock, requests


def test_unlimited_depth_follows_every_page_and_checks_all_bodies(tmp_path, monkeypatch):
    pages = {"1": {"ids": ["101"], "next": "2"}, "2": {"ids": ["102"], "next": "3"}, "3": {"ids": ["104"]}}
    ctx, _, requests = paged_context(tmp_path, monkeypatch, pages)
    run = run_discovery(ctx)
    assert run.state == "succeeded", run.error_message
    assert run.counts["details_fetched"] == 3
    assert all(f"/jobs/{pid}" in requests for pid in ("101", "102", "104"))
    assert run.result["discovery"]["pages_completed"] == 3
    assert run.result["discovery"]["posts_considered"] == 3
    assert run.result["discovery"]["continuation_pending"] is False
    with ctx.db.session() as s:
        assert len(s.scalars(select(Lead)).all()) == 3
        assert {t.depth for t in s.scalars(select(CollectionTask))} == {0, 1, 2}
    assert run_discovery(ctx).scan_cycle == run.scan_cycle + 1
    ctx.db.dispose()


def test_batch_resume_keeps_original_page_when_live_list_changes(tmp_path, monkeypatch):
    pages = {"1": {"ids": ["101", "102", "103"], "next": "2"}, "2": {"ids": ["104"]}}
    ctx, clock, requests = paged_context(tmp_path, monkeypatch, pages, limit=4)
    first = run_discovery(ctx)
    assert first.state == "partial" and first.error_code is None
    assert first.counts["requests"] == 4  # robots + list + two details; rejected request never counted
    assert first.result["continuation_pending"] is True
    assert first.result["discovery"]["posts_considered"] == 2
    with ctx.db.session() as s:
        task = s.scalar(select(CollectionTask))
        assert task.checkpoint["next_index"] == 2 and task.state == "pending"
    pages["1"] = {"ids": ["106"], "next": "2"}  # old 103 disappeared from the moving listing
    clock[0] += timedelta(minutes=2)
    second = run_discovery(ctx)
    assert second.state == "succeeded", second.error_message
    assert second.scan_cycle == first.scan_cycle
    assert second.result["discovery"]["resumed"] is True
    assert requests.count("/pages/1") == 1
    assert requests.count("/jobs/101") == requests.count("/jobs/102") == requests.count("/jobs/103") == 1
    with ctx.db.session() as s:
        assert {r.source_post_id for r in s.scalars(select(SourceRecord))} == {"101", "102", "103", "104"}
    ctx.db.dispose()


def test_pending_deep_page_stays_in_same_cycle_and_coverage_is_incomplete(tmp_path, monkeypatch):
    pages = {"1": {"ids": ["101"], "next": "2"}, "2": {"ids": ["102"], "next": "3"}, "3": {"ids": ["104"]}}
    ctx, clock, _ = paged_context(tmp_path, monkeypatch, pages, limit=3)
    first = run_discovery(ctx)
    assert first.state == "partial"
    coverage = client_for(ctx).get("/v1/sources").json()["items"][0]["coverage"]
    assert coverage["completed"] == 0 and coverage["pending"] == 1
    clock[0] += timedelta(minutes=2)
    second = run_discovery(ctx)
    assert second.scan_cycle == first.scan_cycle
    clock[0] += timedelta(minutes=2)
    third = run_discovery(ctx)
    assert third.state == "succeeded" and third.scan_cycle == first.scan_cycle
    coverage = client_for(ctx).get("/v1/sources").json()["items"][0]["coverage"]
    assert coverage["completed"] == 1 and coverage["pending"] == 0
    ctx.db.dispose()


def test_continuation_is_scheduled_once_without_waiting_normal_interval(tmp_path, monkeypatch):
    pages = {"1": {"ids": ["101", "102", "103"]}}
    ctx, clock, _ = paged_context(tmp_path, monkeypatch, pages, limit=3)
    first = run_discovery(ctx)
    with ctx.db.session() as s:
        source = s.get(Source, "fixture-site")
        source.auto_collect_enabled = True
        source.interval_minutes = 180
        due = next_due(s, source, "discovery", clock[0])
        assert due <= clock[0] + timedelta(seconds=61)
    clock[0] += timedelta(minutes=2)
    ids = schedule_due(ctx)
    with ctx.db.session() as s:
        discoveries = [s.get(Run, rid) for rid in ids if s.get(Run, rid).kind == "discovery"]
        assert len(discoveries) == 1
        assert discoveries[0].idempotency_key == f"continue:fixture-site:{first.id}"
    assert schedule_due(ctx) == []
    ctx.db.dispose()


def test_normal_batch_pause_does_not_hide_detail_errors(tmp_path, monkeypatch):
    pages = {"1": {"ids": ["101", "102", "103"]}}
    ctx, _, _ = paged_context(tmp_path, monkeypatch, pages, limit=4, detail_mode={"101": "404"})
    first = run_discovery(ctx)
    assert first.state == "partial" and first.counts["fetch_failures"] == 1
    assert first.result["continuation_pending"] is True
    with ctx.db.session() as s:
        assert s.get(Source, "fixture-site").health_status == "degraded"
        assert s.get(Source, "fixture-site").health_code == "detail_errors"
    assert "오류 1건" in first.note
    ctx.db.dispose()


def test_parser_upgrade_refreshes_cached_details_without_waiting_for_ttl(tmp_path, monkeypatch):
    ctx, _, requests = paged_context(tmp_path, monkeypatch, {"1": {"ids": ["101"]}})
    first = run_discovery(ctx)
    assert first.counts["details_fetched"] == 1
    assert run_discovery(ctx).counts["details_fetched"] == 0
    adapter = ctx.adapters["fixture-site"]
    adapter.parser_version = "fixed-status-parser"
    refreshed = run_discovery(ctx)
    assert refreshed.state == "succeeded"
    assert refreshed.counts["details_fetched"] == 1
    assert refreshed.counts["created"] == 0
    assert requests.count("/jobs/101") == 2
    with ctx.db.session() as s:
        assert s.scalar(select(SourceRecord)).parser_version == "fixed-status-parser"
        assert len(s.scalars(select(Lead)).all()) == 1
    ctx.db.dispose()
