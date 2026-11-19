"""Opt-in: keep Windows awake while collection runs. The display may still turn off."""
from __future__ import annotations

import sys
from collections.abc import Callable

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


def _windows_set(flags: int) -> bool:
    if sys.platform != "win32":
        return False
    import ctypes
    return bool(ctypes.windll.kernel32.SetThreadExecutionState(flags))


class SleepGuard:
    """The execution state belongs to the calling thread: update it from one long-lived thread."""

    def __init__(self, set_state: Callable[[int], bool] = _windows_set):
        self._set = set_state
        self.held = False

    def update(self, want: bool) -> None:
        if want == self.held:
            return
        if self._set(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if want else 0)):
            self.held = want


def wanted(app) -> bool:
    from sqlalchemy import select

    from ..models import Source
    from ..services.settings import get_setting
    with app.db.session() as s:
        if not get_setting(s, "collection").get("prevent_sleep"):
            return False
        return s.scalar(select(Source.id).where(Source.auto_collect_enabled.is_(True)).limit(1)) is not None
