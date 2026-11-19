"""Bounded probes of software requests whose public titles omit keywords."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from tests.test_daangn_feed import NOW, Static, collect, feed, feed_task, site
from worklead.models import DetailFetchState, DiscoveryListing, SourceRecord
from worklead.sources.daangn import create


@pytest.mark.parametrize("title", [
    "간단한 작업 도와주세요", "작은 일 전문가 구합니다", "문서 수정 의뢰", "작업 견적 부탁드립니다",
])
def test_generic_request_is_a_probe_not_a_positive_title_match(title):
    adapter = create()
    assert adapter.title_audit_candidate(title)
    assert adapter.saved_candidate_kind(title, '["홈페이지", "프로그램"]') is None


@pytest.mark.parametrize("title", [
    "식당 서빙 도와주세요", "청소 작업 의뢰", "매장 직원 모집", "단순 포장 작업 하실 분",
    "아르바이트 구합니다", "고수익 간단한 작업", "계정 대여 도움", "단순한 제목", "",
])
def test_employment_physical_and_non_request_titles_are_not_audited(title):
    assert not create().title_audit_candidate(title)


def test_feed_audits_only_open_per_case_unmatched_requests():
    adapter = create()
    posts = [
        ("audit0000001", "간단한 작업 도와주세요", "PER_CASE", False, 1),
        ("audit0000002", "간단한 작업 도와주세요", "HOURLY", False, 2),
        ("audit0000003", "작업 견적 부탁드립니다", "MONTHLY", False, 3),
        ("audit0000004", "문서 수정 의뢰", "PER_CASE", True, 4),
        ("audit0000005", "택배 포장 작업 하실 분", "PER_CASE", False, 5),
    ]
    fetcher = Static(feed(28, posts))
    result = adapter.discover(feed_task(adapter), fetcher)
    assert not result.refs
    assert [ref.source_post_id for ref in result.exploration_refs] == ["audit0000001"]
    assert result.metadata["title_audit_ids"] == ["audit0000001"]
    assert result.metadata["title_audit_candidates"] == 1
    assert result.metadata["feed_saturated"] is False
    assert len(result.listed_refs) == 5 and len(fetcher.urls) == 1
    assert result.next_cursor is None


def test_full_feed_marks_saturation_without_inventing_pagination():
    adapter = create()
    posts = [(f"audit{i:07}", "단순한 제목", "PER_CASE", False, i) for i in range(20)]
    fetcher = Static(feed(28, posts))
    result = adapter.discover(feed_task(adapter), fetcher)
    assert result.metadata["feed_saturated"] is True
    assert result.next_cursor is None and len(fetcher.urls) == 1


def test_audit_has_no_incremental_list_budget_cost():
    adapter = create()
    # Failure and saturation polls share the existing fast allowance rather
    # than reserving every region at the shortest possible revisit interval.
    assert adapter.daily_list_requests(5) == 4828
    assert adapter.remaining_list_requests(86400, 5) == 4939


def test_audit_respects_narrow_and_disabled_keyword_scope():
    adapter = create()
    assert not adapter.title_audit_candidate("간단한 작업 도와주세요", '["엑셀"]')
    assert not adapter.title_audit_candidate("홈페이지 제작 의뢰", '["엑셀"]')
    assert adapter.title_audit_candidate("간단한 작업 도와주세요", '["홈페이지", "프로그램"]')


def test_global_watch_cap_and_durable_unseen_rotation(tmp_path):
    posts = [(f"audit{i:07}", f"간단한 작업 도와주세요 {i}", "PER_CASE", False, i) for i in range(6)]
    ctx, client, clock, calls = site(tmp_path, [[28, "신당동", "A"]], {28: posts})
    try:
        assert client.patch("/v1/settings", json={"query_groups": [{
            "id": "dev", "label": "개발", "enabled": True, "keywords": ["홈페이지", "프로그램"],
        }]}).status_code == 200
        collect(ctx, client)
        first = {url.rsplit("-", 1)[-1] for url in calls if "/job-posts/" in url and "audit" in url}
        assert len(first) == 2
        with ctx.db.session() as session:
            assert len(list(session.scalars(select(DiscoveryListing).where(DiscoveryListing.selection == "audit")))) == 6
            # The detail body in the fixture, rather than the generic title,
            # decides whether a record is software buying demand.
            assert len(list(session.scalars(select(SourceRecord).where(SourceRecord.source_post_id.in_(first))))) == 2
        calls.clear()
        clock[0] = NOW + timedelta(minutes=31)
        collect(ctx, client)
        second = {url.rsplit("-", 1)[-1] for url in calls if "/job-posts/" in url and "audit" in url}
        assert len(second) == 2 and first.isdisjoint(second)
        with ctx.db.session() as session:
            assert len(list(session.scalars(select(DetailFetchState).where(DetailFetchState.url.contains("audit"))))) == 4
    finally:
        ctx.db.dispose()
