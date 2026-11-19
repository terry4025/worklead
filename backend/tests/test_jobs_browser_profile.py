"""Synthetic paths only: choose app sessions and exclude concurrent controllers."""
from pathlib import Path
from types import SimpleNamespace
import os
import subprocess
import sys
import threading

import pytest

from worklead.jobs.browser_profile import LOCK_NAME, ProfileLease, select_managed_profile
from worklead.jobs.messages import MessageBrowser


def test_fresh_selection_does_not_create_a_profile(tmp_path):
    assert select_managed_profile(tmp_path) == tmp_path / "jobs-auto-browser-profile"
    assert not list(tmp_path.iterdir())


def test_existing_worklead_default_is_selected_without_copying_files(tmp_path):
    legacy = tmp_path / "jobs-browser-profile"
    (legacy / "Default").mkdir(parents=True)
    marker = legacy / "Default" / "synthetic-session.txt"
    marker.write_text("fixture", encoding="utf-8")
    assert select_managed_profile(tmp_path) == legacy
    assert marker.read_text(encoding="utf-8") == "fixture"
    assert not (tmp_path / "jobs-auto-browser-profile").exists()


def test_incomplete_legacy_profile_is_not_used(tmp_path):
    (tmp_path / "jobs-browser-profile").mkdir()
    assert select_managed_profile(tmp_path).name == "jobs-auto-browser-profile"


@pytest.mark.parametrize("component", ["jobs-browser-profile", "Default"])
def test_reparse_legacy_directory_is_not_selected(tmp_path, monkeypatch, component):
    legacy = tmp_path / "jobs-browser-profile"
    default = legacy / "Default"
    default.mkdir(parents=True)
    original = Path.lstat
    target = legacy if component == "jobs-browser-profile" else default

    def marked(path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        if path == target:
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
        return info

    monkeypatch.setattr(Path, "lstat", marked)
    assert select_managed_profile(tmp_path).name == "jobs-auto-browser-profile"


def test_linked_data_root_is_rejected(tmp_path, monkeypatch):
    original = Path.lstat

    def marked(path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        if path == tmp_path:
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
        return info

    monkeypatch.setattr(Path, "lstat", marked)
    with pytest.raises(RuntimeError, match="^jobs_profile_unavailable$"):
        select_managed_profile(tmp_path)
    assert not ProfileLease(tmp_path / "synthetic-profile").acquire()


def test_profile_lease_is_exclusive_and_release_is_idempotent(tmp_path):
    first = ProfileLease(tmp_path / "synthetic-profile")
    second = ProfileLease(first.profile)
    assert first.acquire() and first.acquire()
    assert not second.acquire()
    lock = first.profile / LOCK_NAME
    assert lock.exists()
    first.release()
    first.release()
    assert lock.exists()  # Never unlink a file another process may already open.
    assert second.acquire()
    second.release()


def test_separate_process_cannot_acquire_a_live_profile(tmp_path):
    lease = ProfileLease(tmp_path / "synthetic-profile")
    script = "from pathlib import Path; from worklead.jobs.browser_profile import ProfileLease; import sys; x=ProfileLease(Path(sys.argv[1])); print(x.acquire()); x.release()"
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}

    def child():
        return subprocess.run([sys.executable, "-c", script, str(lease.profile)],
            capture_output=True, text=True, env=env, check=True, timeout=15).stdout.strip()

    try:
        assert lease.acquire()
        assert child() == "False"
    finally:
        lease.release()
    assert child() == "True"


def test_threads_have_exactly_one_owner(tmp_path):
    leases = [ProfileLease(tmp_path / "synthetic-profile") for _ in range(8)]
    start = threading.Barrier(len(leases))
    attempted = threading.Barrier(len(leases) + 1)
    finish = threading.Event()
    results = []

    def run(lease):
        start.wait(timeout=5)
        results.append(lease.acquire())
        attempted.wait(timeout=5)
        finish.wait(timeout=5)
        lease.release()

    threads = [threading.Thread(target=run, args=(lease,)) for lease in leases]
    for thread in threads:
        thread.start()
    attempted.wait(timeout=5)
    finish.set()
    for thread in threads:
        thread.join(timeout=5)
    assert results.count(True) == 1


def test_lock_open_failure_does_not_keep_the_process_owner(tmp_path, monkeypatch):
    import worklead.jobs.browser_profile as module
    lease = ProfileLease(tmp_path / "synthetic-profile")
    original = module.os.open

    def denied(*_args, **_kwargs):
        raise PermissionError("synthetic private detail")

    monkeypatch.setattr(module.os, "open", denied)
    assert not lease.acquire()
    monkeypatch.setattr(module.os, "open", original)
    successor = ProfileLease(lease.profile)
    try:
        assert successor.acquire()
    finally:
        successor.release()


def test_message_browser_rejects_an_already_owned_profile_before_start(tmp_path):
    profile = tmp_path / "synthetic-profile"
    lease = ProfileLease(profile)
    assert lease.acquire()
    browser = MessageBrowser(profile)
    try:
        with pytest.raises(RuntimeError, match="^jobs_browser_busy$"):
            browser.open()
        assert browser.driver is browser.context is browser.page is None
    finally:
        browser.close()
        lease.release()


def test_message_browser_failed_launch_releases_lease(tmp_path, monkeypatch):
    import playwright.sync_api
    calls = []

    class FailedStart:
        def start(self):
            calls.append(True)
            raise RuntimeError("synthetic launch failure")

    monkeypatch.setattr(playwright.sync_api, "sync_playwright", FailedStart)
    browser = MessageBrowser(tmp_path / "synthetic-profile")
    with pytest.raises(RuntimeError, match="synthetic launch failure"):
        browser.open()
    successor = ProfileLease(browser.profile)
    try:
        assert successor.acquire()
        assert calls == [True]
    finally:
        successor.release()


def test_message_browser_close_releases_even_if_context_and_driver_fail(tmp_path):
    browser = MessageBrowser(tmp_path / "synthetic-profile")
    lease = ProfileLease(browser.profile)
    assert lease.acquire()
    browser._profile_lease = lease

    class BrokenContext:
        def close(self):
            raise RuntimeError("synthetic context failure")

    class BrokenDriver:
        def stop(self):
            raise RuntimeError("synthetic driver failure")

    browser.context, browser.driver = BrokenContext(), BrokenDriver()
    with pytest.raises(RuntimeError, match="synthetic driver failure"):
        browser.close()
    successor = ProfileLease(browser.profile)
    try:
        assert successor.acquire()
        assert browser.context is browser.driver is None
    finally:
        successor.release()
