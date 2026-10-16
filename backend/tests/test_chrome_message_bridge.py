"""Loopback extension capability security and aggregate counter lifecycle."""
import json
import threading
from contextlib import contextmanager
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import select

from tests.conftest import client_for
from worklead.jobs.chrome_bridge import ChromeBridgeRuntime, EXTENSION_ORIGIN, Observation
from worklead.jobs.messages import MessageRuntime
from worklead.models import EventOutbox

NOW = datetime(2026, 10, 16, 8, tzinfo=UTC)


@pytest.fixture
def bridge(ctx):
    ctx.clock = lambda: NOW
    clock = [100.0]
    runtime = ChromeBridgeRuntime(ctx, monotonic=lambda: clock[0])
    connection = runtime.start()["connection"]
    yield runtime, connection, clock
    runtime.stop()
    assert runtime._thread is None or not runtime._thread.is_alive()


def headers(connection, **patch):
    return {"Origin": EXTENSION_ORIGIN, "Authorization": f"Bearer {connection['token']}", "Content-Type": "application/json", **patch}


def send(connection, sequence=1, count=0, state="watching", **options):
    with httpx.Client(trust_env=False, timeout=3) as client:
        return client.post(connection["url"] + "/observe", headers=headers(connection),
                           json={"state": state, "unread_count": count, "sequence": sequence}, **options)


def alerts(ctx):
    with ctx.db.session() as s:
        return list(s.scalars(select(EventOutbox).where(EventOutbox.type == "notification.created")))


def test_first_baseline_then_increase_and_sequence_idempotency(ctx, bridge):
    runtime, connection, _ = bridge
    assert runtime.status().provider == "chrome" and runtime.status().state == "starting"
    assert send(connection, count=5).json() == {"accepted": True, "duplicate": False}
    assert alerts(ctx) == []
    assert send(connection, sequence=2, count=6).status_code == 200
    assert len(alerts(ctx)) == 1 and alerts(ctx)[0].payload["kind"] == "daangn_message"
    assert send(connection, sequence=2, count=6).json() == {"accepted": False, "duplicate": True}
    assert send(connection, sequence=2, count=7).status_code == 409
    assert send(connection, sequence=1, count=20).status_code == 409
    assert len(alerts(ctx)) == 1
    assert runtime.status().unread_count == 6
    assert connection["token"] not in runtime.status().model_dump_json()


@pytest.mark.parametrize("changed,status", [
    ({"Origin": "https://jobs.daangn.com"}, 403),
    ({"Origin": "chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}, 403),
    ({"Origin": "null"}, 403),
    ({"Host": "evil.example"}, 403),
    ({"Authorization": "Bearer wrong"}, 401),
    ({"Authorization": ""}, 401),
    ({"Content-Type": "text/plain"}, 415),
])
def test_actual_post_requires_exact_origin_host_auth_and_json(bridge, changed, status):
    _, connection, _ = bridge
    with httpx.Client(trust_env=False, timeout=3) as client:
        response = client.post(connection["url"] + "/observe", headers=headers(connection, **changed), json={"state": "watching", "unread_count": 0, "sequence": 1})
    assert response.status_code == status
    assert connection["token"] not in response.text


def test_cors_preflight_is_only_exact_extension_post_and_two_headers(bridge):
    _, connection, _ = bridge
    preflight = {"Origin": EXTENSION_ORIGIN, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization, content-type"}
    with httpx.Client(trust_env=False, timeout=3) as client:
        allowed = client.options(connection["url"] + "/observe", headers=preflight)
        assert allowed.status_code == 204
        assert allowed.headers["Access-Control-Allow-Origin"] == EXTENSION_ORIGIN
        assert "*" not in str(allowed.headers)
        assert client.options(connection["url"] + "/observe", headers={**preflight, "Origin": "https://jobs.daangn.com"}).status_code == 403
        assert client.options(connection["url"] + "/observe", headers={**preflight, "Access-Control-Request-Method": "GET"}).status_code == 403
        assert client.options(connection["url"] + "/observe", headers={**preflight, "Access-Control-Request-Headers": "authorization, content-type, x-extra"}).status_code == 403
        assert client.get(connection["url"] + "/observe").status_code == 405
        assert client.post(connection["url"] + "/unknown", headers=headers(connection), json={}).status_code == 404
        assert client.post(connection["url"] + "/observe?token=ignored", headers=headers(connection), json={}).status_code == 404


@pytest.mark.parametrize("body", [
    {"state": "watching", "unread_count": True, "sequence": 1},
    {"state": "watching", "unread_count": 1, "sequence": True},
    {"state": "watching", "unread_count": 0, "sequence": 0},
    {"state": "watching", "unread_count": -1, "sequence": 1},
    {"state": "watching", "unread_count": 10_000_000, "sequence": 1},
    {"state": "watching", "unread_count": None, "sequence": 1},
    {"state": "login_required", "unread_count": 0, "sequence": 1},
    {"state": "watching", "unread_count": "1", "sequence": 1},
    {"state": "watching", "unread_count": 1, "sequence": 1, "message_body": "must never be accepted"},
    {"state": "watching", "unread_count": 1, "sequence": 1, "session_id": "unneeded"},
    {"state": "other", "unread_count": None, "sequence": 1},
])
def test_strict_small_observation_never_accepts_message_fields_or_coercion(ctx, bridge, body):
    _, connection, _ = bridge
    with httpx.Client(trust_env=False, timeout=3) as client:
        response = client.post(connection["url"] + "/observe", headers=headers(connection), json=body)
    assert response.status_code == 422 and alerts(ctx) == []


def test_body_size_duplicate_json_keys_and_missing_origin_are_rejected(bridge):
    _, connection, _ = bridge
    with httpx.Client(trust_env=False, timeout=3) as client:
        assert client.post(connection["url"] + "/observe", headers=headers(connection), content=b" " * 513).status_code == 413
        duplicate = b'{"state":"watching","unread_count":0,"sequence":1,"sequence":2}'
        assert client.post(connection["url"] + "/observe", headers=headers(connection), content=duplicate).status_code == 422
        missing_origin = headers(connection)
        del missing_origin["Origin"]
        assert client.post(connection["url"] + "/observe", headers=missing_origin, content=b"{}").status_code == 403


def test_heartbeat_timeout_and_login_loss_rebase_without_false_new_alert(ctx, bridge):
    runtime, connection, clock = bridge
    send(connection, count=5)
    clock[0] += 15
    assert runtime.status().error_code == "chrome_heartbeat_expired"
    assert runtime.status().unread_count is None and runtime.status().browser_open is False
    assert send(connection, sequence=2, count=20).status_code == 200
    assert len(alerts(ctx)) == 1 and runtime.status().state == "watching"
    assert alerts(ctx)[0].payload["title"] == "크롬 감시 재개 후 미읽음 20개 확인이 필요합니다"
    assert send(connection, sequence=3, count=None, state="login_required").status_code == 200
    assert send(connection, sequence=4, count=30).status_code == 200
    assert len(alerts(ctx)) == 1
    send(connection, sequence=5, count=31)
    assert len(alerts(ctx)) == 2


def test_duplicates_do_not_keep_stale_observation_alive(bridge):
    runtime, connection, clock = bridge
    send(connection, count=5)
    clock[0] += 14
    assert send(connection, count=5).json()["duplicate"]
    clock[0] += 1
    assert runtime.status().error_code == "chrome_heartbeat_expired"


def test_repeated_expiry_keeps_gap_baseline_and_same_or_lower_return_does_not_alert(ctx, bridge):
    runtime, connection, clock = bridge
    send(connection, count=5)
    clock[0] += 15
    assert runtime.status().error_code == "chrome_heartbeat_expired"
    clock[0] += 30
    assert runtime.status().error_code == "chrome_heartbeat_expired"
    send(connection, sequence=2, count=6)
    assert len(alerts(ctx)) == 1
    assert alerts(ctx)[0].payload["title"] == "크롬 감시 재개 후 미읽음 6개 확인이 필요합니다"
    assert send(connection, sequence=2, count=6).json()["duplicate"]
    clock[0] += 15
    runtime.status()
    send(connection, sequence=3, count=6)
    clock[0] += 15
    runtime.status()
    send(connection, sequence=4, count=4)
    assert len(alerts(ctx)) == 1
    send(connection, sequence=5, count=5)
    assert len(alerts(ctx)) == 2
    assert alerts(ctx)[1].payload["title"] == "당근알바 읽지 않은 메시지가 5개로 늘었습니다"


@pytest.mark.parametrize("lost_state", ["login_required", "surface_unrecognized", "generation"])
def test_gap_baseline_is_discarded_on_unknown_account_or_restoration(ctx, bridge, lost_state):
    runtime, connection, clock = bridge
    send(connection, count=5)
    clock[0] += 15
    runtime.status()
    if lost_state == "generation":
        with ctx.db.gate.exclusive():
            ctx.db.gate.generation += 1
    else:
        send(connection, sequence=2, count=None, state=lost_state)
    send(connection, sequence=3, count=20)
    assert alerts(ctx) == []


def test_waiting_for_manual_installation_does_not_expire_before_first_sample(bridge):
    runtime, connection, clock = bridge
    clock[0] += 600
    assert runtime.status().state == "starting" and runtime.status().error_code is None
    send(connection, count=20)
    assert runtime.status().state == "watching"


def test_token_is_discarded_on_stop_and_rotated_on_restart(bridge):
    runtime, connection, _ = bridge
    assert runtime.authorized(f"Bearer {connection['token']}")
    runtime.stop()
    assert not runtime.authorized(f"Bearer {connection['token']}")
    assert runtime.status().state == "stopped" and runtime._token == ""
    renewed = runtime.start()["connection"]
    assert renewed["token"] != connection["token"]
    with httpx.Client(trust_env=False, timeout=3) as client:
        wrong = client.post(renewed["url"] + "/observe", headers=headers(renewed, Authorization=f"Bearer {connection['token']}"), json={"state": "watching", "unread_count": 1, "sequence": 1})
    assert wrong.status_code == 401


def test_maintenance_collision_consumes_observation_and_rebases_after_release(ctx, bridge):
    runtime, connection, clock = bridge
    send(connection, count=5)
    clock[0] += 15
    runtime.status()
    entered, release = threading.Event(), threading.Event()
    def hold():
        with ctx.db.gate.exclusive():
            entered.set()
            release.wait(3)
    thread = threading.Thread(target=hold)
    thread.start()
    try:
        assert entered.wait(3)
        assert send(connection, sequence=2, count=20).status_code == 200
        assert runtime.status().state == "surface_unrecognized" and runtime.status().unread_count is None
    finally:
        release.set()
        thread.join(timeout=4)
    send(connection, sequence=3, count=30)
    assert alerts(ctx) == []
    send(connection, sequence=4, count=31)
    assert len(alerts(ctx)) == 1


def test_generation_is_checked_inside_database_session(ctx, bridge, monkeypatch):
    _, connection, _ = bridge
    send(connection, count=5)
    original = ctx.db.session
    changed = [False]
    @contextmanager
    def restored():
        if not changed[0]:
            changed[0] = True
            with ctx.db.gate.exclusive():
                ctx.db.gate.generation += 1
        with original() as s:
            yield s
    monkeypatch.setattr(ctx.db, "session", restored)
    send(connection, sequence=2, count=20)
    assert alerts(ctx) == []
    send(connection, sequence=3, count=21)
    assert len(alerts(ctx)) == 1


def test_database_failure_is_private_and_next_success_is_a_fresh_baseline(ctx, bridge, monkeypatch):
    runtime, connection, _ = bridge
    send(connection, count=5)
    original = ctx.db.session
    @contextmanager
    def broken():
        raise RuntimeError("private SQL secret account data")
        yield
    monkeypatch.setattr(ctx.db, "session", broken)
    failed = send(connection, sequence=2, count=20)
    assert failed.status_code == 503
    assert runtime.status().unread_count is None
    assert "private SQL" not in failed.text + runtime.status().model_dump_json()
    monkeypatch.setattr(ctx.db, "session", original)
    send(connection, sequence=3, count=30)
    assert alerts(ctx) == [] and runtime.status().state == "watching"


def test_main_api_requires_auth_explicit_confirmation_and_keeps_tokens_private(ctx, monkeypatch, tmp_path):
    from worklead.jobs import chrome_extension
    monkeypatch.setattr(chrome_extension, "prepare_extension_directory", lambda _: tmp_path / "extension")
    c = client_for(ctx)
    assert c.get("/v1/jobs/messages/chrome/status", headers={"Authorization": ""}).status_code == 401
    assert c.post("/v1/jobs/messages/chrome/start", json={}).status_code == 422
    assert c.post("/v1/jobs/messages/chrome/start", json={"confirm": False}).status_code == 422
    started = c.post("/v1/jobs/messages/chrome/start", json={"confirm": True})
    try:
        assert started.status_code == 200, started.text
        assert started.headers["Cache-Control"] == "no-store"
        result = started.json()
        assert result["status"]["provider"] == "chrome"
        assert result["extension_directory"] == str(tmp_path / "extension")
        assert result["connection"]["token"] != ctx.token
        status = c.get("/v1/jobs/messages/chrome/status")
        assert "connection" not in status.json() and result["connection"]["token"] not in status.text
        assert ctx.jobs_message_provider == "chrome"
        renewed = c.post("/v1/jobs/messages/chrome/start", json={"confirm": True})
        assert renewed.status_code == 200
        assert renewed.json()["connection"]["token"] != result["connection"]["token"]
        assert not ctx._jobs_chrome_runtime.authorized(f"Bearer {result['connection']['token']}")
    finally:
        c.post("/v1/jobs/messages/chrome/stop")


def test_edge_enabled_blocks_chrome_pairing(ctx, monkeypatch):
    edge = MessageRuntime(ctx)
    edge._update(enabled=True, state="watching")
    ctx._jobs_message_runtime = edge
    response = client_for(ctx).post("/v1/jobs/messages/chrome/start", json={"confirm": True})
    assert response.status_code == 409 and response.json()["error"]["code"] == "edge_watch_active"
