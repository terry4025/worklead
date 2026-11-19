"""Long outages recover without user setup or repeat submission authority."""
import threading
from types import SimpleNamespace

import pytest

from tests.test_auto_pairing import app
from tests.test_managed_auto import Browser, runner_for
from tests.test_job_auto_apply import ACCOUNT, add
from worklead.jobs.auto_apply import runtime as auto_runtime
from worklead.jobs.managed_auto import ManagedAutoRuntime
from worklead.jobs.managed_browser import ManagedBrowserError


def test_long_outage_recovers_after_more_than_three_reopens(app, monkeypatch):
    runner, _ = runner_for(app)
    created, delays = [], []
    def delay(attempt):
        delays.append(attempt)
        return 0
    monkeypatch.setattr(runner, "_reopen_delay", delay)
    class Fails(Browser):
        def open(self):
            raise ManagedBrowserError("browser_unavailable")
    def factory(path):
        value = Fails(path) if len(created) < 6 else Browser(path)
        created.append(value)
        return value
    runner.browser_factory = factory
    step = runner._step
    def recovered(value):
        step(value)
        runner._stop.set()
    monkeypatch.setattr(runner, "_step", recovered)
    runner._run()
    assert len(created) == 7 and delays == list(range(6))
    assert all(value.closed for value in created)
    assert app.auto_pair_store.value["desired"] is True
    assert runner.status()["stage"] == "ready"
    assert not runner.status()["needs_user_action"]


def test_long_recovery_delay_is_capped_and_staggered(monkeypatch):
    import worklead.jobs.managed_auto as module
    calls = []
    monkeypatch.setattr(module.random, "uniform", lambda low, high: calls.append((low, high)) or high)
    assert [ManagedAutoRuntime._reopen_delay(i) for i in range(3)] == [5, 15, 60]
    assert [ManagedAutoRuntime._reopen_delay(i) for i in range(3, 8)] == [120, 240, 480, 900, 900]
    assert ManagedAutoRuntime._reopen_delay(100000) == 900
    assert all(0 < low <= high <= 900 for low, high in calls)


def test_stop_interrupts_long_recovery_without_reopening(app, monkeypatch):
    runner, _ = runner_for(app)
    waiting = threading.Event()
    created = []
    class Failed(Browser):
        def open(self):
            raise ManagedBrowserError("browser_profile_busy")
    def factory(path):
        value = Failed(path)
        created.append(value)
        return value
    def delay(_):
        waiting.set()
        return 900
    runner.browser_factory = factory
    monkeypatch.setattr(runner, "_reopen_delay", delay)
    runner.start()
    assert waiting.wait(3)
    runner.shutdown()
    assert not runner._thread.is_alive()
    assert len(created) == 1 and created[0].closed
    assert runner.status()["stage"] == "stopped"


@pytest.mark.parametrize("reason", ["identity_not_ready", "browser_timeout", "navigation_failed"])
def test_live_browser_that_stays_unready_is_reopened(app, monkeypatch, reason):
    import worklead.jobs.managed_auto as module
    runner, _ = runner_for(app)
    ticks, created = [0.0], []
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: ticks[0]))
    monkeypatch.setattr(runner, "_reopen_delay", lambda _: 0)
    def factory(path):
        value = Browser(path)
        created.append(value)
        return value
    def step(value):
        if len(created) == 2:
            runner._update(stage="ready", error_code=None)
            runner._stop.set()
        elif reason == "identity_not_ready":
            runner._update(stage="starting", error_code=reason)
        else:
            raise ManagedBrowserError(reason)
    def idle(*args, **kwargs):
        ticks[0] += 31
        return False
    runner.browser_factory = factory
    monkeypatch.setattr(runner, "_step", step)
    monkeypatch.setattr(runner, "_idle", idle)
    runner._run()
    assert len(created) == 2
    assert all(value.closed for value in created)
    assert ticks[0] >= module.UNREADY_SECONDS


def test_login_window_stays_open_while_waiting_for_user(app, monkeypatch):
    runner, browser = runner_for(app)
    browser.state = "login"
    created = []
    runner.browser_factory = lambda _: created.append(browser) or browser
    checks = []
    def idle(*args, **kwargs):
        checks.append(runner.status())
        if len(checks) == 6:
            runner._stop.set()
        return False
    monkeypatch.setattr(runner, "_idle", idle)
    runner._run()
    assert len(created) == 1
    assert all(s["stage"] == "login_required" and s["needs_user_action"] for s in checks)
    assert not auto_runtime(app).status().attempts


def test_pending_receipt_survives_browser_reopen_without_another_click(app, monkeypatch):
    add(app)
    runner, first = runner_for(app)
    second = Browser()
    created = []
    real_wait = runner._wait
    def wait(browser, expected, states, seconds=10):
        if browser is first and first.submits:
            raise ManagedBrowserError("browser_closed")
        return real_wait(browser, expected, states, seconds)
    def factory(_):
        value = first if not created else second
        created.append(value)
        return value
    step = runner._step
    def once(browser):
        if browser is second:
            assert runner._pending is not None
            runner._pending["next"] = 0
            second.already = True
        step(browser)
        if browser is second:
            runner._stop.set()
    runner.browser_factory = factory
    monkeypatch.setattr(runner, "_reopen_delay", lambda _: 0)
    monkeypatch.setattr(runner, "_wait", wait)
    monkeypatch.setattr(runner, "_step", once)
    runner._run()
    assert len(created) == 2
    assert first.submits == 1 and second.submits == second.opens == 0
    assert auto_runtime(app).status().today_submitted == 1
    assert auto_runtime(app).status().attempts[0].reason_code == "completion_reconciled"
