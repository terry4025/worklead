from datetime import timedelta
from types import SimpleNamespace
from sqlalchemy import select
import pytest
from tests.test_cafe_collection import prepare, NOW
from worklead.api import schemas as S
from worklead.models import Cafe, CafeBoard, CafePostAttempt
from worklead.cafe.posting import template, update_template, publish, lines
from worklead.cafe.parsing import CafePageError
from worklead.cafe.runtime import CafeRuntime
from worklead.services import cafe as svc


class Browser:
    sent = 0
    visible = []
    mismatch = False
    uncertain = False
    def duplicate_posts(self, board, navigate): return self.visible
    def prepare_editor(self, url, board, value, navigate): self.value, self.url = value, url
    def submit_post(self):
        self.sent += 1
        if self.uncertain: raise TimeoutError('private URL must not be logged')
        return self.url + '/posts/synthetic-published'
    def read_post(self): return {'title': self.value.title, 'body': 'different' if self.mismatch else self.value.body.replace('\n\n', '\n\u3164\n')}


def ready(ctx):
    prepare(ctx)
    with ctx.db.session() as s:
        cafe = s.scalar(select(Cafe))
        cafe.status, cafe.joined = 'approved', True
        board = s.get(CafeBoard, 'test-board')
        board.approved, board.post_allowed = True, True
        update_template(s, S.CafeTemplatePatch(title='합성 개발 서비스', body='첫 문단\n\n둘째 문단\n\n\n끝'))
        return cafe.id


def test_post_linebreak_verify_then_no_second_submission(ctx):
    id_ = ready(ctx)
    browser = Browser()
    result = publish(ctx, browser, id_, lambda _: None, lambda: None)
    assert result['status'] == 'posted' and browser.sent == 1
    assert publish(ctx, browser, id_, lambda _: None, lambda: None)['status'] == 'already_exists'
    assert browser.sent == 1
    with ctx.db.session() as s:
        assert svc.session_row(s).post_count == 1
        row = s.scalar(select(CafePostAttempt))
        assert row.verified_at == NOW and row.post_url.endswith('/synthetic-published')


def test_existing_title_skips_without_author_or_body_match(ctx):
    id_ = ready(ctx)
    browser = Browser()
    browser.visible = [{'title':'합성 개발 서비스', 'body':'old body', 'url':'https://cafe.daangn.com/synthetic/posts/old', 'author':'다른 사람'}]
    assert publish(ctx, browser, id_, lambda _: None, lambda: None)['status'] == 'already_exists'
    assert browser.sent == 0


def test_same_body_with_different_title_does_not_skip(ctx):
    id_ = ready(ctx)
    browser = Browser()
    browser.visible = [{'title':'다른 제목', 'body':'첫 문단\n\n둘째 문단\n\n\n끝', 'url':'https://cafe.daangn.com/synthetic/posts/old'}]
    assert publish(ctx, browser, id_, lambda _: None, lambda: None)['status'] == 'posted'
    assert browser.sent == 1


@pytest.mark.parametrize('mode', ['mismatch', 'uncertain', 'budget', 'interval', 'removed'])
def test_no_automatic_repost_or_retry(ctx, mode):
    id_ = ready(ctx)
    browser = Browser()
    with ctx.db.session() as s:
        usage = svc.session_row(s)
        usage.usage_day = svc.day_key(NOW)
        if mode == 'budget': usage.post_count = 5
        if mode == 'interval': usage.last_post_at = NOW - timedelta(minutes=10)
        if mode == 'removed': s.add(CafePostAttempt(id='removed', cafe_id=id_, board_id='test-board', template_version=1, status='removed', created_at=NOW))
    if mode in ('mismatch', 'uncertain'): setattr(browser, mode, True)
    if mode in ('uncertain', 'budget', 'interval'):
        with pytest.raises(CafePageError): publish(ctx, browser, id_, lambda _: None, lambda: None)
        if mode == 'uncertain':
            with pytest.raises(svc.CafeError, match='불확실'): publish(ctx, browser, id_, lambda _: None, lambda: None)
            assert browser.sent == 1
        else: assert browser.sent == 0
    else:
        result = publish(ctx, browser, id_, lambda _: None, lambda: None)
        assert result['status'] == ('posted_mismatch' if mode == 'mismatch' else 'skipped_rule')
        publish(ctx, browser, id_, lambda _: None, lambda: None)
        assert browser.sent == (1 if mode == 'mismatch' else 0)


def test_template_api_versions_preserve_newlines(client):
    default = client.get('/v1/cafe/template').json()
    assert default['version'] == 1 and 'https://www.seyj.kr/' in default['body']
    new = {'title':'합성 제목', 'body':'첫째\n\n\n둘째'}
    response = client.patch('/v1/cafe/template', json=new)
    assert response.status_code == 200 and response.json()['version'] == 2
    assert response.json()['body'] == new['body']
    assert client.patch('/v1/cafe/template', json=new).json()['version'] == 2
    assert lines('a\n\u3164\nb') == lines('a\n\nb')


def test_only_explicit_removed_repost_is_allowed(ctx, client):
    id_ = ready(ctx)
    publish(ctx, Browser(), id_, lambda _: None, lambda: None)
    with ctx.db.session() as s:
        row = s.scalar(select(CafePostAttempt))
        attempt_id = row.id
        row.status = 'removed'
    endpoint = '/v1/cafe/attempts/' + attempt_id + '/repost'
    assert client.post(endpoint, json={'confirm': False}).status_code == 422
    assert client.post(endpoint, json={'confirm': True}).status_code == 202
    ctx.clock = lambda: NOW + timedelta(minutes=31)
    browser = Browser()
    assert publish(ctx, browser, id_, lambda _: None, lambda: None, repost=True)['status'] == 'posted'
    assert browser.sent == 1


def test_scheduled_post_disabled_before_start_never_opens_browser(ctx):
    id_ = ready(ctx)
    with ctx.db.session() as s:
        svc.update_settings(s, {'auto_post': True})
        job = svc.enqueue_job(s, 'post', NOW, {'cafe_id': id_, 'scheduled': True})
        svc.update_settings(s, {'auto_post': False})
    runtime = CafeRuntime(ctx, browser_factory=lambda _: pytest.fail('꺼진 예약 게시에서는 브라우저를 열지 않음'))
    with pytest.raises(svc.CafeError) as failure:
        runtime.execute(job)
    assert failure.value.code == 'cafe_auto_post_disabled'
    assert runtime.browser is None
    with ctx.db.session() as s:
        assert s.scalar(select(CafePostAttempt)) is None
        assert svc.session_row(s).post_count == 0


def test_scheduled_publish_disabled_before_preparation_never_submits(ctx):
    id_ = ready(ctx)
    browser = Browser()
    browser.duplicate_posts = lambda *_: pytest.fail('꺼진 예약 게시에서는 목록 조사도 하지 않음')
    with pytest.raises(svc.CafeError) as failure:
        publish(ctx, browser, id_, lambda _: None, lambda: None, scheduled=True)
    assert failure.value.code == 'cafe_auto_post_disabled'
    assert browser.sent == 0
    with ctx.db.session() as s:
        assert s.scalar(select(CafePostAttempt)) is None
        assert svc.session_row(s).post_count == 0


@pytest.mark.parametrize('scheduled', [True, False])
def test_runtime_rechecks_auto_post_after_editor_preparation_only_for_scheduled_jobs(ctx, scheduled):
    id_ = ready(ctx)
    with ctx.db.session() as s:
        svc.update_settings(s, {'auto_post': True})
    class PreparingBrowser(Browser):
        def open(self, **_): pass
        def prepare_editor(self, *args):
            super().prepare_editor(*args)
            with ctx.db.session() as s:
                svc.update_settings(s, {'auto_post': False})
    browser = PreparingBrowser()
    runtime = CafeRuntime(ctx, browser_factory=lambda _: browser)
    runtime._navigate = lambda _: None
    runtime._reserve = lambda: None
    job = SimpleNamespace(kind='post', input={'cafe_id': id_, 'scheduled': scheduled})
    if scheduled:
        with pytest.raises(CafePageError) as failure:
            runtime.execute(job)
        assert failure.value.code == 'cafe_auto_post_disabled'
    else:
        assert runtime.execute(job)['status'] == 'posted'
    assert browser.sent == (0 if scheduled else 1)
    with ctx.db.session() as s:
        assert svc.session_row(s).post_count == (0 if scheduled else 1)
        attempt = s.scalar(select(CafePostAttempt))
        assert attempt.status == ('failed' if scheduled else 'posted')
        if scheduled:
            assert attempt.post_url is None
        else:
            assert attempt.post_url.endswith('/synthetic-published')


@pytest.mark.parametrize('revoked', ['cafe', 'board'])
def test_approval_revoked_after_editor_preparation_never_submits(ctx, revoked):
    id_ = ready(ctx)
    class PreparingBrowser(Browser):
        def prepare_editor(self, *args):
            super().prepare_editor(*args)
            with ctx.db.session() as s:
                if revoked == 'cafe':
                    svc.update_cafe(s, id_, 'candidate', NOW)
                else:
                    svc.approve_board(s, 'test-board', False)
    browser = PreparingBrowser()
    with pytest.raises(CafePageError) as failure:
        publish(ctx, browser, id_, lambda _: None, lambda: None)
    assert failure.value.code == 'cafe_scope_changed'
    assert browser.sent == 0
    with ctx.db.session() as s:
        assert svc.session_row(s).post_count == 0
        assert s.scalar(select(CafePostAttempt)).post_url is None
