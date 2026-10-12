"""Native notification privacy, claim durability, quiet hours and recovery."""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import threading
import re
import json
from pathlib import Path

import pytest
from sqlalchemy import event, select
from alembic import command

from tests.conftest import client_for, make_ctx
from worklead.db import MaintenanceBusy
from worklead.api import schemas as S
from worklead.engine.events import emit, notify
from worklead.models import EventOutbox
from worklead.services import native_notifications as native
from worklead.services.backups import create_backup, inspect_backup, restore_backup
from worklead.services.settings import put_setting
from worklead.storage import alembic_config, current_revision, migrate

NOW = datetime(2026, 9, 30, 3, tzinfo=UTC)  # noon KST
SECRET = "원문 개인정보 제목 010-1234-5678 private@example.test"


@pytest.fixture
def app(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.clock = lambda: NOW
    with ctx.db.session() as s:
        put_setting(s, "notifications", {"quiet_hours": {"enabled": False}})
    yield ctx
    ctx.db.dispose()


def add(app, *, kind="new_lead", created_at=NOW, native_delivery=None, has_delivery=False, suppressed=None, notify_=True, type_="notification.created"):
    with app.db.session() as s:
        payload = {"kind": kind, "title": SECRET, "lead_id": "private-lead-id", "source_id": "private-source-id"}
        if has_delivery:
            payload["native_delivery"] = native_delivery
        row = emit(s, type_, payload, notify=notify_, suppressed_reason=suppressed)
        row.created_at = created_at
        return row.id


def row(app, id_):
    with app.db.session() as s:
        return s.scalar(select(EventOutbox).where(EventOutbox.id == id_))


def test_get_is_read_only_private_and_backlog_count_uses_all_eligibility_filters(app):
    c = client_for(app)
    ids = [add(app, created_at=NOW - timedelta(seconds=30-i)) for i in range(30)]
    excluded = [
        add(app, created_at=NOW - timedelta(hours=24)),
        add(app, suppressed="quiet_hours"), add(app, notify_=False),
        add(app, type_="lead.created"), add(app, kind="unrecognized"),
        add(app, has_delivery=True, native_delivery=None),
        add(app, has_delivery=True, native_delivery={"state": "attempted"}),
        add(app, has_delivery=True, native_delivery={"state": "failed"}),
    ]
    snapshots = {id_: row(app, id_).payload for id_ in ids + excluded}
    response = c.get("/v1/native-notifications", params={"limit": 20})
    assert response.status_code == 200
    body = response.json()
    assert body["backlog_count"] == 30
    assert [item["id"] for item in body["items"]] == ids[:20]
    assert all(set(item) == {"id", "kind", "created_at"} for item in body["items"])
    assert SECRET not in response.text and "private-lead-id" not in response.text
    assert {id_: row(app, id_).payload for id_ in ids + excluded} == snapshots
    assert all(row(app, id_).suppressed_reason is None for id_ in excluded if id_ != excluded[1])


def test_concurrent_claim_has_one_durable_winner_without_disclosing_its_token(app):
    id_ = add(app)
    barrier = threading.Barrier(8)

    def attempt(_):
        barrier.wait()
        return native.claim(app, id_)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(8)))
    winners = [result for result in results if result["allowed"]]
    assert len(winners) == 1
    assert len(winners[0]["claim_token"]) == 32
    assert all(result["claim_token"] is None for result in results if not result["allowed"])
    assert row(app, id_).payload["native_delivery"]["claim_token"] == winners[0]["claim_token"]
    assert native.list_pending(app) == {"items": [], "backlog_count": 0}


@pytest.mark.parametrize(("scenario", "reason"), [
    ("old", "expired"), ("created_quiet", "quiet_hours"),
    ("delivery_quiet", "quiet_hours"), ("type_off", "type_disabled"),
    ("not_native", "not_native"), ("unknown_kind", "unsupported_kind"),
])
def test_claim_rechecks_and_records_suppression_while_get_does_not(app, scenario, reason):
    args = {}
    if scenario == "old": args["created_at"] = NOW - timedelta(hours=24)
    if scenario == "created_quiet": args["suppressed"] = "quiet_hours"
    if scenario == "not_native": args["notify_"] = False
    if scenario == "unknown_kind": args["kind"] = "unknown"
    id_ = add(app, **args)
    if scenario == "delivery_quiet":
        app.clock = lambda: NOW.replace(hour=14)  # 23:00 KST
        with app.db.session() as s:
            put_setting(s, "notifications", {"quiet_hours": {"enabled": True, "start": "22:00", "end": "08:00"}})
    if scenario == "type_off":
        with app.db.session() as s:
            put_setting(s, "notifications", {"new_recommended": False})
    before = row(app, id_).payload
    assert native.list_pending(app) == {"items": [], "backlog_count": 0}
    assert row(app, id_).payload == before
    c = client_for(app)
    result = c.post(f"/v1/native-notifications/{id_}/claim")
    assert result.status_code == 200 and result.json()["allowed"] is False
    assert result.json()["state"] == "suppressed" and result.json()["reason"] == reason
    saved = row(app, id_)
    assert saved.payload["native_delivery"]["reason"] == reason
    assert saved.suppressed_reason == reason and saved.notified_at is None


@pytest.mark.parametrize("kind", ["new_lead", "lead_changed", "source_issue"])
def test_type_toggle_and_creation_quiet_are_respected(app, kind):
    with app.db.session() as s:
        put_setting(s, "notifications", {native.TOGGLES[kind]: False})
        assert notify(s, kind, SECRET, dedupe_key=f"disabled:{kind}", now=NOW) is None
    with app.db.session() as s:
        put_setting(s, "notifications", {native.TOGGLES[kind]: True, "quiet_hours": {"enabled": True, "start": "22:00", "end": "08:00"}})
        created = notify(s, kind, SECRET, dedupe_key=f"quiet:{kind}", now=NOW.replace(hour=14))
        id_ = created.id
        assert created.suppressed_reason == "quiet_hours"
    assert native.list_pending(app)["items"] == []  # no morning replay
    assert native.claim(app, id_)["reason"] == "quiet_hours"


@pytest.mark.parametrize("state", ["accepted", "failed", "unknown"])
def test_result_is_idempotent_and_no_terminal_state_is_requeued(app, state):
    id_ = add(app)
    c = client_for(app)
    claim = c.post(f"/v1/native-notifications/{id_}/claim").json()
    assert claim["allowed"] and row(app, id_).payload["native_delivery"]["state"] == "attempted"
    body = {"state": state, "claim_token": claim["claim_token"]}
    result = c.post(f"/v1/native-notifications/{id_}/result", json=body)
    assert result.status_code == 200 and result.json()["state"] == state
    notified = row(app, id_).notified_at
    assert notified == (NOW if state == "accepted" else None)
    app.clock = lambda: NOW + timedelta(minutes=10)
    assert c.post(f"/v1/native-notifications/{id_}/result", json=body).json() == result.json()
    assert row(app, id_).notified_at == notified
    assert not c.post(f"/v1/native-notifications/{id_}/claim").json()["allowed"]
    assert c.get("/v1/native-notifications").json() == {"items": [], "backlog_count": 0}
    assert SECRET not in result.text


def test_unknown_result_cannot_be_promoted_or_claimed_again(app):
    id_ = add(app)
    c = client_for(app)
    token = c.post(f"/v1/native-notifications/{id_}/claim").json()["claim_token"]
    assert c.post(f"/v1/native-notifications/{id_}/result", json={"state": "unknown", "claim_token": token}).status_code == 200
    changed = c.post(f"/v1/native-notifications/{id_}/result", json={"state": "accepted", "claim_token": token})
    assert changed.status_code == 409 and changed.json()["error"]["code"] == "notification_result_conflict"
    assert row(app, id_).notified_at is None


def test_created_after_excludes_old_backlog_with_timezone_and_includes_boundary(app):
    cutoff = NOW - timedelta(minutes=1)
    add(app, created_at=cutoff - timedelta(microseconds=1))
    id_ = add(app, created_at=cutoff)
    later = add(app)
    c = client_for(app)
    body = c.get("/v1/native-notifications", params={"created_after": cutoff.isoformat(), "limit": 1}).json()
    assert body["backlog_count"] == 2 and [x["id"] for x in body["items"]] == [id_]
    plus9 = cutoff.astimezone(datetime.fromisoformat("2026-09-30T12:00:00+09:00").tzinfo).isoformat()
    assert c.get("/v1/native-notifications", params={"created_after": plus9}).json()["backlog_count"] == 2
    assert c.get("/v1/native-notifications", params={"created_after": (NOW + timedelta(seconds=1)).isoformat()}).json()["items"] == []
    assert later != id_


@pytest.mark.parametrize("body", [{"state": "accepted"}, {"state": "pending", "claim_token": "a"*32}, {"state": "failed", "claim_token": "a"*32, "title": SECRET}])
def test_result_rejects_missing_token_unsupported_state_and_arbitrary_text(app, body):
    id_ = add(app)
    assert client_for(app).post(f"/v1/native-notifications/{id_}/result", json=body).status_code == 422
    assert row(app, id_).payload.get("native_delivery") is None


def test_api_rejects_invalid_time_limit_missing_notification_and_unclaimed_result(app):
    c = client_for(app)
    id_ = add(app)
    assert c.get("/v1/native-notifications", params={"created_after": "2026-09-30T03:00:00"}).status_code == 422
    assert c.get("/v1/native-notifications", params={"limit": 101}).status_code == 422
    assert c.post("/v1/native-notifications/missing/claim").status_code == 404
    result = c.post(f"/v1/native-notifications/{id_}/result", json={"state": "accepted", "claim_token": "a"*32})
    assert result.status_code == 409 and result.json()["error"]["code"] == "notification_claim_mismatch"


def test_restart_resumes_unattempted_only(app):
    pending, attempted = add(app), add(app)
    native.claim(app, attempted)
    app.db.dispose()  # new pool and API consumer; durable DB remains
    restarted = client_for(app)
    assert [item["id"] for item in restarted.get("/v1/native-notifications").json()["items"]] == [pending]
    assert not restarted.post(f"/v1/native-notifications/{attempted}/claim").json()["allowed"]
    assert restarted.post(f"/v1/native-notifications/{pending}/claim").json()["allowed"]


def test_restore_requires_a_new_claim_token_for_same_event_id(app):
    id_ = add(app)
    backup = create_backup(app)
    inspected = inspect_backup(app, backup.id)
    old = native.claim(app, id_)
    restore_backup(app, backup.id, inspected.sha256)
    assert native.list_pending(app)["backlog_count"] == 1  # OS ledger owns restore deduplication
    new = native.claim(app, id_)
    assert new["claim_token"] != old["claim_token"]
    c = client_for(app)
    late = c.post(f"/v1/native-notifications/{id_}/result", json={"state": "accepted", "claim_token": old["claim_token"]})
    assert late.status_code == 409 and row(app, id_).notified_at is None
    assert c.post(f"/v1/native-notifications/{id_}/result", json={"state": "unknown", "claim_token": new["claim_token"]}).status_code == 200


def test_maintenance_gate_blocks_poll_claim_and_result_without_modifying_rows(app):
    id_ = add(app)
    c = client_for(app)
    with app.db.gate.exclusive():
        responses = [c.get("/v1/native-notifications"), c.post(f"/v1/native-notifications/{id_}/claim"), c.post(f"/v1/native-notifications/{id_}/result", json={"state": "unknown", "claim_token": "a"*32})]
    assert all(response.status_code == 503 and response.json()["error"]["code"] == "maintenance_busy" for response in responses)
    assert row(app, id_).payload.get("native_delivery") is None


def test_claim_holds_gate_until_committed_before_restore_or_worker_context_changes(app, monkeypatch):
    id_ = add(app)
    entered, release = threading.Event(), threading.Event()
    original = native._row

    def held(s, id_):
        current = original(s, id_)
        entered.set()
        assert release.wait(5)
        return current

    monkeypatch.setattr(native, "_row", held)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(native.claim, app, id_)
        assert entered.wait(5)
        try:
            with pytest.raises(MaintenanceBusy):
                with app.db.gate.exclusive(timeout=0):
                    pass
        finally:
            release.set()
        claimed = future.result(timeout=5)
    assert claimed["allowed"] and row(app, id_).payload["native_delivery"]["state"] == "attempted"


def test_poll_sql_uses_recent_notification_index_and_not_payload_details(app):
    add(app)
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        if "FROM event_outbox" in statement:
            statements.append((statement, parameters))

    event.listen(app.db.engine, "before_cursor_execute", capture)
    try:
        assert native.list_pending(app)["backlog_count"] == 1
    finally:
        event.remove(app.db.engine, "before_cursor_execute", capture)
    statement, parameters = next(item for item in statements if "ORDER BY" in item[0])
    with app.db.engine.connect() as connection:
        plan = connection.exec_driver_sql("EXPLAIN QUERY PLAN " + statement, parameters).all()
    assert any("ix_events_native_pending" in str(part) for part in plan)
    projection = statement.split("\nFROM", 1)[0]
    assert not re.search(r"(?:SELECT|,)\s+event_outbox\.payload(?:\s|,|$)", projection)
    assert "event_outbox.created_at >" in statement


def test_native_contract_fixtures_are_real_schema_valid_private_responses():
    root = Path(__file__).resolve().parents[2] / "contracts"
    for file_name, model in [("native-notifications.json", S.NativeNotificationList), ("native-notification-claim.json", S.NativeNotificationClaimOut), ("native-notification-result.json", S.NativeNotificationResultOut)]:
        data = json.loads((root / "fixtures" / file_name).read_text(encoding="utf-8"))
        model.model_validate(data)
        assert "합성 알림 원문" not in json.dumps(data, ensure_ascii=False)
        assert "title" not in json.dumps(data) and "lead_id" not in json.dumps(data) and "source_id" not in json.dumps(data)
    spec = json.loads((root / "openapi.json").read_text(encoding="utf-8"))
    assert "/v1/native-notifications" in spec["paths"]
    assert "claim_token" in spec["components"]["schemas"]["NativeNotificationResultIn"]["required"]


def test_all_native_endpoints_require_auth_and_suppression_reason_is_private(app):
    id_ = add(app, suppressed="unknown private@example.test")
    c = client_for(app)
    denied = c.post(f"/v1/native-notifications/{id_}/claim").json()
    assert denied["reason"] == "creation_suppressed" and "private@example.test" not in json.dumps(denied)
    responses = [c.get("/v1/native-notifications", headers={"Authorization": ""}), c.post(f"/v1/native-notifications/{id_}/claim", headers={"Authorization": ""}), c.post(f"/v1/native-notifications/{id_}/result", json={"state": "unknown", "claim_token": "a"*32}, headers={"Authorization": ""})]
    assert all(response.status_code == 401 for response in responses)


def test_index_migration_preserves_existing_outbox_and_creates_pre_migration_backup(app):
    id_ = add(app)
    before = row(app, id_).payload
    with app.db.gate.exclusive():
        app.db.dispose()
        command.downgrade(alembic_config(app.config.db_path), "0002_detail_fetch_states")
        assert current_revision(app.config.db_path) == "0002_detail_fetch_states"
        migrate(app.config.db_path, app.config.backup_dir)
    assert current_revision(app.config.db_path) == "0005_page_checkpoints"
    with app.db.engine.connect() as connection:
        indexes = connection.exec_driver_sql("PRAGMA index_list(event_outbox)").all()
    assert any(part[1] == "ix_events_native_pending" for part in indexes)
    assert row(app, id_).payload == before and native.list_pending(app)["backlog_count"] == 1
    assert any("pre-migrate-0002_detail_fetch_states" in file.name for file in app.config.backup_dir.iterdir())
