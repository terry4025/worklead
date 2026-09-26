"""API 계약·보안·사용자 데이터 보존 검증. 요구 검증 사례 10, 13, 14 포함."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest
from sqlalchemy import func, select

from tests.conftest import TOKEN, client_for, make_ctx
from worklead.analysis.provider import AIFailure, AIOutput
from worklead.engine import runs
from worklead.engine.pipeline import ingest
from worklead.engine.worker import Worker
from worklead.models import AnalysisResult, Lead, SourceRecord
from worklead.services.demo import seed
from worklead.sources.base import ParsedPost
from worklead.storage import backup_database, current_revision, head_revision, integrity_ok, restore_database


@pytest.fixture
def demo(tmp_path: Path):
    ctx = make_ctx(tmp_path, demo=True)
    seed(ctx)
    yield ctx, client_for(ctx)
    ctx.db.dispose()


def drain(ctx) -> None:
    w = Worker(ctx, "interactive", runs.INTERACTIVE_KINDS)
    while w.run_once():
        pass


# ── 보안 ────────────────────────────────────────────────────────────
def test_auth_host_origin(demo) -> None:
    ctx, c = demo
    assert c.get("/v1/health", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert c.get("/v1/health", headers={"Authorization": ""}).status_code == 401
    r = c.get("/v1/health", headers={"Host": "evil.example"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_host"
    r = c.get("/v1/health", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    ok = c.get("/v1/health", headers={"Origin": "http://tauri.localhost"})
    assert ok.status_code == 200 and ok.headers["access-control-allow-origin"] == "http://tauri.localhost"
    err = c.get("/v1/leads/nope").json()["error"]
    assert set(err) == {"code", "message", "details", "request_id", "retryable"}


# ── 목록·커서 ───────────────────────────────────────────────────────
def test_cursor_pagination_is_stable(demo) -> None:
    _, c = demo
    seen: list[str] = []
    cursor = None
    while True:
        params = {"queue": "all", "limit": 4, "sort": "priority"}
        if cursor:
            params["cursor"] = cursor
        page = c.get("/v1/leads", params=params).json()
        seen.extend(i["id"] for i in page["items"])
        cursor = page["next_cursor"]
        assert page["has_more"] == (cursor is not None)
        if not cursor:
            break
    total = c.get("/v1/leads/queue-counts").json()["counts"]["all"]
    assert len(seen) == len(set(seen)) == total
    # 필터를 바꾸면 기존 커서는 거부
    first = c.get("/v1/leads", params={"limit": 2}).json()["next_cursor"]
    r = c.get("/v1/leads", params={"limit": 2, "cursor": first, "remote": "confirmed"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_cursor"


def test_filters(demo) -> None:
    _, c = demo
    items = c.get("/v1/leads", params={"remote": "confirmed", "limit": 100}).json()["items"]
    assert items and all(i["work_mode"]["value"] == "fully_remote" and i["work_mode"]["basis"] in ("explicit", "user_confirmed") for i in items)
    closed = c.get("/v1/leads", params={"recruit": "closed"}).json()["items"]
    assert closed and all(i["source_status"] in ("closed", "deleted") for i in closed)
    access = c.get("/v1/leads", params={"recruit": "access_issue"}).json()["items"]
    assert [i["access_status"] for i in access] == ["blocked"]
    assert access[0]["source_status"] == "open", "접근 차단은 마감으로 바뀌지 않는다"
    sellers = c.get("/v1/leads", params={"intent": "seller"}).json()["items"]
    assert len(sellers) == 1
    kw = c.get("/v1/leads", params={"keyword": "VBA"}).json()["items"]
    assert kw and any("vba" in i["title"].lower() for i in kw)


# ── 사례 13·14: 사용자 데이터 보존, won ≠ 수금 ───────────────────────
def test_user_state_survives_reanalysis_and_recollection(demo) -> None:
    ctx, c = demo
    lead = c.get("/v1/leads", params={"queue": "recommended"}).json()["items"][0]
    lid = lead["id"]
    c.patch(f"/v1/leads/{lid}", json={"memo": "내 메모", "sales_stage": "reviewing", "user_mark": "interested"})
    assert c.post(f"/v1/leads/{lid}/reanalyze").status_code == 202
    drain(ctx)
    # 같은 공고가 내용이 바뀐 채 재수집
    with ctx.db.session() as s:
        rec = s.scalar(select(SourceRecord).where(SourceRecord.lead_id == lid))
        p = ParsedPost(title=rec.title, body=(rec.body_text or "") + "\n추가: 일정은 협의 가능합니다.", original_url=None, canonical_url=None, source_post_id=rec.source_post_id)
        r = ingest(s, rec.source_id, p, parser_version="demo", now=rec.first_seen_at, provider=ctx.provider)
        assert r.kind == "updated" and r.lead_id == lid
    d = c.get(f"/v1/leads/{lid}").json()
    assert d["memo"] == "내 메모" and d["sales_stage"] == "reviewing" and d["user_mark"] == "interested"


def test_won_does_not_increase_collected(demo) -> None:
    _, c = demo
    before = c.get("/v1/metrics").json()["sales"]
    lid = c.get("/v1/leads", params={"queue": "recommended"}).json()["items"][0]["id"]
    c.patch(f"/v1/leads/{lid}", json={"sales_stage": "won"})
    after_won = c.get("/v1/metrics").json()["sales"]
    assert after_won["collected"] == before["collected"]
    assert after_won["won"] == before["won"] + 1
    r = c.post(f"/v1/leads/{lid}/outcomes", json={"kind": "payment_received", "amount": 200000, "occurred_on": "2026-09-25"})
    assert r.status_code == 200
    assert c.get("/v1/metrics").json()["sales"]["collected"] == before["collected"] + 200000
    bad = c.post(f"/v1/leads/{lid}/outcomes", json={"kind": "refund", "amount": -5, "occurred_on": "2026-09-25"})
    assert bad.status_code == 422


def test_feedback_is_separate_from_auto(demo) -> None:
    _, c = demo
    lead = next(i for i in c.get("/v1/leads", params={"queue": "needs_review", "limit": 50}).json()["items"] if i["work_mode"]["value"] == "unknown" and i["demand_intent"]["value"] == "buyer_project" and i["source_status"] == "open")
    d = c.post(f"/v1/leads/{lead['id']}/feedback", json={"kind": "remote", "value": "confirmed"}).json()
    assert d["feedback"]["remote"] == "confirmed"
    assert d["work_mode"]["basis"] == "user_confirmed"
    assert any(e["basis"] == "user_confirmed" for e in d["evidence"]["work_mode"])
    d2 = c.post(f"/v1/leads/{lead['id']}/feedback", json={"kind": "remote", "value": "clear"}).json()
    assert d2["feedback"]["remote"] is None and d2["work_mode"]["value"] == "unknown"
    r = c.post(f"/v1/leads/{lead['id']}/feedback", json={"kind": "remote", "value": "yes"})
    assert r.status_code == 422


# ── 수동 입력·내보내기 ──────────────────────────────────────────────
def test_manual_import_and_recheck_unsupported(demo) -> None:
    ctx, c = demo
    text = "랜딩페이지 만들어주실 분\n이벤트 랜딩페이지 만들어주실 분 구합니다. 전 과정 비대면. 예산 70만원."
    r = c.post("/v1/imports", json={"format": "text", "content": text})
    assert r.status_code == 202
    drain(ctx)
    run = c.get(f"/v1/runs/{r.json()['job_id']}").json()
    assert run["state"] == "succeeded", run
    assert "content" not in json.dumps(run["result"])
    lid = run["result"]["lead_ids"][0]
    lead = c.get(f"/v1/leads/{lid}").json()
    assert lead["source_id"] == "manual" and lead["source_status"] == "unknown"
    assert lead["recommendation"] == "needs_review"
    r = c.post(f"/v1/leads/{lid}/recheck")
    assert r.status_code == 409 and r.json()["error"]["code"] == "unsupported"
    # 같은 글 다시 입력 → 중복 리드 없음
    c.post("/v1/imports", json={"format": "text", "content": text})
    drain(ctx)
    assert sum(1 for i in c.get("/v1/leads", params={"source_id": "manual"}).json()["items"]) == 1
    bad = c.post("/v1/imports", json={"format": "json", "content": "{not json"})
    drain(ctx)
    assert c.get(f"/v1/runs/{bad.json()['job_id']}").json()["state"] == "failed"


def test_export_csv_formula_injection(demo) -> None:
    ctx, c = demo
    lid = c.get("/v1/leads").json()["items"][0]["id"]
    c.patch(f"/v1/leads/{lid}", json={"memo": '=HYPERLINK("http://evil")'})
    job = c.post("/v1/exports", json={"kind": "leads_csv"}).json()["job_id"]
    drain(ctx)
    run = c.get(f"/v1/runs/{job}").json()
    path = ctx.config.export_dir / run["result"]["file_name"]
    rows = list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8").lstrip("﻿"))))
    memo = next(r["memo"] for r in rows if r["id"] == lid)
    assert memo.startswith("'=")


# ── 사례 10: AI 장애 ────────────────────────────────────────────────
class FlakyProvider:
    engine = "fake"
    model = "fake-1"

    def __init__(self) -> None:
        self.calls = 0
        self.fail = True

    def available(self) -> bool:
        return True

    def analyze(self, data):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.fail:
            raise AIFailure("timeout", "제공자 응답 시간 초과")
        return AIOutput(summary="AI 요약", deliverables=["랜딩페이지"], cost_krw=None)


def test_ai_failure_keeps_rules_and_does_not_retry_same_input(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    prov = FlakyProvider()
    ctx.provider = prov
    c = client_for(ctx)
    assert c.patch("/v1/settings", json={"ai": {"enabled": True}}).status_code == 422, "고지 동의 없이는 켤 수 없다"
    assert c.patch("/v1/settings", json={"ai": {"enabled": True, "external_transfer_consent": True}}).status_code == 200
    c.post("/v1/imports", json={"format": "text", "content": "랜딩페이지 만들어주실 분\n랜딩페이지 만들어주실 분 구합니다. 전 과정 비대면. 예산 70만원."})
    drain(ctx)
    lid = c.get("/v1/leads").json()["items"][0]["id"]
    d = c.get(f"/v1/leads/{lid}").json()
    assert d["analysis_status"] == "failed"
    assert d["analysis"]["failure"]["code"] == "timeout"
    assert d["recommendation"] in ("recommended", "needs_review") and d["evidence"], "규칙 결과·근거는 조회 가능"
    assert d["body_text"]
    calls = prov.calls
    # 같은 입력으로 자동 재요청하지 않는다 (피드백 등 규칙 재계산)
    c.patch(f"/v1/leads/{lid}", json={"memo": "x"})
    with ctx.db.session() as s:
        lead = s.get(Lead, lid)
        rec = s.get(SourceRecord, lead.primary_record_id)
        from worklead.engine.pipeline import apply_analysis

        apply_analysis(s, lead, rec, now=rec.first_seen_at, provider=prov, source_kind="manual")
    assert prov.calls == calls
    # 사용자가 재분석을 요청하면 한 번 다시 시도
    prov.fail = False
    c.post(f"/v1/leads/{lid}/reanalyze")
    drain(ctx)
    d = c.get(f"/v1/leads/{lid}").json()
    assert d["analysis_status"] == "complete" and d["analysis"]["summary"] == "AI 요약"
    assert prov.calls == calls + 1
    with ctx.db.session() as s:
        assert s.scalar(select(func.count()).select_from(AnalysisResult).where(AnalysisResult.lead_id == lid)) >= 2


# ── 설정 ────────────────────────────────────────────────────────────
def test_profile_change_queues_reanalysis(demo) -> None:
    ctx, c = demo
    s0 = c.get("/v1/settings").json()
    prof = {**s0["profile"], "onsite": "first_meeting", "target_hourly": 50000}
    r = c.patch("/v1/settings", json={"profile": prof})
    assert r.status_code == 200 and r.json()["profile"]["target_hourly"] == 50000
    kinds = [x["kind"] for x in c.get("/v1/runs").json()["items"]]
    assert "reanalyze_all" in kinds
    assert "key" not in json.dumps(r.json()["ai"]).replace("key_configured", "")


# ── 이벤트 스트림 ───────────────────────────────────────────────────
def test_sse_resume_and_reset(demo) -> None:
    ctx, c = demo
    head = c.get("/v1/bootstrap").json()["event_seq"]
    assert head > 0
    from worklead.engine.events import EventHub

    # 스트림은 무한이므로 제너레이터를 직접 소비한다
    import asyncio

    from worklead.api import app as app_mod  # noqa: F401

    async def first_events(after: int, n: int) -> list[bytes]:
        from starlette.requests import Request

        route = next(r for r in c.app.routes if getattr(r, "path", "") == "/v1/events")
        scope = {"type": "http", "method": "GET", "path": "/v1/events", "headers": [], "query_string": b"", "app": c.app}

        async def receive():  # type: ignore[no-untyped-def]
            await asyncio.sleep(3600)
            return {"type": "http.disconnect"}

        req = Request(scope, receive)
        resp = await route.endpoint(req, after_seq=after)  # type: ignore[attr-defined]
        out: list[bytes] = []
        async for chunk in resp.body_iterator:
            if chunk.startswith(b"id:"):
                out.append(chunk)
            if len(out) >= n:
                break
        return out

    got = asyncio.run(first_events(head - 3, 3))
    seqs = [int(x.split(b"\n", 1)[0].split(b":")[1]) for x in got]
    assert seqs == [head - 2, head - 1, head]
    with ctx.db.session() as s:
        from worklead.services.settings import put_setting

        put_setting(s, "events_trimmed_through", head - 1)
    reset = asyncio.run(first_events(1, 1))
    assert b"stream.reset" in reset[0]
    assert isinstance(EventHub(), EventHub)


# ── 백업·복원 ───────────────────────────────────────────────────────
def test_backup_restore_roundtrip(demo, tmp_path: Path) -> None:
    ctx, c = demo
    n = c.get("/v1/leads/queue-counts").json()["counts"]["all"]
    backup = backup_database(ctx.config.db_path, ctx.config.backup_dir, "test")
    assert integrity_ok(backup) and current_revision(backup) == head_revision()
    lid = c.get("/v1/leads").json()["items"][0]["id"]
    c.patch(f"/v1/leads/{lid}", json={"memo": "백업 이후 변경"})
    ctx.db.dispose()
    safety = restore_database(backup, ctx.config.db_path, ctx.config.backup_dir)
    assert safety.exists()
    c2 = client_for(ctx)
    assert c2.get("/v1/leads/queue-counts").json()["counts"]["all"] == n
    assert c2.get(f"/v1/leads/{lid}").json()["memo"] != "백업 이후 변경"
    bad = tmp_path / "bad.sqlite3"
    bad.write_bytes(b"not a database")
    with pytest.raises(Exception):
        restore_database(bad, ctx.config.db_path, ctx.config.backup_dir)
    assert TOKEN



def test_quick_message_uses_profile_and_never_invents(demo) -> None:
    _, c = demo
    lead_id = c.get("/v1/leads", params={"queue": "all"}).json()["items"][0]["id"]
    msg = c.get(f"/v1/leads/{lead_id}").json()["quick_message"]
    assert "설정 > 프로필" in msg  # 비어 있으면 채울 자리로 남긴다 (소개·링크를 지어내지 않음)
    cur = c.get("/v1/settings").json()["profile"]
    r = c.patch("/v1/settings", json={"profile": {**cur, "intro": "엑셀 자동화·홈페이지 만드는 DCORE LAB입니다.", "portfolio_url": "https://kmong.com/@DCORELAB"}})
    assert r.status_code == 200, r.text
    msg = c.get(f"/v1/leads/{lead_id}").json()["quick_message"]
    assert "DCORE LAB" in msg and "https://kmong.com/@DCORELAB" in msg
    bad = c.patch("/v1/settings", json={"profile": {**cur, "portfolio_url": "javascript:alert(1)"}})
    assert bad.status_code == 422
