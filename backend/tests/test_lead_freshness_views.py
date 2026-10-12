"""Freshness is derived on read without overwriting stored judgments or user data."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tests.conftest import client_for, make_ctx
from worklead.models import Lead, SourceRecord
from worklead.services import leads as service
from worklead.services.settings import put_setting


@pytest.fixture
def freshness_api(tmp_path, monkeypatch):
    ctx = make_ctx(tmp_path)
    now = datetime(2026, 9, 30, 12, tzinfo=UTC)
    monkeypatch.setattr(service, "utcnow", lambda: now)
    specs = [
        ("fresh", 23, "recommended", None, "new"),
        ("boundary", 24, "recommended", None, "new"),
        ("stale", 25, "recommended", "interested", "reviewing"),
        ("missing", None, "recommended", None, "new"),
        ("review", 25, "needs_review", None, "new"),
        ("excluded", 25, "excluded", None, "new"),
        ("active", 25, "recommended", "interested", "contacted"),
    ]
    with ctx.db.session() as s:
        for lid, age, recommendation, mark, stage in specs:
            rid = "record_" + lid
            s.add(Lead(
                id=lid, primary_record_id=rid, source_id="manual", title=lid, recommendation=recommendation,
                source_status="open", last_checked_at=now - timedelta(hours=age) if age is not None else None,
                user_mark=mark, sales_stage=stage, memo="내 메모", draft_text="내 초안", draft_edited=True,
                reasons=[{"tone": "positive", "text": "저장된 제작 의뢰 판정"}],
            ))
            s.flush()
            s.add(SourceRecord(id=rid, lead_id=lid, source_id="manual", title=lid, body_text="합성 최신성 표본", content_hash=lid, parser_version="test"))
    yield ctx, client_for(ctx), now
    ctx.db.dispose()


def ids(client, **params):
    response = client.get("/v1/leads", params=params)
    assert response.status_code == 200, response.text
    return {row["id"] for row in response.json()["items"]}


def test_stale_recommendations_move_to_review_in_queues_filters_counts_and_details(freshness_api):
    ctx, client, _now = freshness_api
    assert ids(client, queue="recommended") == {"fresh", "boundary"}
    assert ids(client, queue="needs_review") == {"stale", "missing", "review"}
    assert ids(client, recommendation="recommended") == {"fresh", "boundary"}
    assert ids(client, recommendation="needs_review") == {"stale", "missing", "review", "active"}
    assert ids(client, recommendation=["recommended", "needs_review"]) == {"fresh", "boundary", "stale", "missing", "review", "active"}
    counts = client.get("/v1/leads/queue-counts").json()["counts"]
    assert counts["recommended"] == 2 and counts["needs_review"] == 3
    filtered_counts = client.get("/v1/leads/queue-counts", params={"recommendation": "needs_review"}).json()["counts"]
    assert filtered_counts["all"] == 4 and filtered_counts["recommended"] == 0
    detail = client.get("/v1/leads/stale").json()
    assert detail["recommendation"] == "needs_review" and detail["recheck_due"]
    assert any(reason["tone"] == "caution" and "모집 재확인 필요" in reason["text"] for reason in detail["reasons"])
    assert detail["memo"] == "내 메모" and detail["draft"]["text"] == "내 초안"
    with ctx.db.session() as s:
        saved = s.get(Lead, "stale")
        assert saved.recommendation == "recommended"
        assert saved.reasons == [{"tone": "positive", "text": "저장된 제작 의뢰 판정"}]
        assert saved.user_mark == "interested" and saved.sales_stage == "reviewing"


def test_ttl_boundary_is_fresh_and_user_queues_are_preserved(freshness_api):
    _ctx, client, _now = freshness_api
    boundary = client.get("/v1/leads/boundary").json()
    assert boundary["recommendation"] == "recommended" and not boundary["recheck_due"]
    assert len(boundary["reasons"]) == 1
    assert ids(client, queue="all") == {"fresh", "boundary", "stale", "missing", "review", "excluded", "active"}
    assert ids(client, queue="interested") == {"stale"}
    assert ids(client, queue="active") == {"active"}


def test_fresh_recheck_restores_recommendation_without_changing_user_fields(freshness_api):
    ctx, client, now = freshness_api
    with ctx.db.session() as s:
        lead = s.get(Lead, "stale")
        lead.last_checked_at = now
    assert ids(client, queue="recommended") == {"fresh", "boundary", "stale"}
    restored = client.get("/v1/leads/stale").json()
    assert restored["recommendation"] == "recommended" and not restored["recheck_due"]
    assert len(restored["reasons"]) == 1
    assert restored["memo"] == "내 메모" and restored["user_mark"] == "interested" and restored["sales_stage"] == "reviewing"


def test_current_configured_ttl_is_used_without_reanalysis(freshness_api):
    ctx, client, _now = freshness_api
    with ctx.db.session() as s:
        put_setting(s, "recheck", {"ttl_hours": 26})
    assert ids(client, queue="recommended") == {"fresh", "boundary", "stale"}
    assert ids(client, queue="needs_review") == {"missing", "review"}


def test_expired_confirmation_reason_is_first_and_keeps_the_three_reason_limit(freshness_api):
    ctx, client, _now = freshness_api
    original = [{"tone": "positive", "text": f"기존 근거 {i}"} for i in range(3)]
    with ctx.db.session() as s:
        s.get(Lead, "stale").reasons = original
    detail = client.get("/v1/leads/stale").json()
    assert len(detail["reasons"]) == 3
    assert detail["reasons"][0]["tone"] == "caution" and "모집 재확인 필요" in detail["reasons"][0]["text"]
    assert detail["reasons"][1:] == original[:2]
    listing = client.get("/v1/leads", params={"queue": "needs_review"}).json()["items"]
    assert next(row for row in listing if row["id"] == "stale")["reasons"] == detail["reasons"]
    with ctx.db.session() as s:
        assert s.get(Lead, "stale").reasons == original
