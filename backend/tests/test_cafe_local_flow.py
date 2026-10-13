"""Real Path B reader/editor flow against local HTTP with all traffic intercepted.
The fixture uses observed UI shapes, synthetic text and relative time labels.
"""
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from html import escape
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser
import json
import threading
import httpx
import pytest
from sqlalchemy import select
from tests.test_cafe_posting import ready
from tests.test_cafe_collection import NOW
from worklead.cafe.browser import EdgeBrowser
from worklead.cafe.collection import CafePage, CafePost, collect
from worklead.cafe.comments import Comment, CommentPage, save_page
from worklead.cafe.posting import publish
from worklead.cafe.parsing import detect_session, CafePageError
from worklead.analysis.types import DateInfo
from worklead.sources.base import ParsedPost
from worklead.models import CafeComment, Lead
from worklead.services import cafe as svc


def test_complete_local_browser_flow(ctx, tmp_path):
    cafe_id = ready(ctx)
    site = {'posts': {'buyer': {'title':'홈페이지 제작 의뢰', 'body':'온라인으로 진행할 웹사이트 제작 의뢰합니다.', 'own':False, 'written':'방금 전'}}, 'submissions':0, 'captcha':False}
    prefix = '<meta charset="utf-8"><button aria-label="사용자 메뉴">계정</button>'
    class Site(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def send(self, body, *, json_=False):
            self.send_response(200)
            self.send_header('Content-Type', 'application/json' if json_ else 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(body.encode())
        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
            site['submissions'] += 1
            site['posts']['published'] = {**data, 'own':True, 'written':'방금 전'}
            self.send(json.dumps({'url':'/synthetic/posts/published'}), json_=True)
        def do_GET(self):
            if site['captcha']:
                self.send(prefix + '<div role="dialog">본인 인증</div>'); return
            if self.path.endswith('/posts/new'):
                self.send(prefix + '''<button id="board" role="combobox" aria-haspopup="listbox">자유 게시판</button><div id="boards" role="listbox" style="display:none"><div role="option" aria-selected="true">자유 게시판</div><div role="option" aria-selected="false">외주</div></div><input placeholder="제목을 입력해주세요."><div class="ProseMirror" contenteditable="true"><p><br></p></div><button id="publish">글쓰기</button><script>
document.querySelector('#board').onclick=()=>document.querySelector('#boards').style.display='block';
document.querySelectorAll('[role=option]').forEach(option=>option.onclick=()=>{document.querySelector('#board').textContent=option.textContent;document.querySelector('#boards').style.display='none'});
document.execCommand('defaultParagraphSeparator',false,'p');
document.querySelector('.ProseMirror').addEventListener('input',()=>{if(!document.querySelector('.ProseMirror > p'))document.execCommand('formatBlock',false,'p')});
document.querySelector('#publish').onclick=async()=>{const r=await fetch('/fixture-submit',{method:'POST',body:JSON.stringify({board:document.querySelector('#board').textContent,title:document.querySelector('input').value,body:Array.from(document.querySelectorAll('.ProseMirror > p')).map(p=>p.textContent).join('\\n')})});location.href=(await r.json()).url};
</script>'''); return
            if '/posts/' in self.path:
                value = site['posts'][self.path.rsplit('/',1)[-1]]
                body = ''.join('<div class="article-body"><span>'+escape(line or '\u3164')+'</span></div>' for line in value['body'].split('\n'))
                def comment(id_,body,reply=False):
                    cls = 'pl-[44px]' if reply else ''
                    return f'<div class="{cls}" data-comment-id="{id_}"><div><span class="t4-bold">합성 문의자</span><span>3일 전</span></div><p class="article-body">{body}</p></div>'
                comments = ('<div class="flex items-center gap-x5 flex-1"><button><svg><path d="M99 99"></path></svg><span class="t4-regular">99</span></button>'
                    '<button><svg><path d="M6.8501 8.5C6.8501 7.94772"></path></svg><span class="t4-regular">5</span></button></div>'+comment('request','견적 가능할까요? 연락 주세요')+
                    '<button id="expand">이전 답글 1개 더보기</button><script>document.querySelector("#expand").onclick=()=>{document.querySelector("#expand").outerHTML='+json.dumps(comment('reply','어떤 기술을 쓰나요?',True))+'};</script>') if value['own'] else ''
                self.send(prefix + '<h1>'+escape(value['title'])+'</h1><span class="seed-tag-group-item__label--size_t3">'+value['written']+'</span>'+body+comments); return
            cards = ''.join(f'<a class="block group" href="/synthetic/posts/{id_}"><div data-nosnippet="true"><p class="t5-regular line-clamp-2">{escape(p["title"])}</p></div></a>' for id_,p in site['posts'].items())
            self.send(prefix + '<h2>합성 카페</h2><a href="/synthetic/posts/new">글쓰기</a><nav aria-label="게시판"><a href="/synthetic/boards/jobs-test">외주</a></nav>'+cards)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    stamp = [NOW]
    ctx.clock = lambda: stamp[0]
    browser = EdgeBrowser(tmp_path / 'local-flow-profile')
    def reserve():
        stamp[0] += timedelta(seconds=8)
        with ctx.db.session() as s: assert svc.reserve_navigation(s, stamp[0]) == 0
    def navigate(url):
        reserve()
        html, status = browser.goto(url)
        state, reason = detect_session(html, status=status)
        with ctx.db.session() as s: svc.set_session(s, state, stamp[0], reason)
        if state != 'logged_in': raise CafePageError('cafe_needs_user', '인증 확인 필요')
        return html
    try:
        browser.open()
        browser.navigate, browser.clock = navigate, ctx.clock
        def local(route):
            # Every request is intercepted; no live network origin is fetched.
            parsed = urlsplit(route.request.url)
            if parsed.netloc != 'cafe.daangn.com': route.abort(); return
            response = httpx.request(route.request.method, f'http://127.0.0.1:{server.server_port}'+parsed.path, content=route.request.post_data)
            route.fulfill(status=response.status_code, content_type=response.headers['content-type'], body=response.content)
        browser.context.route('**/*', local)
        browser.robots = RobotFileParser(); browser.robots.parse(['User-agent: *','Allow: /'])
        navigate('https://cafe.daangn.com/synthetic')
        assert collect(ctx, browser.recent_posts)['created'] == 1
        from worklead.cafe.posting import template
        with ctx.db.session() as s: value = template(s)
        with pytest.raises(CafePageError) as absent:
            browser.prepare_editor('https://cafe.daangn.com/synthetic', '미확인 게시판', value, navigate)
        assert absent.value.code == 'cafe_board_unverified'
        assert browser.page.get_by_placeholder('제목을 입력해주세요.').input_value() == ''
        assert browser.page.locator('div.ProseMirror').inner_text().strip() == '' and site['submissions'] == 0
        # Two rendered options with the same name are also unsafe.
        def ambiguous_editor(url):
            html = navigate(url)
            if url.endswith('/posts/new'):
                browser.page.evaluate("document.querySelector('#boards').append(document.querySelector('[role=option]:last-child').cloneNode(true))")
            return html
        with pytest.raises(CafePageError) as ambiguous:
            browser.prepare_editor('https://cafe.daangn.com/synthetic', '외주', value, ambiguous_editor)
        assert ambiguous.value.code == 'cafe_board_unverified'
        assert browser.page.get_by_placeholder('제목을 입력해주세요.').input_value() == '' and site['submissions'] == 0
        result = publish(ctx, browser, cafe_id, navigate, reserve)
        assert result['status'] == 'posted' and site['submissions'] == 1
        assert site['posts']['published']['board'] == '외주'
        from worklead.models import CafePostAttempt
        with ctx.db.session() as s: attempt_id = s.scalar(select(CafePostAttempt)).id
        assert save_page(ctx, attempt_id, browser.visible_comments(result['post_url']))['created'] == 2
        from worklead.cafe.comments import listing
        with ctx.db.session() as s:
            coverage = listing(s).coverage[0]
            assert coverage.reported_count == 5 and coverage.read_count == 2 and coverage.needs_app_check
        assert publish(ctx, browser, cafe_id, navigate, reserve)['status'] == 'already_exists'
        assert site['submissions'] == 1
        site['captcha'] = True
        with pytest.raises(CafePageError): navigate('https://cafe.daangn.com/synthetic')
        with ctx.db.session() as s:
            assert svc.session_row(s).state == 'needs_user'
            with pytest.raises(svc.CafeError): svc.enqueue_job(s, 'discover', stamp[0])
            assert len(list(s.scalars(select(Lead)))) == 1 and len(list(s.scalars(select(CafeComment)))) == 2
    finally:
        browser.close(); server.shutdown(); server.server_close()
