"""Real worker/SQLite/HTTP fixtures for bounded, fair discovery watches."""
from datetime import timedelta

from sqlalchemy import select

from tests.test_discovery_exploration_quality import site, collect
from tests.test_daangn import Cached, NOW, NS
from worklead.engine.freshness import observe_listings, ordered_candidates, ref_key, watch_reserve
from worklead.engine.worker import housekeeping
from worklead.engine.worker import Worker
from worklead.engine import runs
from worklead.models import DiscoveryListing, DetailFetchState, HostBudget, Lead, Run, Source, SourceRecord
from worklead.sources.base import DiscoverResult, PostRef
from worklead.sources.daangn import create


def test_named_page_request_promotes_without_broad_physical_jobs():
    adapter = create()
    titles = ["온라인 상세페이지 만들어주실수 있는분이요", "상품페이지 수정해주실 분", "소개페이지 제작 의뢰",
              "상세페이지 상품 포장 직원", "상품페이지 판매 직원", "소개페이지 구경하세요"]
    refs = [PostRef(f"https://jobs.daangn.com/job-posts/{title.replace(' ', '-')}-fresh{i:07}", f"fresh{i:07}")
            for i, title in enumerate(titles)]
    xml = f'<urlset {NS}>' + ''.join(f'<url><loc>{r.url}</loc></url>' for r in refs) + '</urlset>'
    task = adapter.plan_discovery([{"id": "web", "keywords": ["홈페이지"]}], NOW).tasks[0]
    result = adapter.discover(task, Cached(xml))
    assert {r.title_hint for r in result.refs} == set(titles[:3])
    assert len(result.listed_refs) == 6
    excel = adapter.plan_discovery([{"id": "excel", "keywords": ["엑셀"]}], NOW).tasks[0]
    assert adapter.discover(excel, Cached(xml)).refs == []


def incremental(site):
    ctx, adapter, calls, _ = site
    adapter.profile['discovery']['detail_requests_per_watch'] = 4
    original = adapter.discover
    filtered = PostRef(f"https://jobs.fixture.test/jobs/999", "999", "식당 서빙")
    def listing(spec, fetcher):
        result = original(spec, fetcher)
        result.listed_refs = result.refs + result.exploration_refs + [filtered]
        return result
    adapter.discover = listing
    return ctx, adapter, calls


def test_public_metadata_survives_title_change_and_includes_filtered(site):
    ctx, adapter, calls = incremental(site)
    first = collect(ctx)
    with ctx.db.session() as s:
        row = s.get(DiscoveryListing, (adapter.source_id, ref_key(PostRef('', '999'))))
        assert row.selection == 'filtered' and row.first_listed_at == ctx.clock()
        initial_digest = row.title_digest
        initial_time = row.first_listed_at
    later = ctx.clock() + timedelta(hours=1)
    ctx.clock = lambda: later
    observe_listings(ctx, adapter.source_id, DiscoverResult([], listed_refs=[PostRef(
        'https://jobs.fixture.test/jobs/new-999', '999', '제목 변경')]))
    with ctx.db.session() as s:
        row = s.get(DiscoveryListing, (adapter.source_id, ref_key(PostRef('', '999'))))
        assert row.first_listed_at == initial_time
        assert row.last_listed_at == later and row.title_digest != initial_digest
    assert first.result['discovery']['listing_digest']


def test_four_request_watch_reserves_weak_slot_and_rotates_unseen(site, monkeypatch):
    ctx, adapter, calls = incremental(site)
    first_core = adapter.core[0]
    adapter.core = [PostRef(f'https://jobs.fixture.test/jobs/{100+i}', str(100+i), first_core.title_hint,
                            first_core.source_updated_at - timedelta(minutes=i)) for i in range(8)]
    from worklead.engine.http import Fetcher
    paced = []
    original_pace = Fetcher._pace
    def observe_pace(self, host):
        paced.append(host)
        return original_pace(self, host)
    monkeypatch.setattr(Fetcher, '_pace', observe_pace)
    first = collect(ctx)
    details = [url for url in calls if url.startswith('/jobs/')]
    assert details == ['/jobs/100', '/jobs/101', '/jobs/102', '/jobs/200']
    assert first.state == 'succeeded'
    # robots plus the four actual details only: no sleeps for deferred candidates.
    assert len(paced) <= 5
    assert first.result['discovery']['watch_detail_requests'] == 4
    assert first.result['detail_progress']['done'] == 4
    assert first.result['detail_progress']['total'] > first.result['detail_progress']['done']
    calls.clear()
    second = collect(ctx)
    details = [url for url in calls if url.startswith('/jobs/')]
    assert details == ['/jobs/103', '/jobs/104', '/jobs/105', '/jobs/201']
    assert second.result['discovery']['watch_deferred_details'] > 0


def test_remaining_list_watch_budget_is_protected(site):
    ctx, adapter, calls = incremental(site)
    instant = ctx.clock().replace(hour=12, minute=0, second=0, microsecond=0)
    ctx.clock = lambda: instant
    with ctx.db.session() as s:
        source = s.get(Source, adapter.source_id)
        source.auto_collect_enabled, source.interval_minutes = True, 30
        assert watch_reserve(s, ctx, adapter.source_id, adapter) == 46
        budget = s.scalar(select(HostBudget))
        budget.used = 253  # list uses one, remaining 46 must survive
    run = collect(ctx)
    assert calls == ['/listing']
    assert run.state == 'succeeded' and run.result['discovery']['watch_detail_requests'] == 0
    with ctx.db.session() as s:
        assert s.scalar(select(HostBudget.used)) == 254
        assert not list(s.scalars(select(DetailFetchState)))


def test_stale_unlinked_public_metadata_expires_after_30_days(site):
    ctx, adapter, _ = incremental(site)
    collect(ctx)
    now = ctx.clock() + timedelta(days=31)
    ctx.clock = lambda: now
    housekeeping(ctx)
    with ctx.db.session() as s:
        assert s.get(DiscoveryListing, (adapter.source_id, ref_key(PostRef('', '999')))) is None
        assert s.get(DiscoveryListing, (adapter.source_id, ref_key(adapter.core[0]))) is not None


def test_existing_backlog_refresh_is_bounded_and_gap_is_recorded(site):
    ctx, adapter, calls = incremental(site)
    original = adapter.core[0]
    adapter.core = [PostRef(f'https://jobs.fixture.test/jobs/{100+i}', str(100+i), original.title_hint,
                            original.source_updated_at - timedelta(minutes=i)) for i in range(3)]
    adapter.extra = []
    first = collect(ctx)
    assert first.counts['details_fetched'] == 3
    later = ctx.clock() + timedelta(days=3)
    ctx.clock = lambda: later
    calls.clear()
    second = collect(ctx)
    assert len([path for path in calls if path.startswith('/jobs/')]) == 1
    assert second.result['discovery']['watch_refresh_requests'] == 1
    assert second.result['collection_timing']['previous_finished_at'] == first.finished_at.isoformat()
    assert second.result['collection_timing']['gap_seconds'] > 2 * 24 * 3600


def test_automatic_recheck_preserves_future_watch_requests(site):
    ctx, adapter, calls = incremental(site)
    instant = ctx.clock().replace(hour=12, minute=0, second=0, microsecond=0)
    ctx.clock = lambda: instant
    collect(ctx)
    with ctx.db.session() as s:
        source = s.get(Source, adapter.source_id)
        source.auto_collect_enabled, source.interval_minutes = True, 30
        lead = s.scalar(select(Lead).where(Lead.recommendation == 'needs_review'))
        lead.last_checked_at = instant - timedelta(hours=13)
        s.get(SourceRecord, lead.primary_record_id).last_checked_at = lead.last_checked_at
        s.scalar(select(HostBudget)).used = 254
        recheck, _ = runs.enqueue(s, 'recheck_recent', source_id=adapter.source_id)
        run_id = recheck.id
    calls.clear()
    assert Worker(ctx, 'reserved-recheck', runs.COLLECTION_KINDS).run_once()
    with ctx.db.session() as s:
        run = s.get(Run, run_id)
        assert run.state == 'succeeded' and run.result['discovery_budget_reserved'] is True
        assert run.result['rechecks_deferred'] == 1
    assert calls == []


def test_new_list_id_precedes_unfetched_backlog_even_without_lastmod(site):
    ctx, adapter, _ = incremental(site)
    old = PostRef('https://jobs.fixture.test/jobs/old', 'old', '홈페이지 제작 요청', ctx.clock())
    observe_listings(ctx, adapter.source_id, DiscoverResult([old], listed_refs=[old]))
    later = ctx.clock() + timedelta(minutes=30)
    ctx.clock = lambda: later
    new = PostRef('https://jobs.fixture.test/jobs/new', 'new', '홈페이지 제작 요청')
    result = DiscoverResult([old, new], listed_refs=[old, new])
    observe_listings(ctx, adapter.source_id, result)
    assert [ref.source_post_id for _, ref, _ in ordered_candidates(ctx, adapter.source_id, result)] == ['new', 'old']


def test_old_automatic_rechecks_cannot_spend_more_than_two_requests(site):
    ctx, adapter, calls = incremental(site)
    adapter.profile['discovery']['recheck_requests_per_run'] = 2
    original = adapter.core[0]
    adapter.core = [PostRef(f'https://jobs.fixture.test/jobs/{100+i}', str(100+i), original.title_hint,
                            original.source_updated_at - timedelta(minutes=i)) for i in range(3)]
    adapter.extra = []
    collect(ctx)
    with ctx.db.session() as s:
        # Seed three stale, reviewable targets to exercise scheduling quotas;
        # classification behavior is covered independently by source fixtures.
        for lead in s.scalars(select(Lead)):
            lead.recommendation = 'needs_review'
            lead.last_checked_at = ctx.clock() - timedelta(hours=13)
        run, _ = runs.enqueue(s, 'recheck_recent', source_id=adapter.source_id)
        run_id = run.id
    calls.clear()
    assert Worker(ctx, 'bounded-recheck', runs.COLLECTION_KINDS).run_once()
    with ctx.db.session() as s:
        run = s.get(Run, run_id)
        assert run.counts['requests'] == 2
        assert run.result['rechecks_deferred'] == 1
    assert len([path for path in calls if path.startswith('/jobs/')]) == 2
