"""Collector regressions; synthetic HTTP and disposable databases only."""
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from tests.conftest import make_ctx
from tests.test_engine import setup
from worklead.engine import http, runs
from worklead.engine.http import Fetcher, PolicyStop, RunHalt
from worklead.engine.worker import Worker
from worklead.models import HostBudget, RobotsCache, Run

NOW = datetime(2026, 9, 30, 5, tzinfo=UTC)


def clock(monkeypatch):
    current = [NOW]

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return current[0] if tz is not None else current[0].replace(tzinfo=None)

    monkeypatch.setattr(http, "datetime", FrozenDateTime)
    monkeypatch.setattr(http, "utcnow", lambda: current[0])
    return current


def fetcher(ctx, handler, budget=20):
    return Fetcher(ctx.db, allowed_hosts={"fixture.test"}, user_agent="collector-safety-test", daily_budget=budget,
                   min_interval_s=0, sleep=lambda _: None, transport=httpx.MockTransport(handler))


def used(ctx, host="fixture.test"):
    with ctx.db.session() as s:
        row = s.get(HostBudget, (host, "2026-09-30"))
        return row.used if row else 0


@pytest.mark.parametrize(("status", "code"), [(401, "login_required"), (403, "http_403")])
@pytest.mark.parametrize("legacy_cache", [False, True])
def test_robots_denial_never_requests_content_or_reuses_legacy_allow_cache(tmp_path, monkeypatch, status, code, legacy_cache):
    clock(monkeypatch)
    ctx = make_ctx(tmp_path)
    calls = []
    if legacy_cache:
        with ctx.db.session() as s:
            s.add(RobotsCache(host="fixture.test", fetched_at=NOW, status="missing", http_status=status, body=None))

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(status if request.url.path == "/robots.txt" else 200, text="denied")

    f = fetcher(ctx, handler)
    try:
        with pytest.raises(PolicyStop) as raised:
            f.get("https://fixture.test/jobs")
        assert raised.value.code == code and raised.value.http_status == status
        assert calls == ["/robots.txt"] and used(ctx) == 1
    finally:
        f.close(); ctx.db.dispose()


def test_robots_429_honors_retry_then_rechecks_and_accounts_only_wire_requests(tmp_path, monkeypatch):
    current = clock(monkeypatch)
    ctx = make_ctx(tmp_path)
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/robots.txt":
            if current[0] == NOW:
                return httpx.Response(429, headers={"Retry-After": "120"})
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        return httpx.Response(200, text="content")

    f = fetcher(ctx, handler)
    try:
        with pytest.raises(RunHalt) as raised:
            f.get("https://fixture.test/jobs")
        assert raised.value.code == "rate_limited" and raised.value.retry_after == NOW + timedelta(seconds=120)
        assert calls == ["/robots.txt"] and used(ctx) == 1
        with ctx.db.session() as s:
            assert s.get(RobotsCache, "fixture.test") is None
        current[0] += timedelta(seconds=121)
        assert f.get("https://fixture.test/jobs").outcome == "ok"
        assert calls == ["/robots.txt", "/robots.txt", "/jobs"] and used(ctx) == 3
    finally:
        f.close(); ctx.db.dispose()


def test_legacy_429_missing_cache_is_revalidated_before_content(tmp_path, monkeypatch):
    clock(monkeypatch)
    ctx = make_ctx(tmp_path)
    calls = []
    with ctx.db.session() as s:
        s.add(RobotsCache(host="fixture.test", fetched_at=NOW - timedelta(hours=1), status="missing", http_status=429))

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(429, headers={"Retry-After": "120"})

    f = fetcher(ctx, handler)
    try:
        with pytest.raises(RunHalt, match="429"):
            f.get("https://fixture.test/jobs")
        assert calls == ["/robots.txt"] and used(ctx) == 1
    finally:
        f.close(); ctx.db.dispose()


def test_transient_robots_error_is_deferred_then_rechecked_instead_of_cached_for_a_day(tmp_path, monkeypatch):
    current = clock(monkeypatch)
    ctx = make_ctx(tmp_path)
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(503) if current[0] == NOW else httpx.Response(200, text="User-agent: *\nAllow: /\n")
        return httpx.Response(200, text="content")

    f = fetcher(ctx, handler)
    try:
        with pytest.raises(RunHalt) as raised:
            f.get("https://fixture.test/jobs")
        assert raised.value.code == "robots_unreachable" and raised.value.retry_after == NOW + timedelta(minutes=5)
        assert calls == ["/robots.txt"] and used(ctx) == 1
    finally:
        f.close()
    current[0] += timedelta(minutes=1)
    f = fetcher(ctx, handler)
    try:
        with pytest.raises(RunHalt) as raised:
            f.get("https://fixture.test/jobs")
        assert raised.value.retry_after == NOW + timedelta(minutes=5)
        assert calls == ["/robots.txt"] and used(ctx) == 1
    finally:
        f.close()
    current[0] += timedelta(minutes=5)
    f = fetcher(ctx, handler)
    try:
        assert f.get("https://fixture.test/jobs").outcome == "ok"
        assert calls == ["/robots.txt", "/robots.txt", "/jobs"] and used(ctx) == 3
    finally:
        f.close(); ctx.db.dispose()


@pytest.mark.parametrize("status", [404, 410])
def test_missing_robots_does_not_bypass_daily_budget(tmp_path, monkeypatch, status):
    clock(monkeypatch)
    ctx = make_ctx(tmp_path)
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(status if request.url.path == "/robots.txt" else 200)

    f = fetcher(ctx, handler, budget=1)
    try:
        with pytest.raises(RunHalt) as raised:
            f.get("https://fixture.test/jobs")
        assert raised.value.code == "budget_exhausted"
        assert calls == ["/robots.txt"] and used(ctx) == 1
    finally:
        f.close(); ctx.db.dispose()


@pytest.mark.parametrize("action", ["pause", "cancel"])
def test_control_between_robots_and_content_does_not_count_or_spend_an_unsent_request(tmp_path, monkeypatch, action):
    clock(monkeypatch)
    ctx, _ = setup(tmp_path)
    calls = []
    with ctx.db.session() as s:
        run, _ = runs.enqueue(s, "discovery", source_id="fixture-site")
        run_id = run.id

    def handler(request):
        calls.append(request.url.path)
        with ctx.db.session() as s:
            current = s.get(Run, run_id)
            setattr(current, f"{action}_requested", True)
        return httpx.Response(200, text="User-agent: *\nAllow: /\n")

    ctx.transport = httpx.MockTransport(handler)
    try:
        assert Worker(ctx, "collector-safety", runs.COLLECTION_KINDS).run_once()
        with ctx.db.session() as s:
            current = s.get(Run, run_id)
            assert current.state == ("paused" if action == "pause" else "cancelled")
            assert current.counts["requests"] == 1
        assert calls == ["/robots.txt"] and used(ctx, "jobs.fixture.test") == 1
    finally:
        ctx.db.dispose()


@pytest.mark.parametrize("status", [400, 405])
def test_other_legacy_missing_cache_is_revalidated_not_treated_as_absent_robots(tmp_path, monkeypatch, status):
    clock(monkeypatch)
    ctx = make_ctx(tmp_path)
    calls = []
    with ctx.db.session() as s:
        s.add(RobotsCache(host="fixture.test", fetched_at=NOW, status="missing", http_status=status))

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, text="User-agent: *\nAllow: /\n" if request.url.path == "/robots.txt" else "content")

    f = fetcher(ctx, handler)
    try:
        assert f.get("https://fixture.test/jobs").outcome == "ok"
        assert calls == ["/robots.txt", "/jobs"] and used(ctx) == 2
    finally:
        f.close(); ctx.db.dispose()


@pytest.mark.parametrize("status", [404, 410])
def test_legal_missing_robots_cache_remains_usable(tmp_path, monkeypatch, status):
    clock(monkeypatch)
    ctx = make_ctx(tmp_path)
    calls = []
    with ctx.db.session() as s:
        s.add(RobotsCache(host="fixture.test", fetched_at=NOW, status="missing", http_status=status))

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, text="content")

    f = fetcher(ctx, handler)
    try:
        assert f.get("https://fixture.test/jobs").outcome == "ok"
        assert calls == ["/jobs"] and used(ctx) == 1
    finally:
        f.close(); ctx.db.dispose()


@pytest.mark.parametrize("custom", [False, True])
def test_captcha_html_at_robots_stops_before_raw_detail_request(tmp_path, monkeypatch, custom):
    clock(monkeypatch)
    ctx = make_ctx(tmp_path)
    calls = []
    body = '<html><body>fixture challenge</body></html>' if custom else '<html><body><div class="hcaptcha"></div></body></html>'

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, text=body, headers={"Content-Type": "text/html"})

    f = fetcher(ctx, handler)
    if custom:
        f.block_detector = lambda result: "fixture challenge" if "fixture challenge" in (result.text or "") else None
    try:
        with pytest.raises(PolicyStop) as raised:
            f.get("https://fixture.test/jobs")
        assert raised.value.code == "captcha_or_block_page"
        assert calls == ["/robots.txt"] and used(ctx) == 1
        with ctx.db.session() as s:
            assert s.get(RobotsCache, "fixture.test") is None
    finally:
        f.close(); ctx.db.dispose()


def test_legacy_cached_captcha_html_is_not_reused_as_empty_robots_rules(tmp_path, monkeypatch):
    clock(monkeypatch)
    ctx = make_ctx(tmp_path)
    with ctx.db.session() as s:
        s.add(RobotsCache(host="fixture.test", fetched_at=NOW, status="ok", http_status=200, body='<html><div class="hcaptcha"></div></html>'))
    request = []
    f = fetcher(ctx, lambda r: request.append(r) or httpx.Response(200))
    try:
        with pytest.raises(PolicyStop) as raised:
            f.get("https://fixture.test/jobs")
        assert raised.value.code == "captcha_or_block_page"
        assert request == [] and used(ctx) == 0
    finally:
        f.close(); ctx.db.dispose()


@pytest.mark.parametrize("rules", ["User-agent: *\nDisallow: /\n", "User-agent: *\nDisallow: /hcaptcha\nAllow: /\n"])
def test_intact_robots_rules_are_policy_not_unreachable_or_false_captcha(tmp_path, monkeypatch, rules):
    clock(monkeypatch)
    ctx = make_ctx(tmp_path)
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, text=rules if request.url.path == "/robots.txt" else "content")

    f = fetcher(ctx, handler)
    try:
        if "Disallow: /\n" in rules:
            with pytest.raises(PolicyStop) as raised:
                f.get("https://fixture.test/jobs")
            assert raised.value.code == "robots_disallowed"
            assert calls == ["/robots.txt"] and used(ctx) == 1
        else:
            assert f.get("https://fixture.test/jobs").outcome == "ok"
            assert calls == ["/robots.txt", "/jobs"] and used(ctx) == 2
    finally:
        f.close(); ctx.db.dispose()
