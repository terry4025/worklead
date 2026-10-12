from datetime import timedelta

from sqlalchemy import select

from tests.conftest import client_for, make_ctx
from tests.test_source_fields import NOW, sample
from worklead.engine.pipeline import apply_analysis, ingest
from worklead.models import FieldEvidence, Lead, SourceRecord


def test_work_start_date_keeps_its_source_span_after_reanalysis(tmp_path):
    ctx = make_ctx(tmp_path)
    try:
        post = sample()
        post.body += " 10월 10일부터 시작합니다."
        with ctx.db.session() as s:
            first = ingest(s, "daangn-alba", post, parser_version="test", now=NOW)
        with ctx.db.session() as s:
            lead, rec = s.get(Lead, first.lead_id), s.get(SourceRecord, first.record_id)
            apply_analysis(s, lead, rec, now=NOW + timedelta(hours=1))
            assert rec.work_start_at is not None and rec.work_start_precision == "day"
            evidence = list(s.scalars(select(FieldEvidence).where(FieldEvidence.lead_id == lead.id, FieldEvidence.field == "work_start")))
            assert len(evidence) == 1
            ev = evidence[0]
            assert post.body[ev.span_start:ev.span_end] == ev.quote
            assert "10월 10일부터" in ev.quote and ev.value == "day"
        detail = client_for(ctx).get(f"/v1/leads/{first.lead_id}").json()
        assert len(detail["evidence"]["work_start"]) == 1
        assert detail["work_period"]["start"]["precision"] == "day"
        assert detail["deadline"]["at"] != detail["work_period"]["start"]["at"]
    finally:
        ctx.db.dispose()
