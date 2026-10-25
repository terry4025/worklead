"""Real loopback transport and one-shot durable submission transitions."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json

import httpx
import pytest
from sqlalchemy import select

from tests.conftest import client_for
from tests.test_job_applications import post as draft_post
from worklead.engine.pipeline import ingest
from worklead.jobs.auto_apply import AutoApplyRuntime, job_identity, runtime
from worklead.jobs.auto_apply_models import AutoApplyStartIn
from worklead.jobs.chrome_bridge import EXTENSION_ORIGIN, runtime as unread_runtime
from worklead.models import JobAutoAttempt, Lead, SourcePolicy, SourceRecord

NOW = datetime(2026, 10, 25, 4, tzinfo=UTC)
ACCOUNT, PROFILE = "a" * 64, "b" * 64


@pytest.fixture
def app(ctx):
    ctx.clock = lambda: NOW
    with ctx.db.session() as s:
        s.get(SourcePolicy, "daangn-alba").status = "allowed"
    yield ctx
    if ctx._jobs_auto_apply_runtime: ctx._jobs_auto_apply_runtime.stop()
    if ctx._jobs_chrome_runtime: ctx._jobs_chrome_runtime.stop()


def add(app, score=50, key="ABCDEFGHIJK1", **changes):
    post = replace(draft_post(), published=replace(draft_post().published, at=NOW - timedelta(hours=1)),
        original_url=f"https://jobs.daangn.com/job-posts/test-{key}",
        canonical_url=f"https://jobs.daangn.com/job-posts/test-{key}", source_post_id=key)
    for key_, value in changes.items(): setattr(post, key_, value)
    with app.db.session() as s:
        id_ = ingest(s, "daangn-alba", post, parser_version="test", now=NOW).lead_id
        row = s.get(Lead, id_)
        row.priority_total, row.recommendation = score, "needs_review"
        return id_


def connect(app, **options):
    bridge = runtime(app)
    setup = bridge.start(AutoApplyStartIn(confirm=True, **options))
    connection = setup["connection"]
    client = httpx.Client(base_url=connection["url"], headers={"Origin": EXTENSION_ORIGIN, "Authorization": "Bearer " + connection["token"]}, timeout=5)
    return bridge, client, connection


def claimed(client):
    result = client.post("/next", json={"account_hash": ACCOUNT})
    assert result.status_code == 200, result.text
    job = result.json()["job"]
    if job is None: return None
    return {"attempt_id": job["attempt_id"], "lease_token": job["lease_token"], "account_hash": ACCOUNT, "profile_hash": PROFILE}


def ready(client, body):
    result = client.post("/review", json={**body, "outcome": "ready", "reason_code": "ready"})
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "ready"
    assert len(result.json()["receipt_token"]) >= 32
    return result.json()["receipt_token"]


def test_50_inclusive_review_candidates_and_visible_success_only(app):
    low, accepted = add(app, 49), add(app, 50, "ABCDEFGHIJK2")
    bridge, client, connection = connect(app)
    try:
        body = claimed(client)
        with app.db.session() as s:
            assert s.get(JobAutoAttempt, body["attempt_id"]).lead_id == accepted
        ready(client, body)
        assert client.post("/permit", json=body).json() == {"permit": True, "expires_in_seconds": 3}
        assert client.post("/permit", json=body).status_code == 409
        assert client.post("/result", json={**body, "outcome": "submitted", "reason_code": "submitted"}).status_code == 422
        result = client.post("/result", json={**body, "outcome": "submitted", "reason_code": "submitted", "evidence": "cancel_button"})
        assert result.json() == {"status": "submitted"}
        assert claimed(client) is None
        status = bridge.status().model_dump(mode="json")
        assert status["today_submitted"] == 1 and status["today_reserved"] == 0
        assert ACCOUNT not in json.dumps(status) and PROFILE not in json.dumps(status) and connection["token"] not in json.dumps(status)
        with app.db.session() as s:
            assert s.get(Lead, accepted).sales_stage == "contacted"
            assert s.get(Lead, low).sales_stage == "new"
    finally: client.close()


@pytest.mark.parametrize("change", ["score", "dismiss", "closed", "permission", "profile"])
def test_permit_rechecks_current_facts(app, change):
    id_ = add(app)
    bridge, client, _ = connect(app)
    try:
        body = claimed(client)
        ready(client, body)
        with app.db.session() as s:
            lead = s.get(Lead, id_)
            if change == "score": lead.priority_total = 49
            if change == "dismiss": lead.user_mark = "dismissed"
            if change == "closed": lead.source_status = "closed"
            if change == "permission": s.get(SourcePolicy, "daangn-alba").status = "restricted"
        if change == "profile": body["profile_hash"] = "c" * 64
        result = client.post("/permit", json=body)
        assert result.status_code == 409 if change == "profile" else result.json()["permit"] is False
        assert bridge.status().today_submitted == 0
    finally: client.close()


def test_uncertain_permit_never_reissued_even_after_restore_erases_row(app):
    add(app)
    bridge, client, first = connect(app)
    body = claimed(client)
    ready(client, body)
    assert client.post("/permit", json=body).json()["permit"]
    client.close()
    bridge.stop()
    assert bridge.status().uncertain == 1
    with app.db.session() as s:
        row = s.get(JobAutoAttempt, body["attempt_id"])
        s.delete(row)
    app.db.gate.restored()
    bridge, client, second = connect(app)
    try:
        assert first["token"] != second["token"]
        assert claimed(client) is None
    finally: client.close()


def test_account_switch_and_restore_pause_before_permit(app):
    add(app)
    bridge, client, _ = connect(app)
    try:
        body = claimed(client)
        ready(client, body)
        assert client.post("/permit", json={**body, "account_hash": "c" * 64}).status_code == 409
        assert bridge.status().state == "paused"
        assert client.post("/permit", json=body).status_code == 409
    finally: client.close()
    bridge, client, _ = connect(app)
    try:
        app.db.gate.restored()
        assert client.post("/next", json={"account_hash": ACCOUNT}).status_code == 409
        assert bridge.status().error_code == "database_restored"
    finally: client.close()


def test_timeout_and_daily_budget_count_uncertain(app):
    add(app)
    add(app, key="ABCDEFGHIJK2")
    bridge, client, _ = connect(app, daily_limit=1)
    try:
        body = claimed(client)
        ready(client, body)
        assert client.post("/permit", json=body).json()["permit"]
        app.clock = lambda: NOW + timedelta(seconds=16)
        assert bridge.status().uncertain == 1
        assert claimed(client) is None
        assert bridge.status().today_reserved == 1
    finally: client.close()


def test_prepermit_timeout_holds_without_contacting(app):
    add(app)
    bridge, client, _ = connect(app)
    try:
        body = claimed(client)
        app.clock = lambda: NOW + timedelta(seconds=91)
        result = client.post("/review", json={**body, "outcome": "ready", "reason_code": "ready"})
        assert result.status_code == 409
        assert bridge.status().held == 1
        assert bridge.status().today_reserved == 0
    finally: client.close()


def test_bridge_boundaries_cross_purpose_and_main_api_auth(app):
    bridge, client, connection = connect(app)
    unread = unread_runtime(app).start()["connection"]
    try:
        assert client.get("/next").status_code == 405
        assert client.post("/observe", json={}).status_code == 404
        assert client.post("/next?x=1", json={}).status_code == 404
        assert client.post("/next", headers={"Origin": "https://jobs.daangn.com"}, json={"account_hash": ACCOUNT}).status_code == 403
        assert client.post("/next", headers={"Authorization": "Bearer " + unread["token"]}, json={"account_hash": ACCOUNT}).status_code == 401
        assert client.post("/next", headers={"Authorization": "Bearer " + app.token}, json={"account_hash": ACCOUNT}).status_code == 401
        assert client.post("/next", json={"account_hash": ACCOUNT, "cookie": "forbidden"}).status_code == 422
        assert client.post("/next", content='{"account_hash":"'+ACCOUNT+'","account_hash":"'+ACCOUNT+'"}', headers={"Content-Type": "application/json"}).status_code == 422
        assert client.post("/next", content="x" * 2049, headers={"Content-Type": "application/json"}).status_code == 413
        preflight = client.options("/next", headers={"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization,content-type"})
        assert preflight.status_code == 204
        c = client_for(app)
        assert c.get("/v1/jobs/auto-apply/status").status_code == 200
        assert c.get("/v1/jobs/auto-apply/status", headers={"Authorization": ""}).status_code == 401
    finally: client.close()


@pytest.mark.parametrize("body", [{"confirm": False}, {"confirm": True, "min_score": -1}, {"confirm": True, "min_score": 101}, {"confirm": True, "daily_limit": 0}, {"confirm": True, "daily_limit": 201}, {"confirm": True, "min_score": True}])
def test_policy_boundaries(app, body):
    assert client_for(app).post("/v1/jobs/auto-apply/start", json=body).status_code == 422


def test_business_registration_conflict_is_held_not_generic_questions(app):
    add(app, body="홈페이지 제작이 필요합니다. 사업자등록증 필수입니다. 세금계산서 발행 요청합니다.")
    bridge, client, _ = connect(app)
    try:
        assert claimed(client) is None
        assert bridge.status().held == 1
        assert bridge.status().attempts[0].reason_code == "condition_conflict"
    finally: client.close()


def test_same_public_job_slug_dedup_and_restart_is_off(app):
    id_ = add(app)
    bridge, client, _ = connect(app)
    body = claimed(client)
    client.post("/review", json={**body, "outcome": "already_applied", "reason_code": "already_applied"})
    with app.db.session() as s:
        record = s.get(SourceRecord, s.get(Lead, id_).primary_record_id)
        original = job_identity(record)
        record.original_url = "https://jobs.daangn.com/job-posts/new-slug-ABCDEFGHIJK1"
        assert job_identity(record) == original
    assert claimed(client) is None
    client.close()
    bridge.stop()
    new_runtime = AutoApplyRuntime(app)
    assert new_runtime.status().enabled is False
    assert new_runtime.status().min_score == 50
    new_runtime.stop()


def test_main_setup_rotates_connection_and_never_exposes_capability_in_status(app):
    c = client_for(app)
    first = c.post("/v1/jobs/auto-apply/start", json={"confirm": True, "min_score": 50, "daily_limit": 20})
    assert first.status_code == 200, first.text
    assert first.headers["cache-control"] == "no-store"
    second = c.post("/v1/jobs/auto-apply/start", json={"confirm": True, "min_score": 50, "daily_limit": 20})
    assert second.status_code == 200, second.text
    assert first.json()["connection"]["token"] != second.json()["connection"]["token"]
    status = c.get("/v1/jobs/auto-apply/status")
    assert status.json()["state"] == "waiting" and status.json()["enabled"]
    assert "token" not in status.text and "connection" not in status.json()
    assert c.post("/v1/jobs/auto-apply/stop").json()["enabled"] is False


def test_failed_commit_after_fsynced_permit_cannot_retry(app, monkeypatch):
    add(app)
    bridge, client, _ = connect(app)
    try:
        body = claimed(client)
        ready(client, body)
        original = bridge._remember_permit
        def write_then_fail(key):
            original(key)
            raise OSError("simulated crash before SQL commit")
        monkeypatch.setattr(bridge, "_remember_permit", write_then_fail)
        assert client.post("/permit", json=body).status_code == 503
        monkeypatch.setattr(bridge, "_remember_permit", original)
        assert client.post("/permit", json=body).json()["permit"] is False
        assert bridge.status().uncertain == 1
        with app.db.session() as s:
            assert s.scalar(select(Lead.sales_stage)) == "new"
    finally: client.close()


def test_source_interval_boundaries_preserve_other_source_minimum(app):
    c = client_for(app)
    assert c.patch("/v1/sources/daangn-alba", json={"interval_minutes": 5}).status_code == 200
    assert c.patch("/v1/sources/daangn-alba", json={"interval_minutes": 4}).status_code == 422
    assert c.patch("/v1/sources/iboss", json={"interval_minutes": 5}).status_code == 422
    assert c.patch("/v1/sources/iboss", json={"interval_minutes": 15}).status_code == 200


def test_prior_backup_migrates_preserving_user_data_and_requires_new_journal_table(tmp_path):
    from alembic import command
    from worklead import storage
    import sqlite3
    path = tmp_path / "prior.sqlite3"
    command.upgrade(storage.alembic_config(path), "0007_discovery_listings")
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO app_settings(key,value,updated_at) VALUES ('preserved','{\"value\":42}','2026-10-24')")
    assert storage.validate_database(path) == ("0007_discovery_listings", 0)
    storage.migrate(path, tmp_path / "backups")
    assert storage.validate_database(path) == ("0008_job_auto_attempts", 0)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT value FROM app_settings WHERE key='preserved'").fetchone()[0] == '{"value":42}'
        connection.execute("DROP TABLE job_auto_attempts")
    with pytest.raises(ValueError, match="필수 테이블"):
        storage.validate_database(path)
    assert list((tmp_path / "backups").glob("*.sqlite3"))


def test_already_applied_after_bound_intro_does_not_require_opening_intro(app):
    add(app)
    add(app, key="ABCDEFGHIJK2")
    bridge, client, _ = connect(app)
    try:
        first = claimed(client)
        ready(client, first)
        # This candidate never receives a final permit, so end its pre-submit lease.
        app.clock = lambda: NOW + timedelta(seconds=91)
        bridge.status()
        body = claimed(client)
        result = client.post("/review", json={**body, "profile_hash": "0" * 64,
            "outcome": "already_applied", "reason_code": "already_applied"})
        assert result.json() == {"status": "already_applied"}
        assert bridge.status().today_reserved == 0
    finally: client.close()
