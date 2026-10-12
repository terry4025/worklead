from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select

from tests.test_engine import setup, run_discovery
from tests.conftest import client_for
from worklead.engine import runs
from worklead.engine.http import Fetcher, RunHalt
from worklead.engine.worker import Worker, schedule_due
from worklead.models import Lead, Run, Source


def enabled(tmp_path, monkeypatch):
    ctx, behavior = setup(tmp_path)
    clock = [datetime(2026, 9, 28, 2, 59, tzinfo=UTC)]
    ctx.clock = lambda: clock[0]
    monkeypatch.setattr(runs, "utcnow", ctx.clock)
    with ctx.db.session() as s:
        source = s.get(Source, "fixture-site")
        source.auto_collect_enabled = True
        source.interval_minutes = 180
    return ctx, behavior, clock


def finish_initial(ctx):
    with ctx.db.session() as s:
        for run in s.scalars(select(Run)):
            runs.finish(ctx.db, run.id, "succeeded")


def test_interval_uses_last_run_not_wall_clock_bucket(tmp_path, monkeypatch):
    ctx, _, clock = enabled(tmp_path, monkeypatch)
    first = schedule_due(ctx)
    assert len(first) == 3
    finish_initial(ctx)
    # Cross a 3-hour wall-clock boundary one minute after the last run.
    clock[0] += timedelta(minutes=1)
    assert schedule_due(ctx) == []
    clock[0] += timedelta(hours=3)
    ids = schedule_due(ctx)
    assert len(ids) == 1
    with ctx.db.session() as s:
        assert s.get(Run, ids[0]).kind == "discovery"


def test_restart_keeps_enabled_and_catches_up_only_once(tmp_path, monkeypatch):
    ctx, _, clock = enabled(tmp_path, monkeypatch)
    assert len(schedule_due(ctx)) == 3
    finish_initial(ctx)
    ctx.db.dispose()
    restarted, _ = setup(tmp_path)
    clock[0] += timedelta(days=3)
    restarted.clock = lambda: clock[0]
    ids = schedule_due(restarted)
    assert len(ids) == 3
    assert schedule_due(restarted) == []
    with restarted.db.session() as s:
        assert s.get(Source, "fixture-site").auto_collect_enabled


def test_manual_discovery_delays_next_automatic_discovery(tmp_path, monkeypatch):
    ctx, _, clock = enabled(tmp_path, monkeypatch)
    with ctx.db.session() as s:
        manual, _ = runs.enqueue(s, "discovery", source_id="fixture-site")
        run_id = manual.id
    runs.finish(ctx.db, run_id, "succeeded")
    ids = schedule_due(ctx)
    with ctx.db.session() as s:
        assert {s.get(Run, rid).kind for rid in ids} == {"recheck_recent", "recheck_stale"}


def test_recheck_rate_limit_holds_already_queued_source_work(tmp_path, monkeypatch):
    ctx, _, clock = enabled(tmp_path, monkeypatch)
    ids = schedule_due(ctx)
    with ctx.db.session() as s:
        limited = next(s.get(Run, rid) for rid in ids if s.get(Run, rid).kind == "recheck_recent")
        limited.state = "partial"
        limited.retry_after = clock[0] + timedelta(hours=1)
        limited.finished_at = clock[0]
    assert schedule_due(ctx) == []
    assert runs.claim_next(ctx.db, "worker", runs.COLLECTION_KINDS, 60) is None
    clock[0] += timedelta(hours=1, seconds=1)
    assert runs.claim_next(ctx.db, "worker", runs.COLLECTION_KINDS, 60) is not None


def test_budget_exhaustion_has_persistent_next_day_retry(tmp_path):
    ctx, _ = setup(tmp_path)
    f = Fetcher(ctx.db, allowed_hosts={"fixture.test"}, user_agent="test", daily_budget=1, min_interval_s=0,
                transport=httpx.MockTransport(lambda r: httpx.Response(200, text="User-agent: *\nAllow: /\n")))
    try:
        f.get("https://fixture.test/jobs")
        raise AssertionError("Expected daily budget exhaustion")
    except RunHalt as e:
        assert e.code == "budget_exhausted"
        assert e.retry_after > datetime.now(UTC)
        assert e.retry_after.hour == e.retry_after.minute == e.retry_after.second == 0
    finally:
        f.close()


def test_enable_starts_once_and_disable_cancels_only_automatic_queue(tmp_path):
    ctx, _ = setup(tmp_path)
    c = client_for(ctx)
    for _ in range(2):
        assert c.patch('/v1/sources/fixture-site', json={'auto_collect_enabled': True}).status_code == 200
    with ctx.db.session() as s:
        automatic = list(s.scalars(select(Run).where(Run.kind == 'discovery')))
        assert len(automatic) == 1 and automatic[0].trigger == 'schedule'
        manual, _ = runs.enqueue(s, 'recheck_recent', source_id='fixture-site')
        manual_id = manual.id
    assert c.patch('/v1/sources/fixture-site', json={'auto_collect_enabled': False}).status_code == 200
    with ctx.db.session() as s:
        assert s.get(Run, automatic[0].id).state == 'cancelled'
        assert s.get(Run, manual_id).state == 'queued'


def test_closed_recheck_preserves_user_notes_and_does_not_duplicate(tmp_path):
    ctx, behavior = setup(tmp_path)
    run_discovery(ctx)
    c = client_for(ctx)
    lead = next(x for x in c.get('/v1/leads', params={'queue': 'all'}).json()['items'] if x['title'].startswith('쇼핑몰 주문'))
    assert lead['source_status'] == 'open'
    # The fixture source provides explicit structured recruitment state.
    with ctx.db.session() as s:
        saved = s.get(Lead, lead['id'])
        saved.memo = "협의 내역 유지"
        saved.user_mark = "interested"
    behavior.closed.add('101')
    response = c.post('/v1/leads/' + lead['id'] + '/recheck')
    assert response.status_code == 202
    Worker(ctx, 'interactive', runs.INTERACTIVE_KINDS).run_once()
    after = c.get('/v1/leads/' + lead['id']).json()
    assert after['memo'] == '협의 내역 유지' and after['user_mark'] == 'interested'
    assert after['id'] == lead['id']
    assert after['source_status'] == 'closed' and after['recommendation'] == 'excluded'
    assert len(c.get('/v1/leads', params={'queue': 'all'}).json()['items']) == 6


def test_seven_days_of_schedule_no_duplicate_jobs_or_leads(tmp_path, monkeypatch):
    ctx, _, clock = enabled(tmp_path, monkeypatch)
    from worklead.engine import http

    class ClockDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz) if tz else clock[0].replace(tzinfo=None)

    monkeypatch.setattr(http, 'datetime', ClockDateTime)
    monkeypatch.setattr(http, 'utcnow', ctx.clock)
    worker = Worker(ctx, 'week-simulation', runs.COLLECTION_KINDS)
    # Advance the application clock; every scheduled job uses the real worker,
    # persistent DB and synthetic HTTP source. This is not a real-week soak.
    for hour in range(0, 7 * 24 + 1, 3):
        if hour:
            clock[0] += timedelta(hours=3, seconds=1)
        created = schedule_due(ctx)
        assert len(created) == 1 + (hour % 6 == 0) + (hour % 24 == 0)
        assert schedule_due(ctx) == []
        for _ in created:
            assert worker.run_once()
        assert not worker.run_once()
        assert schedule_due(ctx) == []
    with ctx.db.session() as s:
        jobs = list(s.scalars(select(Run)))
        assert len(jobs) == 57 + 29 + 8
        assert all(r.state == 'succeeded' for r in jobs)
        assert len(list(s.scalars(select(Lead)))) == 6
        assert len({r.idempotency_key for r in jobs}) == len(jobs)


def test_expired_worker_lease_recovers_without_new_schedule_job(tmp_path, monkeypatch):
    ctx, _, clock = enabled(tmp_path, monkeypatch)
    ids = schedule_due(ctx)
    first = runs.claim_next(ctx.db, 'interrupted-worker', runs.COLLECTION_KINDS, 60)
    assert first is not None
    clock[0] += timedelta(seconds=61)
    assert runs.recover_expired(ctx.db) == 1
    assert schedule_due(ctx) == []
    worker = Worker(ctx, 'restarted-worker', runs.COLLECTION_KINDS)
    for _ in ids:
        assert worker.run_once()
    assert not worker.run_once()
    with ctx.db.session() as s:
        assert s.get(Run, first.id).state == 'succeeded'
        assert len(list(s.scalars(select(Run)))) == 3
