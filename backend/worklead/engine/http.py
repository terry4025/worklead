"""공통 HTTP 실행기. 사이트 어댑터는 이 실행기를 통해서만 요청한다.

보장
- 허용 호스트 외 요청·리다이렉트 거부
- robots.txt 준수 (캐시 24시간)
- 호스트별 일일 요청 예산 공유 (같은 소스의 모든 작업)
- 호스트별 최소 요청 간격
- 같은 실행 안에서 자동 재시도하지 않음
- 401/403/CAPTCHA → 소스 정지(PolicyStop), 429 → Retry-After 존중하며 실행 중단(RunHalt)
- 차단·오류 페이지를 정상 결과로 돌려주지 않음
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Literal
from urllib.parse import urljoin, urlsplit

import httpx
from sqlalchemy import select, update

from ..db import Database, utcnow
from ..models import HostBudget, RobotsCache
from .robots import RobotsRules

log = logging.getLogger("worklead.http")

MAX_BYTES = 3 * 1024 * 1024
MAX_REDIRECTS = 3
ROBOTS_TTL = timedelta(hours=24)

Outcome = Literal[
    "ok",
    "not_found",
    "gone",
    "server_error",
    "network_error",
    "too_large",
    "client_error",
]


class PolicyStop(Exception):
    """소스 전체를 정지해야 하는 응답 (401·403·CAPTCHA·차단 페이지, 허용 외 호스트, robots 금지)."""

    def __init__(self, code: str, message: str, http_status: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


class RunHalt(Exception):
    """이번 실행만 중단 (429, 예산 소진). 다음 실행에서 이어간다."""

    def __init__(self, code: str, message: str, retry_after: datetime | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retry_after = retry_after


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int | None
    outcome: Outcome
    text: str | None
    headers: dict[str, str] = field(default_factory=dict)
    elapsed_ms: int = 0
    error: str | None = None


BlockDetector = Callable[[FetchResult], str | None]


def default_block_detector(result: FetchResult) -> str | None:
    """일반적인 CAPTCHA·봇 차단 표시. 어댑터가 사이트별 판별기를 추가할 수 있다."""
    if not result.text:
        return None
    head = result.text[:20000].lower()
    for marker in ("g-recaptcha", "hcaptcha", "cf-challenge", "captcha-container", "please verify you are a human", "자동입력 방지"):
        if marker in head:
            return f"captcha:{marker}"
    return None


class Fetcher:
    _host_locks: dict[str, threading.Lock] = {}
    _host_last: dict[str, float] = {}
    _guard = threading.Lock()

    def __init__(
        self,
        db: Database,
        *,
        allowed_hosts: set[str],
        user_agent: str,
        daily_budget: int,
        min_interval_s: float,
        block_detector: BlockDetector | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout_s: float = 20.0,
        sleep: Callable[[float], None] = time.sleep,
        on_request: Callable[[], None] | None = None,
    ):
        self.db = db
        self.allowed_hosts = {h.lower() for h in allowed_hosts}
        self.user_agent = user_agent
        self.daily_budget = daily_budget
        self.min_interval_s = min_interval_s
        self.block_detector = block_detector
        self.sleep = sleep
        self.on_request = on_request
        self.client = httpx.Client(
            transport=transport,
            timeout=httpx.Timeout(timeout_s, connect=10.0),
            follow_redirects=False,
            headers={"User-Agent": user_agent, "Accept-Language": "ko-KR,ko;q=0.9"},
        )
        self._robots: dict[str, RobotsRules] = {}

    def close(self) -> None:
        self.client.close()

    # ── 가드 ────────────────────────────────────────────────────────
    def _check_host(self, url: str) -> str:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            raise PolicyStop("scheme_not_allowed", f"허용되지 않은 스킴: {parts.scheme}")
        host = (parts.hostname or "").lower()
        if host not in self.allowed_hosts:
            raise PolicyStop("host_not_allowed", f"허용 호스트가 아닙니다: {host}")
        return host

    def _reserve_budget(self, host: str) -> None:
        window = datetime.now(UTC).strftime("%Y-%m-%d")
        with self.db.session() as s:
            row = s.get(HostBudget, (host, window))
            if row is None:
                s.add(HostBudget(host=host, window=window, used=0, limit=self.daily_budget))
                s.flush()
            res = s.execute(
                update(HostBudget)
                .where(HostBudget.host == host, HostBudget.window == window, HostBudget.used < HostBudget.limit)
                .values(used=HostBudget.used + 1, last_request_at=utcnow())
            )
            if res.rowcount == 0:  # type: ignore[attr-defined]
                raise RunHalt("budget_exhausted", f"{host} 일일 요청 예산 소진 ({self.daily_budget}회)")

    def _pace(self, host: str) -> None:
        with Fetcher._guard:
            lock = Fetcher._host_locks.setdefault(host, threading.Lock())
        with lock:
            last = Fetcher._host_last.get(host)
            if last is not None:
                wait = self.min_interval_s - (time.monotonic() - last)
                if wait > 0:
                    self.sleep(wait)
            Fetcher._host_last[host] = time.monotonic()

    # ── robots.txt ──────────────────────────────────────────────────
    def robots_for(self, url: str) -> RobotsRules:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if host in self._robots:
            return self._robots[host]
        with self.db.session() as s:
            cached = s.get(RobotsCache, host)
            if cached and cached.fetched_at > utcnow() - ROBOTS_TTL:
                rules = self._rules_from_cache(cached)
                self._robots[host] = rules
                return rules
        robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
        result = self._raw_get(robots_url, host)
        if result.status is not None and 200 <= result.status < 300:
            status, body = "ok", result.text or ""
        elif result.status is not None and 400 <= result.status < 500:
            status, body = "missing", None
        else:
            status, body = "unreachable", None
        with self.db.session() as s:
            row = s.get(RobotsCache, host) or RobotsCache(host=host)
            row.fetched_at = utcnow()
            row.status = status
            row.http_status = result.status
            row.body = body
            s.merge(row)
        rules = self._rules_from_cache(RobotsCache(host=host, fetched_at=utcnow(), status=status, body=body))
        self._robots[host] = rules
        return rules

    @staticmethod
    def _rules_from_cache(row: RobotsCache) -> RobotsRules:
        if row.status == "ok":
            return RobotsRules(row.body or "")
        if row.status == "missing":
            return RobotsRules(None, mode="allow_all")
        return RobotsRules(None, mode="disallow_all")

    # ── 요청 ────────────────────────────────────────────────────────
    def _raw_get(self, url: str, host: str) -> FetchResult:
        self._reserve_budget(host)
        self._pace(host)
        if self.on_request:
            self.on_request()
        started = time.monotonic()
        try:
            with self.client.stream("GET", url) as resp:
                chunks: list[bytes] = []
                size = 0
                for chunk in resp.iter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        return FetchResult(url, url, resp.status_code, "too_large", None, dict(resp.headers), int((time.monotonic() - started) * 1000))
                    chunks.append(chunk)
                raw = b"".join(chunks)
                encoding = resp.charset_encoding or "utf-8"
                text = raw.decode(encoding, errors="replace")
                return FetchResult(url, url, resp.status_code, "ok", text, dict(resp.headers), int((time.monotonic() - started) * 1000))
        except httpx.HTTPError as exc:
            return FetchResult(url, url, None, "network_error", None, {}, int((time.monotonic() - started) * 1000), type(exc).__name__)

    def get(self, url: str) -> FetchResult:
        """허용 호스트·robots·예산·간격을 지킨 GET. 정상/빈 결과와 오류를 구분해 돌려준다."""
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            host = self._check_host(current)
            robots = self.robots_for(current)
            if robots.mode == "disallow_all":
                raise RunHalt("robots_unreachable", "robots.txt 를 확인할 수 없어(5xx·네트워크) 이번 실행을 중단합니다")
            if not robots.allowed(current, self.user_agent):
                raise PolicyStop("robots_disallowed", f"robots.txt 가 금지한 경로입니다: {urlsplit(current).path}")
            result = self._raw_get(current, host)
            result.url = url
            result.final_url = current
            status = result.status
            log.info("fetch host=%s status=%s outcome=%s ms=%s", host, status, result.outcome, result.elapsed_ms)
            if status is None:
                return result
            if status in (301, 302, 303, 307, 308):
                loc = result.headers.get("location")
                if not loc:
                    result.outcome = "client_error"
                    return result
                current = urljoin(current, loc)
                continue
            if status == 401:
                raise PolicyStop("login_required", "로그인이 필요한 응답(401) — 소스를 정지합니다", status)
            if status == 403:
                raise PolicyStop("http_403", "접근 차단 응답(403) — 소스를 정지합니다. 우회하지 않습니다.", status)
            if status == 429:
                raise RunHalt("rate_limited", "요청 제한 응답(429) — Retry-After 까지 유예합니다", _retry_after(result.headers))
            if status == 404:
                result.outcome = "not_found"
                return result
            if status == 410:
                result.outcome = "gone"
                return result
            if status >= 500:
                result.outcome = "server_error"
                return result
            if status >= 400:
                result.outcome = "client_error"
                return result
            detectors = [default_block_detector] + ([self.block_detector] if self.block_detector else [])
            for detect in detectors:
                blocked = detect(result)
                if blocked:
                    raise PolicyStop("captcha_or_block_page", f"차단·CAPTCHA 페이지 감지({blocked}) — 소스를 정지합니다", status)
            return result
        raise PolicyStop("too_many_redirects", "리다이렉트가 너무 많습니다")


def _retry_after(headers: dict[str, str]) -> datetime:
    value = headers.get("retry-after") or headers.get("Retry-After")
    now = datetime.now(UTC)
    if value:
        if value.strip().isdigit():
            return now + timedelta(seconds=int(value.strip()))
        try:
            dt = parsedate_to_datetime(value)
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        except (TypeError, ValueError):
            pass
    return now + timedelta(minutes=30)


def budget_status(db: Database, hosts: set[str], daily_budget: int) -> tuple[int, int]:
    window = datetime.now(UTC).strftime("%Y-%m-%d")
    used = 0
    with db.session() as s:
        for row in s.scalars(select(HostBudget).where(HostBudget.window == window, HostBudget.host.in_(hosts))):
            used += row.used
    return used, daily_budget * max(1, len(hosts))
