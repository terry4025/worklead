"""Real system Edge against an HTTP fake site; zero real cafe requests."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.robotparser import RobotFileParser
import threading
import httpx
from worklead.cafe.browser import EdgeBrowser
from worklead.cafe.parsing import detect_session, cafe_home


def test_edge_persistent_profile_local_site_and_redirect_guard(tmp_path):
    class Site(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write('<button aria-label="사용자 메뉴">계정</button><h2>합성 외주 카페</h2><a href="/synthetic/boards/jobs-test">구인구직</a><script>setTimeout(()=>{document.body.insertAdjacentHTML("beforeend",\'<a href="/synthetic/posts/new">글쓰기</a>\')},1600)</script>'.encode())
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Site)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    browser = EdgeBrowser(tmp_path / 'test-only-profile')
    try:
        for iteration in range(2):
            browser.open()
            # Intercept the exact production origin and fulfill ONLY from the
            # synthetic localhost server. No response is fetched from Daangn.
            browser.context.route('https://cafe.daangn.com/**', lambda route: route.fulfill(status=200, content_type='text/html; charset=utf-8', body=httpx.get(f'http://127.0.0.1:{server.server_port}').text))
            browser.robots = RobotFileParser()
            browser.robots.parse(['User-agent: *', 'Allow: /', 'Disallow: /api/'])
            html, code = browser.goto('https://cafe.daangn.com/synthetic')
            assert code == 200 and detect_session(html)[0] == 'logged_in'
            assert cafe_home(html, 'https://cafe.daangn.com/synthetic')['joined'] is True
            # A synthetic paragraph editor keeps the observed ProseMirror p
            # structure; this does not claim the real site's editor was saved.
            browser.page.set_content('<div class="ProseMirror" contenteditable="true"><p><br></p></div><script>document.execCommand("defaultParagraphSeparator",false,"p");document.querySelector(".ProseMirror").addEventListener("input",()=>{if(!document.querySelector(".ProseMirror > p"))document.execCommand("formatBlock",false,"p")})</script>')
            assert browser.fill_editor('첫째\n\n\n둘째') in ('paragraph', 'filler')
            assert [line if line != '\u3164' else '' for line in browser.page.locator('div.ProseMirror > p').all_text_contents()] == ['첫째', '', '', '둘째']
            if iteration == 0:
                browser.context.add_cookies([{'name':'synthetic_session', 'value':'fixture', 'domain':'cafe.daangn.com', 'path':'/', 'expires':2000000000}])
            else:
                assert any(cookie['name'] == 'synthetic_session' for cookie in browser.context.cookies())
            browser.close()
    finally:
        browser.close()
        server.shutdown()
        server.server_close()
