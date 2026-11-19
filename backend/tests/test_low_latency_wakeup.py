"""Committed discovery wakes support immediately; due times are not rounded."""
from datetime import timedelta

from tests.test_auto_pairing import app
from tests.test_job_auto_apply import NOW
from worklead.engine.events import EventHub
from worklead.engine.schedule import scheduler_delay
from worklead.jobs.managed_auto import ManagedAutoRuntime
from worklead.models import Run, Source, SourcePolicy, new_id


def test_event_hub_versions_include_notifications_before_wait():
    hub = EventHub()
    before = hub.current_version()
    hub.notify()
    assert hub.current_version() == before + 1
    assert hub.wait(before, 0) == before + 1


def test_committed_candidate_wakes_browser_without_waiting_five_seconds(app):
    runner = ManagedAutoRuntime(app)
    seen = app.hub.current_version()
    calls = []

    class Browser:
        def pump(self, seconds):
            calls.append(seconds)
            app.hub.notify()  # Same post-commit signal as discovery ingestion.

    assert runner._idle(Browser(), 5, seen_events=seen) is False
    assert len(calls) == 1 and 0 < calls[0] <= 0.25
    assert not runner._stop.is_set()


def test_notification_during_processing_is_not_lost_before_idle(app):
    runner = ManagedAutoRuntime(app)
    seen = app.hub.current_version()
    app.hub.notify()

    class Browser:
        def pump(self, _):
            raise AssertionError("An already committed candidate should not sleep")

    assert runner._idle(Browser(), 5, seen_events=seen) is False


def _due_source(app, seconds):
    app.clock = lambda: NOW
    with app.db.session() as session:
        for source in session.query(Source):
            source.auto_collect_enabled = False
        source = session.get(Source, "daangn-alba")
        source.auto_collect_enabled = True
        source.interval_minutes = 5
        session.get(SourcePolicy, source.id).status = "allowed"
        for kind, interval in (("discovery", timedelta(minutes=5)),
                               ("recheck_recent", timedelta(hours=6)),
                               ("recheck_stale", timedelta(hours=24))):
            at = NOW - interval + timedelta(seconds=seconds)
            session.add(Run(id=new_id("run"), source_id=source.id, kind=kind, state="succeeded",
                created_at=at, started_at=at, finished_at=at))


def test_scheduler_wakes_at_exact_nearby_due_time(app):
    _due_source(app, 2.5)
    assert scheduler_delay(app) == 2.5


def test_scheduler_keeps_regular_sleep_when_no_work_is_near(app):
    _due_source(app, 200)
    assert scheduler_delay(app) == app.config.scheduler_interval_seconds


def test_scheduler_cooldown_and_disabled_collection_do_not_spin(app):
    _due_source(app, 2)
    with app.db.session() as session:
        run = session.query(Run).filter_by(source_id="daangn-alba", kind="discovery").one()
        run.retry_after = NOW + timedelta(hours=1)
    assert scheduler_delay(app) == app.config.scheduler_interval_seconds
    with app.db.session() as session:
        session.get(Source, "daangn-alba").auto_collect_enabled = False
    assert scheduler_delay(app) == app.config.scheduler_interval_seconds


def test_scheduler_bounds_past_due_work_without_advancing_requests(app):
    _due_source(app, -10)
    assert scheduler_delay(app) == 1
