"""EXAONE assist: one gate (switches, key, daily cap), masking and the background worker."""
from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable
from datetime import datetime, timedelta

from pydantic import BaseModel

from ..db import MaintenanceBusy
from ..services.credentials import CredentialError
from ..services.settings import get_setting, put_setting
from .provider import AIFailure, mask_personal

log = logging.getLogger("worklead.assist")
GATE_CODES = frozenset({"disabled", "not_configured", "call_cap_reached", "demo"})
# Bank-account shapes (>= 10 digits, last group 4+). Dates like 2026-11-09 are kept.
_ACCOUNT = re.compile(r"(?<!\d)\d{2,6}-\d{2,6}-\d{4,8}(?!\d)")


def mask_for_ai(text: str) -> str:
    return _ACCOUNT.sub("[계좌번호]", mask_personal(text or ""))


def kst_day(now: datetime) -> str:
    return (now + timedelta(hours=9)).date().isoformat()


def exaone_settings(s) -> dict | None:
    cfg = get_setting(s, "ai")
    if cfg.get("enabled") and cfg.get("external_transfer_consent") and cfg.get("provider") == "exaone":
        return cfg
    return None


def calls_today(s, now: datetime) -> int:
    row = get_setting(s, "exaone_calls")
    return int(row.get("count") or 0) if row.get("day") == kst_day(now) else 0


def take_call(s, cap: int, now: datetime) -> bool:
    used = calls_today(s, now)
    if used >= cap:
        return False
    put_setting(s, "exaone_calls", {"day": kst_day(now), "count": used + 1})
    return True


def call_exaone(app, system: str, user: str, model: type[BaseModel], *, feature: str | None = None,
                effort: str = "low", max_tokens: int = 2000):
    if app.config.demo:
        raise AIFailure("demo", "데모에서는 외부 AI를 호출하지 않습니다")
    with app.ai_control_lock, app.db.session() as s:
        cfg = exaone_settings(s)
        if cfg is None or (feature is not None and not cfg.get(feature)):
            raise AIFailure("disabled", "EXAONE 기능이 꺼져 있습니다")
        try:
            key = app.credential_store.get_key(app.config.data_dir, "exaone")
        except CredentialError as exc:
            raise AIFailure(exc.code, "AI 비밀 저장소를 확인할 수 없습니다") from None
        if not key:
            raise AIFailure("not_configured", "EXAONE API 키가 설정되지 않았습니다")
        if not take_call(s, int(cfg.get("daily_call_cap") or 300), app.clock()):
            raise AIFailure("call_cap_reached", "오늘 EXAONE 호출 상한에 도달했습니다")
    return app.exaone.complete(key, system, user, model, effort=effort, max_tokens=max_tokens)


def default_steps() -> list[Callable]:
    """Priority order: application text > chat reply > quote > recheck. Later tasks append here."""
    from .recheck import recheck_step
    return [recheck_step]


class AssistWorker(threading.Thread):
    POLL_SECONDS = 20.0

    def __init__(self, app):
        super().__init__(name="ai-assist", daemon=True)
        self.app = app
        self._halt, self._wake = threading.Event(), threading.Event()
        self.steps: list[Callable] = []

    def stop(self) -> None:
        self._halt.set()
        self._wake.set()

    def wake(self) -> None:
        self._wake.set()

    def wait_seconds(self) -> float:
        return self.POLL_SECONDS

    def run_once(self) -> None:
        for step in self.steps:
            if self._halt.is_set():
                return
            try:
                with self.app.db.gate.operation():
                    step(self.app)
            except MaintenanceBusy:
                return
            except Exception:  # noqa: BLE001 - one feature must not stop the others
                log.exception("ai assist step failed: %s", getattr(step, "__name__", step))

    def run(self) -> None:
        while not self._halt.is_set():
            self.run_once()
            self._wake.wait(self.wait_seconds())
            self._wake.clear()
