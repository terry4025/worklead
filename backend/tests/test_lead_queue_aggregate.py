"""Single filtered scan preserves overlapping queues and freshness semantics."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event

from tests.conftest import client_for, make_ctx
from worklead.models import Lead, SourceRecord
from worklead.services import leads as service


@pytest.fixture
def aggregate_api(tmp_path, monkeypatch):
    ctx = make_ctx(tmp_path)
    now = datetime(2026, 9, 30, 12, tzinfo=UTC)
    monkeypatch.setattr(service, "utcnow", lambda: now)
    specs = [
        ("recommended", {}),
        ("recommended_interested", {"user_mark": "interested"}),
        ("stale_interested", {"user_mark": "interested", "sales_stage": "reviewing", "last_checked_at": now - timedelta(hours=25)}),
        ("review", {"recommendation": "needs_review"}),
        ("excluded", {"recommendation": "excluded", "pay_unit": "hour", "work_mode": "onsite"}),
        ("excluded_interested", {"recommendation": "excluded", "user_mark": "interested", "sales_stage": "reviewing", "pay_unit": "month"}),
        ("dismissed", {"user_mark": "dismissed"}),
        ("ignored", {"sales_stage": "ignored"}),
        ("active", {"sales_stage": "contacted", "user_mark": "interested"}),
        ("won", {"recommendation": "excluded", "sales_stage": "won"}),
        ("lost", {"sales_stage": "lost", "title": "쇼핑몰 자동화 계약 검토"}),
        ("closed", {"recommendation": "excluded", "source_status": "closed"}),
        ("deleted", {"recommendation": "excluded", "source_status": "deleted"}),
        ("unknown", {"recommendation": "needs_review", "source_status": "unknown", "work_mode": "unknown"}),
        ("blocked", {"recommendation": "needs_review", "access_status": "blocked"}),
        ("missing", {"last_checked_at": None}),
    ]
    with ctx.db.session() as s:
        for index, (lid, patch) in enumerate(specs):
            rid = "record_" + lid
            fields = {
                "id": lid, "primary_record_id": rid, "source_id": "daangn-alba" if index % 2 == 0 else "manual",
                "title": "홈페이지 제작 " + lid, "categories": ["website" if index % 2 == 0 else "automation"],
                "recommendation": "recommended", "source_status": "open", "access_status": "accessible",
                "last_checked_at": now - timedelta(hours=1), "work_mode": "fully_remote", "work_mode_basis": "explicit",
                "demand_intent": "buyer_project", "pay_unit": "project", "sales_stage": "new", **patch,
            }
            fields["search_text"] = fields["title"].lower()
            s.add(Lead(**fields))
            s.flush()
            s.add(SourceRecord(id=rid, lead_id=lid, source_id=fields["source_id"], title=fields["title"], body_text="합성 집계 표본", content_hash=lid, parser_version="test"))
    yield ctx, client_for(ctx)
    ctx.db.dispose()


def test_unfiltered_counts_keep_overlapping_interest_and_independent_sales_queues(aggregate_api):
    _ctx, client = aggregate_api
    response = client.get("/v1/leads/queue-counts")
    assert response.status_code == 200, response.text
    assert response.json()["counts"] == {
        "fresh": 0,
        "recommended": 2, "needs_review": 5, "interested": 3, "active": 2,
        "dismissed": 2, "auto_excluded": 4, "all": 16,
    }


@pytest.mark.parametrize("filters", [
    {}, {"keyword": "홈페이지"}, {"keyword": "쇼핑몰 자동화"}, {"keyword": "존재하지않는단어"},
    {"source_id": "manual", "category": "automation"}, {"remote": "confirmed", "recruit": "open_fresh", "intent": "buyer", "pay_unit": "project"},
    {"recruit": "closed"}, {"recruit": "access_issue"}, {"recruit": "recheck"},
    {"recommendation": "recommended"}, {"recommendation": "needs_review"},
    {"recommendation": ["recommended", "needs_review"], "pay_unit": "project", "source_status": "open"},
])
def test_aggregate_counts_equal_each_filtered_queue_listing(aggregate_api, filters):
    _ctx, client = aggregate_api
    response = client.get("/v1/leads/queue-counts", params=filters)
    assert response.status_code == 200, response.text
    counts = response.json()["counts"]
    for queue, _label, _description in service.QUEUES:
        page = client.get("/v1/leads", params={**filters, "queue": queue, "limit": 100})
        assert page.status_code == 200, page.text
        assert not page.json()["has_more"]
        assert counts[queue] == len(page.json()["items"])


def test_empty_filter_result_returns_zero_in_every_queue(aggregate_api):
    _ctx, client = aggregate_api
    counts = client.get("/v1/leads/queue-counts", params={"keyword": "존재하지않는단어"}).json()["counts"]
    assert counts == {qid: 0 for qid, _label, _description in service.QUEUES}


def test_counts_execute_one_lead_query_including_korean_filter(aggregate_api):
    ctx, client = aggregate_api
    queries = []

    def capture(_conn, _cursor, statement, parameters, _context, _many):
        if "FROM leads" in statement:
            queries.append((statement, parameters))

    event.listen(ctx.db.engine, "before_cursor_execute", capture)
    try:
        response = client.get("/v1/leads/queue-counts", params={"keyword": "홈페이지", "recommendation": "needs_review"})
        assert response.status_code == 200, response.text
    finally:
        event.remove(ctx.db.engine, "before_cursor_execute", capture)
    assert len(queries) == 1
    assert queries[0][0].count("sum(CASE") == 7
    assert "%홈페이지%" in queries[0][1]
