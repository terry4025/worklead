"""Mutually authenticated reconnect; durable keys never cross HTTP.

Only the initially paired companion can exchange one-use nonces for a fresh
process capability. This listener does not issue application permits.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time

from pydantic import BaseModel, ConfigDict, Field

from ..services.message_pair_credentials import MessagePairCredentialStore
from ..services.credentials import CredentialError
from .applications import ApplicationError
from .chrome_bridge import EXTENSION_ORIGIN, _BridgeServer, _Handler, _unique_object

PORT = 58348
URL = f"http://127.0.0.1:{PORT}"


def proof(token, role, client_nonce, server_nonce, account_hash, connection=None):
    parts = ["worklead-message-pair-v1", role, client_nonce, server_nonce, account_hash]
    if connection is not None:
        parts.extend([connection["url"], connection["token"]])
    return hmac.new(token.encode("ascii"), "\n".join(parts).encode("utf-8"), hashlib.sha256).hexdigest()


class Hello(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    client_nonce: str = Field(pattern=r"^[a-f0-9]{64}$")
    account_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class Resume(Hello):
    server_nonce: str = Field(pattern=r"^[a-f0-9]{64}$")
    proof: str = Field(pattern=r"^[a-f0-9]{64}$", repr=False)


class Handler(_Handler):
    def _scope(self):
        if self.path not in ("/hello", "/resume"):
            self._reply(404, {"error": "not_found"}); return False
        if len(self.headers.get_all("Host", [])) != 1 or self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
            self._reply(403, {"error": "host_rejected"}); return False
        if len(self.headers.get_all("Origin", [])) != 1 or self.headers.get("Origin") != EXTENSION_ORIGIN:
            self._reply(403, {"error": "origin_rejected"}); return False
        if self.server.runtime._stop.is_set():
            self._reply(503, {"error": "bridge_stopped"}, cors=True); return False
        return True

    def do_OPTIONS(self):
        if not self._scope(): return
        requested = {v.strip().lower() for v in self.headers.get("Access-Control-Request-Headers", "").split(",") if v.strip()}
        if self.headers.get("Access-Control-Request-Method") != "POST" or requested != {"content-type"}:
            self._reply(403, {"error": "preflight_rejected"}, cors=True); return
        self.send_response(204)
        for key, value in (("Access-Control-Allow-Origin", EXTENSION_ORIGIN), ("Access-Control-Allow-Methods", "POST"),
                           ("Access-Control-Allow-Headers", "Content-Type"), ("Access-Control-Max-Age", "0"),
                           ("Cache-Control", "no-store"), ("Vary", "Origin"), ("Content-Length", "0"), ("Connection", "close")):
            self.send_header(key, value)
        if self.headers.get("Access-Control-Request-Private-Network") == "true":
            self.send_header("Access-Control-Allow-Private-Network", "true")
        self.end_headers()
        self.close_connection = True

    def do_POST(self):
        self._body_read = False
        if not self._scope(): return
        length = self.headers.get("Content-Length", "")
        if (len(self.headers.get_all("Content-Length", [])) != 1 or self.headers.get("Transfer-Encoding") is not None
                or not length.isascii() or not length.isdecimal() or len(length) > 3 or not 1 <= int(length) <= 512
                or self.headers.get("Authorization") is not None):
            self._reply(400, {"error": "invalid_request"}, cors=True); return
        if self.headers.get("Content-Type", "").lower().replace(" ", "") not in {"application/json", "application/json;charset=utf-8"}:
            self._reply(415, {"error": "json_required"}, cors=True); return
        try:
            self._body_read = True
            raw = self.rfile.read(int(length))
            if len(raw) != int(length): raise ValueError()
            body = (Hello if self.path == "/hello" else Resume).model_validate(json.loads(raw, object_pairs_hook=_unique_object))
        except (ValueError, TypeError, UnicodeError, OSError, TimeoutError):
            self._reply(422, {"error": "invalid_request"}, cors=True); return
        try:
            result = getattr(self.server.runtime, self.path[1:])(body)
        except ApplicationError as exc:
            self._reply(exc.status, {"error": exc.code}, cors=True)
        except Exception:
            self._reply(503, {"error": "pairing_unavailable"}, cors=True)
        else:
            self._reply(200, result, cors=True)


class MessagePairingRuntime:
    def __init__(self, app, *, port=PORT, monotonic=time.monotonic):
        self.app, self.port, self.monotonic = app, port, monotonic
        self.store = app.message_pair_store or MessagePairCredentialStore()
        self.scope = app.config.data_dir.resolve() / ("demo" if app.config.demo else "live")
        # All grant/session mutations share one lock; neither direction may
        # acquire a pairing lock while another thread holds a session lock.
        from .chrome_bridge import runtime as chrome_runtime
        self._session = chrome_runtime(app)
        self._lock = self._session._lock
        self._stop = threading.Event()
        self._server = self._thread = None
        self._nonces = {}
        self._state = None
        self._loaded = False

    def _read(self):
        if not self._loaded:
            try: self._state = self.store.read(self.scope)
            except CredentialError:
                raise ApplicationError(503, "pairing_credential_unavailable", "크롬 연결 정보를 Windows 자격 증명에서 확인하지 못했습니다") from None
            self._loaded = True
        return self._state

    def _write(self, state):
        try: self.store.write(self.scope, state)
        except CredentialError:
            raise ApplicationError(503, "pairing_credential_unavailable", "크롬 연결 정보를 Windows 자격 증명에 저장하지 못했습니다") from None
        self._state = dict(state)

    def _listen(self):
        if self._thread and self._thread.is_alive() and not self._stop.is_set(): return
        try:
            server = _BridgeServer(("127.0.0.1", self.port), Handler)
        except OSError:
            raise ApplicationError(503, "pairing_port_unavailable", "크롬 메시지 자동 연결 포트를 사용할 수 없습니다. 다른 Worklead 실행을 확인하세요") from None
        server.runtime = self
        self._server = server
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="jobs-message-pairing")
        self._thread.start()

    def _run(self):
        server = self._server
        try:
            while not self._stop.is_set(): server.handle_request()
        except (OSError, ValueError): pass
        finally: server.server_close()

    def start(self, *, capture_visible_chat=False):
        with self._lock:
            state = self._read()
            created = state is None
            state = ({"version": 1, "token": secrets.token_urlsafe(32), "desired": True,
                "account": None, "capture": capture_visible_chat, "paused": None} if created else
                {**state, "desired": True, "capture": capture_visible_chat, "paused": None})
            self._listen()
            self._write(state)
            connection = ({"url": f"http://127.0.0.1:{self._server.server_port}", "token": state["token"],
                "purpose": "messages", "version": 1} if not state["account"] else None)
        try:
            self._session.shutdown()
            self._session._start_session(capture_visible_chat=capture_visible_chat, pair_state=state)
        except Exception:
            self.disable()
            raise
        self.app.jobs_message_provider = "chrome"
        return {"connection": connection, "status": self._session.status()}

    def boot(self):
        with self._lock:
            state = self._read()
            if not state: return False
            session = self._session
            self.app.jobs_message_provider = "chrome"
            session._update(remembers_connection=True, paired=bool(state["account"]))
            if state["paused"]:
                session._pause_session(state["paused"])
            if not state["desired"] or state["paused"]: return False
            self._listen()
        try:
            session._start_session(capture_visible_chat=state["capture"], pair_state=state)
        except Exception:
            self.disable()
            raise
        return True

    def _active(self):
        state = self._read()
        if not state or not state["desired"] or state["paused"]:
            raise ApplicationError(409, "message_watch_stopped", "메시지 감시가 중지되었습니다")
        return state

    def hello(self, body):
        with self._lock:
            state = self._active()
            now = self.monotonic()
            self._nonces = {k: v for k, v in self._nonces.items() if v[2] > now}
            if len(self._nonces) >= 8:
                raise ApplicationError(429, "pairing_busy", "연결 확인을 잠시 후 다시 시도하세요")
            server_nonce = secrets.token_hex(32)
            self._nonces[server_nonce] = (body.client_nonce, body.account_hash, now + 30)
            return {"server_nonce": server_nonce, "proof": proof(state["token"], "server", body.client_nonce, server_nonce, body.account_hash)}

    def resume(self, body):
        with self._lock:
            state = self._active()
            nonce = self._nonces.pop(body.server_nonce, None)
            if (not nonce or nonce[:2] != (body.client_nonce, body.account_hash) or nonce[2] <= self.monotonic()
                    or not hmac.compare_digest(body.proof, proof(state["token"], "client", body.client_nonce, body.server_nonce, body.account_hash))):
                raise ApplicationError(401, "pairing_proof_invalid", "크롬 연결 확인에 실패했습니다")
            if state["account"] and state["account"] != body.account_hash:
                self.disable("account_changed")
                self._session._pause_session("account_changed")
                raise ApplicationError(409, "account_changed", "로그인 계정이 바뀌어 메시지 자동 연결을 중지했습니다")
            if not state["account"]:
                state = {**state, "account": body.account_hash}
                self._write(state)
            connection = self._session._resume_session(state["account"])
            return {"connection": connection, "proof": proof(state["token"], "session", body.client_nonce, body.server_nonce, body.account_hash, connection)}

    def disable(self, reason=None):
        with self._lock:
            state = self._read()
            if state: self._write({**state, "desired": False, "paused": reason})
            self._nonces.clear()

    def forget(self):
        with self._lock:
            try: self.store.delete(self.scope)
            except CredentialError:
                raise ApplicationError(503, "pairing_credential_unavailable", "저장된 크롬 연결을 해제하지 못했습니다") from None
            self._state, self._loaded = None, True
            self._nonces.clear()

    def close(self):
        self._stop.set()
        with self._lock:
            self._nonces.clear()
            server, thread = self._server, self._thread
        if server: server.server_close()
        if thread and thread is not threading.current_thread(): thread.join(timeout=3)


def runtime(app):
    with app.ai_control_lock:
        if app._jobs_message_pairing_runtime is None:
            app._jobs_message_pairing_runtime = MessagePairingRuntime(app)
        return app._jobs_message_pairing_runtime


def boot(app):
    try: runtime(app).boot()
    except ApplicationError as exc:
        from .chrome_bridge import runtime as chrome_runtime
        chrome_runtime(app)._update(state="error", error_code=exc.code, message=exc.message, needs_user_action=True)


def revoke_for_restore(app):
    pair = app._jobs_message_pairing_runtime
    if pair is None: return
    deleted = False
    try:
        pair.disable("database_restored")
        pair.forget()
        deleted = True
    finally:
        if app._jobs_chrome_runtime is not None:
            app._jobs_chrome_runtime.shutdown()
            if deleted: app._jobs_chrome_runtime._update(remembers_connection=False, paired=False)
