"""After the app/PC was off, read the nationwide sitemap now instead of up to 12 h later."""
from datetime import UTC, datetime, timedelta

from worklead.engine.freshness import catch_up_after_gap
from worklead.models import Run

NOW = datetime(2026, 11, 9, 3, tzinfo=UTC)


def plan_for(ctx):
    return ctx.adapters["daangn-alba"].plan_discovery([{"id": "d", "keywords": ["프로그램"]}], NOW)


def add_run(ctx, finished):
    with ctx.db.session() as s:
        s.add(Run(id=f"r{finished:%H%M}", source_id="daangn-alba", kind="discovery", state="succeeded",
                  trigger="schedule", created_at=finished, started_at=finished, finished_at=finished))


def sitemap_minutes(plan):
    return next(t.min_revisit_minutes for t in plan.tasks if t.region_scope == "nationwide-sitemap")


def test_long_gap_reads_the_sitemap_now(ctx):
    add_run(ctx, NOW - timedelta(hours=3))
    with ctx.db.session() as s:
        plan = catch_up_after_gap(s, "daangn-alba", plan_for(ctx), NOW)
    assert sitemap_minutes(plan) == 0
    assert any("멈췄다가" in note for note in plan.notes)


def test_short_gap_keeps_the_twelve_hour_sitemap(ctx):
    add_run(ctx, NOW - timedelta(minutes=10))
    with ctx.db.session() as s:
        plan = catch_up_after_gap(s, "daangn-alba", plan_for(ctx), NOW)
    assert sitemap_minutes(plan) == 720


def test_first_run_ever_is_not_a_gap(ctx):
    with ctx.db.session() as s:
        plan = catch_up_after_gap(s, "daangn-alba", plan_for(ctx), NOW)
    assert sitemap_minutes(plan) == 720


def test_plan_without_sitemap_gets_no_note(ctx):
    add_run(ctx, NOW - timedelta(hours=3))
    plan = plan_for(ctx)
    plan.tasks = [t for t in plan.tasks if t.region_scope != "nationwide-sitemap"]
    tasks, notes = list(plan.tasks), list(plan.notes)
    with ctx.db.session() as s:
        plan = catch_up_after_gap(s, "daangn-alba", plan, NOW)
    assert plan.tasks == tasks
    assert plan.notes == notes
