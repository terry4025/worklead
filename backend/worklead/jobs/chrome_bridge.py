"""A short-lived loopback capability for one fixed Chrome companion extension.

Only visible aggregate unread counts enter this process. No browser profile,
cookies, account identifiers, chat text, remote APIs or submission transport.
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

EXTENSION_ID = "ljakiellilahkchbfjalkecollnfldoc"
EXTENSION_ORIGIN = f"chrome-extension://{EXTENSION_ID}"
MAX_BODY = 512
HEARTBEAT_SECONDS = 15


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    state: Literal["watching", "login_required", "surface_unrecognized"]
    unread_count: StrictInt | None = Field(ge=0, le=9_999_999)
    sequence: StrictInt = Field(ge=1, le=9_007_199_254_740_991)

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
        if self.path != "/observe":
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
        if len(raw_length) > 4:
            self._reply(413, {"error": "body_too_large"}, cors=True)
            return
        length = int(raw_length)
        if length < 1 or length > MAX_BODY:
            self._reply(413, {"error": "body_too_large"}, cors=True)
            return
        if self.headers.get("Content-Type", "").lower().replace(" ", "") not in {"application/json", "application/json;charset=utf-8"}:
            self._reply(415, {"error": "json_required"}, cors=True)
            return
        try:
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("incomplete body")
            observation = Observation.model_validate(json.loads(raw, object_pairs_hook=_unique_object))
        except (ValueError, TypeError, UnicodeError, OSError, TimeoutError):
            self._reply(422, {"error": "invalid_observation"}, cors=True)
            return
        try:
            result = runtime.observe(observation)
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

    def start(self):
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
            self._status = MessageStatus(provider="chrome", enabled=True, state="starting", needs_user_action=True,
                                         message="크롬 확장 연결 대기 중입니다. 선택한 당근 탭을 연결하세요")
            self._thread = threading.Thread(target=self._run, name="jobs-chrome-bridge", daemon=True)
            self._thread.start()
            return {"connection": {"url": self._url, "token": self._token}, "status": self.status()}

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

    def observe(self, observation: Observation):
        with self._lock:
            if self._stop.is_set() or not self._status.enabled:
                raise ApplicationError(503, "bridge_stopped", "크롬 연결이 중지되었습니다")
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
                                 message="선택한 크롬 탭의 읽지 않은 메시지 수만 감시합니다. 본문·발신자는 읽거나 저장하지 않습니다")
                    if increased:
                        self._update(last_increase_at=now)
            self._heartbeat = self.monotonic()
            self._sequence, self._last_observation = observation.sequence, value
        if increased:
            self.app.hub.notify()
        return {"accepted": True, "duplicate": False}

    def stop(self):
        self._stop.set()
        with self._lock:
            self._token, self._url, self._previous = "", "", None
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
