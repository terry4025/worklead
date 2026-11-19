import json

import httpx
import pytest
from sqlalchemy import select

from tests.conftest import client_for
from worklead.jobs.chrome_bridge import EXTENSION_ORIGIN, runtime
from worklead.models import AppSetting, EventOutbox

ACCOUNT = 'a' * 64
TEXT = '합성 대화 내용: 작업 범위 확인 부탁드립니다. <script>무해한 텍스트</script>'


@pytest.fixture
def paired(ctx):
    ticks = [0.0]
    r = runtime(ctx)
    r.monotonic = lambda: ticks[0]
    connection = r.start(capture_visible_chat=True)['connection']
    with httpx.Client(base_url=connection['url'], headers={'Origin': EXTENSION_ORIGIN,
            'Authorization': 'Bearer ' + connection['token']}, timeout=3, trust_env=False) as c:
        yield r, c, ticks
    r.stop()


def payload(**patch):
    return {'state':'visible','text':TEXT,'account_hash':ACCOUNT,'job_id':None,'sequence':1,**patch}


def test_visible_text_is_ephemeral_authenticated_and_not_a_claim_of_a_matched_reply(ctx, paired):
    r, c, _ = paired
    assert c.post('/chat', json=payload()).json()['accepted']
    response = client_for(ctx).get('/v1/jobs/messages/inbox')
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    assert response.json()['text'] == TEXT and response.json()['lead_id'] is None
    assert ACCOUNT not in response.text and 'token' not in response.json()
    assert response.json()['persists_body'] is False
    with ctx.db.session() as s:
        assert TEXT not in json.dumps([row.value for row in s.scalars(select(AppSetting))])
        assert TEXT not in json.dumps([row.payload for row in s.scalars(select(EventOutbox))])
    assert client_for(ctx).get('/v1/jobs/messages/inbox', headers={'Authorization':''}).status_code == 401
    r.stop()
    assert r.chat_view().text is None and r.chat_view().state == 'off'


def test_same_unread_count_can_still_update_visible_chat_without_f5(paired):
    r, c, ticks = paired
    for sequence, text in [(1,TEXT),(2,'합성 새 답장 두 번째')]:
        assert c.post('/observe',json={'state':'watching','unread_count':1,'sequence':sequence}).status_code == 200
        assert c.post('/chat',json=payload(sequence=sequence,text=text)).status_code == 200
        ticks[0] += 2
        assert r.chat_view().text == text
    assert r.status().unread_count == 1


@pytest.mark.parametrize('boundary', ['timeout','account','login','restore'])
def test_old_or_other_account_body_is_hidden(ctx, paired, boundary):
    r, c, ticks = paired
    c.post('/chat',json=payload())
    if boundary == 'timeout': ticks[0] += 15
    elif boundary == 'restore': ctx.db.gate.restored()
    else:
        change = {'account_hash':'b'*64} if boundary == 'account' else {'state':'login_required','text':None,'account_hash':None}
        assert c.post('/chat',json=payload(sequence=2,**change)).json()['reconnect_required']
    assert r.chat_view().text is None
    if boundary in ('account','login','restore'):
        assert c.post('/chat',json=payload(sequence=3)).status_code in (403,409)


def test_chat_scope_is_opt_in_and_does_not_expand_counter_observation(ctx):
    r = runtime(ctx)
    connection = r.start()['connection']
    try:
        with httpx.Client(base_url=connection['url'],headers={'Origin':EXTENSION_ORIGIN,'Authorization':'Bearer '+connection['token']},trust_env=False) as c:
            assert c.post('/chat',json=payload()).status_code == 403
            assert c.post('/observe',json=payload()).status_code == 422
    finally: r.stop()


def test_bounds_sender_scope_and_order(paired):
    _, c, _ = paired
    assert c.post('/chat',json=payload(),headers={'Origin':'https://jobs.daangn.com'}).status_code == 403
    assert c.post('/chat',json=payload(),headers={'Authorization':'Bearer wrong'}).status_code == 401
    assert c.post('/chat',json=payload(text='가'*4000)).status_code == 200
    assert c.post('/chat',json=payload(text='가'*4000)).status_code == 409
    assert c.post('/chat',json=payload(sequence=2,text='가'*4001)).status_code == 422
    assert c.post('/chat',json=payload(sequence=2,url='https://evil.example')).status_code == 422
    assert c.post('/chat',json=payload(sequence=True)).status_code == 422
    assert c.post('/chat',json=payload(sequence=2,account_hash=None)).status_code == 422
    assert c.post('/chat',json=payload(sequence=2,state='panel_closed')).status_code == 422
