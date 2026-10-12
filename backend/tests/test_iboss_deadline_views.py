"""Stored i-boss deadlines expire on read; observed and user data stay intact."""
from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from tests.conftest import client_for, make_ctx
from worklead.models import Lead, SourceRecord
from worklead.services import leads as service


DEADLINE = datetime(2026, 9, 29, 15, tzinfo=UTC)  # September 30, 00:00 KST
MIDNIGHT = datetime(2026, 9, 30, 15, tzinfo=UTC)  # October 1, 00:00 KST


@pytest.fixture
def deadline_api(tmp_path, monkeypatch):
    ctx = make_ctx(tmp_path)
    clock = {"now": MIDNIGHT}
    monkeypatch.setattr(service, "utcnow", lambda: clock["now"])

    def add(lid, *, lead_patch=None, record_patch=None):
        with ctx.db.session() as s:
            lead = Lead(**{
                "id": lid, "primary_record_id": "record_" + lid, "source_id": "iboss",
                "title": "합성 개발 의뢰 " + lid, "source_status": "open", "access_status": "accessible",
                "recommendation": "recommended", "last_checked_at": MIDNIGHT - timedelta(hours=1),
                "memo": "내 메모", "draft_text": "내 초안", "draft_edited": True,
                "reasons": [{"tone": "positive", "text": "저장된 제작 의뢰 판정"}],
                **(lead_patch or {}),
            })
            s.add(lead)
            s.flush()
            s.add(SourceRecord(**{
                "id": "record_" + lid, "lead_id": lid, "source_id": lead.source_id,
                "title": lead.title, "body_text": "합성 날짜 표본", "content_hash": lid,
                "parser_version": "test", "source_status": lead.source_status,
                "last_checked_at": lead.last_checked_at, "deadline_at": DEADLINE,
                "deadline_precision": "day", "deadline_raw": "2026.09.30",
                **(record_patch or {}),
            }))

    yield ctx, client_for(ctx), clock, add
    ctx.db.dispose()


def items(client, **params):
    response = client.get("/v1/leads", params={"queue": "all", "limit": 100, **params})
    assert response.status_code == 200, response.text
    return response.json()["items"]


def ids(client, **params):
    return {row["id"] for row in items(client, **params)}


@pytest.mark.parametrize("now, expired", [
    (DEADLINE, False),
    (MIDNIGHT - timedelta(seconds=1), False),
    (MIDNIGHT - timedelta(microseconds=1), False),
    (MIDNIGHT, True),
    (MIDNIGHT + timedelta(hours=8), True),
])
def test_kst_deadline_includes_whole_day_and_expires_at_midnight(deadline_api, now, expired):
    _ctx, client, clock, add = deadline_api
    add("boundary", lead_patch={"last_checked_at": DEADLINE})
    clock["now"] = now
    expected = "excluded" if expired else "recommended"
    queue = "auto_excluded" if expired else "recommended"
    assert ids(client, queue=queue) == {"boundary"}
    assert ids(client, recommendation=expected) == {"boundary"}
    detail = client.get("/v1/leads/boundary").json()
    assert detail["recommendation"] == expected
    assert detail["source_status"] == ("closed" if expired else "open")
    assert detail["recheck_due"] is False
    assert items(client)[0]["reasons"] == detail["reasons"]
    assert any("저장된 날짜 기준" in r["text"] for r in detail["reasons"]) is expired


def test_expired_recommended_review_and_stale_leads_use_same_closed_filters(deadline_api):
    _ctx, client, _clock, add = deadline_api
    add("recommended")
    add("review", lead_patch={"recommendation": "needs_review"})
    add("stale", lead_patch={"last_checked_at": MIDNIGHT - timedelta(days=2)})
    add("unknown", lead_patch={"source_status": "unknown", "recommendation": "needs_review"})
    add("blocked", lead_patch={"access_status": "blocked"})
    expected = {"recommended", "review", "stale", "unknown", "blocked"}
    for queue in ("recommended", "needs_review"):
        assert not ids(client, queue=queue)
    assert ids(client, queue="auto_excluded") == expected
    assert ids(client, recommendation="excluded") == expected
    assert not ids(client, recommendation=["recommended", "needs_review"])
    assert ids(client, source_status="closed") == expected
    assert ids(client, recruit="closed") == expected
    for recruit in ("not_closed", "open_fresh", "recheck", "unknown", "access_issue"):
        assert not ids(client, recruit=recruit)
    assert not ids(client, source_status=["open", "unknown"])


@pytest.mark.parametrize("lead_patch, record_patch", [
    ({}, {"deadline_precision": "unknown"}),
    ({}, {"deadline_precision": "approximate"}),
    ({}, {"deadline_precision": "exact"}),
    ({}, {"deadline_at": None}),
    ({}, {"deadline_at": MIDNIGHT}),
    ({"source_id": "daangn-alba"}, {}),
    ({}, {"source_id": "manual"}),
])
def test_only_iboss_primary_explicit_day_deadlines_are_applied(deadline_api, lead_patch, record_patch):
    _ctx, client, _clock, add = deadline_api
    add("unchanged", lead_patch=lead_patch, record_patch=record_patch)
    assert ids(client, queue="recommended") == {"unchanged"}
    detail = client.get("/v1/leads/unchanged").json()
    assert detail["recommendation"] == "recommended" and detail["source_status"] == "open"
    assert not ids(client, queue="auto_excluded")


def test_secondary_deadline_does_not_expire_primary_or_duplicate_aggregate_counts(deadline_api):
    ctx, client, _clock, add = deadline_api
    add("future", record_patch={"deadline_at": MIDNIGHT})
    add("expired")
    with ctx.db.session() as s:
        for lid in ("future", "expired"):
            s.add(SourceRecord(
                id="secondary_" + lid, lead_id=lid, source_id="iboss", title=lid,
                content_hash="secondary_" + lid, parser_version="test",
                deadline_at=DEADLINE, deadline_precision="day",
            ))
    assert ids(client, queue="recommended") == {"future"}
    assert ids(client, queue="auto_excluded") == {"expired"}
    counts = client.get("/v1/leads/queue-counts").json()["counts"]
    assert counts["all"] == 2 and counts["recommended"] == 1 and counts["auto_excluded"] == 1


@pytest.mark.parametrize("filters", [
    {}, {"recommendation": "excluded"}, {"recommendation": "recommended"},
    {"recommendation": ["excluded", "needs_review"]}, {"source_status": "closed"},
    {"source_status": ["open", "unknown"]}, {"recruit": "closed"},
    {"recruit": "not_closed"}, {"recruit": "open_fresh"}, {"recruit": "recheck"},
    {"recruit": "unknown"}, {"recruit": "access_issue"},
])
def test_deadline_aggregate_matches_all_filtered_user_and_automatic_queues(deadline_api, filters):
    _ctx, client, _clock, add = deadline_api
    add("expired")
    add("interested", lead_patch={"user_mark": "interested", "recommendation": "needs_review"})
    add("active", lead_patch={"user_mark": "interested", "sales_stage": "contacted"})
    add("dismissed", lead_patch={"user_mark": "dismissed"})
    add("future", record_patch={"deadline_at": MIDNIGHT})
    add("unknown", lead_patch={"source_status": "unknown"}, record_patch={"deadline_precision": "unknown"})
    add("blocked", lead_patch={"access_status": "blocked"}, record_patch={"deadline_at": None})
    response = client.get("/v1/leads/queue-counts", params=filters)
    assert response.status_code == 200, response.text
    counts = response.json()["counts"]
    for queue, _label, _description in service.QUEUES:
        assert counts[queue] == len(ids(client, queue=queue, **filters))
    assert ids(client, queue="interested") == {"interested"}
    assert ids(client, queue="active") == {"active"}
    assert ids(client, queue="dismissed") == {"dismissed"}
    assert ids(client, queue="auto_excluded") == {"expired", "interested"}


def test_read_projection_preserves_all_stored_lead_and_record_fields(deadline_api):
    ctx, client, _clock, add = deadline_api
    original_reasons = [{"tone": "positive", "text": f"저장된 근거 {i}"} for i in range(3)]
    add("preserved", lead_patch={"user_mark": "interested", "sales_stage": "reviewing", "reasons": original_reasons})
    add("deleted", lead_patch={"source_status": "deleted", "recommendation": "excluded"})

    def stored():
        with ctx.db.session() as s:
            return deepcopy([
                [dict(row) for row in s.execute(select(model.__table__)).mappings()]
                for model in (Lead, SourceRecord)
            ])

    before = stored()
    for queue, _label, _description in service.QUEUES:
        items(client, queue=queue)
    client.get("/v1/leads/queue-counts")
    detail = client.get("/v1/leads/preserved").json()
    assert detail["source_status"] == "closed" and detail["recommendation"] == "excluded"
    assert detail["memo"] == "내 메모" and detail["draft"]["text"] == "내 초안"
    assert detail["user_mark"] == "interested" and detail["sales_stage"] == "reviewing"
    assert len(detail["reasons"]) == 3 and detail["reasons"][0]["tone"] == "negative"
    assert detail["reasons"][1:] == original_reasons[:2]
    assert client.get("/v1/leads/deleted").json()["source_status"] == "deleted"
    assert stored() == before


def test_later_confirmed_deadline_extension_restores_view_without_reanalysis(deadline_api):
    ctx, client, _clock, add = deadline_api
    add("extended", lead_patch={"user_mark": "interested"})
    assert ids(client, queue="auto_excluded") == {"extended"}
    with ctx.db.session() as s:
        s.get(SourceRecord, "record_extended").deadline_at = MIDNIGHT + timedelta(days=1)
    assert ids(client, queue="recommended") == {"extended"}
    assert not ids(client, queue="auto_excluded")
    detail = client.get("/v1/leads/extended").json()
    assert detail["source_status"] == "open" and detail["user_mark"] == "interested"
    assert len(detail["reasons"]) == 1
