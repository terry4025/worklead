"""Failure recovery, request fairness and accelerated-poll budget boundaries."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from tests.test_daangn_feed import NOW, collect, lists, site
from tests.test_fast_discovery import plan
from worklead.engine import runs
from worklead.engine.freshness import prioritize_feed_tasks
from worklead.engine.schedule import next_due
from worklead.engine.worker import Worker
from worklead.models import CollectionTask, CoverageUnit, Run, Source, new_id
from worklead.sources.base import ParseError
from worklead.sources.profiled import TaskFailed


def finish(ctx, client):
    accepted = client.post('/v1/runs', json={'kind': 'discovery', 'source_id': 'daangn-alba'}).json()
    assert Worker(ctx, 'recovery-test', runs.COLLECTION_KINDS).run_once()
    with ctx.db.session() as s:
        result = s.get(Run, accepted['run_id'])
        s.expunge(result)
    return result


def test_failed_feed_keeps_success_time_and_retries_twice_then_backs_off(tmp_path, monkeypatch):
    ctx, client, clock, calls = site(tmp_path, [[28, '서울', 'A']], {})
    try:
        collect(ctx, client)
        adapter = ctx.adapters['daangn-alba']
        discover = adapter.discover
        def fail(spec, fetcher):
            if spec.region_scope == 'feed-28':
                raise TaskFailed('network_error', 'synthetic transient')
            return discover(spec, fetcher)
        monkeypatch.setattr(adapter, 'discover', fail)
        clock[0] = NOW + timedelta(minutes=31)
        for count, delay in ((1, 60), (2, 120), (3, 1800)):
            result = finish(ctx, client)
            assert result.state == 'partial'
            with ctx.db.session() as s:
                unit = s.scalar(select(CoverageUnit).where(CoverageUnit.region_scope == 'feed-28'))
                assert unit.last_visited_at == NOW  # Failure is not a successful visit.
                task = s.scalar(select(CollectionTask).where(CollectionTask.run_id == result.id,
                    CollectionTask.region_scope == 'feed-28', CollectionTask.depth == 0))
                assert task.checkpoint['feed_failure_count'] == count
                assert task.checkpoint['feed_transient_retry'] is (count <= 2)
                if count <= 2:
                    assert next_due(s, s.get(Source, 'daangn-alba'), 'discovery', clock[0]) == clock[0] + timedelta(seconds=delay)
            clock[0] += timedelta(seconds=delay + 1)
        # The third failure must not be retried on every five-minute watch.
        clock[0] -= timedelta(minutes=25)
        calls.clear()
        collect(ctx, client)
        assert not lists(calls)
    finally:
        ctx.db.dispose()


def test_parse_error_does_not_use_fast_transient_retry(tmp_path, monkeypatch):
    ctx, client, clock, calls = site(tmp_path, [[28, '서울', 'A']], {})
    try:
        collect(ctx, client)
        def fail(*_):
            raise ParseError('synthetic structural change')
        monkeypatch.setattr(ctx.adapters['daangn-alba'], 'discover', fail)
        clock[0] = NOW + timedelta(minutes=31)
        result = finish(ctx, client)
        assert 'regional_retry_after' not in (result.result or {})
        clock[0] += timedelta(minutes=5)
        calls.clear()
        collect(ctx, client)
        assert not lists(calls)
    finally:
        ctx.db.dispose()


def test_other_regions_are_seen_after_at_most_two_immediate_details(tmp_path):
    posts = [(f'fairx{i:07}', f'프로그램 제작 의뢰 {i}', 'PER_CASE', False, i) for i in range(8)]
    ctx, client, _, calls = site(tmp_path, [[28, '서울', 'A'], [500, '부산', 'A']], {28: posts})
    try:
        collect(ctx, client)
        first = next(i for i, url in enumerate(calls) if '/s?regionId=28&' in url)
        second = next(i for i, url in enumerate(calls) if '/s?regionId=500&' in url)
        assert len([url for url in calls[first:second] if '/job-posts/' in url]) <= 2
        assert len([url for url in calls if '/job-posts/' in url and 'fairx' in url]) == 8
        with ctx.db.session() as s:
            drains = list(s.scalars(select(CollectionTask).where(CollectionTask.cursor.like('detail-drain:%'))))
            assert len(drains) == 1 and drains[0].state == 'done'
    finally:
        ctx.db.dispose()


def test_full_changing_feed_is_revisited_in_five_minutes(tmp_path):
    posts = [(f'fullx{i:07}', '식당 직원 모집', 'HOURLY', False, i) for i in range(20)]
    ctx, client, clock, calls = site(tmp_path, [[28, '서울', 'A']], {28: posts})
    try:
        collect(ctx, client)
        clock[0] = NOW + timedelta(minutes=5)
        calls.clear()
        collect(ctx, client)
        assert lists(calls) == ['/s?regionId=28']
        clock[0] += timedelta(minutes=5)
        calls.clear()
        collect(ctx, client)
        assert lists(calls) == []  # Same 20 cards do not perpetuate saturation.
    finally:
        ctx.db.dispose()


def test_saturated_failed_and_hot_polls_share_the_slot_allowance_including_failures(tmp_path):
    ctx, _, _, _ = site(tmp_path, [[i, str(i), 'A'] for i in range(100, 112)], {})
    adapter = ctx.adapters['daangn-alba']
    try:
        with ctx.db.session() as s:
            run = Run(id=new_id('run'), source_id=adapter.source_id, kind='discovery', state='succeeded')
            s.add(run); s.flush()
            for i in range(12):
                s.add(CollectionTask(id=new_id('task'), run_id=run.id, source_id=adapter.source_id,
                    task_key=f'full:{i}', query_group='q', region_scope=f'feed-{100+i}', scan_cycle=1,
                    state='done', checkpoint={'new_core_candidates': i+1, 'new_listed_entries': 20, 'feed_saturated': True},
                    updated_at=NOW-timedelta(minutes=5)))
            for i in range(9):  # max_hot_anchors (10) - 1
                s.add(CollectionTask(id=new_id('task'), run_id=run.id, source_id=adapter.source_id,
                    task_key=f'slot:{i}', query_group='other', region_scope=f'feed-{100+i}', scan_cycle=2,
                    state='failed', checkpoint={'priority_anchor': True}, updated_at=NOW-timedelta(minutes=1)))
            s.flush()
            chosen = prioritize_feed_tasks(s, adapter, plan(adapter), NOW)
            assert len(adapter._priority_anchor_scopes) <= 1
            assert sum(t.min_revisit_minutes <= 10 for t in chosen.tasks) <= 1
            adopted = s.scalar(select(CollectionTask).where(CollectionTask.task_key == 'slot:0'))
            adopted.attempts = 3  # A reused row represents several attempts.
            s.flush()
            prioritize_feed_tasks(s, adapter, plan(adapter), NOW)
            assert not adapter._priority_anchor_scopes
    finally:
        ctx.db.dispose()
