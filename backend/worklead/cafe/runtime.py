"""One durable cafe queue, one Playwright owner, no external calls in API handlers."""
import threading
from sqlalchemy import select
from ..models import Cafe
from ..db import MaintenanceBusy
from ..services import cafe as svc
from .browser import EdgeBrowser
from .parsing import CAFE_ORIGIN, CATEGORY_PATHS, CafePageError, detect_session, directory_links, cafe_home, notice_links, promotion_rule_sentences


class CafeRuntime:
    def __init__(self, ctx, browser_factory=EdgeBrowser):
        self.ctx = ctx
        self.browser_factory = browser_factory
        self.stopped = threading.Event()
        self.thread = None
        self.browser = None

    def start(self):
        if self.thread:
            return
        with self.ctx.db.session() as s:
            svc.recover_interrupted_jobs(s, self.ctx.clock())
        self.thread = threading.Thread(target=self._run, name="cafe-browser", daemon=True)
        self.thread.start()

    def stop(self):
        self.stopped.set()
        if self.thread:
            self.thread.join(timeout=35)

    def _navigate(self, url, *, allow_recovery=False):
        self._reserve(allow_recovery=allow_recovery)
        html, status = self.browser.goto(url)
        state, reason = detect_session(html, status=status)
        with self.ctx.db.session() as s:
            svc.set_session(s, state, self.ctx.clock(), reason)
        if state != "logged_in":
            raise CafePageError("cafe_" + state, "로그인 또는 인증 확인이 필요합니다")
        return html

    def _reserve(self, *, allow_recovery=False):
        while not self.stopped.is_set():
            with self.ctx.db.session() as s:
                # Explicit login is the sole recovery path from needs_user.
                if allow_recovery:
                    svc.session_row(s).state = "logged_out"
                wait = svc.reserve_navigation(s, self.ctx.clock())
            if not wait:
                break
            if self.stopped.wait(min(wait, 1)):
                raise CafePageError("cafe_cancelled", "앱 종료로 중단했습니다")
        if self.stopped.is_set():
            raise CafePageError("cafe_cancelled", "앱 종료로 중단했습니다")

    def execute(self, job):
        if job.kind == 'collect':
            raise CafePageError('cafe_collection_unverified', '내 글 제외·작성 시각 지원 경로 미확보로 수집을 중단했습니다')
        if job.kind == 'comments':
            raise CafePageError('cafe_comments_incomplete', '전체 댓글 조회 지원 경로 미확보로 감시를 완료하지 않습니다')
        self.browser = self.browser or self.browser_factory(self.ctx.config.data_dir / "cafe-browser")
        self.browser.open(headed=job.kind == "login")
        if job.kind == "login":
            try:
                self._navigate(CAFE_ORIGIN + "/", allow_recovery=True)
            except CafePageError as exc:
                if exc.code != "cafe_logged_out":
                    raise
                if not self.browser.login(self.stopped):
                    raise CafePageError("cafe_login_incomplete", "로그인 완료를 확인하지 못했습니다")
                self._navigate(CAFE_ORIGIN + "/")
            return {}
        self._navigate(CAFE_ORIGIN + "/")
        if job.kind == "check":
            return {}
        if job.kind == 'post':
            from .posting import publish
            return publish(self.ctx, self.browser, job.input.get('cafe_id'), self._navigate, self._reserve)
        if job.kind == "collect":
            from .collection import collect
            return collect(self.ctx, self.browser.recent_posts)
        with self.ctx.db.session() as s:
            settings = svc.get_settings(s)
            manual = [{"url": row.url} for row in s.scalars(select(Cafe).where(Cafe.status != "excluded"))]
        candidates = {item["url"]: item for item in manual}
        if job.kind == "discover":
            for path in CATEGORY_PATHS:
                html = self._navigate(CAFE_ORIGIN + path)
                for item in directory_links(html, CAFE_ORIGIN + path, settings["keywords"]):
                    candidates[item["url"]] = item
        # Persist candidates before detailed inspection; a daily budget stop
        # retains findings and never presents the entire discovery as complete.
        with self.ctx.db.session() as s:
            svc.save_discovery(s, list(candidates.values()), self.ctx.clock())
        inspected = 0
        for item in candidates.values():
            home = self._navigate(item["url"])
            rules = []
            # Only the actual rendered notices link is followed.
            from .parsing import _nodes
            from urllib.parse import urljoin
            links = [urljoin(item["url"] + "/", n.attrs.get("href", "")) for n in _nodes(home) if n.tag == "a"]
            notices_url = item["url"] + "/notices"
            if notices_url in links:
                listing = self._navigate(notices_url)
                for url in notice_links(listing, item["url"]):
                    rules.extend(promotion_rule_sentences(self._navigate(url)))
            result = cafe_home(home, item["url"], rule_texts=rules)
            with self.ctx.db.session() as s:
                svc.save_discovery(s, [result], self.ctx.clock())
            inspected += 1
        return {"candidates": len(candidates), "inspected": inspected, "search_mode": "category_local_filter"}

    def _run(self):
        generation = self.ctx.db.gate.generation
        try:
            while not self.stopped.wait(.5):
                try:
                    with self.ctx.db.gate.operation():
                        if generation != self.ctx.db.gate.generation:
                            if self.browser:
                                self.browser.close()
                                self.browser = None
                            generation = self.ctx.db.gate.generation
                        with self.ctx.db.session() as s:
                            job = svc.claim_next_job(s, self.ctx.clock())
                        if job is None:
                            continue
                        try:
                            result = self.execute(job)
                            with self.ctx.db.session() as s:
                                svc.finish_job(s, job.id, "succeeded", self.ctx.clock(), result=result)
                        except (CafePageError, svc.CafeError) as exc:
                            with self.ctx.db.session() as s:
                                svc.finish_job(s, job.id, "failed", self.ctx.clock(), error_code=exc.code, error_message=exc.message if hasattr(exc, "message") else str(exc))
                        except Exception:
                            # Browser exceptions can contain URLs or personal text.
                            with self.ctx.db.session() as s:
                                svc.finish_job(s, job.id, "failed", self.ctx.clock(), error_code="cafe_runtime_error", error_message="카페 작업 실패. 브라우저와 세션 상태를 확인하세요")
                        self.ctx.hub.notify()
                except MaintenanceBusy:
                    continue
        finally:
            if self.browser:
                self.browser.close()
