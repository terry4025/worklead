"""Bounded exploratory discovery through real queue/Fetcher and synthetic HTTP."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from tests import fixture_site as fx
from tests.conftest import make_ctx
from worklead.engine import executors, runs
from worklead.engine.http import Fetcher
from worklead.engine.worker import Worker
from worklead.models import DetailFetchState, HostBudget, Lead, RobotsCache, Run, SourcePolicy, SourceRecord
from worklead.sources.base import DiscoverResult, DiscoveryPlan, ParsedPost, PostRef, TaskSpec
from worklead.sources.profiled import ProfiledSiteAdapter


class ExplorationAdapter(ProfiledSiteAdapter):
    def __init__(self, now, count):
        profile = fx.profile(budget=300)
        profile["discovery"] = {"exploration_max_requests": 10, "exploration_reserve_requests": 30}
        super().__init__(profile, name="탐색 합성 사이트", scope_note="test only", adapter_version="test", parser_version="test")
        self.core = [PostRef(f"https://{fx.HOST}/jobs/100", "100", "홈페이지 제작좀 도와주실분", now)]
        self.extra = [PostRef(f"https://{fx.HOST}/jobs/{200 + i}", str(200 + i), "간단한 작업 도와주세요", now - timedelta(minutes=i)) for i in range(count)]

    def plan_discovery(self, groups, now):
        return DiscoveryPlan([TaskSpec("all", "fixture", "합성")], "합성", "not_applicable", "목록", 1)

    def discover(self, spec, fetcher):
        result = fetcher.get(f"https://{fx.HOST}/listing")
        assert result.outcome == "ok"
        return DiscoverResult(self.core, metadata={"core_candidates": len(self.core), "title_candidates": len(self.core), "exploration_candidates": len(self.extra)}, exploration_refs=self.extra)

    def parse(self, ref, result, now):
        data = json.loads(result.text)
        return ParsedPost(data["title"], data["body"], ref.url, ref.url, ref.source_post_id, source_status="open")


@pytest.fixture
def site(tmp_path):
    now = datetime.now(UTC)
    adapter = ExplorationAdapter(now, 13)
    ctx = make_ctx(tmp_path, adapters={adapter.source_id: adapter})
    ctx.clock = lambda: now
    calls = []
    hooks = {}

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if request.url.path == "/listing":
            return httpx.Response(200, text="owned fixture")
        if request.url.path in hooks:
            response = hooks[request.url.path]()
            if response is not None:
                return response
        core = request.url.path == "/jobs/100"
        return httpx.Response(200, json={
            "title": "홈페이지 제작좀 도와주실분" if core else "매장 도와주실 분",
            "body": "홈페이지 제작 능숙하신분 도와주세요. 건당 30만원." if core else "매장 상품진열과 청소 직원 구합니다. 시급 12000원. 출근 필수.",
        })

    ctx.transport = httpx.MockTransport(handler)
    with ctx.db.session() as s:
        policy = s.get(SourcePolicy, adapter.source_id)
        policy.status, policy.basis = "allowed", "synthetic fixture"
        s.add(RobotsCache(host=fx.HOST, fetched_at=now, status="ok", http_status=200, body="User-agent: *\nAllow: /\n"))
        s.add(HostBudget(host=fx.HOST, window=now.strftime("%Y-%m-%d"), used=0, limit=300))
    yield ctx, adapter, calls, hooks
    ctx.db.dispose()


def start(ctx):
    with ctx.db.session() as s:
        run, _ = runs.enqueue(s, "discovery", source_id="fixture-site")
        return run.id


def row(ctx, run_id):
    with ctx.db.session() as s:
        return s.get(Run, run_id)


def collect(ctx):
    run_id = start(ctx)
    assert Worker(ctx, "explore", runs.COLLECTION_KINDS).run_once()
    return row(ctx, run_id)


def budget(ctx):
    with ctx.db.session() as s:
        return s.get(HostBudget, (fx.HOST, datetime.now(UTC).strftime("%Y-%m-%d"))).used


def extra_calls(calls):
    return [path for path in calls if path.startswith("/jobs/") and path != "/jobs/100"]


def test_core_first_ten_extra_attempts_and_existing_body_rules(site):
    ctx, adapter, calls, _ = site
    run = collect(ctx)
    assert run.state == "succeeded"
    assert calls[:2] == ["/listing", "/jobs/100"]
    assert len(extra_calls(calls)) == 10
    assert budget(ctx) == run.counts["requests"] == 12
    assert run.result["discovery"] == {
        "core_candidates": 1, "title_candidates": 1, "exploration_candidates": 13,
        "exploration_requests": 10, "exploration_checked": 10, "exploration_deferred": 3,
        "exploration_limit": 10, "exploration_reserve": 30,
    }
    with ctx.db.session() as s:
        records = list(s.scalars(select(SourceRecord)))
        assert len(records) == 11
        for record in records:
            lead = s.get(Lead, record.lead_id)
            assert lead.recommendation == ("needs_review" if record.source_post_id == "100" else "excluded")


@pytest.mark.parametrize(("used", "extra", "final"), [(267, 1, 270), (268, 0, 270), (269, 0, 271), (298, 0, 300)])
def test_optional_exploration_preserves_thirty_and_core_success_at_zero_remaining(site, used, extra, final):
    ctx, _, calls, _ = site
    with ctx.db.session() as s:
        s.get(HostBudget, (fx.HOST, datetime.now(UTC).strftime("%Y-%m-%d"))).used = used
    run = collect(ctx)
    assert run.state == "succeeded"
    assert len(extra_calls(calls)) == extra
    assert budget(ctx) == final
    assert run.result["discovery"]["exploration_requests"] == extra
    assert run.result["discovery"]["exploration_deferred"] == 13 - extra


def test_persisted_visits_rotate_unvisited_before_lastmod_and_recent_core_keeps_user_state(site):
    ctx, adapter, calls, _ = site
    first = collect(ctx)
    with ctx.db.session() as s:
        record = s.scalar(select(SourceRecord).where(SourceRecord.source_post_id == "100"))
        lead = s.get(Lead, record.lead_id)
        lead.user_mark, lead.memo, lead.sales_stage = "interested", "보존할 메모", "contacted"
        core_id, core_body = lead.id, record.body_text
    calls.clear()
    second = collect(ctx)
    assert second.state == "succeeded"
    assert extra_calls(calls) == ["/jobs/210", "/jobs/211", "/jobs/212"]
    assert second.result["discovery"]["exploration_checked"] == 13
    assert second.result["discovery"]["exploration_requests"] == 3
    assert second.result["discovery"]["exploration_deferred"] == 0
    assert "/jobs/100" not in calls
    with ctx.db.session() as s:
        lead = s.get(Lead, core_id)
        record = s.get(SourceRecord, lead.primary_record_id)
        assert (lead.user_mark, lead.memo, lead.sales_stage, record.body_text) == ("interested", "보존할 메모", "contacted", core_body)


def test_oldest_visit_wins_and_retry_deferrals_do_not_spend_extra_budget(site):
    ctx, adapter, calls, _ = site
    adapter.extra = adapter.extra[:3]
    now = ctx.clock()
    with ctx.db.session() as s:
        for i, ref in enumerate(adapter.extra):
            s.add(DetailFetchState(source_id=adapter.source_id, ref_key=executors._ref_key(ref), url=ref.url,
                title_hint=ref.title_hint, source_updated_at=ref.source_updated_at, outcome="http_404",
                attempted_at=now - timedelta(days=i + 1), retry_at=now + timedelta(hours=1) if i == 1 else None))
    run = collect(ctx)
    assert extra_calls(calls) == ["/jobs/202", "/jobs/200"]
    assert run.result["discovery"]["exploration_requests"] == 2
    assert run.result["discovery"]["exploration_checked"] == 3
    assert run.result["discovery"]["exploration_deferred"] == 1


@pytest.mark.parametrize("action", ["pause", "cancel"])
def test_controls_stop_optional_requests_and_pause_resume_retains_run_limit(site, action):
    ctx, adapter, calls, hooks = site
    run_id = start(ctx)

    def control():
        with ctx.db.session() as s:
            setattr(s.get(Run, run_id), action + "_requested", True)

    hooks["/jobs/200"] = control
    assert Worker(ctx, "first", runs.COLLECTION_KINDS).run_once()
    paused = row(ctx, run_id)
    assert paused.state == ("paused" if action == "pause" else "cancelled")
    assert paused.result["discovery"]["exploration_requests"] == 1
    assert len(extra_calls(calls)) == 1
    if action == "pause":
        hooks.clear()
        adapter.profile["discovery"]["exploration_max_requests"] = 50
        with ctx.db.session() as s:
            runs.request_action(s, s.get(Run, run_id), "resume")
        assert Worker(ctx, "resumed", runs.COLLECTION_KINDS).run_once()
        completed = row(ctx, run_id)
        assert completed.state == "succeeded"
        assert len(extra_calls(calls)) == 10
        assert completed.result["discovery"]["exploration_requests"] == 10
        assert completed.result["discovery"]["exploration_checked"] == 10
        assert completed.result["discovery"]["exploration_deferred"] == 3
        assert completed.result["discovery"]["exploration_limit"] == 10


def test_crash_after_reservation_before_send_keeps_persisted_limit_on_recovery(site, monkeypatch):
    ctx, adapter, calls, _ = site
    run_id = start(ctx)
    original = executors._Exploration.record_request

    class ProcessLost(BaseException):
        pass

    def interrupt(self):
        if self.active:
            raise ProcessLost()
        original(self)

    monkeypatch.setattr(executors._Exploration, "record_request", interrupt)
    with pytest.raises(ProcessLost):
        Worker(ctx, "lost", runs.COLLECTION_KINDS).run_once()
    current = row(ctx, run_id)
    assert current.checkpoint["exploration"]["reserved"] == 1
    assert current.result["discovery"]["exploration_requests"] == 0
    assert len(extra_calls(calls)) == 0 and budget(ctx) == 3
    monkeypatch.setattr(executors._Exploration, "record_request", original)
    adapter.profile["discovery"]["exploration_max_requests"] = 50
    with ctx.db.session() as s:
        s.get(Run, run_id).lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert runs.recover_expired(ctx.db) == 1
    assert Worker(ctx, "recovered", runs.COLLECTION_KINDS).run_once()
    current = row(ctx, run_id)
    assert current.state == "succeeded"
    assert current.checkpoint["exploration"]["reserved"] == 10
    assert current.result["discovery"]["exploration_requests"] == len(extra_calls(calls)) == 9
    assert budget(ctx) == 13  # listing+core+unsent reservation+resumed listing+nine details


def test_redirect_hops_spend_extra_slots_and_never_exceed_ten(site):
    ctx, _, calls, hooks = site
    hooks["/jobs/200"] = lambda: httpx.Response(302, headers={"Location": "/jobs/900"})
    run = collect(ctx)
    assert run.state == "succeeded"
    assert extra_calls(calls)[:2] == ["/jobs/200", "/jobs/900"]
    assert len(extra_calls(calls)) == 10
    assert run.result["discovery"]["exploration_requests"] == 10
    assert run.result["discovery"]["exploration_checked"] == 9


def test_reservation_callback_error_rolls_back_host_and_run_state(site):
    ctx, adapter, _, _ = site
    run_id = start(ctx)
    fetcher = ctx.fetcher_for(adapter)

    def reject(session, host_budget):
        current = session.get(Run, run_id)
        current.checkpoint = {"should_rollback": True}
        raise ValueError("owned injected failure")

    fetcher.reserve_request = reject
    try:
        with pytest.raises(ValueError, match="owned injected failure"):
            fetcher._reserve_budget(fx.HOST)
        assert budget(ctx) == 0
        assert row(ctx, run_id).checkpoint == {}
    finally:
        fetcher.close()


@pytest.mark.parametrize(("used", "reserved", "accepted", "limit"), [(269, 0, 1, 10), (0, 9, 1, 10), (0, 49, 1, 50)])
def test_concurrent_reservations_enforce_both_host_reserve_and_same_run_limit(site, used, reserved, accepted, limit):
    ctx, adapter, _, _ = site
    adapter.profile["discovery"]["exploration_max_requests"] = limit
    run_id = start(ctx)
    claimed = runs.claim_next(ctx.db, "same-owner", runs.COLLECTION_KINDS, 30)
    with ctx.db.session() as s:
        s.get(HostBudget, (fx.HOST, datetime.now(UTC).strftime("%Y-%m-%d"))).used = used
        s.get(Run, run_id).checkpoint = {"exploration": {"reserved": reserved}}
    barrier = threading.Barrier(2)

    def reserve():
        scope = executors.RunScope(ctx, claimed, "same-owner")
        exploration = executors._Exploration(ctx, scope, adapter)
        exploration.active = True
        fetcher = ctx.fetcher_for(adapter)
        fetcher.reserve_request = exploration.reserve_request
        try:
            barrier.wait(timeout=5)
            fetcher._reserve_budget(fx.HOST)
            return True
        except executors._ExplorationDeferred:
            return False
        finally:
            fetcher.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(reserve) for _ in range(2)]
        results = [future.result(timeout=10) for future in futures]
    assert sum(results) == accepted
    assert budget(ctx) == used + accepted
    assert row(ctx, run_id).checkpoint["exploration"]["reserved"] == reserved + accepted


@pytest.mark.parametrize("configured", [None, 50, 99])
def test_new_run_default_and_hard_maximum_are_fifty_with_core_first(site, configured):
    ctx, adapter, calls, _ = site
    adapter.extra = ExplorationAdapter(ctx.clock(), 53).extra
    if configured is None:
        adapter.profile["discovery"].pop("exploration_max_requests")
    else:
        adapter.profile["discovery"]["exploration_max_requests"] = configured
    run = collect(ctx)
    assert run.state == "succeeded"
    assert calls[:2] == ["/listing", "/jobs/100"]
    assert len(extra_calls(calls)) == 50
    assert run.counts["requests"] == budget(ctx) == 52
    assert run.checkpoint["exploration"]["reserved"] == 50
    assert run.result["discovery"]["exploration_limit"] == 50
    assert run.result["discovery"]["exploration_requests"] == 50
    assert run.result["discovery"]["exploration_checked"] == 50
    assert run.result["discovery"]["exploration_deferred"] == 3


def test_new_run_checks_thirty_seven_remaining_candidates_with_available_budget(site):
    ctx, adapter, calls, _ = site
    adapter.extra = ExplorationAdapter(ctx.clock(), 37).extra
    adapter.profile["discovery"]["exploration_max_requests"] = 50
    with ctx.db.session() as s:
        s.get(HostBudget, (fx.HOST, datetime.now(UTC).strftime("%Y-%m-%d"))).used = 220
    run = collect(ctx)
    assert run.state == "succeeded"
    assert len(extra_calls(calls)) == 37
    assert run.result["discovery"]["exploration_deferred"] == 0
    assert budget(ctx) == 259


@pytest.mark.parametrize(("remaining", "expected_requests", "expected_deferred"), [(30, 0, 53), (31, 1, 52)])
def test_fifty_limit_never_consumes_reserved_host_budget(site, remaining, expected_requests, expected_deferred):
    ctx, adapter, calls, _ = site
    adapter.extra = ExplorationAdapter(ctx.clock(), 53).extra
    adapter.profile["discovery"]["exploration_max_requests"] = 50
    with ctx.db.session() as s:
        s.get(HostBudget, (fx.HOST, datetime.now(UTC).strftime("%Y-%m-%d"))).used = 300 - remaining - 2
    run = collect(ctx)
    assert run.state == "succeeded"
    assert len(extra_calls(calls)) == expected_requests
    assert budget(ctx) == 270
    assert run.result["discovery"]["exploration_deferred"] == expected_deferred


@pytest.mark.parametrize("exhaustion", ["run_limit", "host_reserve"])
def test_cache_and_retry_deferral_are_processed_without_http_after_slots_end(site, monkeypatch, exhaustion):
    ctx, adapter, calls, _ = site
    adapter.extra = adapter.extra[:2]
    assert collect(ctx).state == "succeeded"  # cache /jobs/200 and /jobs/201
    adapter.extra = ExplorationAdapter(ctx.clock(), 54).extra
    adapter.profile["discovery"]["exploration_max_requests"] = 50
    deferred = adapter.extra[-1]
    with ctx.db.session() as s:
        s.add(DetailFetchState(source_id=adapter.source_id, ref_key=executors._ref_key(deferred), url=deferred.url,
            title_hint=deferred.title_hint, source_updated_at=deferred.source_updated_at, outcome="http_404",
            attempted_at=ctx.clock() - timedelta(days=2), retry_at=ctx.clock() + timedelta(hours=1)))
        if exhaustion == "host_reserve":
            s.get(HostBudget, (fx.HOST, datetime.now(UTC).strftime("%Y-%m-%d"))).used = 269
    before_budget = budget(ctx)
    calls.clear()
    paced = []
    pace = Fetcher._pace

    def count_pacing(self, host):
        paced.append(host)
        return pace(self, host)

    monkeypatch.setattr(Fetcher, "_pace", count_pacing)
    run = collect(ctx)
    sent = 50 if exhaustion == "run_limit" else 0
    assert run.state == "succeeded"
    assert len(extra_calls(calls)) == sent
    assert run.result["discovery"]["exploration_requests"] == sent
    assert run.result["discovery"]["exploration_checked"] == sent + 3
    assert run.result["discovery"]["exploration_deferred"] == 51 - sent + 1
    assert run.result["deferred_details"] == 1
    assert budget(ctx) == before_budget + 1 + sent
    assert len(paced) <= len(calls) + 1  # no repeated pacing/HTTP attempts for blocked candidates
