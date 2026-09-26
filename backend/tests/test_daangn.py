"""당근알바 어댑터 검증 (합성 사이트 — tests/fixture_daangn.py). 실제 사이트를 호출하지 않는다."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from tests import fixture_daangn as fx
from tests.conftest import client_for, make_ctx
from worklead.engine import runs
from worklead.engine.http import FetchResult
from worklead.engine.worker import Worker
from worklead.models import Lead, Run, Snapshot, SourcePolicy, SourceRecord
from worklead.services.settings import DEFAULT_QUERY_GROUPS
from worklead.sources import daangn
from worklead.sources.base import PostRef
from worklead.sources.daangn.adapter import title_from_url, title_keywords
from worklead.sources.manual import ManualAdapter

SID = "daangn-alba"


def setup(tmp_path: Path, behavior: fx.Behavior | None = None, *, allow: bool = True, **cfg_kw):
    behavior = behavior or fx.Behavior()
    ad = daangn.create()
    ctx = make_ctx(tmp_path, adapters={SID: ad, "manual": ManualAdapter()}, **cfg_kw)
    ctx.transport = fx.transport(behavior)
    if allow:
        with ctx.db.session() as s:
            pol = s.get(SourcePolicy, SID)
            pol.status, pol.basis = "allowed", "테스트 — 합성 사이트"
    return ctx, behavior


def run_discovery(ctx) -> Run:
    with ctx.db.session() as s:
        run, _ = runs.enqueue(s, "discovery", source_id=SID)
        run_id = run.id
    w = Worker(ctx, "collector", runs.COLLECTION_KINDS)
    while w.run_once():
        pass
    with ctx.db.session() as s:
        r = s.get(Run, run_id)
        assert r is not None
        return r


def test_research_ready_but_policy_pending_blocks_runs(tmp_path: Path) -> None:
    ctx, behavior = setup(tmp_path, allow=False)
    c = client_for(ctx)
    src = next(i for i in c.get("/v1/sources").json()["items"] if i["id"] == SID)
    assert src["policy"]["status"] == "permission_pending"
    assert src["research"]["ready"] is True
    caps = {cap["key"]: cap["support"] for cap in src["capabilities"]}
    assert caps["nationwide_search"] == "supported" and caps["remote_filter"] == "unsupported"
    r = c.post("/v1/runs", json={"source_id": SID, "kind": "discovery"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "policy_not_allowed"
    assert behavior.requests == []  # 정책 확인 전에는 요청하지 않는다


def test_sitemap_discovery_filters_titles_and_parses_details(tmp_path: Path) -> None:
    ctx, behavior = setup(tmp_path)
    run = run_discovery(ctx)
    assert run.state == "partial", (run.state, run.error_code, run.error_message)  # 구조가 바뀐 상세 1건 = 파싱 실패
    assert run.counts["parse_failures"] == 1
    # 제목 선별: 홀서버(업무 무관)·200일 전 공고는 상세를 요청하지 않는다
    detail_paths = [p for p in behavior.requests if p.startswith("/job-posts/")]
    assert not any("홀서버" in p for p in detail_paths)
    assert not any("e5f6g7h8i9j0" in p for p in detail_paths)
    assert len(detail_paths) == 4
    # robots.txt 가 금지한 경로·사이트맵의 다른 파일은 요청하지 않는다
    assert not any(p.startswith(("/api/", "/me", "/auth/")) or p.endswith("/apply") for p in behavior.requests)
    assert "/sitemaps/region-searches.xml" not in behavior.requests

    c = client_for(ctx)
    items = {i["title"]: i for i in c.get("/v1/leads", params={"queue": "all"}).json()["items"]}
    assert set(items) == {"재고 엑셀 매크로 만들어주실 분", "카페 홈페이지 제작 도와주실 분", "앱 개발해주실 분 구합니다"}
    macro = items["재고 엑셀 매크로 만들어주실 분"]
    assert macro["recommendation"] == "recommended", macro
    assert macro["source_status"] == "open"
    assert macro["pay"]["unit"] == "project" and macro["pay"]["min"] == 300000
    assert items["카페 홈페이지 제작 도와주실 분"]["recommendation"] == "excluded"  # 매장 출근
    closed = items["앱 개발해주실 분 구합니다"]
    assert closed["source_status"] == "closed" and closed["recommendation"] == "excluded"

    with ctx.db.session() as s:
        rec = s.scalar(select(SourceRecord).where(SourceRecord.source_post_id == "a1b2c3d4e5f6"))
        assert rec is not None and rec.workplace_raw == "서울특별시 마포구 합정동"
        snap = s.scalar(select(Snapshot).where(Snapshot.source_record_id == rec.id))
        assert snap is not None
        # 작성자 정보·상세 주소·원본 연락처는 저장하지 않는다 (마스킹본 본문만)
        assert "합성닉네임" not in snap.body_text and "상세 주소" not in snap.body_text
        assert "010-1234-5678" not in snap.body_text


def test_repeat_run_does_not_refetch_recent_details(tmp_path: Path) -> None:
    ctx, behavior = setup(tmp_path)
    run_discovery(ctx)
    first = len([p for p in behavior.requests if p.startswith("/job-posts/")])
    behavior.requests.clear()
    run2 = run_discovery(ctx)
    again = [p for p in behavior.requests if p.startswith("/job-posts/")]
    # 12시간 안에 확인한 공고는 다시 요청하지 않는다 (파싱 실패 1건만 재시도)
    assert first == 4 and len(again) == 1
    assert run2.counts["created"] == 0
    with ctx.db.session() as s:
        assert len(s.scalars(select(Lead)).all()) == 3


def test_broken_sitemap_is_parse_failure_not_empty_success(tmp_path: Path) -> None:
    ctx, _ = setup(tmp_path, fx.Behavior(sitemap_mode="broken"))
    run = run_discovery(ctx)
    assert run.state != "succeeded"
    assert run.counts["parse_failures"] >= 1


def test_sitemap_403_stops_source(tmp_path: Path) -> None:
    ctx, _ = setup(tmp_path, fx.Behavior(sitemap_mode="403"))
    run = run_discovery(ctx)
    assert run.state == "failed" and run.error_code
    c = client_for(ctx)
    src = next(i for i in c.get("/v1/sources").json()["items"] if i["id"] == SID)
    assert src["health"]["status"] == "blocked"


def test_parse_picks_this_page_post_not_review_post() -> None:
    ad = daangn.create()
    jid = "a1b2c3d4e5f6"
    url = fx.job_url(jid)
    res = FetchResult(url, url, 200, "ok", fx.detail_html(jid, False), {}, 5)
    p = ad.parse(PostRef(url, jid), res, datetime.now(UTC))
    assert p.title == "재고 엑셀 매크로 만들어주실 분"
    assert p.source_status == "open" and p.source_post_id == jid
    assert p.pay is not None and p.pay.raw == "건당 300,000원"
    assert "— 게시판 표시 조건 —" in p.body and "시간: 협의" in p.body
    # 급여 형식·게시판 이름은 본문에 넣지 않는다 (판정 규칙이 고용 신호로 읽음 — 실제 수집에서 확인된 오류)
    assert "시급" not in p.body and "알바" not in p.body
    from worklead.analysis.rules import classify_intent

    assert classify_intent(p.title, p.body).value == "buyer_project"


def test_title_keywords_skip_intent_group_and_slug_titles() -> None:
    kws = title_keywords(DEFAULT_QUERY_GROUPS["groups"])
    assert "구합니다" not in kws and "홈페이지" in kws and "매크로" in kws
    assert title_from_url("https://jobs.daangn.com/job-posts/%EC%95%B1-%EA%B0%9C%EB%B0%9C-tni24p97pfru") == "앱 개발"


def test_sitemap_larger_than_page_limit_is_read(tmp_path: Path) -> None:
    # 실제 사이트맵은 압축 해제 후 7.5MB (2026-09-26) — 일반 페이지 상한(3MB)보다 크다
    behavior = fx.Behavior(sitemap_extra=30000)
    assert len(fx.job_sitemap(30000).encode()) > 3 * 1024 * 1024
    ctx, _ = setup(tmp_path, behavior)
    run = run_discovery(ctx)
    assert run.counts["fetch_failures"] == 0
    assert run.counts["created"] == 3
    assert not any("x000" in p for p in behavior.requests if p.startswith("/job-posts/"))


def test_long_task_keeps_lease_alive(tmp_path: Path, monkeypatch) -> None:
    """상세를 여러 건 처리하는 동안 임대가 만료되어 실행이 '중단된 작업'으로 오인되지 않는다."""
    from worklead.engine import executors

    ctx, _ = setup(tmp_path, lease_seconds=2)
    original = executors._process_ref

    def slow(*args, **kwargs):
        time.sleep(1.2)  # 실제 요청 간격(4초)을 줄여 흉내
        runs.recover_expired(ctx.db)  # 스케줄러의 정리 작업이 도중에 돈다
        return original(*args, **kwargs)

    monkeypatch.setattr(executors, "_process_ref", slow)
    run = run_discovery(ctx)
    assert run.state == "partial", (run.state, run.error_code)  # 구조 변경 상세 1건 때문 (취소 아님)
    assert run.counts["created"] == 3


def test_reanalysis_keeps_structured_pay(tmp_path: Path) -> None:
    """재분석(규칙 변경·설정 변경) 뒤에도 원천의 구조화 급여가 유지된다 — 본문에 금액이 없어도."""
    ctx, _ = setup(tmp_path)
    run_discovery(ctx)
    c = client_for(ctx)
    lead = next(i for i in c.get("/v1/leads", params={"queue": "all"}).json()["items"] if i["title"] == "카페 홈페이지 제작 도와주실 분")
    assert lead["pay"]["unit"] == "hour" and lead["pay"]["min"] == 15000
    assert c.post(f"/v1/leads/{lead['id']}/reanalyze").status_code == 202
    w = Worker(ctx, "interactive", runs.INTERACTIVE_KINDS)
    while w.run_once():
        pass
    again = c.get(f"/v1/leads/{lead['id']}").json()
    assert again["pay"]["unit"] == "hour" and again["pay"]["min"] == 15000
