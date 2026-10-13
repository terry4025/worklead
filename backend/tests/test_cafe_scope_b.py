from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from tests.test_cafe_collection import NOW
from tests.test_cafe_posting import Browser, ready
from worklead.cafe.browser import EdgeBrowser
from worklead.cafe.parsing import CafePageError, board_listing_ready, post_relative_time, relative_date, template_match
from worklead.cafe.posting import publish
from worklead.models import CafePostAttempt, Source
from worklead.services import cafe as svc


@pytest.mark.parametrize('raw,days', [('3일 전', 3), ('30일 전', 30), ('31일 전', 31), ('1개월 전', 30), ('방금 전', 0)])
def test_relative_dates_retain_approximate_evidence(raw, days):
    value = relative_date(raw, NOW)
    assert value.at == NOW - timedelta(days=days)
    assert value.precision == 'approximate' and value.raw == raw


def test_post_time_above_title_excludes_sidebar_and_comment_times():
    html = '<span>5시간 전 활동</span><span class="seed-tag-group-item__label--size_t3">5시간 전</span><h1>제목</h1><div class="article-body">본문</div><span>3일 전</span>'
    assert post_relative_time(html) == '5시간 전'


@pytest.mark.parametrize('body,match', [('https://www.seyj.kr/projects', True), ('seyj.kr', True),
    ('www.seyj.kr/projects', True), ('https://seyj.kr.evil/projects', False), ('seyj.kr.evil', False),
    ('notseyj.kr', False), ('www.seyj.kr-example.com', False)])
def test_portfolio_exact_host_only(body, match):
    assert template_match('다른 제목', body, '템플릿 제목') is match


class StaticPage:
    def __init__(self, html): self.html = html
    def content(self): return self.html
    def evaluate(self, _): return 1000
    def wait_for_timeout(self, _): pass


@pytest.mark.parametrize('html', ['<h2>로딩 중</h2>',
    '<h2>외주</h2><nav aria-label="게시판"></nav><a href="/synthetic/posts/old"><div>합성 개발 서비스</div></a>'])
def test_unknown_or_loading_listing_never_types_or_submits(ctx, tmp_path, html):
    cafe_id = ready(ctx)
    browser = EdgeBrowser(tmp_path)
    browser.page = StaticPage(html)
    browser.prepare_editor = lambda *_: pytest.fail('목록 구조 미확인 상태에서 입력하지 않음')
    browser.submit_post = lambda: pytest.fail('목록 구조 미확인 상태에서 제출하지 않음')
    with pytest.raises(CafePageError) as failure:
        publish(ctx, browser, cafe_id, lambda _: html, lambda: None)
    assert failure.value.code == 'cafe_listing_unrecognized'
    with ctx.db.session() as s:
        assert s.scalar(select(CafePostAttempt)).status == 'failed'
        assert svc.session_row(s).post_count == 0
    # The collection board reader applies the same safety guard.
    with pytest.raises(CafePageError): browser.board_posts(SimpleNamespace(url='https://cafe.daangn.com/synthetic/boards/jobs'), lambda _: html)


def test_explicit_empty_listing_is_distinct_from_loading(tmp_path):
    html = '<h2>외주</h2><nav aria-label="게시판"></nav><p>작성된 게시글이 없어요.</p>'
    assert board_listing_ready(html, 'https://cafe.daangn.com/synthetic/boards/jobs')
    browser = EdgeBrowser(tmp_path)
    browser.page = StaticPage(html)
    assert browser.board_posts(SimpleNamespace(url='https://cafe.daangn.com/synthetic/boards/jobs'), lambda _: html) == []


def test_serial_scheduler_defaults_approval_and_no_failed_auto_retry(ctx):
    cafe_id = ready(ctx)
    with ctx.db.session() as s:
        assert svc.schedule_due(s, NOW) is None  # auto-post is off by default.
        svc.update_settings(s, {'auto_post': True})
        job = svc.schedule_due(s, NOW)
        assert job.kind == 'post' and job.input['cafe_id'] == cafe_id
        assert svc.schedule_due(s, NOW) is None  # one active job.
        svc.finish_job(s, job.id, 'failed', NOW, error_code='cafe_listing_unrecognized')
        s.add(CafePostAttempt(id='failed', cafe_id=cafe_id, board_id='test-board', template_version=2,
            status='failed', error_code='cafe_submission_uncertain', created_at=NOW))
    with ctx.db.session() as s:
        assert svc.schedule_due(s, NOW + timedelta(minutes=31)) is None
        source = s.get(Source, 'daangn_cafe')
        source.auto_collect_enabled = True
        collected = svc.schedule_due(s, NOW + timedelta(minutes=31))
        assert collected.kind == 'collect'
        svc.finish_job(s, collected.id, 'succeeded', NOW + timedelta(minutes=31))
        assert svc.schedule_due(s, NOW + timedelta(minutes=32)) is None  # stored collect cadence.
        svc.set_session(s, 'needs_user', NOW + timedelta(hours=4))
        assert svc.schedule_due(s, NOW + timedelta(hours=4)) is None
        svc.set_session(s, 'logged_in', NOW + timedelta(hours=4))
        usage = svc.session_row(s)
        usage.usage_day, usage.page_count = svc.day_key(NOW), 300
        assert svc.schedule_due(s, NOW + timedelta(hours=4)) is None


def test_source_autocollection_uses_only_cafe_queue(ctx, client):
    from worklead.engine.worker import schedule_due
    from worklead.models import Run, CafeJob
    ready(ctx)
    response = client.patch('/v1/sources/daangn_cafe', json={'auto_collect_enabled': True})
    assert response.status_code == 200
    assert schedule_due(ctx) == []
    with ctx.db.session() as s:
        assert s.scalar(select(Run).where(Run.source_id == 'daangn_cafe')) is None
        job = s.scalar(select(CafeJob))
        assert job.kind == 'collect' and job.input['scheduled'] is True
    assert client.patch('/v1/sources/daangn_cafe', json={'auto_collect_enabled': False}).status_code == 200
    with ctx.db.session() as s: assert s.scalar(select(CafeJob)).state == 'cancelled'
