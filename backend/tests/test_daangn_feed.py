"""Region feed discovery: synthetic store in the 2026-10-17 public HTML shape."""
from datetime import UTC, datetime, timedelta
import json
import math

import httpx
import pytest
from sqlalchemy import select

from tests.conftest import make_ctx, client_for
from tests.test_daangn import INDEX, JOBS, NOW, markup
from worklead.engine import runs
from worklead.engine.freshness import watch_reserve
from worklead.engine.worker import Worker
from worklead.models import DiscoveryListing, Run, Source
from worklead.sources.base import ParseError
from worklead.sources.daangn import create
from worklead.sources.daangn.adapter import _post_slug

TASKS = '["ETC","LIGHT_WORK","OFFICE_ASSISTANCE","TUTORING"]'
FEED_URL = "https://jobs.daangn.com/s?regionId=28&jobTasks=%5B%22ETC%22%2C%22LIGHT_WORK%22%2C%22OFFICE_ASSISTANCE%22%2C%22TUTORING%22%5D"


def feed(region, posts):
    """posts: (public_id, title, salaryType, closed, minutes_before_NOW)."""
    key = (f'searchedFeed(advertisement:false,filter:{{"jobTasks":{TASKS}}},first:20,'
           f'where:{{"region":{region},"regionRange":"RANGE_4","searchKeyword":""}})')
    store = {"client:root": {key: {"__ref": "feed"}}, "feed": {"edges": {"__refs": []}}}
    for i, (public_id, title, salary, closed, minutes) in enumerate(posts):
        store["feed"]["edges"]["__refs"].append(f"edge{i}")
        store[f"edge{i}"] = {"node": {"__ref": f"card{i}"}}
        store[f"card{i}"] = {"__typename": "FeedCard", "jobPost": {"__ref": f"job{i}"}}
        store[f"job{i}"] = {"__typename": "JobPost", "title": title, "salaryType": salary, "closed": closed,
                            "publishedAt": (NOW - timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z"),
                            "daangnPermalink": {"__ref": f"link{i}"}}
        store[f"link{i}"] = {"publicId": public_id}
    payload = json.dumps(json.dumps(store, ensure_ascii=False), ensure_ascii=False)
    return f"<html><body><script>window.__RELAY_STORE__ = {payload};</script></body></html>"


POSTS = [
    ("feedcore0001", "홈페이지 제작 의뢰 (워드프레스)", "PER_CASE", False, 1),
    ("feedcore0002", "프로그램 제작해주실 분", "HOURLY", False, 2),
    ("feedjobs0003", "홈페이지 관리 사무직", "MONTHLY", False, 3),
    ("feedjobs0004", "상세페이지 촬영 직원", "MONTHLY", False, 4),
    ("feedexpl0005", "상세페이지 수정", "PER_CASE", False, 5),
    ("feedclos0006", "홈페이지 제작 의뢰", "PER_CASE", True, 6),
    ("feedserv0007", "식당 서빙", "HOURLY", False, 7),
]


class Static:
    def __init__(self, text):
        self.text, self.urls = text, []

    def get(self, url, **kwargs):
        from worklead.engine.http import FetchResult
        self.urls.append(url)
        return FetchResult(url, url, 200, "ok", self.text)


def feed_task(adapter, region=28):
    adapter.profile["discovery"]["feed"]["anchors"] = [[region, "신당동", "A"]]
    return next(t for t in adapter.plan_discovery([{"id": "dev", "keywords": ["프로그램", "홈페이지"]}], NOW).tasks
                if t.region_scope == f"feed-{region}")


def test_feed_filters_titles_salary_and_closed_without_paging():
    adapter = create()
    fetcher = Static(feed(28, POSTS))
    found = adapter.discover(feed_task(adapter), fetcher)
    assert fetcher.urls == [FEED_URL]
    assert [r.source_post_id for r in found.refs] == ["feedcore0001", "feedcore0002"]
    assert [r.source_post_id for r in found.exploration_refs] == ["feedjobs0003", "feedexpl0005"]
    assert [r.priority for r in found.refs] == [1, 0]
    assert len(found.listed_refs) == 7 and found.next_cursor is None
    assert found.refs[0].url == "https://jobs.daangn.com/job-posts/홈페이지-제작-의뢰-워드프레스-feedcore0001"
    assert found.refs[0].source_updated_at == NOW - timedelta(minutes=1)
    meta = found.metadata
    assert (meta["closed_listings"], meta["employment_exploration_filtered"], meta["employment_core_demoted"]) == (1, 1, 1)
    assert meta["oldest_published_at"] == (NOW - timedelta(minutes=7)).isoformat()


def test_feed_rejects_other_region_or_changed_shape():
    adapter = create()
    with pytest.raises(ParseError):
        adapter.discover(feed_task(adapter), Static(feed(29, POSTS)))
    with pytest.raises(ParseError):
        adapter.discover(feed_task(adapter), Static(feed(28, POSTS).replace('\\"publicId\\": \\"feedcore0001\\"', '\\"publicId\\": \\"x/y\\"')))
    with pytest.raises(ParseError):
        adapter.discover(feed_task(adapter), Static(feed(28, POSTS).replace("TUTORING", "SERVING")))
    with pytest.raises(ParseError):
        adapter.discover(feed_task(adapter), Static("<html></html>"))


def test_slug_matches_public_detail_urls():
    assert _post_slug("10월3일(토)뚝섬역 맥주펍 홀서빙 도와주실분!!") == "10월3일-토-뚝섬역-맥주펍-홀서빙-도와주실분"
    assert _post_slug("Ai프로그램 활용잘하시는분") == "ai프로그램-활용잘하시는분"


def test_plan_tiers_and_daily_list_requests():
    adapter = create()
    adapter.profile["discovery"]["feed"]["anchors"] = [[28, "신당동", "A"], [500, "영도", "B"], [2000, "홍천", "C"]]
    tasks = adapter.plan_discovery([{"id": "dev", "keywords": ["프로그램"]}], NOW).tasks
    assert [(t.region_scope, t.min_revisit_minutes) for t in tasks] == [
        ("nationwide-sitemap", 720), ("feed-28", 0), ("feed-500", 60), ("feed-2000", 120)]
    # A every watch (period 15 min + 1.375 paced feeds x 7 s), B 24, C 12,
    # two sitemap visits of index + map
    assert adapter.daily_list_requests(15) == 135
    adapter.profile["discovery"]["feed"]["anchors"] *= 20  # 60 feeds: 9.6-minute watches
    assert adapter.daily_list_requests(15) == 20 * (24 + 12) + 4 + math.ceil(20 * 1440 / (15 + 27.5 * 7 / 60))


def site(tmp_path, anchors, posts_by_region):
    adapter = create()
    adapter.profile["discovery"]["feed"]["anchors"] = anchors
    ctx = make_ctx(tmp_path, adapters={adapter.source_id: adapter})
    clock = [NOW]
    ctx.clock = lambda: clock[0]
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\nDisallow: /api/\n")
        if request.url.path == "/sitemap.xml":
            return httpx.Response(200, text=INDEX)
        if request.url.path.startswith("/sitemaps/"):
            return httpx.Response(200, text=JOBS)
        if request.url.path == "/s":
            region = int(request.url.params["regionId"])
            return httpx.Response(200, text=feed(region, posts_by_region.get(region, [])))
        public_id = request.url.path.rsplit("-", 1)[1]
        return httpx.Response(200, text=markup("detail").replace("fixture00001", public_id))

    ctx.transport = httpx.MockTransport(handler)
    c = client_for(ctx)
    assert c.patch("/v1/sources/daangn-alba", json={"policy": {"status": "restricted", "basis": "합성 표본"}}).status_code == 200
    c.patch("/v1/settings", json={"query_groups": [{"id": "dev", "label": "개발", "enabled": True, "keywords": ["프로그램"]}]})
    return ctx, c, clock, calls


def collect(ctx, c):
    accepted = c.post("/v1/runs", json={"kind": "discovery", "source_id": "daangn-alba"}).json()
    assert Worker(ctx, "test", runs.COLLECTION_KINDS).run_once()
    with ctx.db.session() as s:
        run = s.get(Run, accepted["run_id"])
        s.expunge(run)
    assert run.state == "succeeded", (run.error_code, run.error_message)
    return run


def lists(calls):
    return [url.split("jobs.daangn.com")[1].split("&")[0] for url in calls
            if "/s?" in url or url.endswith("/sitemap.xml")]


def test_tiered_units_revisit_on_their_own_interval(tmp_path):
    ctx, c, clock, calls = site(tmp_path, [[28, "신당동", "A"], [500, "영도", "B"]], {})
    collect(ctx, c)
    assert lists(calls) == ["/s?regionId=28", "/s?regionId=500", "/sitemap.xml"]
    for minutes, expected in ((15, ["/s?regionId=28"]), (30, ["/s?regionId=28"]),
                              (60, ["/s?regionId=28", "/s?regionId=500"]), (75, ["/s?regionId=28"])):
        calls.clear()
        clock[0] = NOW + timedelta(minutes=minutes, seconds=40)
        collect(ctx, c)
        assert sorted(lists(calls)) == expected, minutes
    calls.clear()
    clock[0] = NOW + timedelta(hours=12, minutes=1)
    collect(ctx, c)
    assert "/sitemap.xml" in lists(calls)
    ctx.db.dispose()


def test_detail_allowance_grows_with_fresh_feed_candidates(tmp_path):
    posts = [(f"burst{i:07}", f"프로그램 제작 의뢰 {i}", "PER_CASE" if i % 2 else "HOURLY", False, i) for i in range(9)]
    ctx, c, clock, calls = site(tmp_path, [[28, "신당동", "A"]], {28: posts})
    run = collect(ctx, c)
    details = [url for url in calls if "/job-posts/" in url and "burst" in url]
    # Nine fresh feed candidates exceed the base four; all drain in this
    # watch, per-case first. The sitemap adds its own fresh candidate.
    assert len(details) == 9
    assert all(int(url[-1]) % 2 for url in details[:4])
    assert run.result["discovery"]["detail_requests_per_watch"] == 14
    with ctx.db.session() as s:
        row = s.scalar(select(DiscoveryListing).where(DiscoveryListing.source_post_id == "burst0000000"))
        assert row.source_updated_at == NOW and row.first_listed_at == NOW
    ctx.db.dispose()


def test_detail_allowance_is_capped(tmp_path):
    posts = [(f"burst{i:07}", f"프로그램 제작 의뢰 {i}", "PER_CASE", False, i) for i in range(20)]
    ctx, c, clock, calls = site(tmp_path, [[28, "신당동", "A"]], {28: posts})
    ctx.adapters["daangn-alba"].profile["discovery"]["detail_requests_per_watch_max"] = 10
    collect(ctx, c)
    assert len([url for url in calls if "/job-posts/" in url]) == 10  # feed and sitemap share the cap
    ctx.db.dispose()


def test_watch_reserve_uses_declared_tier_volume(tmp_path):
    ctx, c, clock, calls = site(tmp_path, [[28, "신당동", "A"], [500, "영도", "B"]], {})
    clock[0] = NOW.replace(hour=12)
    adapter = ctx.adapters["daangn-alba"]
    with ctx.db.session() as s:
        source = s.get(Source, "daangn-alba")
        source.auto_collect_enabled, source.interval_minutes = True, 15
        # half of the UTC day's (95 + 24 + 4) list requests remain
        assert watch_reserve(s, ctx, "daangn-alba", adapter) == math.ceil(adapter.daily_list_requests(15) / 2) == 62
    ctx.db.dispose()


def test_raised_source_budget_applies_to_todays_existing_window(tmp_path):
    from worklead.engine.http import Fetcher
    from worklead.models import HostBudget
    ctx = make_ctx(tmp_path)
    window = datetime.now(UTC).strftime("%Y-%m-%d")
    with ctx.db.session() as s:
        s.add(HostBudget(host="jobs.daangn.com", window=window, used=300, limit=300))
    fetcher = Fetcher(ctx.db, allowed_hosts={"jobs.daangn.com"}, user_agent="test", daily_budget=5000, min_interval_s=0)
    try:
        fetcher._reserve_budget("jobs.daangn.com")  # an upgrade mid-day must not stay capped at the old 300
        with ctx.db.session() as s:
            row = s.get(HostBudget, ("jobs.daangn.com", window))
            assert (row.used, row.limit) == (301, 5000)
    finally:
        fetcher.close()
    ctx.db.dispose()


def test_all_due_feeds_fit_one_watch_beyond_default_task_cap(tmp_path):
    anchors = [[1000 + i, f"앵커{i}", "A"] for i in range(70)]
    ctx, c, clock, calls = site(tmp_path, anchors, {})
    collect(ctx, c)
    assert len([u for u in lists(calls) if u.startswith("/s?")]) == 70
    calls.clear()
    clock[0] = NOW + timedelta(minutes=20)
    collect(ctx, c)
    assert len(lists(calls)) == 70  # every fast unit again, nothing left over
    ctx.db.dispose()



def test_interrupted_leftovers_do_not_hold_due_fast_units(tmp_path):
    from worklead.engine.executors import _current_cycle
    from worklead.models import CollectionTask, new_id
    ctx = make_ctx(tmp_path)
    with ctx.db.session() as s:
        run, _ = runs.enqueue(s, "discovery", source_id="daangn-alba")
        for scope, state in (("feed-28", "done"), ("feed-500", "pending")):
            s.add(CollectionTask(id=new_id("task"), run_id=run.id, source_id="daangn-alba", task_key=f"k{scope}",
                                 query_group="q", region_scope=scope, cursor=None, scan_cycle=5, depth=0,
                                 order_no=1, state=state))
        s.flush()
        keys = {"feed-28|q", "feed-500|q"}
        assert _current_cycle(s, "daangn-alba", keys) == 5  # untiered sources still finish the cycle first
        assert _current_cycle(s, "daangn-alba", keys, revisit=True) == 6
    ctx.db.dispose()
