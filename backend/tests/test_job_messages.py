from datetime import datetime, timezone
from contextlib import contextmanager
from pathlib import Path
import threading
import pytest

from sqlalchemy import select

from worklead.jobs.messages import MessageRuntime, badge_count, record_count
from worklead.models import AppSetting, EventOutbox
from worklead.services.settings import put_setting

NOW = datetime(2026, 10, 16, 8, tzinfo=timezone.utc)


def test_exact_visible_badge_only():
    assert badge_count('채팅') == 0
    assert badge_count('채팅, 읽지 않은 메시지 12개') == 12
    for value in (None, '로그인', '채팅, 읽지 않은 메시지 ?개', '채팅, 읽지 않은 메시지 -1개', '채팅, 읽지 않은 메시지 12개 추가', '채팅, 읽지 않은 메시지 99999999999999개'):
        assert badge_count(value) is None


def test_increase_only_no_body_no_duplicate_baseline(ctx):
    with ctx.db.session() as s:
        assert not record_count(s, 9, NOW, previous=None)
        assert not record_count(s, 9, NOW, previous=9)
        assert not record_count(s, 3, NOW, previous=9)
        assert record_count(s, 4, NOW, previous=3)
    with ctx.db.session() as s:
        rows = list(s.scalars(select(EventOutbox).where(EventOutbox.type == 'notification.created')))
        assert len(rows) == 1
        assert rows[0].payload['kind'] == 'daangn_message'
        assert rows[0].payload['title'] == '당근알바 읽지 않은 메시지가 4개로 늘었습니다'
        assert set(rows[0].payload) == {'kind', 'title', 'lead_id', 'source_id'}
        assert s.get(AppSetting, 'jobs_message_serial').value == 1


def test_disabled_toggle_no_alert(ctx):
    with ctx.db.session() as s:
        put_setting(s, 'notifications', {'daangn_messages': False})
    with ctx.db.session() as s:
        record_count(s, 2, NOW, previous=1)
    with ctx.db.session() as s:
        assert not list(s.scalars(select(EventOutbox)))


def test_event_and_serial_rollback_together(ctx):
    with pytest.raises(RuntimeError):
        with ctx.db.session() as s:
            record_count(s, 2, NOW, previous=1)
            raise RuntimeError('rollback')
    with ctx.db.session() as s:
        assert not list(s.scalars(select(EventOutbox)))
        assert s.get(AppSetting, 'jobs_message_serial') is None
        record_count(s, 2, NOW, previous=1)
    with ctx.db.session() as s:
        assert s.get(AppSetting, 'jobs_message_serial').value == 1


def test_quiet_hours_keep_message_native_pending(ctx):
    from worklead.services.native_notifications import list_pending
    quiet = NOW.replace(hour=14)  # 23:00 KST.
    ctx.clock = lambda: quiet
    with ctx.db.session() as s:
        record_count(s, 2, quiet, previous=1)
    with ctx.db.session() as s:
        event = s.scalar(select(EventOutbox))
        assert event.suppressed_reason == 'quiet_hours'
        assert event.payload['native_delivery']['state'] == 'pending'
    assert list_pending(ctx)['backlog_count'] == 0
    ctx.clock = lambda: NOW.replace(day=17, hour=0)  # 09:00 KST.
    assert list_pending(ctx)['backlog_count'] == 1


def test_native_delivery_accepts_generic_message_kind(ctx):
    from worklead.services.native_notifications import list_pending, claim
    ctx.clock = lambda: NOW
    with ctx.db.session() as s:
        record_count(s, 2, NOW, previous=1)
    pending = list_pending(ctx)
    assert len(pending['items']) == 1
    assert pending['items'][0]['kind'] == 'daangn_message'
    result = claim(ctx, pending['items'][0]['id'])
    assert result['allowed'] is True


def test_message_routes_require_auth_and_explicit_start(client, ctx, monkeypatch):
    runtime = MessageRuntime(ctx)
    ctx._jobs_message_runtime = runtime
    started = []
    monkeypatch.setattr(runtime, 'start', lambda: started.append(True) or runtime.status())
    assert client.get('/v1/jobs/messages/status', headers={'Authorization': ''}).status_code == 401
    assert client.get('/v1/jobs/messages/status').json()['state'] == 'stopped'
    assert not started
    assert client.post('/v1/jobs/messages/start', json={}).status_code == 422
    assert client.post('/v1/jobs/messages/start', json={'confirm': False}).status_code == 422
    assert not started
    assert client.post('/v1/jobs/messages/start', json={'confirm': True}).status_code == 200
    assert started == [True]


def test_runtime_rebases_after_lost_surface_and_never_clicks(ctx):
    done = threading.Event()
    states = iter([('watching', 5, 'default'), ('watching', 6, 'default'),
                   ('login_required', None, 'unknown'), ('watching', 20, 'default'), ('closed', None, 'unknown')])

    class Browser:
        def __init__(self, profile): assert isinstance(profile, Path)
        def open(self): pass
        def sample(self): return next(states)
        def close(self): done.set()

    class Stop:
        def is_set(self): return False
        def wait(self, delay): return False

    ctx.clock = lambda: NOW
    runtime = MessageRuntime(ctx, browser_factory=Browser)
    runtime._stop = Stop()
    runtime._run()
    assert done.is_set()
    assert runtime.status().state == 'error'
    assert runtime.status().unread_count is None
    assert runtime.status().browser_open is False
    with ctx.db.session() as s:
        rows = list(s.scalars(select(EventOutbox)))
        assert len(rows) == 1  # 5 baseline -> 6 alert; re-login 20 baseline.
        assert '6개' in rows[0].payload['title']


def test_generic_failure_does_not_expose_browser_exception(ctx):
    class Browser:
        def __init__(self, _): pass
        def open(self): raise RuntimeError('secret phone body account-redirect-url')
        def close(self): pass

    runtime = MessageRuntime(ctx, browser_factory=Browser)
    runtime._run()
    status = runtime.status().model_dump_json()
    assert runtime.status().error_code == 'browser_unavailable'
    for secret in ('secret phone', 'account-redirect', 'RuntimeError'):
        assert secret not in status


def test_stop_closes_browser_on_its_owner_thread(ctx):
    sampled = threading.Event()
    closed = []
    owner = []

    class Browser:
        def __init__(self, _): pass
        def open(self): owner.append(threading.get_ident())
        def sample(self):
            sampled.set()
            return 'watching', 0, 'default'
        def close(self): closed.append(threading.get_ident())

    runtime = MessageRuntime(ctx, browser_factory=Browser)
    runtime.start()
    assert sampled.wait(5)
    status = runtime.stop()
    assert closed == owner
    assert owner[0] != threading.get_ident()
    assert status.state == 'stopped' and status.browser_open is False
    assert status.enabled is False and status.error_code is None


def test_restore_exclusive_collision_keeps_browser_alive_and_rebases_next_observation(ctx):
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    failures = []

    def hold_restore_gate():
        try:
            with ctx.db.gate.exclusive():
                entered.set()
                if not release.wait(3):
                    failures.append("restore gate release timeout")
        except Exception as exc:
            failures.append(type(exc).__name__)
        finally:
            finished.set()

    holder = threading.Thread(target=hold_restore_gate)
    observations, closed = [], []

    class Browser:
        def __init__(self, _): self.samples = 0
        def open(self): pass
        def sample(self):
            self.samples += 1
            if self.samples == 2:
                holder.start()
                assert entered.wait(3)
            if self.samples > 3:
                raise AssertionError("watcher did not stop after bounded samples")
            return 'watching', 5 if self.samples == 1 else 20, 'default'
        def close(self): closed.append(threading.get_ident())

    class Stop:
        def is_set(self): return False
        def wait(self, delay):
            status = runtime.status()
            observations.append((status.state, status.unread_count, status.browser_open, status.error_code))
            if len(observations) == 2:
                release.set()
                assert finished.wait(3)
            return len(observations) >= 3

    ctx.clock = lambda: NOW
    runtime = MessageRuntime(ctx, browser_factory=Browser)
    runtime._stop = Stop()
    try:
        runtime._run()
    finally:
        release.set()
        if holder.ident is not None:
            holder.join(timeout=4)
    assert not holder.is_alive() and not failures
    assert observations == [
        ('watching', 5, True, None),
        ('surface_unrecognized', None, True, None),
        ('watching', 20, True, None),
    ]
    assert len(closed) == 1 and runtime.status().error_code is None
    with ctx.db.session() as s:
        assert not list(s.scalars(select(EventOutbox)))  # 20 is a fresh baseline, never a false increase.


def test_generation_change_at_session_entry_rebases_inside_the_database_gate(ctx, monkeypatch):
    original = ctx.db.session
    entries = []

    @contextmanager
    def restore_completed_before_entry():
        # Reproduce completion after the previous outer generation check but
        # before the next DB read. Only the check inside the session sees it.
        if len(entries) == 1:
            with ctx.db.gate.exclusive():
                ctx.db.gate.generation += 1
        entries.append(True)
        with original() as session:
            yield session

    monkeypatch.setattr(ctx.db, 'session', restore_completed_before_entry)
    states = iter([('watching', 5, 'default'), ('watching', 20, 'default'), ('watching', 21, 'default')])
    counts = []

    class Browser:
        def __init__(self, _): pass
        def open(self): pass
        def sample(self): return next(states)
        def close(self): pass

    class Stop:
        def is_set(self): return False
        def wait(self, delay):
            counts.append(runtime.status().unread_count)
            return len(counts) >= 3

    ctx.clock = lambda: NOW
    runtime = MessageRuntime(ctx, browser_factory=Browser)
    runtime._stop = Stop()
    runtime._run()
    assert counts == [5, 20, 21] and runtime.status().error_code is None
    with original() as s:
        events = list(s.scalars(select(EventOutbox).where(EventOutbox.type == 'notification.created')))
        assert len(events) == 1
        assert events[0].payload['title'] == '당근알바 읽지 않은 메시지가 21개로 늘었습니다'
