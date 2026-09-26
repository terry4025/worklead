"""이벤트 outbox. DB 변경과 같은 트랜잭션에 기록하고, seq 로 이어받기를 지원한다.

같은 dedupe_key 는 한 번만 기록된다 (재수집·재시작 후에도 알림이 증식하지 않음).
"""

from __future__ import annotations

import threading
from datetime import datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..db import utcnow
from ..models import EventOutbox, new_id
from ..services.settings import get_setting, put_setting

KST = timezone(timedelta(hours=9))
RETAIN_EVENTS = 5000


class EventHub:
    """SSE 스트림을 깨우는 조건 변수. 커밋 후 notify() 를 호출한다 (놓쳐도 폴링으로 복구)."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._version = 0

    def notify(self) -> None:
        with self._cond:
            self._version += 1
            self._cond.notify_all()

    def wait(self, seen_version: int, timeout: float) -> int:
        with self._cond:
            if self._version == seen_version:
                self._cond.wait(timeout)
            return self._version


def emit(
    s: Session,
    type_: str,
    payload: dict[str, Any],
    dedupe_key: str | None = None,
    notify: bool = False,
    suppressed_reason: str | None = None,
) -> EventOutbox | None:
    if dedupe_key is not None:
        exists = s.scalar(select(EventOutbox.seq).where(EventOutbox.dedupe_key == dedupe_key))
        if exists is not None:
            return None
    row = EventOutbox(
        id=new_id("evt"),
        type=type_,
        payload=payload,
        dedupe_key=dedupe_key,
        notify=notify,
        suppressed_reason=suppressed_reason,
        created_at=utcnow(),
    )
    s.add(row)
    s.flush()
    return row


def _in_quiet_hours(cfg: dict[str, Any], now: datetime) -> bool:
    if not cfg.get("enabled"):
        return False
    try:
        start = time.fromisoformat(cfg["start"])
        end = time.fromisoformat(cfg["end"])
    except (KeyError, ValueError):
        return False
    local = now.astimezone(KST).time()
    return (start <= local or local < end) if start > end else (start <= local < end)


def notify(
    s: Session,
    kind: str,
    title: str,
    *,
    dedupe_key: str,
    lead_id: str | None = None,
    source_id: str | None = None,
    now: datetime | None = None,
) -> EventOutbox | None:
    """알림 이벤트. 설정에서 끈 종류는 기록하지 않고, 조용한 시간에는 억제 사유를 남긴다."""
    cfg = get_setting(s, "notifications")
    toggle = {"new_lead": "new_recommended", "lead_changed": "meaningful_change", "source_issue": "source_issue"}[kind]
    if not cfg.get(toggle, True):
        return None
    suppressed = "quiet_hours" if _in_quiet_hours(cfg.get("quiet_hours", {}), now or utcnow()) else None
    return emit(
        s,
        "notification.created",
        {"kind": kind, "title": title, "lead_id": lead_id, "source_id": source_id},
        dedupe_key=dedupe_key,
        notify=True,
        suppressed_reason=suppressed,
    )


def trim(s: Session) -> None:
    max_seq = s.scalar(select(func.max(EventOutbox.seq))) or 0
    cutoff = max_seq - RETAIN_EVENTS
    if cutoff > 0 and cutoff > int(get_setting(s, "events_trimmed_through")):
        # dedupe_key 가 있는 행은 중복 방지 기록이라 남긴다
        s.execute(delete(EventOutbox).where(EventOutbox.seq <= cutoff, EventOutbox.dedupe_key.is_(None)))
        put_setting(s, "events_trimmed_through", cutoff)
