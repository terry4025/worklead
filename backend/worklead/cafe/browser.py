"""System Edge only. Browser objects never leave their owner thread."""
from pathlib import Path
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser
import os

from .parsing import CafePageError, safe_cafe_url

AUTH_ORIGIN = "https://accounts.daangn.com"  # Observed via official login button, 2026-10-12.


class EdgeBrowser:
    def __init__(self, profile: Path):
        self.profile = profile
        self.driver = self.context = self.page = None
        self.headed = False
        self.robots = None
        self.blocked_navigation = False

    def open(self, *, headed=False):
        if self.context and self.headed == headed:
            return
        self.close()
        from playwright.sync_api import sync_playwright
        self.driver = sync_playwright().start()
        self.profile.mkdir(parents=True, exist_ok=True)
        options = {"channel": "msedge", "headless": not headed}
        # Some packaged/Windows environments do not expose ProgramFiles to the
        # driver's channel resolver; use the installed Edge, never download Chromium.
        if os.name == "nt":
            for base in (os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), os.environ.get("ProgramFiles", r"C:\Program Files")):
                path = Path(base) / "Microsoft/Edge/Application/msedge.exe"
                if path.is_file():
                    options["executable_path"] = str(path)
                    break
        try:
            self.context = self.driver.chromium.launch_persistent_context(str(self.profile), **options)
            self.context.set_default_timeout(10000)
            self.headed = headed
            self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
            self.context.route("**/*", self._guard)
        except Exception:
            self.close()
            raise CafePageError("cafe_browser_unavailable", "시스템 Edge 실행 실패. 다른 카페 창을 닫고 다시 확인하세요") from None

    def _guard(self, route):
        request = route.request
        if request.is_navigation_request() and request.frame.parent_frame is None:
            p = urlsplit(request.url)
            try:
                # Permit natural steps within the observed official login origin
                # only while the user explicitly requested a visible login window.
                allowed = request.url == "about:blank" or bool(self.headed and p.scheme == "https" and p.netloc == "accounts.daangn.com")
                if not allowed:
                    safe_cafe_url(request.url)
                route.continue_()
            except CafePageError:
                self.blocked_navigation = True
                route.abort()
        else:
            # Natural page subrequests are permitted; no private API is called directly.
            route.continue_()

    def robots_check(self):
        self.open(headed=self.headed)
        response = self.context.request.get("https://cafe.daangn.com/robots.txt", timeout=15000)
        content = response.text()
        if response.status != 200 or "user-agent:" not in content.lower():
            raise CafePageError("cafe_robots_unavailable", "robots.txt 허용 규칙을 확인하지 못했습니다")
        parser = RobotFileParser()
        parser.parse(content.splitlines())
        self.robots = parser

    def goto(self, url):
        safe_cafe_url(url)
        if self.robots is None:
            self.robots_check()
        if not self.robots.can_fetch("Worklead", url):
            raise CafePageError("cafe_robots_denied", "robots.txt에서 금지한 경로입니다")
        self.blocked_navigation = False
        response = self.page.goto(url, wait_until="domcontentloaded", timeout=25000)
        self.page.wait_for_timeout(700)
        # The profile controls hydrate after the server-rendered cafe heading.
        # Wait for an observed membership marker; absence stays unknown.
        path = urlsplit(url).path.rstrip('/')
        if path.count('/') == 1 and path:
            from playwright.sync_api import TimeoutError as PlaywrightTimeout
            try:
                self.page.wait_for_function("writePath => Array.from(document.querySelectorAll('a[href]')).some(a => new URL(a.href).pathname === writePath) || Array.from(document.querySelectorAll('button')).some(b => b.textContent.trim() === '카페 가입하기')", arg=path + '/posts/new', timeout=3500)
            except PlaywrightTimeout:
                pass
        if self.blocked_navigation:
            raise CafePageError("cafe_redirect_blocked", "허용되지 않은 외부 이동을 중단했습니다")
        return self.page.content(), response.status if response else None

    def login(self, stopped):
        # User enters credentials. This code only waits for the visible account marker.
        button = self.page.get_by_role("button", name="로그인", exact=True)
        if button.count():
            button.click()
        for _ in range(300):
            if stopped.wait(1):
                return False
            if self.page.is_closed():
                return False
            if self.blocked_navigation:
                raise CafePageError("cafe_redirect_blocked", "로그인에서 허용되지 않은 이동을 중단했습니다")
            if urlsplit(self.page.url).netloc == 'accounts.daangn.com':
                from .parsing import detect_session
                state, reason = detect_session(self.page.content())
                if state == 'needs_user' and reason == 'authentication_challenge':
                    raise CafePageError('cafe_authentication_challenge', '인증·약관·차단 화면을 직접 확인하세요')
            if urlsplit(self.page.url).netloc == "cafe.daangn.com" and self.page.get_by_role("button", name="사용자 메뉴").count():
                return True
        return False

    def recent_posts(self, board, since):
        from datetime import timedelta
        from .collection import CafePage, CafePost
        from .parsing import relative_date, post_relative_time
        from ..sources.base import ParsedPost
        if not getattr(self, 'navigate', None):
            raise CafePageError('cafe_browser_queue_required', '직렬 브라우저 큐에서만 수집합니다')
        cards = self.board_posts(board, self.navigate)
        observed_at = since + timedelta(days=30)
        result = []
        # Always rescan the visible 30-day window. Relative day labels cannot
        # support an exact incremental timestamp cursor.
        for card in cards:
            stamp = relative_date(card['written_raw'], observed_at)
            if stamp.at and stamp.at < since: continue
            html = self.navigate(card['url'])
            actual = self.read_post()
            if stamp.at is None: stamp = relative_date(post_relative_time(html), observed_at)
            result.append(CafePost(ParsedPost(actual['title'], actual['body'], card['url'], card['url'],
                urlsplit(card['url']).path.rsplit('/', 1)[-1], published=stamp), None))
        return CafePage(result, True)

    def board_posts(self, board, navigate):
        from .parsing import board_posts, board_listing_ready
        navigate(board.url)
        cards = {}
        previous, stable = None, 0
        for _ in range(40):
            html = self.page.content()
            for value in board_posts(html, board.url): cards[value['url']] = value
            state = (len(cards), self.page.evaluate('document.documentElement.scrollHeight'))
            stable = stable + 1 if state == previous else 0
            if stable >= 3:
                if not board_listing_ready(html, board.url):
                    raise CafePageError('cafe_listing_unrecognized', '게시판 목록이 준비됐는지 확인하지 못했습니다. 게시하지 않습니다')
                return list(cards.values())
            previous = state
            self.page.evaluate('window.scrollTo(0,document.documentElement.scrollHeight)')
            self.page.wait_for_timeout(700)
        raise CafePageError('cafe_listing_unstable', '게시판 목록이 계속 바뀌어 안전한 대조를 마치지 못했습니다')

    def duplicate_posts(self, board, navigate):
        from .parsing import board_posts, board_listing_ready
        # Also inspect the cafe's rendered home feed. Finding the same title
        # in another board must not cause a new post in the selected board.
        home_url = board.url.split('/boards/', 1)[0]
        html = navigate(home_url)
        if not board_listing_ready(html, home_url):
            raise CafePageError('cafe_listing_unrecognized', '카페 글 목록 구조를 확인하지 못했습니다. 게시하지 않습니다')
        home = board_posts(html, home_url)
        approved_board = self.board_posts(board, navigate)
        return list({value['url']: value for value in home + approved_board}.values())

    def visible_comments(self, url):
        import re
        from .comments import Comment, CommentPage
        from .parsing import relative_date
        self.navigate(url)
        # Only the observed expandable reply control; never click the app-only
        # 전체보기 link, reply composer, or an unrelated 더보기 control.
        for _ in range(40):
            buttons = self.page.get_by_role('button', name=re.compile(r'^이전 답글 \d+개 더보기$'))
            if not buttons.count(): break
            before = self.page.locator('[data-comment-id]').count()
            buttons.first.click()
            self.page.wait_for_timeout(700)
            if self.page.locator('[data-comment-id]').count() <= before:
                raise CafePageError('cafe_reply_unexpanded', '답글 펼치기가 완료되지 않았습니다')
        else: raise CafePageError('cafe_reply_limit', '답글 펼치기 안전 한도에 도달했습니다')
        rows = self.page.locator('[data-comment-id]').evaluate_all('''nodes => nodes.map(n => {
            const body = n.querySelector('p.article-body');
            const author = n.querySelector('span.t4-bold');
            const header = author?.parentElement?.parentElement;
            return {id:n.getAttribute('data-comment-id'),body:body?.innerText ?? null,
                author:author?.innerText ?? '',time:Array.from(header?.querySelectorAll('span') ?? []).map(x=>x.innerText),
                own:Array.from(header?.querySelectorAll('span') ?? []).some(x=>x.innerText.trim()==='글쓴이'),
                reply:Array.from((function*(v){while(v){yield v;v=v.parentElement;}})(n)).some(v=>v.classList.contains('pl-[44px]'))};
        })''')
        comments = []
        for value in rows:
            if value['body'] is None: raise CafePageError('cafe_comment_unrecognized', '댓글 본문 구조를 확인하지 못했습니다')
            raw = next((v for v in value['time'] if relative_date(v, self.clock()).at is not None), None)
            stamp = relative_date(raw, self.clock())
            comments.append(Comment(value['author'], value['body'], stamp.at, value['own'], value['id'], raw, value['reply']))
        headings = self.page.locator('h3').all_text_contents()
        counts = [int(m.group(1).replace(',', '')) for text in headings if (m := re.fullmatch(r'\s*댓글\s*([\d,]+)\s*', text))]
        if not counts:
            # Observed comment-bubble icon identifies the count, unlike the
            # neighboring numeric like button. A changed icon yields unknown.
            bubbles = self.page.locator('button').filter(has=self.page.locator('svg path[d^="M6.8501 8.5"]'))
            counts = [int(text.replace(',', '')) for text in bubbles.locator('span.t4-regular').all_text_contents() if re.fullmatch(r'[\d,]+', text.strip())]
        return CommentPage(comments, True, reported_count=counts[0] if counts else None)

    def prepare_editor(self, cafe_url, board_name, template, navigate):
        navigate(cafe_url)
        link = self.page.locator('a').filter(has_text='글쓰기')
        urls = [link.nth(i).get_attribute('href') for i in range(link.count())]
        from urllib.parse import urljoin
        editor_url = next((urljoin(cafe_url + '/', url) for url in urls if url and url.endswith('/posts/new')), None)
        if not editor_url: raise CafePageError('cafe_editor_unavailable', '실제 글쓰기 링크를 찾지 못했습니다')
        navigate(editor_url)
        # Observed editor control is a button with combobox semantics, followed
        # by listbox/option rows. Select only the exact approved board name.
        control = self.page.locator('button[role="combobox"][aria-haspopup="listbox"]')
        if control.count() != 1:
            raise CafePageError('cafe_board_unverified', '게시판 선택 구조를 확인하지 못했습니다. 게시하지 않습니다')
        if control.inner_text().strip() != board_name:
            control.click()
            lists = self.page.get_by_role('listbox')
            if lists.count() != 1:
                raise CafePageError('cafe_board_unverified', '게시판 목록을 확인하지 못했습니다. 게시하지 않습니다')
            option = lists.get_by_role('option', name=board_name, exact=True)
            if option.count() != 1:
                raise CafePageError('cafe_board_unverified', '승인된 게시판을 하나로 확인하지 못했습니다. 게시하지 않습니다')
            option.click()
            from playwright.sync_api import expect
            try: expect(control).to_have_text(board_name, timeout=3000)
            except AssertionError:
                raise CafePageError('cafe_board_unverified', '게시판 선택 결과가 다릅니다. 게시하지 않습니다') from None
        if control.count() != 1 or control.inner_text().strip() != board_name:
            raise CafePageError('cafe_board_unverified', '선택된 게시판을 확인하지 못했습니다. 게시하지 않습니다')
        self.page.get_by_placeholder('제목을 입력해주세요.', exact=True).fill(template.title)
        self.fill_editor(template.body)

    def fill_editor(self, body):
        editor = self.page.locator('div.ProseMirror[contenteditable="true"]')
        if editor.count() != 1: raise CafePageError('cafe_editor_unrecognized', '확인한 편집기 구조와 다릅니다')
        target = body.replace('\r\n', '\n').split('\n')
        for filler in (False, True):
            editor.click()
            self.page.keyboard.press('Control+A')
            self.page.keyboard.press('Backspace')
            for index, line in enumerate(target):
                if index: self.page.keyboard.press('Enter')
                if line or filler: self.page.keyboard.insert_text(line or '\u3164')
            actual = editor.locator(':scope > p').all_text_contents()
            if [line if line != '\u3164' else '' for line in actual] == target:
                return 'filler' if filler else 'paragraph'
        raise CafePageError('cafe_linebreak_mismatch', '저장 전 편집기의 줄바꿈이 다릅니다. 게시하지 않습니다')

    def submit_post(self):
        self.page.get_by_role('button', name='글쓰기', exact=True).last.click()
        self.page.wait_for_url(lambda url: '/posts/' in url and not url.endswith('/new'), timeout=25000)
        return self.page.url

    def read_post(self):
        title = self.page.locator('h1')
        body = self.page.locator('div.article-body')
        if title.count() != 1 or body.count() < 1:
            raise CafePageError('cafe_post_unrecognized', '게시된 제목·본문 구조를 확인하지 못했습니다')
        # The observed site has either one paragraph container or sibling
        # article-body blocks. Reading only p descendants loses the latter.
        paragraphs = body.evaluate_all('''nodes => nodes.filter(n=>!n.parentElement.closest('div.article-body')).flatMap(n => {
            const p=Array.from(n.querySelectorAll(':scope > p'));
            return p.length ? p.map(x=>x.innerText) : [n.innerText];
        })''')
        return {'title': title.inner_text(), 'body': '\n'.join(paragraphs)}

    def close(self):
        try:
            if self.context:
                self.context.close()
        finally:
            self.context = self.page = None
            if self.driver:
                self.driver.stop()
                self.driver = None
            self.robots = None
