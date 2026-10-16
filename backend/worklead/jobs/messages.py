"""Observe the official, visible unread badge. Never read/send chat messages.

The dedicated browser owns its normal authenticated session. No cookies, private
API calls, websocket interception, notification permission grants or read receipts.
An unread counter is an aggregate, not a lossless stream of incoming messages.
"""
from __future__ import annotations

import os
import re
import threading
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel
from typing import Literal

from ..engine.events import notify
from ..db import MaintenanceBusy
from ..models import AppSetting

HOME = "https://jobs.daangn.com/"
BADGE = re.compile(r"^채팅(?:, 읽지 않은 메시지 ([0-9]{1,7})개)?$")
POLL_SECONDS = 2


class MessageStatus(BaseModel):
    provider: Literal["edge", "chrome"] = "edge"
    enabled: bool = False
    state: Literal["stopped", "starting", "login_required", "watching", "surface_unrecognized", "error"] = "stopped"
    unread_count: int | None = None
    last_observed_at: datetime | None = None
    last_increase_at: datetime | None = None
    error_code: str | None = None
    message: str = "메시지 감시가 꺼져 있습니다"
    poll_interval_seconds: int = POLL_SECONDS
    observation: str = "visible_unread_badge"
    captures_message_body: bool = False
    browser_notifications: str = "unknown"
    browser_open: bool = False
    needs_user_action: bool = True


def badge_count(label: str | None) -> int | None:
    match = BADGE.fullmatch(label or "")
    return int(match[1] or 0) if match else None


def record_count(s, count: int, now: datetime, *, previous: int | None, resumed: bool = False):
    """Only increases within this live observation produce an alert.

    First observation/reconnection is a baseline: account switches, existing
    unread counts and temporary missing surfaces cannot manufacture a new event.
    The durable serial and event commit together; no private user/chat IDs stored.
    """
    if previous is None or count <= previous:
        return False
    row = s.get(AppSetting, "jobs_message_serial")
    serial = int(row.value) + 1 if row and isinstance(row.value, int) else 1
    if row is None:
        s.add(AppSetting(key="jobs_message_serial", value=serial, updated_at=now))
    else:
        row.value, row.updated_at = serial, now
    title = f"크롬 감시 재개 후 미읽음 {count}개 확인이 필요합니다" if resumed else f"당근알바 읽지 않은 메시지가 {count}개로 늘었습니다"
    notify(s, "daangn_message", title, now=now,
           dedupe_key=f"notify:jobs_message:{serial}", source_id="daangn-alba")
    return True


class MessageBrowser:
    """All Playwright objects stay on their owning watcher thread."""
    def __init__(self, profile: Path):
        self.profile = profile
        self.driver = self.context = self.page = None
        self.blocked = False

    def open(self):
        from playwright.sync_api import sync_playwright
        self.driver = sync_playwright().start()
        self.profile.mkdir(parents=True, exist_ok=True)
        options = {"channel": "msedge", "headless": False}
        if os.name == "nt":
            for base in (os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), os.environ.get("ProgramFiles", r"C:\Program Files")):
                path = Path(base) / "Microsoft/Edge/Application/msedge.exe"
                if path.is_file():
                    options["executable_path"] = str(path)
                    break
        self.context = self.driver.chromium.launch_persistent_context(str(self.profile), **options)
        self.context.set_default_timeout(1500)
        self.context.route("**/*", self._guard)
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        self.page.on("response", self._response)
        self.page.goto(HOME, wait_until="domcontentloaded", timeout=25000)

    def _guard(self, route):
        req = route.request
        if req.is_navigation_request() and req.frame.parent_frame is None:
            url = urlsplit(req.url)
            if not (url.scheme == "https" and url.netloc in {"jobs.daangn.com", "accounts.daangn.com"}):
                route.abort()
                return
        route.continue_()  # The page itself owns its normal subrequests.

    def _response(self, response):
        if response.request.resource_type == "document" and response.status in {403, 429}:
            self.blocked = True

    def sample(self):
        if self.page.is_closed():
            return "closed", None, "unknown"
        # Dispatch browser events without issuing a network request or reloading.
        self.page.wait_for_timeout(10)
        if self.blocked:
            return "blocked", None, "unknown"
        parsed = urlsplit(self.page.url)
        if parsed.netloc == "accounts.daangn.com":
            return "login_required", None, "unknown"
        if parsed.netloc != "jobs.daangn.com":
            return "surface_unrecognized", None, "unknown"
        if self.page.get_by_role("button", name="로그인", exact=True).is_visible():
            return "login_required", None, "unknown"
        badge = self.page.get_by_role("button", name=BADGE)
        if badge.count() != 1 or not badge.is_visible():
            return "surface_unrecognized", None, "unknown"
        count = badge_count(badge.get_attribute("aria-label"))
        if count is None:
            return "surface_unrecognized", None, "unknown"
        permission = self.page.evaluate("() => typeof Notification === 'undefined' ? 'unsupported' : Notification.permission")
        return "watching", count, permission if permission in {"default", "granted", "denied", "unsupported"} else "unknown"

    def close(self):
        try:
            if self.context:
                self.context.close()
        finally:
            if self.driver:
                self.driver.stop()
            self.context = self.page = self.driver = None


class MessageRuntime:
    def __init__(self, app, browser_factory=MessageBrowser):
        self.app = app
        self.browser_factory = browser_factory
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self._status = MessageStatus()

    def status(self):
        with self._lock:
            return self._status.model_copy(deep=True)

    def _update(self, **changes):
        with self._lock:
            self._status = self._status.model_copy(update=changes)

    def start(self):
        from .applications import ApplicationError
        with self._lock:
            if self._thread and self._thread.is_alive():
                if self._stop.is_set():
                    raise ApplicationError(409, "message_watch_stopping", "기존 메시지 감시 창을 정리하고 있습니다. 잠시 후 다시 시작하세요")
                return self.status()
            self._stop.clear()
            self._status = MessageStatus(enabled=True, state="starting", message="전용 Edge를 열고 있습니다. 로그인은 직접 진행하세요")
            self._thread = threading.Thread(target=self._run, name="jobs-message-watch", daemon=True)
            self._thread.start()
            return self.status()

    def stop(self):
        self._stop.set()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=30)
        self._update(enabled=False, state="stopped", message="메시지 감시가 중지되었습니다", unread_count=None,
                     browser_open=bool(thread and thread.is_alive()), needs_user_action=True, error_code=None)
        return self.status()

    def _run(self):
        browser = self.browser_factory(self.app.config.data_dir / "jobs-browser-profile")
        previous = None
        generation = self.app.db.gate.generation
        try:
            browser.open()
            self._update(browser_open=True)
            while not self._stop.is_set():
                state, count, permission = browser.sample()
                now = self.app.clock()
                if state in {"closed", "blocked"}:
                    self._update(enabled=False, state="error", error_code="browser_closed" if state == "closed" else "site_blocked",
                                 message="전용 Edge가 닫혔습니다. 감시를 다시 시작하세요" if state == "closed" else "접근 제한을 확인해 메시지 감시를 중단했습니다", needs_user_action=True, unread_count=None)
                    break
                if state != "watching":
                    previous = None
                    self._update(state=state, unread_count=None, last_observed_at=None,
                                 message="전용 Edge에서 당근에 직접 로그인하세요" if state == "login_required" else "읽지 않은 메시지 표시를 확인하지 못했습니다. Edge에서 로그인·연결 상태를 확인하세요",
                                 needs_user_action=True)
                else:
                    try:
                        with self.app.db.session() as s:
                            # The session holds the gate: restoration cannot race
                            # between this generation check and the event commit.
                            if self.app.db.gate.generation != generation:
                                previous = None
                                generation = self.app.db.gate.generation
                            increased = record_count(s, count, now, previous=previous)
                    except MaintenanceBusy:
                        previous = None
                        self._update(state="surface_unrecognized", unread_count=None, last_observed_at=None,
                                     message="데이터 복원·정리 중이라 감시 기록을 보류합니다. 작업 후 다시 확인합니다", needs_user_action=False)
                        if self._stop.wait(POLL_SECONDS):
                            break
                        continue
                    if increased:
                        self.app.hub.notify()
                        self._update(last_increase_at=now)
                    previous = count
                    self._update(state="watching", unread_count=count, last_observed_at=now,
                                 browser_notifications=permission, needs_user_action=False,
                                 message="읽지 않은 메시지 수를 감시 중입니다. 본문·발신자는 읽거나 저장하지 않습니다")
                if self._stop.wait(POLL_SECONDS):
                    break
        except Exception:
            # Playwright exceptions may contain page text and account URLs.
            self._update(enabled=False, state="error", error_code="browser_unavailable", unread_count=None,
                         message="전용 Edge 감시를 완료하지 못했습니다. 창 상태를 확인하고 다시 연결하세요", needs_user_action=True)
        finally:
            try:
                browser.close()
            except Exception:
                pass
            self._update(browser_open=False)


def runtime(app):
    with app.ai_control_lock:
        if app._jobs_message_runtime is None:
            app._jobs_message_runtime = MessageRuntime(app)
        return app._jobs_message_runtime
