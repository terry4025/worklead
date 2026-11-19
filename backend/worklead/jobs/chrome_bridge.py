"""A short-lived loopback capability for one fixed Chrome companion extension.

Unread counts and separately enabled visible chat text enter this process.
Chat text stays in memory. No browser profile, cookies, remote APIs or sends.
"""
from __future__ import annotations

import hmac
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from ..db import MaintenanceBusy
from .applications import ApplicationError
from .messages import MessageStatus, record_count
from .chat_view import ChatObservation, ChatView

EXTENSION_ID = "ljakiellilahkchbfjalkecollnfldoc"
EXTENSION_ORIGIN = f"chrome-extension://{EXTENSION_ID}"
MAX_BODY = 512
HEARTBEAT_SECONDS = 15


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    state: Literal["watching", "login_required", "surface_unrecognized"]
    unread_count: StrictInt | None = Field(ge=0, le=9_999_999)
    sequence: StrictInt = Field(ge=1, le=9_007_199_254_740_991)
    account_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def coherent(self):
        if (self.state == "watching") != (self.unread_count is not None):
            raise ValueError("count is present only while watching")
        return self


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


class _BridgeServer(HTTPServer):
    allow_reuse_address = False
    timeout = 0.2

    def get_request(self):
        client, address = super().get_request()
        client.settimeout(2)
        return client, address

    def handle_error(self, request, client_address):
        pass  # Never log a request, body, account URL or exception text.


class _Handler(BaseHTTPRequestHandler):
    server_version = "WorkleadBridge"

    def log_message(self, *_):
        pass

    def _reply(self, code: int, data=None, *, cors=False):
        payload = json.dumps(data or {}, separators=(",", ":")).encode()
        try:
            # Drain a small rejected POST before closing. An unread body can
            # turn an intended 4xx into a TCP reset on Windows. No parsing/logging.
            if self.command == "POST" and not getattr(self, "_body_read", False) and not getattr(self, "_body_consumed", False):
                length = self.headers.get("Content-Length", "")
                cap = 25_000
                if len(self.headers.get_all("Content-Length", [])) == 1 and len(length) <= 5 and length.isascii() and length.isdecimal() and 0 < int(length) <= cap:
                    self.connection.settimeout(0.2)
                    try: self.rfile.read(int(length))
                    except (OSError, TimeoutError): pass
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            if cors:
                self.send_header("Access-Control-Allow-Origin", EXTENSION_ORIGIN)
                self.send_header("Vary", "Origin")
            self.end_headers()
            self.wfile.write(payload)
        except (OSError, TimeoutError):
            pass
        self.close_connection = True

    def _scope(self):
        runtime = self.server.runtime
        if self.path not in ("/observe", "/chat", "/ping"):
            self._reply(404, {"error": "not_found"})
            return False
        if len(self.headers.get_all("Host", [])) != 1 or self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
            self._reply(403, {"error": "host_rejected"})
            return False
        if len(self.headers.get_all("Origin", [])) != 1 or self.headers.get("Origin") != EXTENSION_ORIGIN:
            self._reply(403, {"error": "origin_rejected"})
            return False
        if runtime._stop.is_set():
            self._reply(503, {"error": "bridge_stopped"}, cors=True)
            return False
        return True

    def do_OPTIONS(self):
        # Browser preflight has no Bearer token. It is allowed only for this
        # exact extension, endpoint and two request headers; never for a site.
        if not self._scope():
            return
        requested = {value.strip().lower() for value in self.headers.get("Access-Control-Request-Headers", "").split(",") if value.strip()}
        if self.headers.get("Access-Control-Request-Method") != "POST" or requested != {"authorization", "content-type"}:
            self._reply(403, {"error": "preflight_rejected"}, cors=True)
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", EXTENSION_ORIGIN)
        self.send_header("Access-Control-Allow-Methods", "POST")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Max-Age", "0")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Vary", "Origin")
        if self.headers.get("Access-Control-Request-Private-Network") == "true":
            self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

    def do_GET(self):
        self._reply(405, {"error": "method_not_allowed"})

    def do_POST(self):
        self._body_read = False
        if not self._scope():
            return
        runtime = self.server.runtime
        authorization = self.headers.get("Authorization", "")
        if len(self.headers.get_all("Authorization", [])) != 1 or not runtime.authorized(authorization):
            self._reply(401, {"error": "unauthorized"}, cors=True)
            return
        if (len(self.headers.get_all("Content-Length", [])) != 1
                or self.headers.get("Transfer-Encoding") is not None):
            self._reply(400, {"error": "length_required"}, cors=True)
            return
        raw_length = self.headers.get("Content-Length", "")
        if not raw_length.isascii() or not raw_length.isdecimal():
            self._reply(400, {"error": "invalid_length"}, cors=True)
            return
        if len(raw_length) > (5 if self.path == "/chat" else 4):
            self._reply(413, {"error": "body_too_large"}, cors=True)
            return
        length = int(raw_length)
        if length < 1 or length > (25_000 if self.path == "/chat" else MAX_BODY):
            self._reply(413, {"error": "body_too_large"}, cors=True)
            return
        if self.headers.get("Content-Type", "").lower().replace(" ", "") not in {"application/json", "application/json;charset=utf-8"}:
            self._reply(415, {"error": "json_required"}, cors=True)
            return
        try:
            self._body_read = True
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("incomplete body")
            value = json.loads(raw, object_pairs_hook=_unique_object)
            if self.path == "/ping":
                if value != {}: raise ValueError("empty ping required")
                observation = None
            else:
                model = ChatObservation if self.path == "/chat" else Observation
                observation = model.model_validate(value)
        except (ValueError, TypeError, UnicodeError, OSError, TimeoutError):
            self._reply(422, {"error": "invalid_observation"}, cors=True)
            return
        try:
            # A reconnect can rotate the capability while this request reads
            # its body. Recheck it under the observation/rotation lock.
            with runtime._lock:
                if not runtime.authorized(authorization):
                    raise ApplicationError(401, "unauthorized", "크롬 연결 권한이 만료되었습니다")
                result = ({"purpose": "messages", "version": 1} if self.path == "/ping" else
                    runtime.observe_chat(observation) if self.path == "/chat" else runtime.observe(observation))
        except ApplicationError as exc:
            self._reply(exc.status, {"error": exc.code}, cors=True)
        except Exception:
            self._reply(503, {"error": "observation_unavailable"}, cors=True)
        else:
            self._reply(200, result, cors=True)


class ChromeBridgeRuntime:
    def __init__(self, app, *, monotonic=time.monotonic):
        self.app = app
        self.monotonic = monotonic
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = self._server = None
        self._token = ""
        self._url = ""
        self._sequence = 0
        self._last_observation = None
        self._previous = None
        self._gap_previous = None
        self._heartbeat = None
        self._generation = app.db.gate.generation
        self._status = MessageStatus(provider="chrome")
        self._chat = ChatView()
        self._chat_account = None
        self._chat_sequence = 0
        self._chat_heartbeat = None
        self._chat_capture = False
        self._bound_account = None

    def _update(self, **changes):
        self._status = self._status.model_copy(update=changes)

    def _expire(self):
        if self._status.enabled and self._heartbeat is not None and self.monotonic() - self._heartbeat >= HEARTBEAT_SECONDS:
            if self._status.error_code != "chrome_heartbeat_expired":
                self._gap_previous = self._previous
            self._previous = None
            self._update(state="error", error_code="chrome_heartbeat_expired", unread_count=None,
                         last_observed_at=None, browser_open=False, needs_user_action=True,
                         message="크롬 확장 연결이 끊겼습니다. 선택한 당근 탭과 확장 연결을 확인하세요")

    def status(self):
        with self._lock:
            self._expire()
            return self._status.model_copy(deep=True)

    def authorized(self, authorization: str) -> bool:
        with self._lock:
            return bool(self._token and not self._stop.is_set() and hmac.compare_digest(authorization.encode(), f"Bearer {self._token}".encode()))

    def start(self, *, capture_visible_chat=False, remember_connection=False):
        if remember_connection:
            from .message_pairing import runtime as pairing_runtime
            return pairing_runtime(self.app).start(capture_visible_chat=capture_visible_chat)
        pair = self.app._jobs_message_pairing_runtime
        if pair is not None: pair.disable()
        return self._start_session(capture_visible_chat=capture_visible_chat)

    def _start_session(self, *, capture_visible_chat=False, pair_state=None):
        with self._lock:
            if self._thread and self._thread.is_alive():
                if self._stop.is_set():
                    raise ApplicationError(409, "chrome_watch_stopping", "크롬 확장 연결을 정리 중입니다. 잠시 후 다시 시작하세요")
                return {"connection": {"url": self._url, "token": self._token}, "status": self.status()}
            self._stop.clear()
            self._server = _BridgeServer(("127.0.0.1", 0), _Handler)
            self._server.runtime = self
            self._token = secrets.token_urlsafe(32)
            self._url = f"http://127.0.0.1:{self._server.server_port}"
            self._sequence, self._last_observation, self._previous = 0, None, None
            self._gap_previous = None
            self._generation = self.app.db.gate.generation
            self._heartbeat = None  # Installation/pairing can precede the first observation.
            self._chat_capture = capture_visible_chat
            self._bound_account = pair_state["account"] if pair_state else None
            self._chat = ChatView(enabled=capture_visible_chat, state="waiting" if capture_visible_chat else "off")
            self._chat_account, self._chat_sequence, self._chat_heartbeat = None, 0, None
            self._status = MessageStatus(provider="chrome", enabled=True, state="starting", needs_user_action=True,
                                         remembers_connection=bool(pair_state), paired=bool(pair_state and pair_state["account"]),
                                         captures_message_body=capture_visible_chat,
                                         message="크롬 확장 연결 대기 중입니다. 선택한 당근 탭을 연결하세요")
            self._thread = threading.Thread(target=self._run, name="jobs-chrome-bridge", daemon=True)
            self._thread.start()
            return {"connection": {"url": self._url, "token": self._token}, "status": self.status()}

    def _resume_session(self, account_hash):
        """Rotate an authenticated companion without losing its unread baseline."""
        with self._lock:
            if not self._status.enabled or self._stop.is_set():
                raise ApplicationError(409, "message_watch_stopped", "메시지 감시가 중지되었습니다")
            if self._generation != self.app.db.gate.generation:
                self._pause_persistent("database_restored")
                raise ApplicationError(409, "database_restored", "복원 후 메시지 연결을 다시 준비하세요")
            if self._bound_account and self._bound_account != account_hash:
                self._pause_persistent("account_changed")
                raise ApplicationError(409, "account_changed", "로그인 계정이 바뀌어 메시지 자동 연결을 중지했습니다")
            self._expire()
            self._token = secrets.token_urlsafe(32)
            self._bound_account = account_hash
            self._sequence, self._last_observation = 0, None
            self._chat_account, self._chat_sequence, self._chat_heartbeat = None, 0, None
            self._chat = ChatView(enabled=self._chat_capture, state="waiting" if self._chat_capture else "off")
            self._update(paired=True)
            return {"url": self._url, "token": self._token}

    def _run(self):
        server = self._server
        try:
            while not self._stop.is_set():
                server.handle_request()
                with self._lock:
                    self._expire()
        except (OSError, ValueError):
            if not self._stop.is_set():
                with self._lock:
                    self._previous = None
                    self._gap_previous = None
                    self._update(enabled=False, state="error", error_code="chrome_bridge_unavailable", unread_count=None,
                                 browser_open=False, needs_user_action=True, message="크롬 확장 연결을 시작하지 못했습니다. 다시 연결하세요")
        finally:
            server.server_close()

    def chat_view(self):
        with self._lock:
            if self._generation != self.app.db.gate.generation:
                self._chat_capture = False
                self._chat = ChatView()
            if self._chat.enabled and self._chat_heartbeat is not None and self.monotonic() - self._chat_heartbeat >= HEARTBEAT_SECONDS:
                self._chat = ChatView(enabled=True, state="stale")
            return self._chat.model_copy(deep=True)

    def observe_chat(self, observation: ChatObservation):
        with self._lock:
            if not self._chat_capture or self._stop.is_set() or not self._status.enabled:
                raise ApplicationError(403, "chat_capture_not_enabled", "보이는 채팅 표시를 먼저 켜세요")
            if self._generation != self.app.db.gate.generation:
                self._chat_capture = False
                self._chat = ChatView()
                raise ApplicationError(409, "database_restored", "복원 후 새 코드로 다시 연결하세요")
            if observation.sequence <= self._chat_sequence:
                raise ApplicationError(409, "stale_sequence", "지난 채팅 관찰입니다")
            if observation.state == "login_required" or (self._chat_account and self._chat_account != observation.account_hash) or (self._bound_account and self._bound_account != observation.account_hash):
                state = "login_required" if observation.state == "login_required" else "account_changed"
                self._pause_persistent(state)
                return {"accepted": False, "reconnect_required": True}
            lead_id = lead_title = None
            if observation.job_id:
                from sqlalchemy import select
                from ..models import Lead, SourceRecord
                with self.app.db.session() as s:
                    rows = s.scalars(select(SourceRecord).where(SourceRecord.source_id == "daangn-alba",
                        SourceRecord.source_post_id == observation.job_id)).all()
                    ids = {r.lead_id for r in rows if r.lead_id}
                    if len(ids) == 1:
                        lead = s.get(Lead, next(iter(ids)))
                        if lead:
                            lead_id, lead_title = lead.id, lead.title
            self._chat_account = observation.account_hash
            self._chat_sequence = observation.sequence
            self._chat_heartbeat = self.monotonic()
            self._chat = ChatView(enabled=True, state=observation.state, text=observation.text,
                observed_at=self.app.clock(), lead_id=lead_id, lead_title=lead_title)
            # Body is never written to SQLite, notification payloads, logs or AI.
            return {"accepted": True, "reconnect_required": False}

    def observe(self, observation: Observation):
        with self._lock:
            if self._stop.is_set() or not self._status.enabled:
                raise ApplicationError(503, "bridge_stopped", "크롬 연결이 중지되었습니다")
            if self._status.remembers_connection:
                if self._generation != self.app.db.gate.generation:
                    self._pause_persistent("database_restored")
                    raise ApplicationError(409, "database_restored", "복원 후 메시지 연결을 다시 준비하세요")
                if observation.state == "login_required":
                    self._pause_persistent("login_required")
                    return {"accepted": False, "reconnect_required": True}
                if not observation.account_hash:
                    raise ApplicationError(422, "account_unverified", "연결한 계정 표식을 확인하지 못했습니다")
                if self._bound_account != observation.account_hash:
                    self._pause_persistent("account_changed")
                    return {"accepted": False, "reconnect_required": True}
            elif self._chat_capture and (observation.state == "login_required" or
                    self._chat_account and observation.account_hash and self._chat_account != observation.account_hash):
                # A count-only update must also revoke a previously captured
                # body when the tab reports login or identity loss.
                reason = "login_required" if observation.state == "login_required" else "account_changed"
                self._pause_persistent(reason)
                return {"accepted": False, "reconnect_required": True}
            self._expire()
            value = observation.model_dump()
            if observation.sequence == self._sequence and value == self._last_observation:
                return {"accepted": False, "duplicate": True}
            if observation.sequence <= self._sequence:
                raise ApplicationError(409, "stale_sequence", "지난 관찰 순서입니다")
            now = self.app.clock()
            increased = False
            if observation.state != "watching":
                self._previous = None
                self._gap_previous = None
                self._update(state=observation.state, unread_count=None, last_observed_at=None, error_code=None,
                             browser_open=True, needs_user_action=True,
                             message="선택한 크롬 탭에서 직접 로그인하세요" if observation.state == "login_required" else "선택한 크롬 탭의 읽지 않은 메시지 표시를 확인하지 못했습니다")
            else:
                try:
                    with self.app.db.session() as s:
                        if self._generation != self.app.db.gate.generation:
                            self._previous = None
                            self._gap_previous = None
                            self._generation = self.app.db.gate.generation
                        resumed = self._gap_previous is not None
                        increased = record_count(s, observation.unread_count, now,
                            previous=self._gap_previous if resumed else self._previous, resumed=resumed)
                except MaintenanceBusy:
                    self._previous = None
                    self._gap_previous = None
                    self._update(state="surface_unrecognized", unread_count=None, last_observed_at=None, error_code=None,
                                 browser_open=True, needs_user_action=False, message="데이터 작업 중이라 관찰 기록을 보류합니다. 다음 관찰에서 다시 확인합니다")
                except Exception:
                    self._previous = None
                    self._gap_previous = None
                    self._update(state="error", unread_count=None, last_observed_at=None,
                                 error_code="chrome_observation_unavailable", browser_open=True, needs_user_action=True,
                                 message="관찰 상태를 기록하지 못했습니다. 다음 관찰에서 연결을 다시 확인합니다")
                    raise ApplicationError(503, "observation_unavailable", "관찰 상태를 기록하지 못했습니다") from None
                else:
                    self._previous = observation.unread_count
                    self._gap_previous = None
                    self._update(state="watching", unread_count=observation.unread_count, last_observed_at=now,
                                 error_code=None, browser_open=True, needs_user_action=False,
                                 message=("미읽음 수를 감시하며 보이는 채팅 내용을 일시 표시합니다. 대화방 선택·메시지 전송은 하지 않습니다"
                                     if self._chat_capture else "선택한 크롬 탭의 읽지 않은 메시지 수만 감시합니다. 본문·발신자는 읽거나 저장하지 않습니다"))
                    if increased:
                        self._update(last_increase_at=now)
            self._heartbeat = self.monotonic()
            self._sequence, self._last_observation = observation.sequence, value
        if increased:
            self.app.hub.notify()
        return {"accepted": True, "duplicate": False}

    def _pause_session(self, reason):
        with self._lock:
            self._chat_capture = False
            self._chat = ChatView(state="login_required" if reason == "login_required" else "account_changed" if reason == "account_changed" else "off")
            self._previous = self._gap_previous = None
            self._update(enabled=False, state="login_required" if reason == "login_required" else "error", error_code=reason,
                captures_message_body=False, unread_count=None, last_observed_at=None, browser_open=False, needs_user_action=True,
                message="계정·로그인 또는 데이터 상태가 바뀌어 메시지 자동 연결을 중지했습니다. 확인 후 다시 시작하세요")

    def _pause_persistent(self, reason):
        self._pause_session(reason)
        if self.app._jobs_message_pairing_runtime is not None:
            self.app._jobs_message_pairing_runtime.disable(reason)

    def stop(self):
        try:
            if self.app._jobs_message_pairing_runtime is not None:
                self.app._jobs_message_pairing_runtime.disable()
        except ApplicationError:
            self.shutdown()
            raise
        return self.shutdown()

    def forget(self):
        from .message_pairing import runtime as pairing_runtime
        pair = pairing_runtime(self.app)
        self.shutdown()
        pair.disable()
        pair.forget()
        pair.close()
        self._update(remembers_connection=False, paired=False)
        return self.status()

    def shutdown(self):
        self._stop.set()
        with self._lock:
            self._token, self._url, self._previous = "", "", None
            self._chat_capture = False
            self._chat = ChatView()
            self._chat_account = None
            self._gap_previous = None
            self._update(enabled=False, state="stopped", unread_count=None, last_observed_at=None,
                         error_code=None, browser_open=False, needs_user_action=True, message="크롬 확장 연결이 중지되었습니다")
            server, thread = self._server, self._thread
        if server:
            server.server_close()
        if thread and thread is not threading.current_thread():
            thread.join(timeout=3)
        return self.status()


def runtime(app):
    with app.ai_control_lock:
        if getattr(app, "_jobs_chrome_runtime", None) is None:
            app._jobs_chrome_runtime = ChromeBridgeRuntime(app)
        return app._jobs_chrome_runtime
