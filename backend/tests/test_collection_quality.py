from datetime import timedelta

import httpx
from sqlalchemy import select

from tests.conftest import client_for, make_ctx
from tests.test_daangn import INDEX, JOBS, NOW, URL, changed, markup
from worklead.engine import runs
from worklead.engine.worker import Worker
from worklead.models import DetailFetchState, Lead, SourceRecord
from worklead.sources.daangn import create


def setup(tmp_path, monkeypatch):
    adapter = create()
    ctx = make_ctx(tmp_path, adapters={adapter.source_id: adapter})
    clock = [NOW]
    ctx.clock = lambda: clock[0]
    monkeypatch.setattr(runs, "utcnow", ctx.clock)
    state = {"jobs": JOBS, "detail": markup("detail"), "status": 200, "fetches": 0}

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if request.url.path == "/sitemap.xml":
            return httpx.Response(200, text=INDEX)
        if request.url.path.startswith("/sitemaps/"):
            return httpx.Response(200, text=state["jobs"])
        state["fetches"] += 1
        return httpx.Response(state["status"], text=state["detail"])

    ctx.transport = httpx.MockTransport(handler)
    c = client_for(ctx)
    assert c.patch("/v1/sources/daangn-alba", json={"policy": {"status": "restricted", "basis": "합성 검증"}}).status_code == 200
    c.patch("/v1/settings", json={"query_groups": [{"id": "dev", "label": "개발", "enabled": True, "keywords": ["프로그램"]}]})
    return ctx, c, clock, state


def execute(ctx, c, kind="discovery", **extra):
    response = c.post(f"/v1/leads/{extra['lead_id']}/recheck") if kind == "recheck_lead" else c.post("/v1/runs", json={"kind": kind, "source_id": "daangn-alba", **extra})
    assert response.status_code == 202, response.json()
    run_id = response.json()["run_id"]
    assert Worker(ctx, "quality-test", (kind,)).run_once()
    return c.get("/v1/runs/" + run_id).json()


def test_modified_timestamp_refreshes_cached_post_and_preserves_user_fields(tmp_path, monkeypatch):
    ctx, c, clock, state = setup(tmp_path, monkeypatch)
    try:
        execute(ctx, c)
        with ctx.db.session() as s:
            lead = s.scalar(select(Lead))
            lead.memo, lead.user_mark, lead.sales_stage, lead.draft_text = "유지", "interested", "contacted", "초안"
        clock[0] += timedelta(hours=1)
        state["jobs"] = JOBS.replace("2026-09-28T00:00:00Z", "2026-09-28T01:00:00Z")
        state["detail"] = changed(state["detail"], lambda data: data["job"].update(closed=True))
        run = execute(ctx, c)
        assert state["fetches"] == 2
        assert run["counts"]["updated"] == 1
        assert run["result"]["detail_progress"] == {"done": 1, "total": 1, "title": "프로그램 제작"}
        with ctx.db.session() as s:
            lead = s.scalar(select(Lead))
            assert lead.source_status == "closed"
            assert (lead.memo, lead.user_mark, lead.sales_stage, lead.draft_text) == ("유지", "interested", "contacted", "초안")
        execute(ctx, c)
        assert state["fetches"] == 2
    finally:
        ctx.db.dispose()


def test_title_url_change_refreshes_without_lastmod_change(tmp_path, monkeypatch):
    ctx, c, clock, state = setup(tmp_path, monkeypatch)
    try:
        execute(ctx, c)
        clock[0] += timedelta(hours=1)
        state["jobs"] = JOBS.replace("프로그램-제작-fixture", "프로그램-수정-fixture")
        run = execute(ctx, c)
        assert state["fetches"] == 2
        assert run["counts"]["details_fetched"] == 1
    finally:
        ctx.db.dispose()


def test_http_404_is_recorded_and_deferred_then_retried(tmp_path, monkeypatch):
    ctx, c, clock, state = setup(tmp_path, monkeypatch)
    try:
        state["status"] = 404
        first = execute(ctx, c)
        assert first["state"] == "partial"
        issue = first["result"]["detail_issues"][0]
        assert issue["url"] == URL and issue["code"] == "http_404" and issue["http_status"] == 404
        assert issue["retry_at"]
        second = execute(ctx, c)
        assert state["fetches"] == 1
        assert second["counts"]["fetch_failures"] == 0
        assert second["result"]["deferred_details"] == 1
        assert "유예" in second["note"]
        clock[0] += timedelta(hours=24, seconds=1)
        state["status"] = 200
        third = execute(ctx, c)
        assert state["fetches"] == 2 and third["counts"]["created"] == 1
        with ctx.db.session() as s:
            remembered = s.scalar(select(DetailFetchState))
            assert remembered.retry_at is None and remembered.failures == 0
    finally:
        ctx.db.dispose()


def test_modified_listing_bypasses_error_defer(tmp_path, monkeypatch):
    ctx, c, clock, state = setup(tmp_path, monkeypatch)
    try:
        state["status"] = 404
        execute(ctx, c)
        clock[0] += timedelta(hours=1)
        state["status"] = 200
        state["jobs"] = JOBS.replace("2026-09-28T00:00:00Z", "2026-09-28T01:00:00Z")
        assert execute(ctx, c)["counts"]["created"] == 1
        assert state["fetches"] == 2
    finally:
        ctx.db.dispose()


def test_recheck_collection_exclusion_is_skip_and_manual_recheck_can_retry(tmp_path, monkeypatch):
    ctx, c, clock, state = setup(tmp_path, monkeypatch)
    try:
        execute(ctx, c)
        lead_id = c.get("/v1/leads", params={"queue": "all"}).json()["items"][0]["id"]
        state["detail"] = changed(state["detail"], lambda data: data["user"].update(webCrawlNotAllowed=True))
        run = execute(ctx, c, "recheck_lead", lead_id=lead_id)
        assert run["state"] == "succeeded"
        assert run["counts"]["skipped"] == 1 and run["counts"]["parse_failures"] == 0
        assert run["result"]["detail_issues"][0]["code"] == "collection_disallowed"
        with ctx.db.session() as s:
            rec = s.scalar(select(SourceRecord))
            assert rec.access_status == "blocked" and rec.source_status == "open"
            assert s.scalar(select(Lead)).recommendation == "excluded"
        execute(ctx, c)
        assert state["fetches"] == 2  # The next automatic scan defers it.
        state["detail"] = markup("detail")
        run = execute(ctx, c, "recheck_lead", lead_id=lead_id)
        assert state["fetches"] == 3  # Explicit per-lead retry bypasses defer.
        assert run["counts"]["details_fetched"] == 1
        with ctx.db.session() as s:
            assert s.scalar(select(SourceRecord)).access_status == "accessible"
    finally:
        ctx.db.dispose()


def test_one_bad_sitemap_address_keeps_valid_candidates_and_reports_partial(tmp_path, monkeypatch):
    ctx, c, clock, state = setup(tmp_path, monkeypatch)
    try:
        state["jobs"] = JOBS.replace("</urlset>", "<url><loc>https://other.example/bad</loc></url></urlset>")
        run = execute(ctx, c)
        assert run["counts"]["created"] == 1
        assert run["state"] == "partial" and run["counts"]["parse_failures"] == 1
        assert run["result"]["discovery"]["invalid_urls"] == 2  # Same malformed entry in two maps.
    finally:
        ctx.db.dispose()


def test_unchanged_excluded_post_refreshes_after_48_hours(tmp_path, monkeypatch):
    ctx, c, clock, state = setup(tmp_path, monkeypatch)
    try:
        state["detail"] = changed(state["detail"], lambda data: data["job"].update(closed=True))
        execute(ctx, c)
        clock[0] += timedelta(hours=13)
        assert execute(ctx, c)["counts"]["duplicates"] == 1
        assert state["fetches"] == 1
        clock[0] += timedelta(hours=36)
        assert execute(ctx, c)["counts"]["details_fetched"] == 1
        assert state["fetches"] == 2
    finally:
        ctx.db.dispose()


def test_collection_exclusion_is_persisted_without_any_lead_or_body(tmp_path, monkeypatch):
    ctx, c, clock, state = setup(tmp_path, monkeypatch)
    try:
        state["detail"] = changed(state["detail"], lambda data: data["user"].pop("webCrawlNotAllowed"))
        first = execute(ctx, c)
        assert first["counts"]["skipped"] == 1 and first["state"] == "succeeded"
        execute(ctx, c)
        assert state["fetches"] == 1
        with ctx.db.session() as s:
            assert s.scalar(select(Lead)) is None and s.scalar(select(SourceRecord)) is None
            assert s.scalar(select(DetailFetchState)).outcome == "collection_permission_unknown"
    finally:
        ctx.db.dispose()


def test_upgrade_from_previous_schema_preserves_existing_leads(tmp_path, monkeypatch):
    from alembic import command
    from sqlalchemy import inspect
    from worklead.storage import alembic_config, current_revision, head_revision, integrity_ok, migrate

    ctx, c, clock, state = setup(tmp_path, monkeypatch)
    execute(ctx, c)
    with ctx.db.session() as s:
        lead = s.scalar(select(Lead))
        lead_id = lead.id
        lead.memo = "마이그레이션 보존"
    db_path = ctx.db.path
    ctx.db.dispose()
    command.downgrade(alembic_config(db_path), "0001")
    assert current_revision(db_path) == "0001"
    migrate(db_path, tmp_path / "migration-backup")
    assert current_revision(db_path) == head_revision() and integrity_ok(db_path)
    from worklead.db import Database
    db = Database(db_path)
    try:
        schema = inspect(db.engine)
        assert {"source_id", "ref_key", "url", "title_hint", "source_updated_at", "outcome", "attempted_at", "retry_at", "failures"} <= {column["name"] for column in schema.get_columns("detail_fetch_states")}
        native_index = next(index for index in schema.get_indexes("event_outbox") if index["name"] == "ix_events_native_pending")
        assert native_index["column_names"] == ["type", "notify", "notified_at", "created_at", "seq"]
        with db.session() as s:
            assert s.get(Lead, lead_id).memo == "마이그레이션 보존"
            assert s.scalar(select(DetailFetchState)) is None
    finally:
        db.dispose()
    assert list((tmp_path / "migration-backup").glob("*.sqlite3"))


def test_failed_ingest_does_not_acknowledge_a_listing_change(tmp_path, monkeypatch):
    from worklead.engine import executors

    ctx, c, clock, state = setup(tmp_path, monkeypatch)
    try:
        execute(ctx, c)
        clock[0] += timedelta(hours=1)
        state["jobs"] = JOBS.replace("2026-09-28T00:00:00Z", "2026-09-28T01:00:00Z")
        original = executors.ingest

        def fail_ingest(*args, **kwargs):
            raise RuntimeError("합성 저장 실패")

        monkeypatch.setattr(executors, "ingest", fail_ingest)
        assert execute(ctx, c)["state"] == "failed"
        with ctx.db.session() as s:
            assert s.scalar(select(DetailFetchState)).source_updated_at == NOW
        monkeypatch.setattr(executors, "ingest", original)
        assert execute(ctx, c)["counts"]["details_fetched"] == 1
        assert state["fetches"] == 3
    finally:
        ctx.db.dispose()


def test_deferred_recheck_candidates_do_not_starve_eligible_listing(tmp_path, monkeypatch):
    from dataclasses import replace
    from tests.test_daangn import response
    from worklead.engine.executors import _recheck_targets, _remember_detail
    from worklead.engine.pipeline import ingest
    from worklead.models import Run
    from worklead.sources.base import PostRef

    ctx, c, clock, state = setup(tmp_path, monkeypatch)
    try:
        template = create().parse(PostRef(URL), response(markup("detail")), NOW)
        refs, rec_ids = [], []
        for i in range(41):
            post_id = f"fixture{i:05d}"
            url = URL.replace("fixture00001", post_id)
            refs.append(PostRef(url, post_id))
            with ctx.db.session() as s:
                result = ingest(s, "daangn-alba", replace(template, original_url=url, canonical_url=url, source_post_id=post_id), parser_version="test", now=NOW + timedelta(seconds=i))
                rec_ids.append(result.record_id)
        clock[0] += timedelta(hours=13)
        for ref in refs[:40]:
            _remember_detail(ctx, "daangn-alba", ref, "http_404")
        targets = _recheck_targets(ctx, Run(kind="recheck_recent", source_id="daangn-alba"))
        assert targets == [rec_ids[40]]
    finally:
        ctx.db.dispose()


def test_recheck_rejects_another_listing_without_refreshing_original(tmp_path, monkeypatch):
    from tests.test_daangn import response
    from worklead.sources.base import PostRef, Revalidation

    ctx, c, clock, state = setup(tmp_path, monkeypatch)
    try:
        execute(ctx, c)
        with ctx.db.session() as s:
            lead_id = s.scalar(select(Lead.id))
            checked_at = s.scalar(select(SourceRecord.last_checked_at))
        clock[0] += timedelta(hours=1)
        another = create().parse(PostRef(URL), response(markup("detail")), NOW)
        another.source_post_id = "fixture00002"
        monkeypatch.setattr(ctx.adapters["daangn-alba"], "revalidate", lambda *args: Revalidation("open", "accessible", parsed=another))
        run = execute(ctx, c, "recheck_lead", lead_id=lead_id)
        assert run["state"] == "failed" and run["counts"]["parse_failures"] == 1
        assert "공고 ID" in run["result"]["detail_issues"][0]["message"]
        with ctx.db.session() as s:
            assert len(list(s.scalars(select(Lead)))) == 1
            rec = s.scalar(select(SourceRecord))
            assert rec.last_checked_at == checked_at and rec.source_status == "open"
    finally:
        ctx.db.dispose()
