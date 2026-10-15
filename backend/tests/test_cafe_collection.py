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
    assert seen == [NOW - timedelta(days=30)]


@pytest.mark.parametrize('unknown', ['date', 'incomplete'])
def test_unverified_reader_does_not_store_or_advance_checkpoint(ctx, unknown):
    prepare(ctx)
    value = post('one')
    if unknown == 'date': value.published = DateInfo()
    page = CafePage([CafePost(value, None)], unknown != 'incomplete')
    with pytest.raises(svc.CafeError): collect(ctx, lambda board, since: page)
    with ctx.db.session() as s:
        assert s.get(AppSetting, 'cafe_collection_checkpoints') is None
        assert s.scalar(select(Lead)) is None


def test_actual_source_cannot_bypass_browser_gate(client):
    assert client.post('/v1/cafe/consent', json={'version': svc.CONSENT_VERSION}).status_code == 200
    response = client.post('/v1/runs', json={'source_id': 'daangn_cafe', 'kind': 'discovery'})
    assert response.status_code == 409 and response.json()['error']['code'] == 'cafe_browser_queue_required'


def test_scope_b_template_portfolio_and_relative_date(ctx):
    from worklead.cafe.posting import template
    from worklead.cafe.parsing import relative_date
    prepare(ctx)
    with ctx.db.session() as s: title = template(s).title
    values = [post('same-title'), post('portfolio'), post('other'), post('old-relative', 31)]
    values[0].title = title
    values[1].body += ' 포트폴리오 https://www.seyj.kr/projects'
    for value, raw in zip(values, ('3일 전', '1일 전', '3일 전', '31일 전')):
        value.published = relative_date(raw, NOW)
    result = collect(ctx, lambda *_: CafePage([CafePost(p, None) for p in values], True))
    assert result['created'] == 1 and result['skipped_own'] == 2 and result['skipped_old'] == 1
    with ctx.db.session() as s:
        record = s.scalar(select(SourceRecord))
        assert record.source_post_id == 'other' and record.published_precision == 'approximate'

@pytest.mark.parametrize('joined', [False, None, True])
def test_explicit_collect_only_opt_in_and_schedule(ctx, joined):
    from worklead.models import Cafe, Source
    prepare(ctx)
    with ctx.db.session() as s:
        cafe = s.scalar(select(Cafe)); cafe.status, cafe.joined = 'candidate', joined
        s.get(Source, 'daangn_cafe').auto_collect_enabled = True
        if joined is not True:
            with pytest.raises(svc.CafeError): svc.update_cafe(s, cafe.id, 'collect_only', NOW)
            assert svc.schedule_due(s, NOW) is None
            return
        svc.update_cafe(s, cafe.id, 'collect_only', NOW)
        assert svc.schedule_due(s, NOW).kind == 'collect'
        assert cafe.approved_at is None and not s.get(CafeBoard, 'test-board').approved
    result = collect(ctx, lambda *_: CafePage([CafePost(post('read-only'), False)], True))
    assert result['created'] == 1


def test_no_collection_target_is_actionable_not_empty_success(ctx):
    from worklead.models import Cafe
    prepare(ctx)
    with ctx.db.session() as s: s.scalar(select(Cafe)).status = 'candidate'
    with pytest.raises(svc.CafeError, match='수집 전용') as err:
        collect(ctx, lambda *_: pytest.fail('must not read'))
    assert err.value.code == 'cafe_collection_target_required'


def test_collect_only_revocation_during_read_prevents_storage(ctx):
    from worklead.models import Cafe
    prepare(ctx)
    with ctx.db.session() as s:
        row = s.scalar(select(Cafe)); row.joined, row.status = True, 'collect_only'
        id_ = row.id
    def reader(*_):
        with ctx.db.session() as s: svc.update_cafe(s, id_, 'candidate', NOW)
        return CafePage([CafePost(post('revoked'), False)], True)
    with pytest.raises(svc.CafeError) as err: collect(ctx, reader)
    assert err.value.code == 'cafe_scope_changed'
    with ctx.db.session() as s:
        assert s.scalar(select(Lead)) is None
        assert s.get(AppSetting, 'cafe_collection_checkpoints') is None


def test_daily_budget_keeps_completed_counts_and_rotates_unfinished_boards_first(ctx):
    from worklead.models import Cafe
    prepare(ctx)
    with ctx.db.session() as s:
        row=s.scalar(select(Cafe))
        s.add(CafeBoard(id='z-second-board',cafe_id=row.id,url=row.url+'/boards/second',name='구인구직',kind='job_seek',post_allowed=False,approved=False))
    order=[]
    def limited(board,_since):
        order.append(board.id)
        if board.id=='z-second-board':raise svc.CafeError(409,'cafe_daily_budget','오늘 한도')
        return CafePage([CafePost(post('completed-first'),False)],True)
    with pytest.raises(svc.CafeError) as err:collect(ctx,limited)
    assert order==['test-board','z-second-board']
    assert err.value.partial_result['created']==1 and err.value.partial_result['boards']==1
    order=[]
    collect(ctx,lambda board,_since:(order.append(board.id)or CafePage([],True)))
    assert order==['z-second-board','test-board']
