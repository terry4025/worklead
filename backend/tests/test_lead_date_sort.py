"""Native date ordering, precise cursors and index-backed pagination."""
from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event

from tests.conftest import client_for, make_ctx
from worklead.models import Lead, SourceRecord
from worklead.services.leads import decode_cursor


@pytest.fixture
def date_api(tmp_path):
    ctx = make_ctx(tmp_path)
    now = datetime(2026, 9, 30, 12, tzinfo=UTC)
    moment = now - timedelta(hours=1)
    dates = {
        "date_latest": now,
        "date_micro_newer_a": moment.replace(microsecond=123201),
        "date_micro_older_z": moment.replace(microsecond=123101),
        "date_same_z": now - timedelta(hours=2),
        "date_same_a": now - timedelta(hours=2),
        "date_null_z": None,
        "date_null_a": None,
    }
    with ctx.db.session() as s:
        for lid, at in dates.items():
            rid = "record_" + lid
            s.add(Lead(id=lid, primary_record_id=rid, source_id="manual", title=lid, recommendation="needs_review", published_at=at, last_checked_at=at))
            s.flush()
            s.add(SourceRecord(id=rid, lead_id=lid, source_id="manual", title=lid, body_text="합성 날짜 검증", content_hash=lid, parser_version="test"))
    yield ctx, client_for(ctx), list(dates)
    ctx.db.dispose()


@pytest.mark.parametrize("sort", ["published", "checked"])
@pytest.mark.parametrize("limit", [1, 2, 3])
def test_date_pages_preserve_microseconds_equal_timestamp_ties_and_null_tail(date_api, sort, limit):
    _ctx, client, expected = date_api
    cursor = None
    seen = []
    while True:
        params = {"sort": sort, "limit": limit}
        if cursor:
            params["cursor"] = cursor
        response = client.get("/v1/leads", params=params)
        assert response.status_code == 200, response.text
        page = response.json()
        ids = [row["id"] for row in page["items"]]
        assert not set(ids).intersection(seen)
        seen.extend(ids)
        cursor = page["next_cursor"]
        if not cursor:
            break
        decoded = decode_cursor(cursor)
        assert decoded["cv"] == 2
        if ids[-1].startswith("date_null"):
            assert decoded["v"] is None
        else:
            parsed = datetime.fromisoformat(decoded["v"])
            assert parsed.tzinfo is not None
            if ids[-1] == "date_micro_newer_a":
                assert parsed.microsecond == 123201
    assert seen == expected


@pytest.mark.parametrize("sort,index", [("published", "ix_leads_published"), ("checked", "ix_leads_checked")])
def test_date_first_and_next_page_use_existing_date_index_without_temp_sort(date_api, sort, index):
    ctx, client, _expected = date_api
    queries = []

    def capture(_conn, _cursor, statement, parameters, _context, _many):
        if "FROM leads" in statement and "ORDER BY" in statement:
            queries.append((statement, parameters))

    event.listen(ctx.db.engine, "before_cursor_execute", capture)
    try:
        first = client.get("/v1/leads", params={"sort": sort, "limit": 2}).json()
        second = client.get("/v1/leads", params={"sort": sort, "limit": 2, "cursor": first["next_cursor"]})
        assert second.status_code == 200, second.text
    finally:
        event.remove(ctx.db.engine, "before_cursor_execute", capture)
    assert len(queries) == 2
    with ctx.db.engine.connect() as connection:
        for statement, parameters in queries:
            plan = " ".join(row[3] for row in connection.exec_driver_sql("EXPLAIN QUERY PLAN " + statement, parameters))
            assert index in plan
            assert "TEMP B-TREE" not in plan


@pytest.mark.parametrize("sort", ["published", "checked"])
@pytest.mark.parametrize("invalid", ["not-a-date", "2026-09-30T12:00:00", 12, {}, []])
def test_bad_date_cursors_return_client_error(date_api, sort, invalid):
    _ctx, client, _expected = date_api
    first = client.get("/v1/leads", params={"sort": sort, "limit": 2}).json()
    payload = decode_cursor(first["next_cursor"])
    payload["v"] = invalid
    cursor = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    response = client.get("/v1/leads", params={"sort": sort, "limit": 2, "cursor": cursor})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_cursor"


@pytest.mark.parametrize("sort", ["published", "checked"])
def test_previous_date_cursor_requires_first_page_refresh(date_api, sort):
    _ctx, client, _expected = date_api
    payload = decode_cursor(client.get("/v1/leads", params={"sort": sort, "limit": 2}).json()["next_cursor"])
    payload.pop("cv")
    payload["v"] = "20260930110000.123"
    cursor = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    response = client.get("/v1/leads", params={"sort": sort, "limit": 2, "cursor": cursor})
    assert response.status_code == 400
    assert "처음부터" in response.json()["error"]["message"]
