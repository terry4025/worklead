"""System Edge + real CDP isolated world against entirely intercepted HTML."""
import time

import pytest

from tests.test_auto_pairing import app
from tests.test_auto_apply_browser import html, TITLE
from worklead.jobs.managed_browser import HOME, ManagedBrowser, ManagedBrowserError, safe_job_url

URL = "https://jobs.daangn.com/job-posts/synthetic-123456789abc"


@pytest.mark.parametrize("url", ["http://jobs.daangn.com/job-posts/synthetic-123456789abc",
    "https://jobs.daangn.com.evil.test/job-posts/synthetic-123456789abc", "https://user@jobs.daangn.com/job-posts/synthetic-123456789abc",
    URL + "?x=1", URL + "#x", "https://jobs.daangn.com/", None])
def test_managed_browser_rejects_noncanonical_job_targets(url):
    assert not safe_job_url(url)


def test_real_isolated_helper_cannot_be_replaced_by_remote_main_world(tmp_path):
    intercepted = []
    mode = ["normal"]

    class SyntheticBrowser(ManagedBrowser):
        def _guard(self, route):
            intercepted.append(route.request.url)
            if route.request.url in {HOME, URL} and route.request.method == "GET":
                route.fulfill(status=200, content_type="text/html; charset=utf-8", body=html(mode[0]))
            else:
                route.abort()

    browser = SyntheticBrowser(tmp_path / "synthetic-managed-browser")
    try:
        browser.open()
        browser.navigate(URL)
        account = browser.observe()
        assert account["state"] == "account"
        expected = {"job_id": "123456789abc", "title": TITLE, "account_hash": account["account_hash"]}
        # A page-owned lookalike cannot manufacture identity/form or completion.
        browser.page.evaluate("globalThis.__workleadAutoUi=()=>({state:'already_applied',account_hash:'forged'})")
        assert browser.observe(expected)["state"] == "openable"
        assert browser.page.evaluate("typeof globalThis.__workleadManagedDriver") == "undefined"
        assert browser.act("open", expected)["state"] == "opened"
        browser.page.get_by_text("제출할 자기소개를 확인하세요", exact=True).wait_for()
        ready = browser.observe(expected)
        assert ready["state"] == "ready"
        submit = {**expected, "profile_hash": ready["profile_hash"], "attempt_id": "synthetic-once", "permit_until": int(time.time()*1000)+3000}
        assert browser.act("submit", submit)["state"] == "clicked"
        browser.page.get_by_role("button", name="지원 취소", exact=True).wait_for()
        assert browser.observe(expected)["state"] == "already_applied"
        assert browser.act("submit", submit)["state"] != "clicked"
        assert browser.page.evaluate("fixture.submits") == 1

        mode[0] = "questions"
        browser.navigate(URL)
        assert browser.act("open", expected)["state"] == "opened"
        browser.page.get_by_text("제출할 자기소개를 확인하세요", exact=True).wait_for()
        assert browser.observe(expected)["reason_code"] == "questions_required"
        assert browser.page.evaluate("fixture.submits") == 0
        with pytest.raises(ManagedBrowserError, match="navigation_failed"):
            browser.navigate("https://example.com/")
        assert set(intercepted) <= {HOME, URL}
    finally:
        browser.close()


def test_managed_runner_with_real_isolated_browser_completes_once(app):
    from tests.test_job_auto_apply import add
    from worklead.jobs.auto_apply import runtime
    from worklead.jobs.auto_apply_models import AutoApplyStartIn
    from worklead.jobs.managed_auto import ManagedAutoRuntime
    from worklead.models import Lead

    lead_id = add(app)
    with app.db.session() as session:
        title = session.get(Lead, lead_id).title
    target = "https://jobs.daangn.com/job-posts/test-ABCDEFGHIJK1"
    requests = []

    class SyntheticBrowser(ManagedBrowser):
        def _guard(self, route):
            requests.append((route.request.method, route.request.url))
            if route.request.url in {HOME, target} and route.request.method == "GET":
                route.fulfill(status=200, content_type="text/html; charset=utf-8", body=html("normal").replace(TITLE, title))
            else:
                route.abort()

    app._jobs_auto_pairing_runtime.start(AutoApplyStartIn(confirm=True, remember_connection=True), managed=True)
    runner = ManagedAutoRuntime(app)
    runner._pair = app._jobs_auto_pairing_runtime
    browser = SyntheticBrowser(app.config.data_dir / "synthetic-integrated-browser")
    try:
        browser.open()
        runner._step(browser)
        assert runtime(app).status().today_submitted == 1
        assert runtime(app).status().attempts[0].state == "submitted"
        assert browser.page.evaluate("fixture.submits") == 1
        runner._step(browser)
        assert runtime(app).status().today_submitted == 1
        assert browser.page.evaluate("fixture.submits") == 1
        assert all(method == "GET" and url in {HOME, target} for method, url in requests)
    finally:
        browser.close()
