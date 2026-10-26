"""Purpose-bound durable message reconnect; no browser, private data or sends."""
from copy import deepcopy
import json
import secrets
import socket
import threading
from http.client import HTTPResponse
from urllib.parse import urlsplit

import httpx
import pytest
from sqlalchemy import select

from tests.conftest import client_for
from worklead.jobs.applications import ApplicationError
from worklead.jobs.chrome_bridge import EXTENSION_ORIGIN, Observation, runtime as chrome_runtime
from worklead.jobs.message_pairing import MessagePairingRuntime, Hello, Resume, proof, revoke_for_restore
from worklead.jobs.auto_pairing import proof as auto_proof
from worklead.models import EventOutbox
from worklead.services.credentials import CredentialError

ACCOUNT = "a" * 64

class MemoryStore:
    def __init__(self): self.value = None; self.fail_write = self.fail_delete = False
    def read(self, _): return deepcopy(self.value)
    def write(self, _, value):
        if self.fail_write: raise CredentialError("credential_unavailable")
        self.value = deepcopy(value)
    def delete(self, _):
        if self.fail_delete: raise CredentialError("credential_unavailable")
        self.value = None

@pytest.fixture
def app(ctx):
    ctx.message_pair_store = MemoryStore()
    ctx._jobs_message_pairing_runtime = MessagePairingRuntime(ctx, port=0)
    yield ctx
    chrome_runtime(ctx).shutdown()
    ctx._jobs_message_pairing_runtime.close()

def start(app, capture=False):
    return chrome_runtime(app).start(remember_connection=True, capture_visible_chat=capture)

def handshake(code, account=ACCOUNT):
    with httpx.Client(base_url=code["url"], headers={"Origin": EXTENSION_ORIGIN}, trust_env=False, timeout=5) as client:
        nonce = secrets.token_hex(32)
        response = client.post("/hello", json={"client_nonce": nonce, "account_hash": account})
        assert response.status_code == 200, response.text
        hello = response.json()
        assert hello["proof"] == proof(code["token"], "server", nonce, hello["server_nonce"], account)
        body = {"client_nonce": nonce, "server_nonce": hello["server_nonce"], "account_hash": account,
            "proof": proof(code["token"], "client", nonce, hello["server_nonce"], account)}
        response = client.post("/resume", json=body)
        if response.status_code == 200:
            value = response.json()
            assert value["proof"] == proof(code["token"], "session", nonce, hello["server_nonce"], account, value["connection"])
        return response, body

def endpoint(code):
    return httpx.Client(base_url=code["url"], headers={"Origin": EXTENSION_ORIGIN,
        "Authorization": "Bearer " + code["token"]}, trust_env=False, timeout=5)

def sample(**kw):
    return {"state": "watching", "unread_count": 2, "sequence": 1, "account_hash": ACCOUNT, **kw}

def reboot(app):
    chrome_runtime(app).shutdown(); app._jobs_message_pairing_runtime.close()
    app._jobs_chrome_runtime = None
    app._jobs_message_pairing_runtime = MessagePairingRuntime(app, port=0)
    return app._jobs_message_pairing_runtime.boot()

def code_after_reboot(app, code):
    return {**code, "url": f"http://127.0.0.1:{app._jobs_message_pairing_runtime._server.server_port}"}

def alerts(app):
    with app.db.session() as s: return list(s.scalars(select(EventOutbox).where(EventOutbox.type == "notification.created")))

def test_mutual_proof_ephemeral_ping_counter_and_replay(app):
    setup = start(app); code = setup["connection"]
    assert code["purpose"] == "messages" and code["version"] == 1
    assert setup["status"].remembers_connection and not setup["status"].paired
    response, body = handshake(code); assert response.status_code == 200
    session = response.json()["connection"]
    assert session["token"] != code["token"]
    with endpoint(session) as client:
        assert client.post("/ping", json={}).json() == {"purpose": "messages", "version": 1}
        assert chrome_runtime(app).status().state == "starting"
        assert client.post("/observe", json=sample()).status_code == 200
        assert alerts(app) == []
        assert client.post("/observe", json=sample(sequence=2, unread_count=3)).status_code == 200
        assert len(alerts(app)) == 1
    with httpx.Client(base_url=code["url"], headers={"Origin": EXTENSION_ORIGIN}, trust_env=False) as c:
        assert c.post("/resume", json=body).status_code == 401
    assert chrome_runtime(app).status().paired
    assert code["token"] not in chrome_runtime(app).status().model_dump_json()
    assert not list(app.config.data_dir.glob("*pair*.json"))

def test_reboot_same_grant_new_session_no_code_and_clean_alert_baseline(app):
    code = start(app)["connection"]; old = handshake(code)[0].json()["connection"]
    with endpoint(old) as c: assert c.post("/observe", json=sample(unread_count=8)).status_code == 200
    assert reboot(app)
    assert chrome_runtime(app).status().enabled and chrome_runtime(app).status().paired
    assert app.jobs_message_provider == "chrome"
    code = code_after_reboot(app, code); new = handshake(code)[0].json()["connection"]
    assert old["token"] != new["token"]
    with endpoint(new) as c: assert c.post("/observe", json=sample(unread_count=10)).status_code == 200
    assert alerts(app) == []  # First post-reboot count is a baseline.
    assert start(app)["connection"] is None

@pytest.mark.parametrize("expired", [False, True])
def test_same_process_resume_rotates_key_resets_sequences_and_preserves_counter(app, expired):
    bridge = chrome_runtime(app)
    ticks = [0.0]
    bridge.monotonic = lambda: ticks[0]
    code = start(app, capture=True)["connection"]
    old = handshake(code)[0].json()["connection"]
    with endpoint(old) as c:
        for sequence in (1, 2):
            assert c.post("/observe", json=sample(sequence=sequence, unread_count=4)).status_code == 200
            assert c.post("/chat", json={"state":"visible", "text":"synthetic prior body", "sequence":sequence,
                "account_hash":ACCOUNT, "job_id":None}).status_code == 200
    assert alerts(app) == []
    heartbeat = bridge._heartbeat
    if expired: ticks[0] = 20.0
    new = handshake(code)[0].json()["connection"]
    assert new["token"] != old["token"] and new["url"] == old["url"]
    assert bridge._heartbeat == heartbeat
    assert bridge.chat_view().state == "waiting" and bridge.chat_view().text is None
    with endpoint(old) as c:
        assert c.post("/ping", json={}).status_code == 401
        assert c.post("/observe", json=sample(sequence=3, unread_count=5)).status_code == 401
    with endpoint(new) as c:
        assert c.post("/observe", json=sample(sequence=1, unread_count=4)).status_code == 200
        assert c.post("/chat", json={"state":"visible", "text":"synthetic resumed body", "sequence":1,
            "account_hash":ACCOUNT, "job_id":None}).status_code == 200
        assert alerts(app) == []
        assert c.post("/observe", json=sample(sequence=2, unread_count=5)).status_code == 200
        assert len(alerts(app)) == 1
    assert bridge.chat_view().text == "synthetic resumed body"


def test_inflight_old_capability_cannot_write_after_authenticated_resume(app, monkeypatch):
    bridge = chrome_runtime(app)
    code = start(app)["connection"]
    old = handshake(code)[0].json()["connection"]
    old_authorized = threading.Event()
    authorized = bridge.authorized
    def check(authorization):
        accepted = authorized(authorization)
        if accepted and authorization == "Bearer " + old["token"]: old_authorized.set()
        return accepted
    monkeypatch.setattr(bridge, "authorized", check)
    url = urlsplit(old["url"])
    body = json.dumps(sample(sequence=9, unread_count=9)).encode()
    headers = (f"POST /observe HTTP/1.1\r\nHost: {url.netloc}\r\nOrigin: {EXTENSION_ORIGIN}\r\n"
        f"Authorization: Bearer {old['token']}\r\nContent-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n").encode()
    with socket.create_connection((url.hostname, url.port), timeout=5) as sock:
        sock.sendall(headers)
        assert old_authorized.wait(2)
        new = handshake(code)[0].json()["connection"]
        sock.sendall(body)
        response = HTTPResponse(sock); response.begin()
        assert response.status == 401
        response.read()
    assert bridge._sequence == 0 and alerts(app) == []
    with endpoint(new) as c:
        assert c.post("/observe", json=sample()).status_code == 200


@pytest.mark.parametrize("reason", ["login_required", "account_changed"])
def test_account_loss_pauses_count_only_grant_across_reboot(app, reason):
    code = start(app)["connection"]; conn = handshake(code)[0].json()["connection"]
    with endpoint(conn) as c:
        body = sample(state="login_required", unread_count=None, account_hash=None) if reason == "login_required" else sample(account_hash="b"*64)
        assert c.post("/observe", json=body).json()["reconnect_required"]
    assert app.message_pair_store.value["paused"] == reason
    assert not reboot(app)
    assert not chrome_runtime(app).status().enabled and chrome_runtime(app).status().error_code == reason

@pytest.mark.parametrize("capture", [False, True])
def test_chat_requires_both_stored_backend_optin_and_bound_account(app, capture):
    code = start(app, capture=capture)["connection"]; conn = handshake(code)[0].json()["connection"]
    text = "synthetic only, never persist"
    with endpoint(conn) as c:
        body = {"state": "visible", "text": text, "sequence": 1, "account_hash": ACCOUNT, "job_id": None}
        response = c.post("/chat", json=body)
        assert response.status_code == (200 if capture else 403)
        if capture:
            assert chrome_runtime(app).chat_view().text == text
            assert c.post("/chat", json={**body,"sequence":2,"account_hash":"b"*64}).json()["reconnect_required"]
            assert chrome_runtime(app).chat_view().text is None
            assert chrome_runtime(app).status().error_code == "account_changed"
    assert text not in json.dumps(app.message_pair_store.value)
    if capture: assert not reboot(app)
    else:
        assert reboot(app)
        assert not chrome_runtime(app).status().captures_message_body


@pytest.mark.parametrize("remember", [False, True])
@pytest.mark.parametrize("reason", ["login_required", "account_changed"])
def test_aggregate_identity_loss_also_erases_visible_body_and_reports_reason(app, remember, reason):
    setup = start(app, capture=True) if remember else chrome_runtime(app).start(capture_visible_chat=True)
    conn = handshake(setup["connection"])[0].json()["connection"] if remember else setup["connection"]
    with endpoint(conn) as c:
        assert c.post("/chat", json={"state":"visible", "text":"synthetic body", "sequence":1,
            "account_hash":ACCOUNT, "job_id":None}).status_code == 200
        body = sample(state="login_required", unread_count=None, account_hash=None) if reason == "login_required" else sample(account_hash="b"*64)
        assert c.post("/observe", json=body).json()["reconnect_required"]
    status = chrome_runtime(app).status()
    assert status.error_code == reason and not status.enabled and not status.captures_message_body
    assert chrome_runtime(app).chat_view().text is None
    if remember: assert not app.message_pair_store.value["desired"]


def test_count_identity_cannot_be_omitted_from_a_remembered_session(app):
    code = start(app)["connection"]; conn = handshake(code)[0].json()["connection"]
    with endpoint(conn) as c:
        body = sample(); body.pop("account_hash")
        assert c.post("/observe", json=body).status_code == 422
    assert chrome_runtime(app).status().unread_count is None


def test_stop_persists_but_explicit_start_reuses_pair_and_forget_revokes(app):
    code = start(app)["connection"]; handshake(code)
    assert not chrome_runtime(app).stop().enabled
    assert not reboot(app)
    assert chrome_runtime(app).status().paired and chrome_runtime(app).status().remembers_connection
    assert start(app)["connection"] is None
    assert chrome_runtime(app).forget().paired is False
    assert app.message_pair_store.value is None
    assert not reboot(app)


def test_capture_optin_can_be_removed_when_restarting_same_pair(app):
    code = start(app, True)["connection"]; handshake(code)
    assert start(app, False)["connection"] is None
    assert app.message_pair_store.value["capture"] is False
    assert not chrome_runtime(app).status().captures_message_body
    assert reboot(app) and not chrome_runtime(app).status().captures_message_body


def test_wrong_purpose_proof_bad_nonce_and_expiry_are_rejected(app):
    code = start(app)["connection"]; pair = app._jobs_message_pairing_runtime
    for kind in ("purpose", "expired", "mismatch"):
        nonce = secrets.token_hex(32); hello = pair.hello(Hello(client_nonce=nonce, account_hash=ACCOUNT))
        request = Resume(client_nonce=nonce, server_nonce=hello["server_nonce"], account_hash=ACCOUNT,
            proof=(auto_proof if kind == "purpose" else proof)(code["token"], "client", nonce, hello["server_nonce"], ACCOUNT))
        if kind == "expired": pair._nonces[hello["server_nonce"]] = (nonce, ACCOUNT, 0)
        if kind == "mismatch": request.account_hash = "b"*64
        with pytest.raises(ApplicationError) as caught: pair.resume(request)
        assert caught.value.code == "pairing_proof_invalid"
        with pytest.raises(ApplicationError): pair.resume(request)
    assert app.message_pair_store.value["account"] is None


def test_wrong_account_requires_valid_proof_before_persistent_pause(app):
    code = start(app)["connection"]; handshake(code)
    response,_ = handshake(code, "b"*64)
    assert response.status_code == 409 and response.json()["error"] == "account_changed"
    assert not reboot(app)


def test_ping_checks_auth_origin_host_and_empty_body_without_observation(app):
    conn = start(app)["connection"]; conn = handshake(conn)[0].json()["connection"]
    with endpoint(conn) as c:
        assert c.post("/ping", json={}, headers={"Authorization":"Bearer wrong"}).status_code == 401
        assert c.post("/ping", json={}, headers={"Origin":"https://jobs.daangn.com"}).status_code == 403
        assert c.post("/ping", json={}, headers={"Host":"localhost"}).status_code == 403
        assert c.post("/ping", json={"extra":True}).status_code == 422
    assert chrome_runtime(app).status().last_observed_at is None
    assert alerts(app) == []


def test_restore_hook_and_delete_failure_keep_live_session_stopped(app):
    code = start(app)["connection"]; handshake(code)
    app.message_pair_store.fail_delete = True
    with pytest.raises(ApplicationError): revoke_for_restore(app)
    assert not chrome_runtime(app).status().enabled
    assert not app.message_pair_store.value["desired"]
    assert not reboot(app)
    app.message_pair_store.fail_delete = False
    start(app); revoke_for_restore(app)
    assert app.message_pair_store.value is None and not reboot(app)


def test_actual_backup_restore_api_revokes_message_grant(app):
    from tests.test_backup_restore_quality import create_and_inspect, restore
    code = start(app)["connection"]; handshake(code)
    with client_for(app) as client:
        inspection = create_and_inspect(client)
        response = restore(client, inspection)
        assert response.status_code == 200, response.text
    assert app.message_pair_store.value is None and not chrome_runtime(app).status().enabled
    assert not reboot(app)


def test_start_forget_api_contract_auth_and_no_secret_in_status(app):
    with client_for(app) as client:
        response = client.post("/v1/jobs/messages/chrome/start", json={"confirm":True,"remember_connection":True,"capture_visible_chat":True})
        assert response.status_code == 200, response.text
        body = response.json(); assert body["connection"]["purpose"] == "messages"
        handshake(body["connection"])
        assert client.post("/v1/jobs/messages/chrome/start", json={"confirm":True,"remember_connection":True}).json()["connection"] is None
        status = client.get("/v1/jobs/messages/status").json()
        assert status["paired"] and body["connection"]["token"] not in json.dumps(status)
        assert client.post("/v1/jobs/messages/chrome/forget", headers={"Authorization":"Bearer wrong"}).status_code == 401
        assert not client.post("/v1/jobs/messages/chrome/forget").json()["remembers_connection"]


def test_port_conflict_and_vault_error_do_not_enable_message_capture(app):
    pair = app._jobs_message_pairing_runtime
    with socket.socket() as sock:
        sock.bind(("127.0.0.1",0)); sock.listen(); pair.port=sock.getsockname()[1]
        with pytest.raises(ApplicationError) as caught: start(app,True)
        assert caught.value.code == "pairing_port_unavailable"
    pair.port=0; app.message_pair_store.fail_write=True
    with pytest.raises(ApplicationError): start(app,True)
    assert not chrome_runtime(app).status().enabled


def test_restore_delete_failure_aborts_database_swap_and_live_message_session(app):
    from tests.test_backup_restore_quality import create_and_inspect, restore
    from tests.test_job_auto_apply import add
    from worklead.models import Lead
    lead=add(app)
    code=start(app)["connection"];session=handshake(code)[0].json()["connection"]
    with client_for(app) as client:
        inspection=create_and_inspect(client)
        with app.db.session() as s:s.get(Lead,lead).memo='keep current data'
        app.message_pair_store.fail_delete=True
        response=restore(client,inspection)
        assert response.status_code==503
    assert app.db.gate.generation==0
    with app.db.session() as s:assert s.get(Lead,lead).memo=='keep current data'
    assert not chrome_runtime(app).status().enabled
    assert not chrome_runtime(app).authorized('Bearer '+session['token'])
    assert app.message_pair_store.value['paused']=='database_restored'
    assert not reboot(app)
