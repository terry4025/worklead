"""Run the existing one-shot submission protocol in an app-owned Edge window.

All Playwright objects stay on this runtime's thread. The persistent approval,
account/profile binding, source gates and at-most-once journal remain owned by
auto_apply/auto_pairing; this runner cannot create a submission bypass.
"""
from __future__ import annotations

import re
import random
import threading
import time

from ..db import MaintenanceBusy
from ..analysis.writer import mark_used
from ..models import AppSetting
from .applications import ApplicationError
from .auto_apply import runtime as auto_runtime
from .auto_apply_models import IdentityIn, NextIn, ReconcileIn, ResultIn, ReviewIn
from .managed_browser import ManagedBrowser, ManagedBrowserError, safe_job_url
from .browser_profile import select_managed_profile

POLL_SECONDS = 5
REOPEN_DELAYS = (5, 15, 60)
MAX_REOPEN_SECONDS = 15 * 60
UNREADY_SECONDS = 60
UNREADY_CHECKS = 3
RECONCILE_DELAYS = (5, 15, 30)
RECOVERY_RESET_SECONDS = 60
HASH = re.compile(r"^[a-f0-9]{64}$")
HELD_REASONS = {"questions_required", "profile_changed", "condition_conflict", "closed", "ui_unrecognized",
                "navigation_failed", "login_required", "account_changed", "browser_timeout", "ai_text_unavailable"}


class ManagedAutoRuntime:
    def __init__(self, app, browser_factory=ManagedBrowser):
        self.app = app
        self.auto = auto_runtime(app)
        self.browser_factory = browser_factory
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self._pair = None
        self._pending = None
        self._status = {"stage": "stopped", "browser_open": False, "message": "자동 연결이 꺼져 있습니다",
                        "error_code": None, "needs_user_action": False}

    def status(self):
        with self._lock:
            return dict(self._status)

    def _update(self, **changes):
        with self._lock:
            self._status.update(changes)
            status = dict(self._status)
        # Notification delivery must never block or retire browser authority.
        try:
            from .managed_alerts import update_alert
            update_alert(self.app, status)
        except Exception:
            pass

    @staticmethod
    def _reopen_delay(failures):
        if failures < len(REOPEN_DELAYS):
            return REOPEN_DELAYS[failures]
        # A temporary outage must not leave an enabled installation permanently
        # idle after three launches. Keep later attempts bounded and staggered.
        ceiling = min(MAX_REOPEN_SECONDS, 120 * 2 ** min(failures - len(REOPEN_DELAYS), 4))
        return random.uniform(ceiling * 0.8, ceiling)

    def start(self):
        with self._lock:
            if self._thread and self._thread.is_alive():
                if self._stop.is_set():
                    raise ApplicationError(409, "managed_auto_stopping", "자동 지원 창을 정리하고 있습니다. 잠시 후 다시 시도하세요")
                return dict(self._status)
            self._pair = self.app._jobs_auto_pairing_runtime
            if self._pair is None or not self.auto._status.enabled:
                raise ApplicationError(409, "auto_apply_stopped", "자동 지원 등록을 먼저 확인해야 합니다")
            self._stop.clear()
            self._pending = None
            self._status = {"stage": "starting", "browser_open": False, "message": "자동 지원 전용 창을 준비하고 있습니다",
                            "error_code": None, "needs_user_action": False}
            self._thread = threading.Thread(target=self._run, name="jobs-managed-auto", daemon=True)
            self._thread.start()
            return dict(self._status)

    def shutdown(self, *, user_stop=False):
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=30)
        alive = bool(thread and thread.is_alive())
        self._update(stage="stopped", message="자동 지원 연결이 중지되었습니다", error_code=None,
                     browser_open=alive, needs_user_action=False)
        if user_stop:
            try:
                from .managed_alerts import update_alert
                update_alert(self.app, {"stage": "stopped", "explicit_stop": True})
            except Exception:
                pass
        return self.status()

    def _active(self):
        # The auto lock gives stop, account switching and final submission a
        # common linearization boundary. Callers must not hold our status lock.
        with self.auto._lock:
            return (not self._stop.is_set() and self.auto._status.enabled and not self.auto._stop.is_set()
                    and self.auto._status.state != "paused" and self.auto._generation == self.app.db.gate.generation)

    def _run(self):
        reopen_count = 0
        while self._active():
            browser = None
            reopen = False
            stable_since = None
            unready_since = None
            unready_checks = 0
            try:
                browser = self.browser_factory(select_managed_profile(self.app.config.data_dir))
                browser.open()
                self._update(browser_open=True)
                while self._active():
                    seen_events = self.app.hub.current_version()
                    try:
                        self._step(browser)
                        if self.status()["stage"] == "ready":
                            unready_since, unready_checks = None, 0
                            now = time.monotonic()
                            if stable_since is None:
                                stable_since = now
                            elif now - stable_since >= RECOVERY_RESET_SECONDS:
                                reopen_count = 0
                                stable_since = now
                        else:
                            stable_since = None
                            if self.status()["error_code"] == "identity_not_ready":
                                now = time.monotonic()
                                unready_since = now if unready_since is None else unready_since
                                unready_checks += 1
                                if unready_checks >= UNREADY_CHECKS and now - unready_since >= UNREADY_SECONDS:
                                    raise ManagedBrowserError("identity_not_ready")
                            else:
                                # Login is user-controlled. Do not repeatedly
                                # close the window while the user authenticates.
                                unready_since, unready_checks = None, 0
                    except MaintenanceBusy:
                        unready_since, unready_checks = None, 0
                        self._update(stage="starting", error_code="maintenance_busy", needs_user_action=False,
                                     message="데이터 정리가 끝나면 자동으로 연결을 이어갑니다")
                    except ApplicationError as exc:
                        self._update(stage="blocked", error_code=exc.code, needs_user_action=True,
                                     message="계정·저장 자기소개 또는 자동 지원 상태를 확인해야 합니다")
                        break
                    except ManagedBrowserError as exc:
                        stable_since = None
                        if exc.code in {"site_blocked", "browser_closed", "browser_unavailable", "browser_profile_busy", "identity_not_ready"}:
                            raise
                        if exc.code in {"navigation_failed", "browser_timeout"}:
                            now = time.monotonic()
                            unready_since = now if unready_since is None else unready_since
                            unready_checks += 1
                            if unready_checks >= UNREADY_CHECKS and now - unready_since >= UNREADY_SECONDS:
                                raise
                        self._update(stage="starting", error_code=exc.code, needs_user_action=False,
                                     message="전용 창의 응답을 다시 확인하고 있습니다")
                    if self._idle(browser, POLL_SECONDS, seen_events=seen_events):
                        break
            except ManagedBrowserError as exc:
                reopen = exc.code in {"browser_closed", "browser_unavailable", "browser_profile_busy",
                                      "navigation_failed", "browser_timeout", "identity_not_ready"}
                if not reopen:
                    self._update(stage="blocked", error_code=exc.code, needs_user_action=True,
                                 message="사이트 접근 제한을 확인해 자동 지원을 멈췄습니다")
            except Exception:
                reopen = True
            finally:
                try:
                    if browser is not None:
                        browser.close()
                except Exception:
                    pass
                self._update(browser_open=False)
            if not reopen or not self._active():
                break
            delay = self._reopen_delay(reopen_count)
            reopen_count += 1
            self._update(stage="starting", error_code="browser_reconnecting", needs_user_action=False,
                         message=f"전용 창을 {max(1, round(delay))}초 뒤 자동으로 다시 연결합니다 ({reopen_count}회)")
            if self._stop.wait(delay):
                break
        if not self._stop.is_set() and not self._active():
            self._update(stage="blocked", needs_user_action=True, error_code=self.auto._status.error_code,
                         message="계정·저장 자기소개 또는 자동 지원 중지 상태를 확인해야 합니다")

    def _idle(self, browser, seconds, *, seen_events=None):
        """Keep browser I/O moving while leaving stop responsive within 250ms."""
        deadline = time.monotonic() + seconds
        while not self._stop.is_set():
            if seen_events is not None and self.app.hub.current_version() != seen_events:
                break  # Committed new/updated leads do not wait for the next poll.
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            pump = getattr(browser, "pump", None)
            if pump is None:  # Browser-free test doubles.
                if self._stop.wait(min(0.25, remaining)):
                    return True
            else:
                pump(min(0.25, remaining))
        return self._stop.is_set()

    def _wait(self, browser, expected, states, seconds=10):
        until = time.monotonic() + seconds
        view = {"state": "held", "reason_code": "browser_timeout"}
        while self._active() and time.monotonic() < until:
            view = browser.observe(expected)
            if view.get("state") in states:
                return view
            if view.get("reason_code") in {"questions_required", "profile_changed", "condition_conflict", "closed",
                                           "login_required", "account_changed"}:
                return view
            if self._idle(browser, 0.2):
                break
        return view

    def _step(self, browser):
        observed = browser.observe()
        if observed.get("state") != "account" or not HASH.fullmatch(str(observed.get("account_hash", ""))):
            login = observed.get("reason_code") == "login_required"
            self._update(stage="login_required" if login else "starting", needs_user_action=login,
                         error_code="login_required" if login else "identity_not_ready",
                         message="전용 창에서 당근 로그인만 완료하면 자동으로 이어집니다" if login else "당근 계정 화면을 확인하고 있습니다")
            return
        account = observed["account_hash"]
        with self.auto._lock:
            if not self._active():
                return
            self._pair.bind_account(account)
        if self._pending is not None:
            self._recover_pending(browser, account)
            return
        with self.auto._lock:
            if not self._active():
                return
            result = self.auto.next(NextIn(account_hash=account))
        self._update(stage="ready", needs_user_action=False, error_code=None,
                     message="전용 창 연결을 확인했습니다. 기준에 맞는 공고를 자동으로 처리합니다")
        job = result.get("job")
        if not job:
            return
        if (not isinstance(job, dict) or not safe_job_url(job.get("url"))
                or not isinstance(job.get("title"), str) or not job["title"].strip()):
            raise ManagedBrowserError("navigation_failed")
        self._process(browser, account, job)

    def _process(self, browser, account, job):
        identity = {"attempt_id": job["attempt_id"], "lease_token": job["lease_token"],
                    "account_hash": account, "profile_hash": "0" * 64}
        # Validate the local lease before any browser action, too.
        IdentityIn(**identity)
        from urllib.parse import unquote, urlsplit
        job_id = re.search(r"([A-Za-z0-9]{12})/?$", unquote(urlsplit(job["url"]).path))[1]
        expected = {"job_id": job_id, "title": job["title"], "account_hash": account}
        permitted = False
        permit_boundary = False
        receipt = None
        try:
            if not self._active():
                return
            browser.navigate(job["url"])
            view = self._wait(browser, expected, {"openable", "ready", "already_applied"})
            if view.get("account_hash") and view["account_hash"] != account:
                self._pair.bind_account(view["account_hash"])
            if view.get("state") == "already_applied":
                self.auto.review(ReviewIn(**identity, outcome="already_applied", reason_code="already_applied"))
                return
            if view.get("state") == "openable" and self._active():
                browser.act("open", expected)
                view = self._wait(browser, expected, {"ready", "already_applied"})
            if (view.get("state") != "ready" or view.get("account_hash") != account
                    or not HASH.fullmatch(str(view.get("profile_hash", "")))):
                reason = view.get("reason_code")
                reason = reason if reason in HELD_REASONS else "ui_unrecognized"
                self.auto.review(ReviewIn(**identity, outcome="held", reason_code=reason))
                return
            text = job.get("application_text")
            if text is not None:
                if not isinstance(text, str) or not HASH.fullmatch(str(job.get("text_hash") or "")):
                    raise ManagedBrowserError("ui_unrecognized")
                filled = browser.act("fill", {**expected, "text": text})
                if filled.get("state") == "filled" and filled.get("profile_hash") == job["text_hash"]:
                    view = self._wait(browser, expected, {"ready"})
                if (filled.get("state") != "filled" or view.get("state") != "ready"
                        or view.get("profile_hash") != job["text_hash"]):
                    reason = filled.get("reason_code") if filled.get("reason_code") in HELD_REASONS else "ui_unrecognized"
                    self.auto.review(ReviewIn(**identity, outcome="held", reason_code=reason))
                    return
            identity["profile_hash"] = view["profile_hash"]
            reviewed = self.auto.review(ReviewIn(**identity, outcome="ready", reason_code="ready", evidence="application_form"))
            if reviewed.get("status") != "ready":
                return
            receipt = reviewed.get("receipt_token")
            with self.app.db.gate.operation(), self.auto._lock:
                if not self._active():
                    return
                # The gate prevents a restore between validation and the click;
                # the auto lock prevents stop/account mutation in that interval.
                requested = time.monotonic()
                deadline_ms = int(time.time() * 1000) + 3000
                permit_boundary = True
                permit = self.auto.permit(IdentityIn(**identity))
                permitted = permit.get("permit") is True
                if not permitted or permit.get("expires_in_seconds") != 3:
                    return
                if not self._active() or time.monotonic() - requested >= 3:
                    return
                clicked = browser.act("submit", {**expected, "profile_hash": view["profile_hash"],
                    "attempt_id": job["attempt_id"], "permit_until": deadline_ms})
                if clicked.get("state") != "clicked":
                    raise ManagedBrowserError("submission_unconfirmed")
            completed = self._wait(browser, expected, {"already_applied"})
            if completed.get("state") == "already_applied" and completed.get("account_hash") == account:
                try:
                    self.auto.result(ResultIn(**identity, outcome="submitted", reason_code="submitted", evidence="cancel_button"))
                    if job.get("text_hash"): mark_used(self.app, job["lead_id"], job["text_hash"])
                except ApplicationError:
                    # A late official completion may only reconcile the already
                    # issued receipt. No permit or click is ever repeated.
                    if receipt:
                        self.auto.reconcile(ReconcileIn(attempt_id=identity["attempt_id"], account_hash=account,
                            profile_hash=identity["profile_hash"], receipt_token=receipt, evidence="cancel_button"))
                    else:
                        raise
            else:
                self.auto.result(ResultIn(**identity, outcome="uncertain", reason_code="submission_unconfirmed"))
                self._remember_pending(identity, receipt, expected, job["url"])
        except Exception as exc:
            try:
                if permitted:
                    self.auto.result(ResultIn(**identity, outcome="uncertain", reason_code="submission_unconfirmed"))
                elif not permit_boundary:
                    code = exc.code if isinstance(exc, ManagedBrowserError) else "ui_unrecognized"
                    code = code if code in {"navigation_failed", "browser_timeout"} else "ui_unrecognized"
                    self.auto.review(ReviewIn(**identity, outcome="held", reason_code=code))
            except Exception:
                pass
            if permit_boundary:
                self._remember_pending(identity, receipt, expected, job["url"])
            if isinstance(exc, (ApplicationError, MaintenanceBusy, ManagedBrowserError)):
                raise
            raise ManagedBrowserError("browser_timeout") from None

    def _remember_pending(self, identity, receipt, expected, url):
        if isinstance(receipt, str) and re.fullmatch(r"[A-Za-z0-9_-]{32,128}", receipt):
            now = time.monotonic()
            self._pending = {"identity": dict(identity), "receipt": receipt, "expected": dict(expected),
                             "url": url, "checks": 0, "created": now, "next": now + RECONCILE_DELAYS[0]}

    def _recover_pending(self, browser, account):
        pending = self._pending
        if pending is None:
            return
        now = time.monotonic()
        if (pending["checks"] >= len(RECONCILE_DELAYS) or now - pending["created"] > 15 * 60
                or account != pending["identity"]["account_hash"]):
            self._pending = None
            return
        if now < pending["next"]:
            return
        # Reserve the read before I/O. Recovery never calls open, submit, review
        # or permit; only official completion can resolve an existing receipt.
        pending["checks"] += 1
        pending["next"] = now + RECONCILE_DELAYS[min(pending["checks"], len(RECONCILE_DELAYS) - 1)]
        try:
            if not self._active():
                return
            browser.navigate(pending["url"])
            view = self._wait(browser, pending["expected"], {"already_applied"}, seconds=3)
            if view.get("state") == "already_applied" and view.get("account_hash") == account:
                identity = pending["identity"]
                self.auto.reconcile(ReconcileIn(attempt_id=identity["attempt_id"], account_hash=account,
                    profile_hash=identity["profile_hash"], receipt_token=pending["receipt"], evidence="cancel_button"))
                self._pending = None
        finally:
            if self._pending is not None and pending["checks"] >= len(RECONCILE_DELAYS):
                self._pending = None


def runtime(app):
    with app.ai_control_lock:
        if getattr(app, "_jobs_managed_auto_runtime", None) is None:
            app._jobs_managed_auto_runtime = ManagedAutoRuntime(app)
        return app._jobs_managed_auto_runtime


def boot(app):
    if app.config.demo:
        return False
    with app.db.session() as session:
        setting = session.get(AppSetting, "job_auto_managed")
        enabled = bool(setting and isinstance(setting.value, dict) and setting.value.get("enabled") is True)
    if enabled and app._jobs_auto_apply_runtime is not None and app._jobs_auto_apply_runtime._status.enabled:
        runtime(app).start()
        return True
    return False
