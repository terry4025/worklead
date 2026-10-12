from datetime import UTC, datetime, timedelta

from tests.conftest import make_ctx
from worklead.analysis.pay import parse_pay
from worklead.analysis.types import DateInfo
from worklead.engine.pipeline import apply_analysis, ingest
from worklead.models import Lead, SourceRecord
from worklead.sources.base import ParsedPost

NOW = datetime(2026, 9, 28, tzinfo=UTC)


def sample():
    return ParsedPost(title='홈페이지 제작 의뢰', body='홈페이지 제작 의뢰합니다. 원격으로 진행합니다.',
                      original_url='https://jobs.daangn.com/job-posts/fixture00001',
                      canonical_url='https://jobs.daangn.com/job-posts/fixture00001', source_post_id='fixture00001',
                      pay=parse_pay('건당 100만원'), source_status='open',
                      deadline=DateInfo(NOW + timedelta(days=7), 'day', '2026-10-05'))


def test_reanalysis_and_unchanged_refetch_preserve_separate_pay_and_deadline(tmp_path):
    ctx = make_ctx(tmp_path)
    p = sample()
    with ctx.db.session() as s:
        first = ingest(s, 'daangn-alba', p, parser_version='test', now=NOW)
        lead = s.get(Lead, first.lead_id)
        lead.memo = '사용자 견적 메모'
        lead.user_mark = 'interested'
    for _ in range(2):
        with ctx.db.session() as s:
            lead, rec = s.get(Lead, first.lead_id), s.get(SourceRecord, first.record_id)
            result = apply_analysis(s, lead, rec, now=NOW + timedelta(days=1))
            assert result.pay.min == 1_000_000 and result.pay.unit == 'project'
            assert result.deadline.at == NOW + timedelta(days=7)
            assert result.pay.evidence[0].quote == '건당 100만원'
            assert lead.memo == '사용자 견적 메모' and lead.user_mark == 'interested'
    with ctx.db.session() as s:
        rec = s.get(SourceRecord, first.record_id)
        # Simulate legacy data that lost separate fields in old reanalysis.
        rec.pay_raw, rec.pay_min, rec.pay_max, rec.pay_unit = None, None, None, 'unknown'
        second = ingest(s, 'daangn-alba', p, parser_version='test', now=NOW + timedelta(hours=2))
        assert second.kind == 'unchanged' and second.lead_id == first.lead_id
        assert s.get(SourceRecord, first.record_id).pay_min == 1_000_000


def test_changed_source_can_remove_the_previously_displayed_amount(tmp_path):
    ctx = make_ctx(tmp_path)
    p = sample()
    with ctx.db.session() as s:
        first = ingest(s, 'daangn-alba', p, parser_version='test', now=NOW)
    p.pay = None
    p.deadline = None
    with ctx.db.session() as s:
        ingest(s, 'daangn-alba', p, parser_version='test', now=NOW + timedelta(hours=1))
        rec = s.get(SourceRecord, first.record_id)
        assert rec.pay_min is None and rec.pay_unit == 'unknown'
        assert rec.deadline_at is None
