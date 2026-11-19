"""Five-minute checks do not compound successful run duration or replay backlog."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from tests.test_daangn import NOW
from worklead.engine.schedule import next_due
from worklead.engine.worker import schedule_due
from worklead.models import Run, Source, SourcePolicy, new_id


def add(ctx, *, state="succeeded", source="daangn-alba", duration=3, retry=None):
    with ctx.db.session() as s:
        row = s.get(Source, source)
        row.interval_minutes = 5
        r = Run(id=new_id("run"), source_id=source, kind="discovery", state=state,
            created_at=NOW, started_at=NOW, finished_at=NOW + timedelta(minutes=duration), retry_after=retry)
        s.add(r)


def test_successful_adaptive_watch_uses_start_cadence_but_other_sources_use_finish(ctx):
    add(ctx)
    add(ctx, source="iboss")
    with ctx.db.session() as s:
        assert next_due(s, s.get(Source, "daangn-alba"), "discovery", NOW) == NOW + timedelta(minutes=5)
        assert next_due(s, s.get(Source, "iboss"), "discovery", NOW) == NOW + timedelta(minutes=8)


@pytest.mark.parametrize("state", ["partial", "failed", "cancelled"])
def test_unsuccessful_runs_keep_the_finish_wait(ctx, state):
    add(ctx, state=state)
    with ctx.db.session() as s:
        assert next_due(s, s.get(Source, "daangn-alba"), "discovery", NOW) == NOW + timedelta(minutes=8)


def test_host_cooldown_and_active_run_override_start_cadence(ctx):
    add(ctx, retry=NOW + timedelta(minutes=60))
    with ctx.db.session() as s:
        source = s.get(Source, "daangn-alba")
        assert next_due(s, source, "discovery", NOW) == NOW + timedelta(minutes=60)
        last = s.scalar(select(Run).where(Run.source_id == source.id))
        last.state = "running"
        assert next_due(s, source, "discovery", NOW) is None


def test_after_long_watch_or_sleep_scheduler_queues_one_watch_not_missed_intervals(ctx):
    add(ctx, duration=8)
    ctx.clock = lambda: NOW + timedelta(hours=2)
    with ctx.db.session() as s:
        source = s.get(Source, "daangn-alba")
        source.auto_collect_enabled = True
        s.get(SourcePolicy, source.id).status = "allowed"
    ids = schedule_due(ctx)
    with ctx.db.session() as s:
        assert len([r for r in (s.get(Run, id_) for id_ in ids) if r.kind == "discovery"]) == 1
    assert schedule_due(ctx) == []
