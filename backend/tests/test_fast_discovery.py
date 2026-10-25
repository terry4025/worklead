"""Bounded adaptive polling; synthetic public HTML only, no live submissions."""
from datetime import timedelta

from sqlalchemy import select

from tests.test_daangn import NOW
from tests.test_daangn_feed import site, collect, lists
from worklead.engine.freshness import prioritize_feed_tasks
from worklead.models import CollectionTask, Run, new_id
from worklead.sources.daangn import create


def plan(adapter):
    return adapter.plan_discovery([{"id": "dev", "keywords": ["프로그램"]}], NOW)


def test_daily_reserve_covers_rotating_hot_set_and_stays_below_host_cap():
    adapter = create()
    assert adapter.default_interval_minutes == 5
    assert adapter.min_interval_seconds == 6
    assert adapter.daily_request_budget == 5500
    assert adapter.daily_list_requests(5) == 4864
    assert adapter.remaining_list_requests(86400, 5) == 4973
    assert adapter.remaining_list_requests(3600, 5) >= 70 * 2 + 29 + 8 * 6
    assert adapter.remaining_list_requests(0, 5) == 0
    # Conservative startup reserve still leaves requests for fresh details.
    assert adapter.remaining_list_requests(86400, 5) < adapter.daily_request_budget


def test_new_core_anchor_gets_ten_minute_poll_without_repolling_cold_region(tmp_path):
    posts = [("priority0001", "프로그램 제작 의뢰", "PER_CASE", False, 1)]
    ctx, c, clock, calls = site(tmp_path, [[28, "신당동", "A"], [500, "영도", "A"]], {28: posts})
    first = collect(ctx, c)
    with ctx.db.session() as s:
        task = s.scalar(select(CollectionTask).where(CollectionTask.run_id == first.id, CollectionTask.region_scope == "feed-28"))
        assert task.checkpoint["new_core_candidates"] == 1
    calls.clear()
    clock[0] = NOW + timedelta(minutes=5)
    collect(ctx, c)
    assert lists(calls) == []
    clock[0] = NOW + timedelta(minutes=10)
    second = collect(ctx, c)
    assert lists(calls) == ["/s?regionId=28"]
    with ctx.db.session() as s:
        task = s.scalar(select(CollectionTask).where(CollectionTask.run_id == second.id, CollectionTask.region_scope == "feed-28"))
        assert task.checkpoint["new_core_candidates"] == 0  # duplicate is not productivity
        assert task.checkpoint["priority_anchor"] is True
    calls.clear()
    clock[0] = NOW + timedelta(minutes=15)
    collect(ctx, c)
    assert lists(calls) == []  # exact ten minutes; no five-minute slack
    clock[0] = NOW + timedelta(minutes=30)
    collect(ctx, c)
    assert set(lists(calls)) == {"/s?regionId=28", "/s?regionId=500"}
    ctx.db.dispose()


def test_overlap_counts_listing_only_once_globally(tmp_path):
    posts = [("overlap00001", "프로그램 제작 의뢰", "PER_CASE", False, 1)]
    ctx, c, _, _ = site(tmp_path, [[28, "신당동", "A"], [500, "영도", "A"]], {28: posts, 500: posts})
    first = collect(ctx, c)
    with ctx.db.session() as s:
        counts = [t.checkpoint.get("new_core_candidates", 0) for t in s.scalars(select(CollectionTask).where(
            CollectionTask.run_id == first.id, CollectionTask.region_scope.like("feed-%")))]
        assert sum(counts) == 1  # overlap is counted once, independent of sitemap
    ctx.db.dispose()


def test_unique_new_production_outranks_duplicates_and_hot_capacity_is_shared(tmp_path):
    anchors = [[100 + i, f"앵커{i}", "A"] for i in range(12)]
    ctx, _, _, _ = site(tmp_path, anchors, {})
    adapter = ctx.adapters["daangn-alba"]
    with ctx.db.session() as s:
        r = Run(id=new_id("run"), source_id=adapter.source_id, kind="discovery", state="succeeded")
        s.add(r); s.flush()
        for i in range(12):
            s.add(CollectionTask(id=new_id("task"), run_id=r.id, source_id=adapter.source_id,
                task_key=f"test:{i}", query_group="q", region_scope=f"feed-{100+i}", scan_cycle=1,
                state="done", result_count=1000 if i == 0 else 1,
                checkpoint={"new_core_candidates": 0 if i == 0 else i},
                updated_at=NOW - timedelta(hours=1)))
        s.flush()
        chosen = prioritize_feed_tasks(s, adapter, plan(adapter), NOW)
        hot = {t.region_scope for t in chosen.tasks if t.min_revisit_minutes == 10}
        assert hot == {f"feed-{i}" for i in range(104, 112)}
        for i in range(8):
            s.add(CollectionTask(id=new_id("task"), run_id=r.id, source_id=adapter.source_id,
                task_key=f"fast:{i}", query_group="q", region_scope=f"feed-{100+i}", scan_cycle=2,
                state="done", result_count=0, checkpoint={"priority_anchor": True}, updated_at=NOW))
        s.flush()
        changed_rank = prioritize_feed_tasks(s, adapter, plan(adapter), NOW + timedelta(minutes=5))
        assert not any(t.min_revisit_minutes == 10 for t in changed_rank.tasks)
        resumed = prioritize_feed_tasks(s, adapter, plan(adapter), NOW + timedelta(minutes=10))
        assert sum(t.min_revisit_minutes == 10 for t in resumed.tasks) == 8
    ctx.db.dispose()


def test_stale_candidate_history_is_not_promoted(tmp_path):
    ctx, _, _, _ = site(tmp_path, [[28, "신당동", "A"]], {})
    adapter = ctx.adapters["daangn-alba"]
    with ctx.db.session() as s:
        r = Run(id=new_id("run"), source_id=adapter.source_id, kind="discovery", state="succeeded")
        s.add(r); s.flush()
        s.add(CollectionTask(id=new_id("task"), run_id=r.id, source_id=adapter.source_id,
            task_key="old", query_group="q", region_scope="feed-28", scan_cycle=1,
            state="done", result_count=999, checkpoint={"new_core_candidates": 50}, updated_at=NOW - timedelta(days=31)))
        s.flush()
        assert all(t.min_revisit_minutes != 10 for t in prioritize_feed_tasks(s, adapter, plan(adapter), NOW).tasks)
    ctx.db.dispose()
