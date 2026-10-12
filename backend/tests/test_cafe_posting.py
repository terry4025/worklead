from datetime import timedelta
from sqlalchemy import select
import pytest
from tests.test_cafe_collection import prepare, NOW
from worklead.api import schemas as S
from worklead.models import Cafe, CafeBoard, CafePostAttempt
from worklead.cafe.posting import template, update_template, publish, lines
from worklead.cafe.parsing import CafePageError
from worklead.services import cafe as svc


class Browser:
    sent = 0
    complete = True
    owned = []
    mismatch = False
    uncertain = False
    def owned_posts(self, url, navigate): return self.owned, self.complete
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


def test_existing_owned_title_or_body_and_unknown_ownership(ctx):
    id_ = ready(ctx)
    browser = Browser()
    browser.complete = False
    with pytest.raises(CafePageError, match='대조'): publish(ctx, browser, id_, lambda _: None, lambda: None)
    assert browser.sent == 0
    browser.complete = True
    browser.owned = [{'title':'합성 개발 서비스', 'body':'old body', 'url':'https://cafe.daangn.com/synthetic/posts/old'}]
    assert publish(ctx, browser, id_, lambda _: None, lambda: None)['status'] == 'already_exists'
    assert browser.sent == 0


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
