"""Real loopback reconnect, fixed-purpose authentication and restart boundaries."""
from copy import deepcopy
import json
import secrets
import socket

import httpx
import pytest

from tests.conftest import client_for
from tests.test_job_auto_apply import ACCOUNT, PROFILE, NOW, add, claimed, ready
from worklead.jobs.applications import ApplicationError
from worklead.jobs.auto_apply import runtime as auto_runtime
from worklead.jobs.auto_apply_models import AutoApplyStartIn
from worklead.jobs.auto_pairing import PairingRuntime, Hello, Resume, proof, revoke_for_restore
from worklead.jobs.chrome_bridge import EXTENSION_ORIGIN
from worklead.models import SourcePolicy, JobAutoAttempt
from worklead.services.credentials import CredentialError
from sqlalchemy import select


class MemoryStore:
    def __init__(self): self.value = None; self.fail_delete = False; self.fail_write = False
    def read(self, _): return deepcopy(self.value)
    def write(self, _, value):
        if self.fail_write: raise CredentialError("credential_unavailable")
        self.value = deepcopy(value)
    def delete(self, _):
        if self.fail_delete: raise CredentialError("credential_unavailable")
        self.value = None


@pytest.fixture
def app(ctx):
    ctx.clock = lambda: NOW
    with ctx.db.session() as s: s.get(SourcePolicy, "daangn-alba").status = "allowed"
    ctx.auto_pair_store = MemoryStore()
    ctx._jobs_auto_pairing_runtime = PairingRuntime(ctx, port=0)
    yield ctx
    auto_runtime(ctx).shutdown()
    ctx._jobs_auto_pairing_runtime.close()


def start(app):
    return auto_runtime(app).start(AutoApplyStartIn(confirm=True, remember_connection=True))


def handshake(code, account=ACCOUNT, *, client=None):
    own = client is None
    client = client or httpx.Client(base_url=code["url"], headers={"Origin": EXTENSION_ORIGIN}, timeout=5)
    try:
        nonce = secrets.token_hex(32)
        hello = client.post("/hello", json={"client_nonce": nonce, "account_hash": account})
        assert hello.status_code == 200, hello.text
        body = hello.json()
        assert body["proof"] == proof(code["token"], "server", nonce, body["server_nonce"], account)
        resume = {"client_nonce": nonce, "server_nonce": body["server_nonce"], "account_hash": account,
                  "proof": proof(code["token"], "client", nonce, body["server_nonce"], account)}
        result = client.post("/resume", json=resume)
        if result.status_code == 200:
            data = result.json()
            assert data["proof"] == proof(code["token"], "session", nonce, body["server_nonce"], account, data["connection"])
        return result, resume
    finally:
        if own: client.close()


def endpoint(code):
    return httpx.Client(base_url=code["url"], headers={"Origin": EXTENSION_ORIGIN, "Authorization": "Bearer " + code["token"]}, timeout=5)


def restart(app):
    auto_runtime(app).shutdown()
    app._jobs_auto_pairing_runtime.close()
    app._jobs_auto_apply_runtime = None
    app._jobs_auto_pairing_runtime = PairingRuntime(app, port=0)
    return app._jobs_auto_pairing_runtime.boot()


def test_mutual_http_handshake_never_sends_durable_bearer_and_one_use(app):
    setup = start(app)
    code = setup["connection"]
    assert setup["status"].remembers_connection and not setup["status"].paired
    result, resume = handshake(code)
    assert result.status_code == 200
    assert auto_runtime(app).status().paired
    connection = result.json()["connection"]
    assert code["token"] != connection["token"]
    with endpoint(connection) as client:
        assert client.post("/next", json={"account_hash": ACCOUNT}).status_code == 200
    with endpoint(code) as client:
        assert client.post("/next", json={"account_hash": ACCOUNT}).status_code == 404
        assert client.post("/hello", json={"client_nonce": "a"*64, "account_hash": ACCOUNT}).status_code == 400
    with httpx.Client(base_url=code["url"], headers={"Origin": EXTENSION_ORIGIN}, timeout=5) as client:
        assert client.post("/resume", json=resume).status_code == 401
    assert code["token"] not in auto_runtime(app).status().model_dump_json()
    assert not list(app.config.data_dir.glob("*pair*.json"))


def test_restart_reuses_one_pair_new_session_and_uncertain_never_resubmits(app):
    add(app)
    code = start(app)["connection"]
    result, _ = handshake(code)
    old = result.json()["connection"]
    with endpoint(old) as client:
        identity = claimed(client)
        ready(client, identity)
        assert client.post("/permit", json=identity).json()["permit"] is True
    assert restart(app)
    pair = app._jobs_auto_pairing_runtime
    code = {**code, "url": f"http://127.0.0.1:{pair._server.server_port}"}
    new = handshake(code)[0].json()["connection"]
    assert old["token"] != new["token"]
    with endpoint(new) as client:
        assert claimed(client) is None
        assert client.post("/permit", json=identity).status_code == 409
    with app.db.session() as s:
        row = s.scalar(select(JobAutoAttempt))
        assert row.state == "uncertain"
    assert start(app)["connection"] is None


def test_stop_persisted_reboot_then_start_needs_no_code(app):
    code = start(app)["connection"]
    handshake(code)
    status = auto_runtime(app).stop()
    assert status.remembers_connection and status.paired
    with httpx.Client(base_url=code["url"], headers={"Origin": EXTENSION_ORIGIN}, timeout=5) as client:
        assert client.post("/hello", json={"client_nonce": "c"*64, "account_hash": ACCOUNT}).status_code == 409
    assert not restart(app)
    status = auto_runtime(app).status()
    assert not status.enabled and status.remembers_connection and status.paired
    assert start(app)["connection"] is None


@pytest.mark.parametrize("reason", ["login_required", "account_changed"])
def test_pause_latches_across_process_restart(app, reason):
    code = start(app)["connection"]
    connection = handshake(code)[0].json()["connection"]
    with endpoint(connection) as client:
        assert client.post("/pause", json={"reason": reason}).status_code == 200
    assert not restart(app)
    assert auto_runtime(app).status().error_code == reason
    assert not app.auto_pair_store.value["desired"]


def test_account_mismatch_requires_valid_proof_before_persistent_pause(app):
    code = start(app)["connection"]
    handshake(code)
    pair = app._jobs_auto_pairing_runtime
    hello = pair.hello(Hello(client_nonce="c"*64, account_hash="d"*64))
    with pytest.raises(ApplicationError) as caught:
        pair.resume(Resume(client_nonce="c"*64, server_nonce=hello["server_nonce"], account_hash="d"*64, proof="0"*64))
    assert caught.value.code == "pairing_proof_invalid"
    assert app.auto_pair_store.value["desired"]
    response, _ = handshake(code, "d"*64)
    assert response.status_code == 409
    assert not restart(app)
    assert app.auto_pair_store.value["paused"] == "account_changed"


def test_bad_expired_mismatched_nonce_and_cap_are_rejected(app):
    code = start(app)["connection"]
    pair = app._jobs_auto_pairing_runtime
    clock = [0]
    pair.monotonic = lambda: clock[0]
    hello = pair.hello(Hello(client_nonce="c"*64, account_hash=ACCOUNT))
    clock[0] = 31
    request = Resume(client_nonce="c"*64, server_nonce=hello["server_nonce"], account_hash=ACCOUNT,
        proof=proof(code["token"], "client", "c"*64, hello["server_nonce"], ACCOUNT))
    with pytest.raises(ApplicationError, match="연결 확인"): pair.resume(request)
    for _ in range(8): pair.hello(Hello(client_nonce=secrets.token_hex(32), account_hash=ACCOUNT))
    with pytest.raises(ApplicationError) as caught: pair.hello(Hello(client_nonce="c"*64, account_hash=ACCOUNT))
    assert caught.value.code == "pairing_busy"


def test_restore_revokes_outside_database_and_forget_failure_cannot_rearm(app):
    code = start(app)["connection"]
    handshake(code)
    app.auto_pair_store.fail_delete = True
    with pytest.raises(ApplicationError): auto_runtime(app).forget()
    assert not app.auto_pair_store.value["desired"]
    assert not restart(app)
    app.auto_pair_store.fail_delete = False
    start(app)
    revoke_for_restore(app)
    assert app.auto_pair_store.value is None
    assert not restart(app)
    assert not auto_runtime(app).status().remembers_connection


def test_real_backup_restore_hook_revokes_grant_and_prevents_restart(app):
    from tests.test_backup_restore_quality import create_and_inspect, restore
    add(app)
    code = start(app)["connection"]
    handshake(code)
    with client_for(app) as client:
        inspection = create_and_inspect(client)
        response = restore(client, inspection)
        assert response.status_code == 200, response.text
        status = client.get("/v1/jobs/auto-apply/status").json()
        assert not status["enabled"] and not status["remembers_connection"]
    assert app.auto_pair_store.value is None
    assert not restart(app)


def test_restore_vault_delete_failure_aborts_restore_and_stops_live_capability(app):
    from tests.test_backup_restore_quality import create_and_inspect, restore
    from worklead.models import Lead
    lead_id = add(app)
    code = start(app)["connection"]
    connection = handshake(code)[0].json()["connection"]
    with client_for(app) as client:
        inspection = create_and_inspect(client)
        with app.db.session() as s:
            s.get(Lead, lead_id).memo = "must remain after failed restore"
        app.auto_pair_store.fail_delete = True
        response = restore(client, inspection)
        assert response.status_code == 503, response.text
        assert not client.get("/v1/jobs/auto-apply/status").json()["enabled"]
        assert app.db.gate.generation == 0
        with app.db.session() as s:
            assert s.get(Lead, lead_id).memo == "must remain after failed restore"
    assert not app.auto_pair_store.value["desired"]
    assert app.auto_pair_store.value["paused"] == "database_restored"
    assert not auto_runtime(app).authorized("Bearer " + connection["token"])
    assert not restart(app)
    assert not auto_runtime(app).status().enabled


def test_profile_binding_is_durable_and_changes_pause_before_new_permit(app):
    add(app)
    code = start(app)["connection"]
    first = handshake(code)[0].json()["connection"]
    with endpoint(first) as client:
        identity = claimed(client)
        ready(client, identity)
        assert app.auto_pair_store.value["profile"] == PROFILE
    assert restart(app)
    add(app, key="ABCDEFGHIJK2")
    code = {**code, "url": f"http://127.0.0.1:{app._jobs_auto_pairing_runtime._server.server_port}"}
    second = handshake(code)[0].json()["connection"]
    with endpoint(second) as client:
        identity = claimed(client)
        assert identity is not None
        identity["profile_hash"] = "c"*64
        response = client.post("/review", json={**identity, "outcome": "ready", "reason_code": "ready", "evidence": "application_form"})
        assert response.status_code == 200 and response.json()["status"] == "held"
        assert client.post("/permit", json=identity).status_code == 409
    assert app.auto_pair_store.value["paused"] == "profile_changed"
    assert not restart(app)


def test_auth_control_api_forget_and_nullable_contract(app):
    code = start(app)["connection"]
    handshake(code)
    with client_for(app) as client:
        assert client.post("/v1/jobs/auto-apply/start", json={"confirm": True, "remember_connection": True}).json()["connection"] is None
        response = client.post("/v1/jobs/auto-apply/forget")
        assert response.status_code == 200 and not response.json()["remembers_connection"]
        assert client.post("/v1/jobs/auto-apply/forget", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_wrong_origin_host_extra_keys_and_pna_preflight(app):
    code = start(app)["connection"]
    payload = {"client_nonce": "c"*64, "account_hash": ACCOUNT}
    with httpx.Client(base_url=code["url"], headers={"Origin": EXTENSION_ORIGIN}, timeout=5) as client:
        assert client.post("/hello", json=payload, headers={"Origin": "https://jobs.daangn.com"}).status_code == 403
        assert client.post("/hello", json=payload, headers={"Host": "localhost"}).status_code == 403
        assert client.post("/hello", json={**payload, "token": code["token"]}).status_code == 422
        response = client.options("/hello", headers={"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type", "Access-Control-Request-Private-Network": "true"})
        assert response.status_code == 204 and response.headers["Access-Control-Allow-Private-Network"] == "true"


def test_port_collision_and_vault_write_failure_do_not_enable(app):
    pair = app._jobs_auto_pairing_runtime
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0)); occupied.listen()
        pair.port = occupied.getsockname()[1]
        with pytest.raises(ApplicationError) as caught: start(app)
        assert caught.value.code == "pairing_port_unavailable"
        assert not auto_runtime(app).status().enabled
    pair.port = 0
    app.auto_pair_store.fail_write = True
    with pytest.raises(ApplicationError) as caught: start(app)
    assert caught.value.code == "pairing_credential_unavailable"
    assert not auto_runtime(app).status().enabled
