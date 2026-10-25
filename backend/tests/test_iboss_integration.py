"""Installed adapter contract: public full body -> strict rules -> searchable local lead."""
from datetime import UTC, datetime
from pathlib import Path
import re
import sqlite3

from alembic import command
import httpx
from sqlalchemy import select

from tests.conftest import client_for, make_ctx
from worklead.engine import runs
from worklead.engine.worker import Worker
from worklead.models import CollectionTask, SourcePolicy
from worklead import storage

FIXTURES = Path(__file__).parent / "fixtures" / "iboss"


def test_iboss_default_registration_fulltext_filter_and_dedup(tmp_path, monkeypatch):
    ctx = make_ctx(tmp_path)
    ctx.clock = lambda: datetime(2026, 9, 30, 12, tzinfo=UTC)
    monkeypatch.setattr("worklead.services.leads.utcnow", lambda: ctx.clock())
    c = client_for(ctx)
    source = next(item for item in c.get("/v1/sources").json()["items"] if item["id"] == "iboss")
    assert source["policy"]["status"] == "permission_pending"
    assert source["coverage"]["region_list_status"] == "not_applicable"
    assert source["coverage"]["depth_limit"] == 0
    assert c.post("/v1/runs", json={"source_id": "iboss", "kind": "discovery"}).status_code == 409
    listing = (FIXTURES / "list.html").read_text(encoding="utf-8")
    listing = re.sub(r'<div class="ABA-list-paging.*?</div>', '<div class="ABA-list-paging"><strong>1</strong></div>', listing)
    detail = (FIXTURES / "detail.html").read_text(encoding="utf-8")
    detail = detail.replace("주문 관리 기능 개발 의뢰", "도와주실 분 찾습니다")
    detail = detail.replace("서버와 DB 연동", "서버와 DB 연동 및 특별문서 자동화")
    marketing = detail.replace("도와주실 분 찾습니다", "쇼핑몰 광고 소재 제작 의뢰")
    marketing = re.sub(r'<div class="ABA-view-body.*?</div>', '<div class="ABA-view-body">쇼핑몰 광고 소재 이미지를 제작해 주실 분을 찾습니다. 온라인으로 진행합니다.</div>', marketing)
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if request.url.path == "/ab-1957":
            return httpx.Response(200, text=listing)
        return httpx.Response(200, text=detail if request.url.path.endswith("900001") else marketing)

    ctx.transport = httpx.MockTransport(handler)
    assert c.patch("/v1/sources/iboss", json={"policy": {"status": "restricted", "basis": "합성 통합 검증"}}).status_code == 200
    for index in range(2):
        job = c.post("/v1/runs", json={"source_id": "iboss", "kind": "discovery"}).json()
        assert Worker(ctx, "iboss-test", runs.COLLECTION_KINDS).run_once()
        result = c.get("/v1/runs/" + job["job_id"]).json()
        assert result["state"] == "succeeded", result
        assert result["counts"]["created"] == (2 if index == 0 else 0)
    assert seen.count("/ab-1958-900001") == seen.count("/ab-1958-900002") == 1
    rows = c.get("/v1/leads", params={"source_id": "iboss", "queue": "all"}).json()["items"]
    assert len(rows) == 2
    by_title = {r["title"]: r for r in rows}
    assert by_title["쇼핑몰 광고 소재 제작 의뢰"]["recommendation"] == "excluded"
    real = c.get("/v1/leads/" + by_title["도와주실 분 찾습니다"]["id"]).json()
    assert real["recommendation"] != "excluded"
    assert "synthetic-contact@example.test" not in str(real)
    assert "월급 900만원" not in str(real)
    found = c.get("/v1/leads", params={"source_id": "iboss", "keyword": "특별문서", "queue": "all"}).json()["items"]
    assert len(found) == 1 and found[0]["id"] == real["id"]
    ctx.clock = lambda: datetime(2026, 9, 30, 15, tzinfo=UTC)  # KST next day, without another fetch
    expired = c.get("/v1/leads/" + real["id"]).json()
    assert expired["recommendation"] == "excluded" and expired["source_status"] == "closed"
    assert seen.count("/ab-1958-900001") == 1
    ctx.db.dispose()


def test_existing_task_and_permission_survive_checkpoint_migration(tmp_path):
    ctx = make_ctx(tmp_path)
    c = client_for(ctx)
    c.patch("/v1/sources/iboss", json={"policy": {"status": "restricted", "basis": "이전 권한 보존"}})
    with ctx.db.session() as s:
        run, _ = runs.enqueue(s, "discovery", source_id="iboss")
        s.add(CollectionTask(id="task-migrate", run_id=run.id, source_id="iboss", task_key="migration",
                             query_group="all", region_scope="public", scan_cycle=1, state="pending"))
    ctx.db.dispose()
    command.downgrade(storage.alembic_config(ctx.config.db_path), "0004_relation_decisions")
    assert storage.validate_database(ctx.config.db_path) == ("0004_relation_decisions", 0)
    storage.migrate(ctx.config.db_path, ctx.config.backup_dir)
    with ctx.db.session() as s:
        assert s.get(CollectionTask, "task-migrate").checkpoint == {}
        assert s.get(SourcePolicy, "iboss").basis == "이전 권한 보존"
    assert storage.validate_database(ctx.config.db_path) == ("0008_job_auto_attempts", 0)
    with sqlite3.connect(ctx.config.db_path) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    ctx.db.dispose()
