from datetime import timedelta

from tests.test_low_latency_wakeup import _due_source
from tests.test_auto_pairing import app
from tests.test_job_auto_apply import NOW
from worklead.engine.schedule import next_due, scheduler_delay
from worklead.engine.worker import schedule_due
from worklead.models import Run, Source


def test_early_region_retry_wakes_at_recorded_time_without_waiting_five_minutes(app):
    _due_source(app, 250)
    with app.db.session() as s:
        run = s.query(Run).filter_by(source_id="daangn-alba", kind="discovery").one()
        run.state = "partial"
        run.result = {"regional_retry_after": (NOW + timedelta(seconds=8)).isoformat()}
        run.error_code = "tasks_failed"
    assert scheduler_delay(app) == 8


def test_early_region_retry_never_bypasses_host_cooldown(app):
    _due_source(app, 250)
    with app.db.session() as s:
        run = s.query(Run).filter_by(source_id="daangn-alba", kind="discovery").one()
        run.result = {"regional_retry_after": NOW.isoformat()}
        run.retry_after = NOW + timedelta(hours=1)
        assert next_due(s, s.get(Source, "daangn-alba"), "discovery", NOW) == run.retry_after


def test_active_region_run_remains_exclusive(app):
    _due_source(app, 250)
    with app.db.session() as s:
        run = s.query(Run).filter_by(source_id="daangn-alba", kind="discovery").one()
        run.state = "running"
        run.result = {"regional_retry_after": NOW.isoformat()}
        assert next_due(s, s.get(Source, "daangn-alba"), "discovery", NOW) is None


def test_bad_region_deadline_keeps_regular_schedule(app):
    _due_source(app, 250)
    with app.db.session() as s:
        run = s.query(Run).filter_by(source_id="daangn-alba", kind="discovery").one()
        for value in ("bad", "2026-11-02T00:00:00", 4, None):
            run.result = {"regional_retry_after": value}
            assert next_due(s, s.get(Source, "daangn-alba"), "discovery", NOW) > NOW


def test_short_retry_enqueues_once_even_inside_same_scheduler_bucket(app):
    _due_source(app, 250)
    with app.db.session() as s:
        previous = s.query(Run).filter_by(source_id="daangn-alba", kind="discovery").one()
        previous.idempotency_key = f"sched:daangn-alba:discovery:{int(NOW.timestamp() // 300)}"
        previous.result = {"regional_retry_after": NOW.isoformat()}
        previous_id = previous.id
    created = schedule_due(app)
    assert len(created) == 1
    assert schedule_due(app) == []
    with app.db.session() as s:
        queued = s.get(Run, created[0])
        assert queued.id != previous_id
        assert queued.idempotency_key == f"regional:daangn-alba:{previous_id}"
