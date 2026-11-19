"""Separate-host collection stays independent without overlapping one source."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import threading

import httpx
import pytest

from tests import fixture_site as fx
from tests.conftest import make_ctx
from worklead.db import utcnow
from worklead.engine import runs
from worklead.engine.worker import Worker, Workers
from worklead.models import Run, SourcePolicy
from worklead.services.settings import put_setting
from worklead.sources.profiled import ProfiledSiteAdapter


def enqueue(ctx, source_id, kind="discovery"):
    with ctx.db.session() as s:
        run, _ = runs.enqueue(s, kind, source_id=source_id, exclusive=False)
        return run.id


def test_simultaneous_claims_never_overlap_collection_for_same_source(ctx):
    first_id = enqueue(ctx, "daangn-alba")
    second_id = enqueue(ctx, "daangn-alba", "recheck_recent")
    barrier = threading.Barrier(2)

    def claim(owner):
        barrier.wait(timeout=3)
        return runs.claim_next(ctx.db, owner, runs.COLLECTION_KINDS, 60,
                               source_ids=("daangn-alba",))

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(claim, f"collector-{i}") for i in range(2)]
        claimed = [future.result(timeout=5) for future in futures]
    assert [run.id for run in claimed if run is not None] == [first_id]
    with ctx.db.session() as s:
        assert s.get(Run, second_id).state == "queued"

    # Interactive work remains independent of the source's serial collection.
    interactive_id = enqueue(ctx, "daangn-alba", "recheck_lead")
    interactive = runs.claim_next(ctx.db, "interactive", runs.INTERACTIVE_KINDS, 60)
    assert interactive.id == interactive_id
    runs.finish(ctx.db, first_id, "succeeded")
    following = runs.claim_next(ctx.db, "collector", runs.COLLECTION_KINDS, 60)
    assert following.id == second_id


@pytest.mark.parametrize("filters,expected", [
    ({"source_ids": ("daangn-alba",)}, "daangn-alba"),
    ({"exclude_source_ids": ("daangn-alba",)}, None),
    ({}, None),
])
def test_source_filters_preserve_source_free_jobs(ctx, filters, expected):
    free_id = enqueue(ctx, None)
    daangn_id = enqueue(ctx, "daangn-alba")
    enqueue(ctx, "iboss")
    claimed = runs.claim_next(ctx.db, "collector", runs.COLLECTION_KINDS, 60, **filters)
    assert claimed.source_id == expected
    assert claimed.id == (daangn_id if expected else free_id)


def test_empty_source_filter_claims_nothing(ctx):
    enqueue(ctx, "daangn-alba")
    enqueue(ctx, None)
    assert runs.claim_next(ctx.db, "collector", runs.COLLECTION_KINDS, 60,
                           source_ids=()) is None


def test_source_cooldown_and_expired_lease_survive_filtered_claims(ctx):
    first_id = enqueue(ctx, "daangn-alba")
    enqueue(ctx, "daangn-alba", "recheck_recent")
    iboss_id = enqueue(ctx, "iboss")
    claimed = runs.claim_next(ctx.db, "old-owner", runs.COLLECTION_KINDS, 60,
                              source_ids=("daangn-alba",))
    assert claimed.id == first_id
    with ctx.db.session() as s:
        s.get(Run, first_id).lease_expires_at = utcnow() - timedelta(seconds=1)
    assert runs.recover_expired(ctx.db) == 1
    recovered = runs.claim_next(ctx.db, "new-owner", runs.COLLECTION_KINDS, 60,
                                source_ids=("daangn-alba",))
    assert recovered.id == first_id and recovered.lease_owner == "new-owner"
    runs.finish(ctx.db, first_id, "partial", retry_after=utcnow() + timedelta(minutes=2))
    assert runs.claim_next(ctx.db, "daangn", runs.COLLECTION_KINDS, 60,
                           source_ids=("daangn-alba",)) is None
    other = runs.claim_next(ctx.db, "other", runs.COLLECTION_KINDS, 60,
                            exclude_source_ids=("daangn-alba",))
    assert other.id == iboss_id


def synthetic_adapter(source_id, host):
    profile = fx.profile(regions=[fx.REGIONS[0]])
    profile["source_id"] = source_id
    profile["allowed_hosts"] = [host]
    profile["search"]["url_template"] = f"https://{host}/search?q={{query}}&region={{region}}&page={{page}}"
    return ProfiledSiteAdapter(profile, name=source_id, scope_note="synthetic only",
                               adapter_version="test", parser_version="test")


def test_slow_other_host_does_not_delay_daangn_collection_and_stop_is_preserved(tmp_path, monkeypatch):
    """Run real discovery/HTTP pipelines concurrently using only fake hosts."""
    from worklead.engine import worker as worker_module

    adapters = {sid: synthetic_adapter(sid, f"{sid}.fixture.test")
                for sid in ("daangn-alba", "iboss")}
    ctx = make_ctx(tmp_path, adapters=adapters, worker_poll_seconds=30)
    other_blocked = threading.Event()
    release_other = threading.Event()
    daangn_finished = threading.Event()
    requested = []
    original_execute = worker_module.execute

    def execute(app, run, owner):
        try:
            original_execute(app, run, owner)
        finally:
            if run.source_id == "daangn-alba":
                daangn_finished.set()

    def transport(request):
        requested.append((request.url.host, request.url.path))
        assert request.url.host in {"iboss.fixture.test", "daangn-alba.fixture.test"}
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow:\n")
        if request.url.host == "iboss.fixture.test":
            other_blocked.set()
            assert release_other.wait(timeout=8), "synthetic transport was not released"
        return httpx.Response(200, text="<html><body>검색 결과가 없어요</body></html>")

    ctx.transport = httpx.MockTransport(transport)
    monkeypatch.setattr(worker_module, "execute", execute)
    with ctx.db.session() as s:
        for sid in adapters:
            s.get(SourcePolicy, sid).status = "allowed"
        put_setting(s, "query_groups", {"version": 2, "groups": [
            {"id": "test", "label": "test", "enabled": True, "keywords": ["개발"]}]})
    other_id = enqueue(ctx, "iboss")
    queued_after_stop = enqueue(ctx, "iboss", "recheck_recent")
    daangn_id = enqueue(ctx, "daangn-alba")
    # Exercise the actual production lane topology, without starting scheduler.
    lanes = [thread for thread in Workers(ctx).threads if isinstance(thread, Worker)
             and thread.kinds == runs.COLLECTION_KINDS]
    daangn, other = lanes
    try:
        other.start()
        assert other_blocked.wait(timeout=4)
        daangn.start()
        assert daangn_finished.wait(timeout=4), "daangn waited for another host's HTTP response"
        with ctx.db.session() as s:
            assert s.get(Run, other_id).state == "running"
            assert s.get(Run, daangn_id).state == "succeeded"
            assert s.get(Run, queued_after_stop).state == "queued"
        assert ("daangn-alba.fixture.test", "/search") in requested
        assert not release_other.is_set()
    finally:
        for lane in lanes:
            lane.stop()
        release_other.set()
        for lane in lanes:
            if lane.ident is not None:
                lane.join(timeout=5)
        assert not any(lane.is_alive() for lane in lanes)
        with ctx.db.session() as s:
            # Stopping an active worker lets its current request finish, then
            # prevents another claim; an idle worker's 30s poll is interrupted.
            assert s.get(Run, queued_after_stop).state == "queued"
        ctx.db.dispose()
