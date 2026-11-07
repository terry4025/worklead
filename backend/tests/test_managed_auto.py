"""The built-in runner reuses durable gates, without external browser traffic."""
import json
import threading
import time

import pytest
from sqlalchemy import select

from tests.test_auto_pairing import app
from tests.test_job_auto_apply import ACCOUNT, PROFILE, add
from worklead.jobs.applications import ApplicationError
from worklead.jobs.auto_apply import runtime as auto_runtime
from worklead.jobs.auto_apply_models import AutoApplyStartIn
from worklead.jobs.managed_auto import ManagedAutoRuntime, boot
from worklead.jobs.managed_browser import ManagedBrowserError
from worklead.models import AppSetting, JobAutoAttempt


class Browser:
    def __init__(self, profile=None):
        self.profile = profile
        self.state = "account"
        self.account = ACCOUNT
        self.profile_hash = PROFILE
        self.questions = False
        self.already = False
        self.fail_navigation = False
        self.opens = self.submits = 0
        self.closed = False
        self.thread_ids = []

    def open(self):
        self.thread_ids.append(threading.get_ident())

    def close(self):
        self.thread_ids.append(threading.get_ident())
        self.closed = True

    def navigate(self, url):
        if self.fail_navigation:
            raise ManagedBrowserError("navigation_failed")
        self.state = "already_applied" if self.already else "openable"

    def observe(self, expected=None):
        self.thread_ids.append(threading.get_ident())
        if self.state == "login":
            return {"state": "held", "reason_code": "login_required"}
        if expected is None:
            return {"state": "account", "account_hash": self.account}
        if self.questions and self.state == "ready":
            return {"state": "held", "reason_code": "questions_required", "account_hash": self.account}
        return {"state": self.state, "account_hash": self.account, "profile_hash": self.profile_hash}

    def act(self, operation, expected=None):
        self.thread_ids.append(threading.get_ident())
        if operation == "open":
            self.opens += 1
            self.state = "ready"
            return {"state": "opened"}
        if operation == "submit":
            self.submits += 1
            self.state = "already_applied"
            return {"state": "clicked"}
        raise AssertionError(operation)


def runner_for(app, browser=None):
    app._jobs_auto_pairing_runtime.start(AutoApplyStartIn(confirm=True, remember_connection=True), managed=True)
    runner = ManagedAutoRuntime(app)
    runner._pair = app._jobs_auto_pairing_runtime
    return runner, browser or Browser()


def test_first_login_needs_no_connection_code_and_resumes_when_account_appears(app):
    add(app)
    runner, browser = runner_for(app)
    browser.state = "login"
    runner._step(browser)
    assert runner.status()["stage"] == "login_required"
    assert app.auto_pair_store.value["desired"] is True
    assert app.auto_pair_store.value["account"] is None
    assert not auto_runtime(app).status().attempts
    browser.state = "account"
    runner._step(browser)
    assert browser.opens == browser.submits == 1
    assert auto_runtime(app).status().today_submitted == 1
    assert app.auto_pair_store.value["account"] == ACCOUNT
    assert app.auto_pair_store.value["profile"] == PROFILE
    runner._step(browser)
    assert browser.submits == 1
    assert all(secret not in json.dumps(runner.status()) for secret in (ACCOUNT, PROFILE, app.auto_pair_store.value["token"]))


@pytest.mark.parametrize("mode,reason", [("questions", "questions_required"), ("already", "already_applied")])
def test_questions_and_existing_application_do_not_submit(app, mode, reason):
    add(app)
    runner, browser = runner_for(app)
    setattr(browser, mode, True)
    runner._step(browser)
    status = auto_runtime(app).status()
    assert browser.submits == 0
    assert status.attempts[0].reason_code == reason


def test_changed_account_and_saved_introduction_pause_existing_approval(app):
    add(app)
    runner, browser = runner_for(app)
    runner._step(browser)
    add(app, key="ABCDEFGHIJK2")
    browser.profile_hash = "c" * 64
    runner._step(browser)
    assert browser.submits == 1
    assert auto_runtime(app).status().state == "paused"
    assert app.auto_pair_store.value["paused"] == "profile_changed"


def test_changed_account_does_not_rebind_grant(app):
    runner, browser = runner_for(app)
    runner._step(browser)
    browser.account = "c" * 64
    with pytest.raises(ApplicationError) as error:
        runner._step(browser)
    assert error.value.code == "account_changed"
    assert app.auto_pair_store.value["account"] == ACCOUNT
    assert app.auto_pair_store.value["paused"] == "account_changed"


def test_definite_navigation_failure_uses_existing_bounded_retry(app):
    add(app)
    runner, browser = runner_for(app)
    browser.fail_navigation = True
    with pytest.raises(ManagedBrowserError, match="navigation_failed"):
        runner._step(browser)
    attempt = auto_runtime(app).status().attempts[0]
    assert attempt.state == "held" and attempt.retry_after is not None
    assert browser.submits == 0


def test_stop_after_permit_does_not_click_or_resubmit(app, monkeypatch):
    add(app)
    runner, browser = runner_for(app)
    auto = auto_runtime(app)
    permit = auto.permit

    def stopped(body):
        result = permit(body)
        runner._stop.set()
        return result

    monkeypatch.setattr(auto, "permit", stopped)
    runner._step(browser)
    assert browser.submits == 0
    with app.db.session() as session:
        row = session.scalar(select(JobAutoAttempt))
        assert row.permitted_at is not None
    auto.shutdown()
    assert auto.status().attempts[0].state == "uncertain"


def test_missing_completion_stays_uncertain_and_never_repeats_click(app, monkeypatch):
    add(app)
    runner, browser = runner_for(app)
    observe = browser.observe

    def wait(expected_browser, expected, states, seconds=10):
        if browser.submits:
            return {"state": "held", "reason_code": "submission_unconfirmed", "account_hash": ACCOUNT}
        return observe(expected)

    monkeypatch.setattr(runner, "_wait", wait)
    runner._step(browser)
    assert auto_runtime(app).status().attempts[0].state == "uncertain"
    runner._step(browser)
    assert browser.submits == 1
    for _ in range(3):
        runner._pending["next"] = 0
        runner._step(browser)
    assert runner._pending is None
    assert browser.opens == browser.submits == 1
    assert auto_runtime(app).status().attempts[0].state == "uncertain"


def test_late_completion_reconciles_receipt_without_another_submit(app, monkeypatch):
    add(app)
    runner, browser = runner_for(app)
    real_wait = runner._wait
    calls = []

    def wait(value, expected, states, seconds=10):
        if browser.submits and not calls:
            calls.append(True)
            return {"state": "held", "reason_code": "submission_unconfirmed", "account_hash": ACCOUNT}
        return real_wait(value, expected, states, seconds)

    monkeypatch.setattr(runner, "_wait", wait)
    runner._step(browser)
    assert auto_runtime(app).status().uncertain == 1
    browser.already = True
    runner._pending["next"] = 0
    runner._step(browser)
    assert runner._pending is None
    assert auto_runtime(app).status().today_submitted == 1
    assert auto_runtime(app).status().attempts[0].reason_code == "completion_reconciled"
    assert browser.opens == browser.submits == 1


def test_browser_launch_failure_recovers_same_profile_without_new_setup(app, monkeypatch):
    import worklead.jobs.managed_auto as module
    runner, browser = runner_for(app)
    monkeypatch.setattr(module, "REOPEN_DELAYS", (0, 0, 0))
    created = []
    observed = threading.Event()

    class FirstFailure(Browser):
        def open(self):
            raise ManagedBrowserError("browser_unavailable")

    def factory(path):
        value = FirstFailure(path) if not created else browser
        created.append((path, value))
        return value

    runner.browser_factory = factory
    step = runner._step

    def once(value):
        step(value)
        observed.set()

    monkeypatch.setattr(runner, "_step", once)
    runner.start()
    assert observed.wait(3)
    runner.shutdown()
    assert len(created) == 2
    assert created[0][0] == created[1][0] == app.config.data_dir / "jobs-auto-browser-profile"
    assert all(value.closed for _, value in created)
    assert app.auto_pair_store.value["desired"] is True


def test_site_block_is_not_retried(app):
    runner, _ = runner_for(app)
    created = []

    class Blocked(Browser):
        def observe(self, expected=None):
            raise ManagedBrowserError("site_blocked")

    runner.browser_factory = lambda path: created.append(Blocked(path)) or created[-1]
    runner._run()
    assert len(created) == 1 and created[0].closed
    assert runner.status()["error_code"] == "site_blocked"


def test_runtime_browser_lifecycle_stays_on_one_owner_thread(app, monkeypatch):
    runner, browser = runner_for(app)
    runner.browser_factory = lambda path: browser
    observed = threading.Event()
    step = runner._step

    def once(value):
        step(value)
        observed.set()

    monkeypatch.setattr(runner, "_step", once)
    runner.start()
    assert observed.wait(3)
    runner.shutdown()
    assert browser.closed
    assert len(set(browser.thread_ids)) == 1
    assert browser.thread_ids[0] != threading.get_ident()
    assert runner.status()["stage"] == "stopped"
    assert app.auto_pair_store.value["desired"] is True


def test_healthy_connections_reset_recovery_budget_without_new_setup(app, monkeypatch):
    from types import SimpleNamespace
    import worklead.jobs.managed_auto as module
    runner, _ = runner_for(app)
    ticks = [0.0]
    created = []
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: ticks[0]))
    monkeypatch.setattr(module, "REOPEN_DELAYS", (0, 0, 0))
    monkeypatch.setattr(module, "POLL_SECONDS", 0)

    def factory(path):
        browser = Browser(path)
        browser.steps = 0
        created.append(browser)
        return browser

    def healthy_then_closed(browser):
        browser.steps += 1
        if browser.steps == 3:
            if len(created) == 5:
                runner._stop.set()
                return
            raise ManagedBrowserError("browser_closed")
        ticks[0] += module.RECOVERY_RESET_SECONDS + 1
        runner._update(stage="ready")

    runner.browser_factory = factory
    monkeypatch.setattr(runner, "_step", healthy_then_closed)
    runner._run()
    assert len(created) == 5
    assert all(browser.closed for browser in created)
    assert runner.status()["error_code"] != "browser_recovery_exhausted"


def test_boot_only_runs_persisted_enabled_approval(app, monkeypatch):
    assert boot(app) is False
    auto_runtime(app)
    with app.db.session() as session:
        session.add(AppSetting(key="job_auto_managed", value={"enabled": True}))
    assert boot(app) is False
    runner, _ = runner_for(app)
    app._jobs_managed_auto_runtime = runner
    starts = []
    monkeypatch.setattr(runner, "start", lambda: starts.append(True))
    assert boot(app) is True and starts == [True]
    app.config.demo = True
    assert boot(app) is False

