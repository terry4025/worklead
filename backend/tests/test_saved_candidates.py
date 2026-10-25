"""Deferred public candidates survive disappearing first-page listings."""
from datetime import timedelta

from sqlalchemy import select

from tests.test_daangn import NOW
from tests.test_daangn_feed import collect, lists, site
from worklead.engine.freshness import ref_key, saved_candidates
from worklead.models import DetailFetchState, DiscoveryListing, SourceRecord
from worklead.sources.base import PostRef


def test_unattempted_candidates_drain_without_waiting_for_feed_or_sitemap(tmp_path):
    posts = [(f"queued{i:06}", f"프로그램 제작 의뢰 {i}", "PER_CASE", False, i + 1) for i in range(3)]
    ctx, c, clock, calls = site(tmp_path, [[28, "신당동", "A"]], {28: posts})
    adapter = ctx.adapters["daangn-alba"]
    adapter.profile["discovery"].update(detail_requests_per_watch=1, detail_requests_per_watch_max=1)
    collect(ctx, c)
    first = [url for url in calls if "/job-posts/" in url]
    assert len(first) == 1
    calls.clear()
    clock[0] = NOW + timedelta(minutes=5)
    second = collect(ctx, c)
    assert lists(calls) == []  # no new list/network discovery required
    details = [url for url in calls if "/job-posts/" in url]
    assert len(details) == 1 and details[0] not in first
    assert second.result["discovery"]["saved_candidate_backlog"] is True
    assert second.result["discovery"]["watch_detail_requests"] == 1
    calls.clear()
    clock[0] = NOW + timedelta(minutes=6)
    collect(ctx, c)
    assert calls == []  # the same metadata cannot repeatedly bypass a watch
    with ctx.db.session() as s:
        listing = s.scalar(select(DiscoveryListing).where(DiscoveryListing.source_post_id == details[0].rsplit('-', 1)[1]))
        assert listing.first_listed_at == NOW and listing.last_listed_at == NOW
    ctx.db.dispose()


def test_saved_candidates_reapply_enabled_queries_and_preserve_optional_classification(tmp_path):
    posts = [("saved0000001", "프로그램 제작 의뢰", "PER_CASE", False, 1)]
    ctx, c, _, _ = site(tmp_path, [[28, "신당동", "A"]], {})
    adapter = ctx.adapters["daangn-alba"]
    with ctx.db.session() as s:
        for key, title, selection in [("saved0000001", "프로그램 제작 의뢰", "core"),
                ("saved0000002", "프로그램 제작 의뢰", "exploration"),
                ("saved0000003", "프로그램 제작 의뢰", "filtered")]:
            ref = PostRef(f"https://jobs.daangn.com/job-posts/test-{key}", key, title, NOW)
            s.add(DiscoveryListing(source_id=adapter.source_id, ref_key=ref_key(ref), source_post_id=key,
                url=ref.url, title_hint=title, title_digest="test", first_listed_at=NOW, last_listed_at=NOW,
                source_updated_at=NOW, selection=selection))
        s.flush()
        result = saved_candidates(s, adapter, '["프로그램"]', NOW)
        assert [r.source_post_id for r in result.refs] == ["saved0000001"]
        assert [r.source_post_id for r in result.exploration_refs] == ["saved0000002"]
        assert saved_candidates(s, adapter, '["엑셀"]', NOW).metadata["backlog_candidates"] == 0
        assert not result.listed_refs  # never re-stamp original list observation
    ctx.db.dispose()


def test_backlog_does_not_retry_failures_or_keep_old_or_unverified_links(tmp_path):
    ctx, _, _, _ = site(tmp_path, [[28, "신당동", "A"]], {})
    adapter = ctx.adapters["daangn-alba"]
    with ctx.db.session() as s:
        for i in range(5):
            ref = PostRef(f"https://jobs.daangn.com/job-posts/test-saved{i:07}", f"saved{i:07}")
            s.add(DiscoveryListing(source_id=adapter.source_id, ref_key=ref_key(ref), source_post_id=ref.source_post_id,
                url=ref.url if i != 4 else "https://untrusted.invalid/post", title_hint="프로그램 제작 의뢰", title_digest="test",
                first_listed_at=NOW, last_listed_at=NOW - timedelta(days=8) if i == 1 else NOW,
                source_updated_at=NOW - timedelta(days=8) if i == 2 else NOW, selection="core"))
            if i == 0:
                s.add(DetailFetchState(source_id=adapter.source_id, ref_key=ref_key(ref), url=ref.url,
                    outcome="http_404", attempted_at=NOW, retry_at=NOW + timedelta(days=1)))
        s.flush()
        result = saved_candidates(s, adapter, '["프로그램"]', NOW)
        assert [r.source_post_id for r in result.refs] == ["saved0000003"]
        assert result.metadata["backlog_candidates"] == 1
        adapter.profile["discovery"]["saved_candidate_limit"] = 0
        assert saved_candidates(s, adapter, '["프로그램"]', NOW).metadata["backlog_candidates"] == 0
    ctx.db.dispose()
