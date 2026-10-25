"""앱 실행 문맥: 설정, DB, 어댑터 목록, 이벤트 허브, HTTP 실행기 생성기."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

import httpx
import threading

from .analysis.provider import AnalysisProvider, NullProvider
from .config import RuntimeConfig
from .db import Database, utcnow
from .engine.events import EventHub
from .engine.http import Fetcher
from .services.credentials import CredentialStore, WindowsCredentialStore


@dataclass
class AppContext:
    config: RuntimeConfig
    db: Database
    adapters: dict[str, object]
    hub: EventHub = field(default_factory=EventHub)
    provider: AnalysisProvider = field(default_factory=NullProvider)
    clock: Callable[[], datetime] = utcnow
    #: 테스트에서 가짜 사이트로 연결하기 위한 transport
    transport: httpx.BaseTransport | None = None
    sleep: Callable[[float], None] | None = None
    #: 토큰은 여기 외 어디에도 기록하지 않는다
    token: str = ""
    credential_store: CredentialStore = field(default_factory=WindowsCredentialStore, repr=False)
    ai_transport: httpx.BaseTransport | None = field(default=None, repr=False)
    ai_control_lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    _ai_coordinator: object | None = field(default=None, init=False, repr=False)
    _cafe_runtime: object | None = field(default=None, init=False, repr=False)
    _jobs_message_runtime: object | None = field(default=None, init=False, repr=False)
    _jobs_chrome_runtime: object | None = field(default=None, init=False, repr=False)
    _jobs_auto_apply_runtime: object | None = field(default=None, init=False, repr=False)
    jobs_message_provider: str = field(default="edge", init=False, repr=False)

    def __post_init__(self) -> None:
        if isinstance(self.provider, NullProvider):
            from .analysis.coordinator import ManagedProvider
            self.provider = ManagedProvider(self)

    @property
    def ai_coordinator(self):
        from .analysis.coordinator import AnalysisCoordinator
        with self.ai_control_lock:
            if self._ai_coordinator is None:
                self._ai_coordinator = AnalysisCoordinator(self)
            return self._ai_coordinator

    def fetcher_for(self, adapter: object) -> Fetcher:
        kwargs = {}
        if self.sleep is not None:
            kwargs["sleep"] = self.sleep
        return Fetcher(
            self.db,
            allowed_hosts=set(getattr(adapter, "allowed_hosts")),
            user_agent=self.config.user_agent,
            daily_budget=int(getattr(adapter, "daily_request_budget")),
            min_interval_s=float(getattr(adapter, "min_interval_seconds")),
            block_detector=getattr(adapter, "block_detector", None),
            transport=self.transport,
            **kwargs,
        )

    @property
    def cafe_runtime(self):
        from .cafe.runtime import CafeRuntime
        if self._cafe_runtime is None:
            self._cafe_runtime = CafeRuntime(self)
        return self._cafe_runtime
