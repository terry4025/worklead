"""App-owned system Edge, with the reviewed UI helper in an isolated world.

The browser owns its normal persistent session. Personal browser profiles,
credentials, login forms, cookies and private remote APIs are never inspected.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

from .browser_profile import ProfileLease

HOME = "https://jobs.daangn.com/"


class ManagedBrowserError(Exception):
    def __init__(self, code="browser_timeout"):
        self.code = code
        super().__init__(code)


def safe_job_url(url):
    try:
        p = urlsplit(url)
        return (p.scheme == "https" and p.netloc == "jobs.daangn.com" and not p.query and not p.fragment
                and bool(re.fullmatch(r"/job-posts/(?:[^/?#]+-)?[A-Za-z0-9]{12}/?", p.path)))
    except (ValueError, TypeError):
        return False


class ManagedBrowser:
    def __init__(self, profile: Path):
        self.profile = profile
        self.driver = self.context = self.page = self._cdp = None
        self.blocked = False
        self._script = None
        self._profile_lease = None

    def open(self):
        from playwright.sync_api import sync_playwright

        self.close()
        if self.profile.is_symlink():
            raise ManagedBrowserError("browser_unavailable")
        self._profile_lease = ProfileLease(self.profile)
        if not self._profile_lease.acquire():
            raise ManagedBrowserError("browser_profile_busy")
        try:
            self.driver = sync_playwright().start()
            options = {"channel": "msedge", "headless": False}
            if os.name == "nt":
                for base in (os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                             os.environ.get("ProgramFiles", r"C:\Program Files")):
                    path = Path(base) / "Microsoft/Edge/Application/msedge.exe"
                    if path.is_file():
                        options["executable_path"] = str(path)
                        break
            self.context = self.driver.chromium.launch_persistent_context(str(self.profile), **options)
            self.context.set_default_timeout(7000)
            self.context.route("**/*", self._guard)
            self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
            self.page.on("response", self._response)
            self._cdp = self.context.new_cdp_session(self.page)
            root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[3]))
            self._script = (root / "extensions/worklead-messages/auto-content.js").read_text(encoding="utf-8")
            self.page.goto(HOME, wait_until="domcontentloaded", timeout=25000)
        except Exception:
            self.close()
            raise ManagedBrowserError("browser_unavailable") from None

    def _guard(self, route):
        request = route.request
        if request.is_navigation_request() and request.frame.parent_frame is None:
            try:
                parsed = urlsplit(request.url)
                allowed = request.url == "about:blank" or (parsed.scheme == "https" and parsed.netloc in
                           {"jobs.daangn.com", "accounts.daangn.com"})
            except (ValueError, TypeError):
                allowed = False
            if not allowed:
                route.abort()
                return
        route.continue_()

    def _response(self, response):
        if response.request.resource_type == "document" and response.status in {403, 429}:
            self.blocked = True

    def is_open(self):
        return bool(self.page is not None and not self.page.is_closed())

    def navigate(self, url):
        if not safe_job_url(url):
            raise ManagedBrowserError("navigation_failed")
        if self.blocked:
            raise ManagedBrowserError("site_blocked")
        try:
            self.page.goto(url, wait_until="domcontentloaded", timeout=15000)
        except Exception:
            raise ManagedBrowserError("navigation_failed") from None

    def observe(self, expected=None):
        return self.act("inspect", expected)

    def pump(self, seconds):
        """Dispatch routed requests/events on the owning Playwright thread.

        Login is user-driven, but its scripts still need our synchronous route
        callbacks. A threading wait cannot dispatch those callbacks.
        """
        if not self.is_open():
            raise ManagedBrowserError("browser_closed")
        try:
            self.page.wait_for_timeout(max(0, seconds) * 1000)
        except Exception:
            raise ManagedBrowserError("browser_closed" if not self.is_open() else "browser_timeout") from None

    def act(self, operation, expected=None):
        if operation not in {"inspect", "open", "fill", "submit"}:
            raise ManagedBrowserError("ui_unrecognized")
        if not self.is_open():
            raise ManagedBrowserError("browser_closed")
        if self.blocked:
            raise ManagedBrowserError("site_blocked")
        self.pump(0)
        try:
            parsed = urlsplit(self.page.url)
            if parsed.scheme == "https" and parsed.netloc == "accounts.daangn.com":
                return {"state": "held", "reason_code": "login_required"}
            if parsed.scheme != "https" or parsed.netloc != "jobs.daangn.com":
                return {"state": "held", "reason_code": "navigation_failed"}
            # CDP creates a separate JS world. Remote page scripts cannot replace
            # our helper, used-attempt set, or expected-identity arguments.
            frame = self._cdp.send("Page.getFrameTree")["frameTree"]["frame"]["id"]
            world = self._cdp.send("Page.createIsolatedWorld", {"frameId": frame, "worldName": "worklead-managed-auto-v1"})
            context_id = world["executionContextId"]
            injected = self._cdp.send("Runtime.evaluate", {"contextId": context_id,
                "expression": "globalThis.__workleadManagedDriver = true;\n" + self._script,
                "returnByValue": True, "awaitPromise": True, "timeout": 7000})
            if injected.get("exceptionDetails"):
                raise ManagedBrowserError("ui_unrecognized")
            args = json.dumps([operation, expected], ensure_ascii=True)
            result = self._cdp.send("Runtime.evaluate", {"contextId": context_id,
                "expression": f"globalThis.__workleadAutoUi(...{args})",
                "returnByValue": True, "awaitPromise": True, "timeout": 7000})
            value = result.get("result", {}).get("value")
            if result.get("exceptionDetails") or not isinstance(value, dict):
                raise ManagedBrowserError("ui_unrecognized")
            return value
        except ManagedBrowserError:
            raise
        except Exception:
            raise ManagedBrowserError("browser_timeout") from None

    def close(self):
        try:
            if self.context is not None:
                self.context.close()
        finally:
            try:
                if self.driver is not None:
                    self.driver.stop()
            finally:
                self.driver = self.context = self.page = self._cdp = None
                if self._profile_lease is not None:
                    self._profile_lease.release()
                    self._profile_lease = None

