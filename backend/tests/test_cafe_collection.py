from datetime import UTC, datetime, timedelta
import pytest
from sqlalchemy import select
from worklead.analysis.types import DateInfo
from worklead.cafe.collection import CafePage, CafePost, collect
from worklead.models import AppSetting, CafeBoard, DuplicateLink, Lead, SourceRecord
from worklead.services import cafe as svc
from worklead.sources.base import ParsedPost
from worklead.engine.pipeline import ingest

NOW = datetime(2026, 10, 12, tzinfo=UTC)


def prepare(ctx):
    ctx.clock = lambda: NOW
    with ctx.db.session() as s:
        svc.accept_consent(s, svc.CONSENT_VERSION, NOW)
        svc.set_session(s, 'logged_in', NOW)
        row = svc.add_cafe(s, 'https://cafe.daangn.com/synthetic', NOW)
        row.status = 'eligible'
        s.add(CafeBoard(id='test-board', cafe_id=row.id, name='외주', url=row.url + '/boards/jobs-test', kind='job_offer', post_allowed=False, approved=False))


def post(id_, age=1):
    url = 'https://cafe.daangn.com/synthetic/posts/' + id_
    return ParsedPost('웹사이트 개발 의뢰', '원격 홈페이지 제작 의뢰합니다. 프로젝트 단위 견적 부탁드립니다.', url, url, id_, published=DateInfo(NOW - timedelta(days=age), 'exact', None))


def test_recent_incremental_own_exclusion_and_cross_source_relation(ctx):
    prepare(ctx)
    with ctx.db.session() as s:
        copy = post('alba')
        copy.original_url = copy.canonical_url = 'https://jobs.daangn.com/job-posts/fixture00001'
        ingest(s, 'daangn-alba', copy, parser_version='test', now=NOW)
    page = CafePage([CafePost(post('one'), False), CafePost(post('mine'), True), CafePost(post('old', 31), False)], True)
    result = collect(ctx, lambda board, since: page)
    assert result['created'] == 1 and result['skipped_own'] == 1 and result['skipped_old'] == 1
    with ctx.db.session() as s:
        leads = list(s.scalars(select(Lead)))
        assert len(leads) == 2 and leads[0].id != leads[1].id
        assert s.scalar(select(DuplicateLink)) is not None
        assert len(list(s.scalars(select(SourceRecord).where(SourceRecord.source_id == 'daangn_cafe')))) == 1
    seen = []
    collect(ctx, lambda board, since: (seen.append(since) or page))
    assert seen == [NOW]


@pytest.mark.parametrize('unknown', ['ownership', 'date', 'incomplete'])
def test_unverified_reader_does_not_store_or_advance_checkpoint(ctx, unknown):
    prepare(ctx)
    value = post('one')
    if unknown == 'date': value.published = DateInfo()
    page = CafePage([CafePost(value, None if unknown == 'ownership' else False)], unknown != 'incomplete')
    with pytest.raises(svc.CafeError): collect(ctx, lambda board, since: page)
    with ctx.db.session() as s:
        assert s.get(AppSetting, 'cafe_collection_checkpoints') is None
        assert s.scalar(select(Lead)) is None


def test_actual_source_cannot_bypass_browser_gate(client):
    assert client.post('/v1/cafe/consent', json={'version': svc.CONSENT_VERSION}).status_code == 200
    response = client.post('/v1/runs', json={'source_id': 'daangn_cafe', 'kind': 'discovery'})
    assert response.status_code == 409 and response.json()['error']['code'] == 'cafe_collection_unverified'
