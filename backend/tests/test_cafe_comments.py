import zipfile
from datetime import timedelta
from sqlalchemy import select
import pytest
from tests.test_cafe_posting import ready, Browser
from tests.test_cafe_collection import NOW
from worklead.cafe.posting import publish
from worklead.cafe.comments import Comment, CommentPage, classify, save_page, monitor
from worklead.models import AppSetting, CafeComment, CafePostAttempt, EventOutbox, Lead
from worklead.services import cafe as svc
from worklead.services.settings import put_setting
from worklead.services.diagnostics import write_diagnostics


def posted(ctx):
    id_ = ready(ctx)
    publish(ctx, Browser(), id_, lambda _: None, lambda: None)
    with ctx.db.session() as s:
        put_setting(s, 'notifications', {'quiet_hours': {'enabled': False}})
        return s.scalar(select(CafePostAttempt)).id


def feed():
    return CommentPage([Comment('합성 작성자', '홈페이지 제작 견적 가능할까요? 연락 주세요', NOW, False, 'request-1'), Comment('나', '안녕하세요', NOW, True, 'own-1'), Comment('다른 합성 작성자', '어떤 기술을 쓰나요?', NOW, False, 'question-1', is_reply=True)], True)


def test_dedupe_own_reply_classification_native_privacy_and_local_actions(ctx, client, tmp_path):
    id_ = posted(ctx)
    assert save_page(ctx, id_, feed()) == {'created': 2, 'complete': True}
    assert save_page(ctx, id_, feed())['created'] == 0
    data = client.get('/v1/cafe/comments').json()
    assert data['unread_count'] == 2
    request = next(row for row in data['items'] if row['classification'] == 'request')
    assert next(row for row in data['items'] if row['classification'] == 'question')['is_reply'] is True
    response = client.patch('/v1/cafe/comments/' + request['id'], json={'read': True, 'memo': '내 견적 메모'})
    assert response.status_code == 200 and response.json()['read_at'] and response.json()['memo'] == '내 견적 메모'
    assert client.get('/v1/cafe/comments').json()['unread_count'] == 1
    draft = client.post('/v1/cafe/comments/' + request['id'] + '/draft')
    assert draft.status_code == 200 and '문의 남겨주셔서 감사합니다' in draft.json()['text']
    with ctx.db.session() as s: assert len(list(s.scalars(select(Lead)))) == 0
    lead = client.post('/v1/cafe/comments/' + request['id'] + '/lead')
    assert lead.status_code == 200 and lead.json()['source_id'] == 'manual'
    assert client.post('/v1/cafe/comments/' + request['id'] + '/lead').json()['id'] == lead.json()['id']
    native = client.get('/v1/native-notifications').json()
    assert len([row for row in native['items'] if row['kind'] == 'cafe_request']) == 1
    assert '합성 작성자' not in str(native) and '견적' not in str(native)
    archive = tmp_path / 'diagnostics.zip'
    write_diagnostics(ctx, archive, lambda: None, lambda: None)
    with zipfile.ZipFile(archive) as zip_:
        raw = b'\n'.join(zip_.read(name) for name in zip_.namelist()).decode()
    assert '합성 작성자' not in raw and 'cafe.daangn.com' not in raw and '홈페이지 제작 견적' not in raw and 'cafe-browser' not in raw


def test_partial_comments_are_not_a_completed_poll(ctx):
    id_ = posted(ctx)
    page = feed()
    page.complete = False
    with pytest.raises(svc.CafeError, match='전체 댓글'): save_page(ctx, id_, page)
    with ctx.db.session() as s:
        assert s.scalar(select(CafeComment)) is None
        assert s.get(AppSetting, 'cafe_comment_checks') is None
    page.complete = True
    page.comments[0].is_own = None
    with pytest.raises(svc.CafeError): save_page(ctx, id_, page)


def test_interval_expiry_removed_and_quiet_hours(ctx):
    id_ = posted(ctx)
    with ctx.db.session() as s:
        put_setting(s, 'notifications', {'quiet_hours': {'enabled': True, 'start': '08:00', 'end': '10:00'}})
    assert monitor(ctx, lambda _: feed())['checked'] == 1
    assert monitor(ctx, lambda _: pytest.fail('30분 전에는 다시 읽지 않음'))['checked'] == 0
    with ctx.db.session() as s:
        row = s.scalar(select(EventOutbox).where(EventOutbox.type == 'notification.created'))
        assert row.suppressed_reason == 'quiet_hours'
    ctx.clock = lambda: NOW + timedelta(minutes=31)
    assert monitor(ctx, lambda _: CommentPage([], True, confirmed_removed=True))['checked'] == 1
    with ctx.db.session() as s: assert s.get(CafePostAttempt, id_).status == 'removed'
    assert monitor(ctx, lambda _: pytest.fail('삭제 글은 다시 감시하지 않음'))['checked'] == 0


@pytest.mark.parametrize(('body','kind'), [('견적 부탁드립니다','request'), ('채팅 드렸어요','request'), ('무슨 언어인가요?','question'), ('좋은 글 감사합니다','other')])
def test_rules(body, kind): assert classify(body) == kind
