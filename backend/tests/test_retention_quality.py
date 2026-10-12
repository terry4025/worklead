from dataclasses import replace
from datetime import timedelta

from sqlalchemy import func, select

from tests.conftest import make_ctx
from tests.test_source_fields import NOW, sample
from worklead.engine.pipeline import ingest
from worklead.engine.worker import housekeeping
from worklead.models import EventOutbox, Lead, Snapshot, SourceRecord


def test_expired_raw_body_is_removed_from_the_search_index(tmp_path):
    ctx = make_ctx(tmp_path)
    try:
        post = replace(sample(), deadline=None)
        with ctx.db.session() as s:
            first = ingest(s, "daangn-alba", post, parser_version="test", now=NOW)
            lead = s.get(Lead, first.lead_id)
            lead.memo = "사용자 메모 보존"
        ctx.clock = lambda: NOW + timedelta(days=31)
        housekeeping(ctx)
        with ctx.db.session() as s:
            rec = s.get(SourceRecord, first.record_id)
            lead = s.get(Lead, first.lead_id)
            assert rec.body_text is None
            assert lead.search_text == post.title.lower()
            assert lead.memo == "사용자 메모 보존"
    finally:
        ctx.db.dispose()


def test_unchanged_refetch_restores_expired_body_and_renews_retention(tmp_path):
    ctx = make_ctx(tmp_path)
    try:
        post = replace(sample(), deadline=None)
        with ctx.db.session() as s:
            first = ingest(s, "daangn-alba", post, parser_version="old", now=NOW)
        renewed_at = NOW + timedelta(days=31)
        ctx.clock = lambda: renewed_at
        housekeeping(ctx)
        with ctx.db.session() as s:
            assert s.get(SourceRecord, first.record_id).body_text is None
            before_events = s.scalar(select(func.count()).select_from(EventOutbox).where(EventOutbox.type == "lead.created"))
            second = ingest(s, "daangn-alba", post, parser_version="new", now=renewed_at)
            assert second.kind == "unchanged" and second.lead_id == first.lead_id
            rec = s.get(SourceRecord, first.record_id)
            lead = s.get(Lead, first.lead_id)
            assert rec.body_text == post.body
            assert rec.raw_retained_until == renewed_at + timedelta(days=30)
            assert rec.parser_version == "new"
            assert post.body.lower() in lead.search_text
            assert lead.demand_intent == "buyer_project"
            assert s.scalar(select(func.count()).select_from(Lead)) == 1
            assert s.scalar(select(func.count()).select_from(EventOutbox).where(EventOutbox.type == "lead.created")) == before_events
            fresh_snapshot = s.scalar(select(Snapshot).where(Snapshot.captured_at == renewed_at))
            assert fresh_snapshot is not None and fresh_snapshot.body_text == post.body
        housekeeping(ctx)
        with ctx.db.session() as s:
            assert s.get(SourceRecord, first.record_id).body_text == post.body
            assert s.scalar(select(Snapshot).where(Snapshot.captured_at == NOW)).body_text is None
    finally:
        ctx.db.dispose()


def test_unchanged_confirmations_renew_body_without_duplicate_snapshots(tmp_path):
    ctx = make_ctx(tmp_path)
    try:
        post = replace(sample(), deadline=None)
        with ctx.db.session() as s:
            first = ingest(s, "daangn-alba", post, parser_version="test", now=NOW)
        with ctx.db.session() as s:
            renewed_at = NOW + timedelta(days=20)
            ingest(s, "daangn-alba", post, parser_version="test", now=renewed_at)
            assert s.get(SourceRecord, first.record_id).raw_retained_until == renewed_at + timedelta(days=30)
            assert s.scalar(select(func.count()).select_from(Snapshot)) == 1
        ctx.clock = lambda: NOW + timedelta(days=31)
        housekeeping(ctx)
        with ctx.db.session() as s:
            assert s.get(SourceRecord, first.record_id).body_text == post.body
    finally:
        ctx.db.dispose()


def test_unchanged_refetch_uses_fresh_source_status_after_a_legacy_date_misclassification(tmp_path):
    ctx = make_ctx(tmp_path)
    try:
        post = replace(sample(), deadline=None)
        with ctx.db.session() as s:
            first = ingest(s, "daangn-alba", post, parser_version="old", now=NOW)
            rec = s.get(SourceRecord, first.record_id)
            rec.source_status = "closed"
            s.get(Lead, first.lead_id).source_status = "closed"
        with ctx.db.session() as s:
            second = ingest(s, "daangn-alba", post, parser_version="new", now=NOW + timedelta(hours=2))
            assert second.kind == "unchanged"
            assert s.get(SourceRecord, first.record_id).source_status == "open"
            assert s.get(Lead, first.lead_id).source_status == "open"
    finally:
        ctx.db.dispose()
