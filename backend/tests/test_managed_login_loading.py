"""Synthetic login waterfalls must finish without main-thread browser calls."""
import threading
import time

import pytest

from tests.test_auto_pairing import app
from worklead.jobs.auto_apply_models import AutoApplyStartIn
from worklead.jobs.managed_auto import ManagedAutoRuntime
from worklead.jobs.managed_browser import HOME, ManagedBrowser, ManagedBrowserError

AUTH = "https://accounts.daangn.com/synthetic-login-loading-test"


def test_login_assets_progress_during_long_poll_without_any_authentication_input(app):
    loaded = threading.Event()
    requests = []
    threads = []
    app._jobs_auto_pairing_runtime.start(AutoApplyStartIn(confirm=True, remember_connection=True), managed=True)

    class LoginBrowser(ManagedBrowser):
        def open(self):
            super().open()
            self.page.goto(AUTH, wait_until="domcontentloaded")

        def _guard(self, route):
            url = route.request.url
            requests.append(url)
            threads.append(threading.get_ident())
            if url == HOME:
                route.fulfill(status=200, content_type="text/html", body="<h1>Synthetic home</h1>")
            elif url == AUTH:
                route.fulfill(status=200, content_type="text/html", body="""<h1>Synthetic login shell</h1>
                <script>setTimeout(async()=>{
                  for(let n=1;n<=4;n++) await (await fetch('/synthetic-asset-'+n)).text();
                  await fetch('/synthetic-ready');
                },100)</script>""")
            elif url.startswith("https://accounts.daangn.com/synthetic-asset-"):
                route.fulfill(status=200, body="fixture")
            elif url == "https://accounts.daangn.com/synthetic-ready":
                route.fulfill(status=200, body="ready")
                loaded.set()
            else:
                route.abort()

    runner = ManagedAutoRuntime(app, browser_factory=LoginBrowser)
    try:
        runner.start()
        # No Playwright call is made from this test thread. A blocking Event.wait
        # in the owner thread leaves the routed waterfall stalled indefinitely.
        assert loaded.wait(4), "Login scripts stalled while the runner waited for user login"
        assert runner.status()["stage"] == "login_required"
        assert len([url for url in requests if "/synthetic-asset-" in url]) == 4
        assert len(set(threads)) == 1 and threads[0] != threading.get_ident()
        assert all(url.startswith(("https://jobs.daangn.com/", "https://accounts.daangn.com/synthetic-")) for url in requests)
        assert app.auto_pair_store.value["account"] is None
        assert not runner.auto.status().attempts
    finally:
        started = time.monotonic()
        runner.shutdown()
        assert time.monotonic() - started < 3


def test_idle_pumps_small_chunks_and_stops_without_finishing_poll(app):
    runner = ManagedAutoRuntime(app)
    calls = []

    class Browser:
        def pump(self, seconds):
            calls.append(seconds)
            runner._stop.set()

    assert runner._idle(Browser(), 5)
    assert len(calls) == 1 and 0 < calls[0] <= 0.25


def test_closed_browser_pump_uses_existing_reconnect_error(tmp_path):
    with pytest.raises(ManagedBrowserError, match="browser_closed"):
        ManagedBrowser(tmp_path / "synthetic-profile").pump(0.1)
