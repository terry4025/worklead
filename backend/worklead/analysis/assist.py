"""EXAONE assist helpers. Task 6 adds the gate, masking and the worker to this module."""
from __future__ import annotations

from datetime import datetime, timedelta

from ..services.settings import get_setting


def kst_day(now: datetime) -> str:
    return (now + timedelta(hours=9)).date().isoformat()


def calls_today(s, now: datetime) -> int:
    row = get_setting(s, "exaone_calls")
    return int(row.get("count") or 0) if row.get("day") == kst_day(now) else 0
