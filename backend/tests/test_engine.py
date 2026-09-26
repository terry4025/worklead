"""수집 엔진 검증 (합성 사이트). 요구 검증 사례 1, 2, 3, 4, 9, 11 을 다룬다."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, select

from tests import fixture_site as fx
from tests.conftest import client_for, make_ctx
from worklead.engine import runs
from worklead.engine.worker import Worker, schedule_due
from worklead.models import CollectionTask, DiscoveryPath, EventOutbox, Lead, Run, Source, SourcePolicy, SourceRecord
from worklead.services.settings import put_setting
from worklead.sources.manual import ManualAdapter

GROUPS = [{"id": "all", "label": "테스트", "enabled": True, "keywords": ["홈페이지", "매크로"]}]


def setup(tmp_path: Path, behavior: fx.SiteBehavior | None = None, **adapter_kw):
    behavior = behavior or fx.SiteBehavior()
    ad = fx.adapter(**adapter_kw)
    ctx = make_ctx(tmp_path, adapters={ad.source_id: ad, "manual": ManualAdapter()})
    ctx.transport = fx.transport(behavior)
    with ctx.db.session() as s:
        pol = s.get(SourcePolicy, ad.source_id)
        pol.status, pol.basis = "allowed", "테스트 사이트 — 합성"
        put_setting(s, "query_groups", {"version": 2, "groups": GROUPS})
    return ctx, behavior


def run_discovery(ctx) -> Run:
    with ctx.db.session() as s:
        run, _ = runs.enqueue(s, "discovery", source_id="fixture-site")
        run_id = run.id
    w = Worker(ctx, "collector", runs.COLLECTION_KINDS)
    while w.run_once():
        pass
    with ctx.db.session() as s:
        r = s.get(Run, run_id)
        assert r is not None
        return r


def count(ctx, model, *where) -> int:
    with ctx.db.session() as s:
        q = select(func.count()).select_from(model)
        for w in where:
            q = q.where(w)
        return s.scalar(q) or 0


def test_nationwide_rotation_dedup_and_paths(tmp_path: Path) -> None:
    ctx, behavior = setup(tmp_path, region_status="verified")
    run = run_discovery(ctx)
    assert run.state == "succeeded", (run.error_code, run.error_message)
    # 5개 지역 × 1개 검색어 묶음 (+ 제주 2쪽)
    assert run.counts["created"] == 6  # 제주 2쪽까지 포함
    assert count(ctx, Lead) == 6
    # 사례 2: 다른 지역에서 같은 공고 → 리드 1개, 발견 경로 2개 보존
    with ctx.db.session() as s:
        rec = s.scalar(select(SourceRecord).where(SourceRecord.source_post_id == "102"))
        assert rec is not None
        paths = s.scalars(select(DiscoveryPath).where(DiscoveryPath.source_record_id == rec.id)).all()
        assert {p.region_scope for p in paths} == {"busan-suyeong", "busan-haeundae"}
        assert set(rec.provenance["found_in"]) == {"부산 수영구", "부산 해운대구"}
    # 판정: 출근 CS 직원·판매자 홍보는 자동 제외, 원격 의뢰는 추천
    c = client_for(ctx)
    recs = {i["title"]: i["recommendation"] for i in c.get("/v1/leads", params={"queue": "all"}).json()["items"]}
    assert recs["쇼핑몰 주문 엑셀 매크로 만들어주실 분"] == "recommended"
    assert recs["온라인 쇼핑몰 CS 직원 (주 5일 출근)"] == "excluded"
    assert recs["홈페이지 제작해 드립니다"] == "excluded"
    notes_before = count(ctx, EventOutbox, EventOutbox.type == "notification.created")
    created_before = count(ctx, EventOutbox, EventOutbox.type == "lead.created")

    # 사례 1: 다음 주기에 반복 수집해도 리드·알림이 늘지 않는다
    run2 = run_discovery(ctx)
    assert run2.state == "succeeded"
    assert run2.scan_cycle == run.scan_cycle + 1
    assert count(ctx, Lead) == 6
    assert count(ctx, EventOutbox, EventOutbox.type == "notification.created") == notes_before
    assert count(ctx, EventOutbox, EventOutbox.type == "lead.created") == created_before
    assert run2.counts["created"] == 0 and run2.counts["duplicates"] >= 6
    # 최근 상세를 확인한 공고는 다시 요청하지 않는다 (목록만)
    assert not any(r.startswith("/jobs/") for r in behavior.requests[-10:])

    cov = c.get("/v1/sources").json()["items"][0]["coverage"]
    assert cov["target_label"] == "전국"
    assert cov["market_coverage"] == "unknown"
    assert cov["planned"] == 5 and cov["completed"] == 5
    assert cov["target_units_total"] == 17 and cov["target_units_covered"] == 3


def test_partial_coverage_is_reported(tmp_path: Path) -> None:
    # 사례 3·4: 지역 목록 전체성 미확인 → 완료돼도 부분 탐색으로 표시
    ctx, _ = setup(tmp_path, region_status="unverified")
    run = run_discovery(ctx)
    assert run.state == "succeeded"
    assert run.note and "부분 탐색" in run.note
    cov = client_for(ctx).get("/v1/sources").json()["items"][0]["coverage"]
    assert cov["region_list_status"] == "unverified"
    assert any("17개 시·도 중 3곳" in n for n in cov["notes"])


def test_403_stops_source_without_marking_closed(tmp_path: Path) -> None:
    # 사례 9: 403 은 정상 0건·마감이 아니라 소스 정지
    ctx, _ = setup(tmp_path, fx.SiteBehavior())
    run_discovery(ctx)
    before = {lead.id: lead.source_status for lead in _leads(ctx)}
    ctx.transport = fx.transport(fx.SiteBehavior(region_mode={r["param"]: "403" for r in fx.REGIONS}))
    run = run_discovery(ctx)
    assert run.state in ("failed", "partial")
    assert run.error_code == "http_403"
    assert run.counts["policy_stops"] == 1
    with ctx.db.session() as s:
        src = s.get(Source, "fixture-site")
        assert src.health_status == "blocked" and src.stopped_reason
    assert {lead.id: lead.source_status for lead in _leads(ctx)} == before
    assert count(ctx, EventOutbox, EventOutbox.type == "notification.created", EventOutbox.payload["kind"].as_string() == "source_issue") == 1
    # 정지된 소스는 스케줄·수동 실행되지 않는다
    c = client_for(ctx)
    r = c.post("/v1/runs", json={"source_id": "fixture-site", "kind": "discovery"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "source_stopped"


def test_captcha_page_is_policy_stop(tmp_path: Path) -> None:
    ctx, _ = setup(tmp_path, fx.SiteBehavior(region_mode={"seoul-gangnam": "captcha"}))
    run = run_discovery(ctx)
    assert run.error_code == "captcha_or_block_page"
    assert run.counts["created"] == 0 or run.state == "partial"


def test_429_halts_and_next_run_resumes(tmp_path: Path) -> None:
    behavior = fx.SiteBehavior(region_mode={"busan-suyeong": "429"})
    ctx, _ = setup(tmp_path, behavior, region_status="verified")
    run = run_discovery(ctx)
    assert run.state == "partial"
    assert run.error_code == "rate_limited"
    assert run.retry_after is not None
    with ctx.db.session() as s:
        pending = s.scalars(select(CollectionTask).where(CollectionTask.state == "pending")).all()
        assert pending, "남은 작업은 다음 실행으로 넘어가야 한다"
        cycle = pending[0].scan_cycle
    ctx.transport = fx.transport(fx.SiteBehavior())
    run2 = run_discovery(ctx)
    assert run2.state == "succeeded"
    assert run2.scan_cycle == cycle, "중단된 주기를 이어서 완료한다"
    assert count(ctx, Lead) == 6


def test_parse_failure_is_not_normal_empty(tmp_path: Path) -> None:
    ctx, _ = setup(tmp_path, fx.SiteBehavior(region_mode={"seoul-gangnam": "broken"}))
    run = run_discovery(ctx)
    assert run.state == "partial"
    assert run.counts["parse_failures"] == 1


def test_detail_without_structured_data_counts_parse_failure(tmp_path: Path) -> None:
    ctx, _ = setup(tmp_path, fx.SiteBehavior(detail_mode={"101": "noscript"}))
    run = run_discovery(ctx)
    assert run.counts["parse_failures"] == 1
    assert count(ctx, Lead) == 5


def test_robots_disallow_stops(tmp_path: Path) -> None:
    ctx, _ = setup(tmp_path, fx.SiteBehavior(robots="User-agent: *\nDisallow: /search\n"))
    run = run_discovery(ctx)
    assert run.error_code == "robots_disallowed"
    assert count(ctx, Lead) == 0


def test_budget_exhaustion_halts(tmp_path: Path) -> None:
    ctx, _ = setup(tmp_path, budget=4)
    run = run_discovery(ctx)
    assert run.state == "partial" and run.error_code == "budget_exhausted"
    assert run.counts["requests"] <= 4


def test_crash_resume_no_duplicates(tmp_path: Path) -> None:
    # 사례 11: 실행 도중 강제 종료 → 임대 만료 후 이어받기, 중복 저장·알림 없음
    ctx, _ = setup(tmp_path, region_status="verified")
    with ctx.db.session() as s:
        run, _ = runs.enqueue(s, "discovery", source_id="fixture-site")
        run_id = run.id
    claimed = runs.claim_next(ctx.db, "dead-worker", runs.COLLECTION_KINDS, lease_s=60)
    assert claimed is not None
    from worklead.engine.executors import run_discovery as exec_discovery

    # 작업 2개까지만 처리하고 "죽는다"
    calls = {"n": 0}
    import worklead.engine.executors as ex

    original = ex._process_ref

    def crash_after(*a, **k):
        calls["n"] += 1
        if calls["n"] > 2:
            raise KeyboardInterrupt("simulated crash")
        return original(*a, **k)

    ex._process_ref = crash_after
    try:
        try:
            exec_discovery(ctx, claimed, "dead-worker")
        except KeyboardInterrupt:
            pass
    finally:
        ex._process_ref = original
    leads_mid = count(ctx, Lead)
    assert 0 < leads_mid < 6
    # 임대 만료 처리
    with ctx.db.session() as s:
        r = s.get(Run, run_id)
        from datetime import timedelta

        from worklead.db import utcnow

        r.lease_expires_at = utcnow() - timedelta(seconds=1)
    assert runs.recover_expired(ctx.db) == 1
    w = Worker(ctx, "collector", runs.COLLECTION_KINDS)
    while w.run_once():
        pass
    with ctx.db.session() as s:
        r = s.get(Run, run_id)
        assert r.state == "succeeded", (r.state, r.error_code, r.error_message)
        # 처리 중이던 작업은 running 으로 남아 있다가 이어받기에서 다시 처리됨
    assert count(ctx, Lead) == 6
    assert count(ctx, EventOutbox, EventOutbox.type == "lead.created") == 6


def test_scheduler_idempotent_and_respects_policy(tmp_path: Path) -> None:
    ctx, _ = setup(tmp_path)
    with ctx.db.session() as s:
        src = s.get(Source, "fixture-site")
        src.auto_collect_enabled = True
    first = schedule_due(ctx)
    again = schedule_due(ctx)
    assert first and not again, "같은 구간에는 한 번만 예약"
    with ctx.db.session() as s:
        s.get(SourcePolicy, "fixture-site").status = "permission_pending"
        for r in s.scalars(select(Run)):
            r.state = "cancelled"
    assert schedule_due(ctx) == []


def test_daangn_not_runnable_until_research(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    c = client_for(ctx)
    src = next(i for i in c.get("/v1/sources").json()["items"] if i["id"] == "daangn-alba")
    assert src["policy"]["status"] == "permission_pending"
    assert src["research"]["ready"] is False
    assert all(cap["support"] == "unverified" for cap in src["capabilities"])
    # 정책만 허용으로 바꿔도 조사 미완료면 실행 불가 (가짜 결과 없음)
    r = c.patch("/v1/sources/daangn-alba", json={"policy": {"status": "allowed", "basis": "테스트"}})
    assert r.status_code == 200
    r = c.post("/v1/runs", json={"source_id": "daangn-alba", "kind": "discovery"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "research_incomplete"
    r = c.patch("/v1/sources/daangn-alba", json={"auto_collect_enabled": True})
    assert r.status_code == 409


def _leads(ctx) -> list[Lead]:
    with ctx.db.session() as s:
        return list(s.scalars(select(Lead)))
