"""One full local HTTP cafe flow in system Edge. Synthetic identity/range
markers are TEST ONLY; they are not selectors claimed for the production web.
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
    site = {'posts': {'buyer': {'title':'홈페이지 제작 의뢰', 'body':'온라인으로 진행할 웹사이트 제작 의뢰합니다.', 'own':False, 'written':(NOW-timedelta(days=1)).isoformat()}}, 'submissions':0, 'captcha':False}
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
            site['posts']['published'] = {**data, 'own':True, 'written':NOW.isoformat()}
            self.send(json.dumps({'url':'/synthetic/posts/published'}), json_=True)
        def do_GET(self):
            if site['captcha']:
                self.send(prefix + '<div role="dialog">본인 인증</div>'); return
            if self.path.endswith('/posts/new'):
                self.send(prefix + '''<button>외주</button><input placeholder="제목을 입력해주세요."><div class="ProseMirror" contenteditable="true"><p><br></p></div><button id="publish">글쓰기</button><script>
document.execCommand('defaultParagraphSeparator',false,'p');
document.querySelector('.ProseMirror').addEventListener('input',()=>{if(!document.querySelector('.ProseMirror > p'))document.execCommand('formatBlock',false,'p')});
document.querySelector('#publish').onclick=async()=>{const r=await fetch('/fixture-submit',{method:'POST',body:JSON.stringify({title:document.querySelector('input').value,body:Array.from(document.querySelectorAll('.ProseMirror > p')).map(p=>p.textContent).join('\\n')})});location.href=(await r.json()).url};
</script>'''); return
            if '/posts/' in self.path:
                value = site['posts'][self.path.rsplit('/',1)[-1]]
                body = ''.join('<p>'+escape(line)+'</p>' for line in value['body'].split('\n'))
                comments = '<div data-comment-id="synthetic-comment" data-test-own="false"><strong>합성 문의자</strong><p class="article-body">견적 가능할까요? 연락 주세요</p></div>' if value['own'] else ''
                self.send(prefix + '<h1>'+escape(value['title'])+'</h1><div class="article-body">'+body+'</div>'+comments); return
            cards = ''.join(f'<a href="/synthetic/posts/{id_}" data-test-own="{str(p["own"]).lower()}" data-test-written="{p["written"]}">{escape(p["title"])}</a>' for id_,p in site['posts'].items())
            self.send(prefix + '<h2>합성 카페</h2><a href="/synthetic/posts/new">글쓰기</a><a href="/synthetic/boards/jobs-test">외주</a>'+cards)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    stamp = [NOW]
    ctx.clock = lambda: stamp[0]
    class FakeEdge(EdgeBrowser):
        def owned_posts(self, url, navigate):
            navigate(url)
            result = []
            for link in self.page.locator('a[data-test-own="true"]').all():
                href = 'https://cafe.daangn.com' + link.get_attribute('href')
                navigate(href)
                result.append({**self.read_post(), 'url':href})
            return result, True
        def recent_posts(self, board, since):
            navigate(board.url)
            cards = self.page.locator('a[data-test-own]').evaluate_all('(nodes)=>nodes.map(n=>({href:n.getAttribute("href"),own:n.dataset.testOwn,written:n.dataset.testWritten}))')
            posts = []
            for card in cards:
                url = 'https://cafe.daangn.com' + card['href']
                navigate(url)
                text = self.read_post()
                posts.append(CafePost(ParsedPost(text['title'],text['body'],url,url,url.rsplit('/',1)[-1],published=DateInfo(datetime.fromisoformat(card['written']),'exact',None)),card['own']=='true'))
            return CafePage(posts, True)
        def full_comments(self, url):
            navigate(url)
            return CommentPage([Comment(n.locator('strong').inner_text(), n.locator('p.article-body').inner_text(), NOW, False, n.get_attribute('data-comment-id')) for n in self.page.locator('[data-comment-id]').all()], True)
    browser = FakeEdge(tmp_path / 'local-flow-profile')
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
        result = publish(ctx, browser, cafe_id, navigate, reserve)
        assert result['status'] == 'posted' and site['submissions'] == 1
        from worklead.models import CafePostAttempt
        with ctx.db.session() as s: attempt_id = s.scalar(select(CafePostAttempt)).id
        assert save_page(ctx, attempt_id, browser.full_comments(result['post_url']))['created'] == 1
        assert publish(ctx, browser, cafe_id, navigate, reserve)['status'] == 'already_exists'
        assert site['submissions'] == 1
        site['captcha'] = True
        with pytest.raises(CafePageError): navigate('https://cafe.daangn.com/synthetic')
        with ctx.db.session() as s:
            assert svc.session_row(s).state == 'needs_user'
            with pytest.raises(svc.CafeError): svc.enqueue_job(s, 'discover', stamp[0])
            assert len(list(s.scalars(select(Lead)))) == 1 and len(list(s.scalars(select(CafeComment)))) == 1
    finally:
        browser.close(); server.shutdown(); server.server_close()
