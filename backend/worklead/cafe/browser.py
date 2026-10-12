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
                allowed = request.url == "about:blank" or bool(self.headed and p.scheme == "https" and p.netloc == "accounts.daangn.com" and p.path == "/sign-in")
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
            if urlsplit(self.page.url).netloc == "cafe.daangn.com" and self.page.get_by_role("button", name="사용자 메뉴").count():
                return True
        return False

    def recent_posts(self, board, since):
        raise CafePageError('cafe_collection_unverified', '내 글 제외와 작성 시각의 지원 경로가 필요합니다')

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
