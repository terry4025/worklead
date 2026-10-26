from datetime import UTC, datetime, timedelta
from pathlib import Path
import sqlite3
import pytest
from sqlalchemy import select
from alembic import command
from worklead.api import schemas as S
from worklead.models import CafeBoard, CafeJob, EventOutbox, SourcePolicy
from worklead.services import cafe as svc
from worklead.cafe.parsing import CafePageError, cafe_home, classify_board, detect_session, directory_links, safe_cafe_url
from worklead.storage import alembic_config, migrate, validate_database

NOW = datetime(2026, 10, 12, tzinfo=UTC)


def consent(client):
    assert client.post('/v1/cafe/consent', json={'version': svc.CONSENT_VERSION}).status_code == 200


def test_consent_auth_and_budget(client, ctx):
    assert client.get('/v1/cafe/status', headers={'Authorization': ''}).status_code == 401
    assert client.get('/v1/cafe/status', headers={'Origin': 'https://evil.example'}).status_code == 403
    assert client.post('/v1/cafe/actions/discover').json()['error']['code'] == 'cafe_consent_required'
    assert client.post('/v1/cafe/consent', json={'version': 'old'}).status_code == 409
    consent(client)
    with ctx.db.session() as s:
        assert s.get(SourcePolicy, 'daangn_cafe').status == 'allowed'
        assert svc.reserve_navigation(s, NOW) == 0
        assert svc.reserve_navigation(s, NOW + timedelta(seconds=3)) == 5
        assert svc.reserve_navigation(s, NOW + timedelta(seconds=8)) == 0
        svc.update_settings(s, {'daily_page_limit': 2})
        with pytest.raises(svc.CafeError, match='한도'):
            svc.reserve_navigation(s, NOW + timedelta(seconds=16))
        assert svc.reserve_navigation(s, NOW + timedelta(days=1)) == 0
        assert svc.session_row(s).page_count == 1
    assert client.get('/v1/cafe/settings').json()['auto_post'] is False
    assert client.patch('/v1/cafe/settings', json={'auto_post': True}).status_code == 409
    with ctx.db.session() as s: svc.set_session(s, 'logged_in', NOW)
    assert client.patch('/v1/cafe/settings', json={'auto_post': True}).status_code == 200
    assert client.patch('/v1/cafe/settings', json={'min_navigation_seconds': 1}).status_code == 422


@pytest.mark.parametrize('url', ['http://cafe.daangn.com/valid', 'https://cafe.daangn.com.evil/valid', 'https://cafe.daangn.com/api', 'https://cafe.daangn.com/a/../b', 'https://cafe.daangn.com/a?key=secret', 'https://x@y/valid', 'https://cafe.daangn.com:443/valid'])
def test_url_rejects_private_paths_and_external(url, client):
    assert client.post('/v1/cafe/cafes', json={'url': url}).status_code == 422
    with pytest.raises(CafePageError):
        safe_cafe_url(url, home_only=True)


def test_manual_add_approval_and_rules(client, ctx):
    consent(client)
    url = 'https://cafe.daangn.com/synthetic'
    one = client.post('/v1/cafe/cafes', json={'url': url}).json()
    assert client.post('/v1/cafe/cafes', json={'url': url}).json()['id'] == one['id']
    assert client.patch('/v1/cafe/cafes/' + one['id'], json={'status': 'approved'}).status_code == 409
    html = '<button aria-label="사용자 메뉴"></button><h2>합성 개발 카페</h2><a href="/synthetic/posts/new">글쓰기</a><a href="/synthetic/boards/jobs-test">외주 홍보</a>'
    positive = cafe_home(html, url, rule_texts=['서비스 홍보가 아니라 구직글 게시 가능'])
    # The rule below is affirmative; a prohibition always takes precedence.
    positive = cafe_home(html, url, rule_texts=['구직글 게시 허용'])
    assert positive['status'] == 'eligible'
    with ctx.db.session() as s:
        svc.save_discovery(s, [positive], NOW)
    assert client.patch('/v1/cafe/cafes/' + one['id'], json={'status': 'approved'}).status_code == 200
    board = client.get('/v1/cafe/cafes').json()['items'][0]['boards'][0]
    assert client.patch('/v1/cafe/boards/' + board['id'], json={'approved': True}).status_code == 200
    negative = cafe_home(html, url, rule_texts=['구직글 게시 허용. 외부 링크 금지'])
    with ctx.db.session() as s:
        svc.save_discovery(s, [negative], NOW)
    row = client.get('/v1/cafe/cafes').json()['items'][0]
    assert row['status'] == 'excluded' and not row['boards'][0]['approved']
    assert not classify_board('아르바이트구인구직')['post_allowed']
    assert not classify_board('외주 홍보', '홍보글 게시 허용하지 않습니다')['post_allowed']
    assert not classify_board('외주 홍보', '구직글 게시 허용되지 않아요')['post_allowed']


def test_session_challenge_stops_and_notifies_once(client, ctx):
    consent(client)
    assert detect_session('<button aria-label="사용자 메뉴"></button>')[0] == 'logged_in'
    assert detect_session('<button>로그인</button>')[0] == 'logged_out'
    assert detect_session('<div role="dialog">본인 인증</div>')[0] == 'needs_user'
    assert detect_session('<button aria-label="사용자 메뉴"></button><p class="article-body">CAPTCHA에 대해 물어봅니다</p>')[0] == 'logged_in'
    with ctx.db.session() as s:
        svc.set_session(s, 'needs_user', NOW, 'authentication_challenge')
        svc.set_session(s, 'needs_user', NOW, 'authentication_challenge')
        assert len(list(s.scalars(select(EventOutbox).where(EventOutbox.type == 'notification.created')))) == 1
    assert client.post('/v1/cafe/actions/discover').status_code == 409
    assert client.post('/v1/cafe/actions/login').status_code == 202


def test_queue_recovery_and_fixture_parsing(client, ctx):
    consent(client)
    assert client.post('/v1/cafe/actions/check').status_code == 202
    assert client.post('/v1/cafe/actions/discover').status_code == 409
    with ctx.db.session() as s:
        job = svc.claim_next_job(s, NOW)
        assert job.state == 'running'
        svc.recover_interrupted_jobs(s, NOW)
        assert s.get(CafeJob, job.id).state == 'failed'
    fixture = Path(__file__).parent / 'fixtures/daangn_cafe/home.html'
    value = cafe_home(fixture.read_text(encoding='utf-8'), 'https://cafe.daangn.com/synthetic')
    assert value['name'] and value['status'] != 'approved'
    assert directory_links('<a href="/synthetic">개발 의뢰 카페</a><a href="/api">개발</a>', 'https://cafe.daangn.com/', ['개발']) == [{'url': 'https://cafe.daangn.com/synthetic', 'name': '개발 의뢰 카페'}]


def test_migration_preserves_0005_and_backup(tmp_path):
    path = tmp_path / 'copy.sqlite3'
    command.upgrade(alembic_config(path), '0005_page_checkpoints')
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO app_settings(key,value,updated_at) VALUES ('preserved','{\"value\":42}','2026-10-12')")
    assert validate_database(path)[0] == '0005_page_checkpoints'
    migrate(path, tmp_path / 'backups')
    assert validate_database(path)[0] == '0009_auto_apply_retry'
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT value FROM app_settings WHERE key='preserved'").fetchone()[0] == '{"value":42}'
        assert conn.execute("SELECT count(*) FROM discovery_listings").fetchone()[0] == 0
    assert list((tmp_path / 'backups').glob('*.sqlite3'))


def test_restore_keeps_cafe_budget_and_cancels_jobs(client, ctx):
    ctx.clock = lambda: NOW
    consent(client)
    with ctx.db.session() as s:
        svc.reserve_navigation(s, NOW)
    backup = client.post('/v1/backups').json()['id']
    with ctx.db.session() as s:
        svc.reserve_navigation(s, NOW + timedelta(seconds=8))
    assert client.post('/v1/cafe/actions/check').status_code == 202
    inspection = client.get('/v1/backups/' + backup).json()
    assert client.post('/v1/backups/' + backup + '/restore', json={'sha256': inspection['sha256'], 'confirm': True}).status_code == 200
    with ctx.db.session() as s:
        assert svc.session_row(s).page_count == 2
        assert svc.session_row(s).state == 'logged_out'
        assert svc.get_settings(s)['auto_post'] is False


def test_restore_retains_backup_today_budget_when_current_day_is_stale(client, ctx):
    ctx.clock = lambda: NOW
    consent(client)
    with ctx.db.session() as s:
        svc.reserve_navigation(s, NOW)
        svc.session_row(s).post_count = 2
    backup = client.post('/v1/backups').json()['id']
    with ctx.db.session() as s:
        row = svc.session_row(s)
        row.usage_day, row.page_count, row.post_count = svc.day_key(NOW - timedelta(days=1)), 0, 0
    check = client.get('/v1/backups/' + backup).json()
    assert client.post('/v1/backups/' + backup + '/restore', json={'sha256': check['sha256'], 'confirm': True}).status_code == 200
    with ctx.db.session() as s:
        row = svc.session_row(s)
        assert row.usage_day == svc.day_key(NOW) and row.page_count == 1 and row.post_count == 2


def test_scoped_board_permission_does_not_spread():
    rule = '외주 게시판에서만 구직글 게시 허용'
    assert classify_board('외주', rule)['post_allowed']
    assert not classify_board('구인구직', rule)['post_allowed']
    assert not classify_board('홍보', rule)['post_allowed']


def test_collect_only_survives_inspection_without_becoming_post_target(ctx):
    with ctx.db.session() as s:
        svc.accept_consent(s, svc.CONSENT_VERSION, NOW)
        row = svc.add_cafe(s, 'https://cafe.daangn.com/synthetic-read', NOW)
        data = {'url':row.url, 'joined':True, 'status':'candidate', 'boards':[{'name':'외주', 'url':row.url+'/boards/jobs', 'kind':'job_offer', 'evidence':''}]}
        svc.save_discovery(s, [data], NOW)
        svc.update_cafe(s, row.id, 'collect_only', NOW)
        svc.update_settings(s, {'auto_approve':True})
        data['status'] = 'eligible'; data['boards'][0]['evidence'] = '구직글 게시 허용'
        svc.save_discovery(s, [data], NOW)
        assert row.status == 'collect_only' and row.approved_at is None
        entry = svc.entry_out(s, row)
        assert not entry.boards[0].approved
        svc.save_discovery(s, [{**data, 'joined':False, 'status':'needs_join'}], NOW)
        assert row.status == 'needs_join'


def test_inspection_revokes_removed_board_but_directory_card_does_not(ctx):
    from worklead.models import CafeBoard
    with ctx.db.session() as s:
        row = svc.add_cafe(s, 'https://cafe.daangn.com/synthetic-removed', NOW)
        row.status, row.joined = 'approved', True
        s.add(CafeBoard(id='removed-board', cafe_id=row.id, url=row.url+'/boards/jobs', name='외주', kind='job_offer', approved=True, post_allowed=True))
    with ctx.db.session() as s:
        svc.save_discovery(s, [{'url':'https://cafe.daangn.com/synthetic-removed'}], NOW)
        assert s.get(CafeBoard, 'removed-board').approved
        svc.save_discovery(s, [{'url':'https://cafe.daangn.com/synthetic-removed','boards':[]}], NOW)
        board=s.get(CafeBoard, 'removed-board')
        assert not board.approved and not board.post_allowed and board.kind == 'other'


def test_unlimited_page_budget_preserves_usage_spacing_and_scheduling(ctx):
    from datetime import timedelta
    from worklead.models import Source, CafeBoard
    with ctx.db.session() as s:
        svc.accept_consent(s,svc.CONSENT_VERSION,NOW)
        svc.set_session(s,'logged_in',NOW)
        svc.update_settings(s,{'daily_page_limit':0})
        row=svc.session_row(s);row.usage_day=svc.day_key(NOW);row.page_count=10000;row.last_navigation_at=NOW
        assert svc.reserve_navigation(s,NOW)==8
        assert svc.reserve_navigation(s,NOW+timedelta(seconds=8))==0
        assert row.page_count==10001
        cfg=svc.get_settings(s)
        assert cfg['daily_post_limit']==5 and cfg['min_navigation_seconds']==8
        cafe=svc.add_cafe(s,'https://cafe.daangn.com/unlimited-test',NOW);cafe.status='collect_only';cafe.joined=True
        s.add(CafeBoard(id='unlimited-board',cafe_id=cafe.id,name='구인구직',url=cafe.url+'/boards/jobs',kind='job_seek',post_allowed=False,approved=False))
        s.get(Source,'daangn_cafe').auto_collect_enabled=True
    with ctx.db.session() as s:
        assert svc.schedule_due(s,NOW+timedelta(seconds=8)).kind=='collect'
        svc.update_settings(s,{'daily_page_limit':300})
        with pytest.raises(svc.CafeError) as failure:svc.reserve_navigation(s,NOW+timedelta(seconds=16))
        assert failure.value.code=='cafe_daily_budget'
