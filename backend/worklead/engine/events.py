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
NOTIFICATION_TOGGLES = {
    "new_lead": "new_recommended", "new_review_lead": "new_review",
    "lead_changed": "meaningful_change", "source_issue": "source_issue", "cafe_request": "new_recommended",
    "daangn_message": "daangn_messages",
}


class EventHub:
    """SSE 스트림을 깨우는 조건 변수. 커밋 후 notify() 를 호출한다 (놓쳐도 폴링으로 복구)."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._version = 0

    def notify(self) -> None:
        with self._cond:
            self._version += 1
            self._cond.notify_all()

    def current_version(self) -> int:
        """Snapshot notifications without blocking a browser's event pump."""
        with self._cond:
            return self._version

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


def quiet_delivery(cfg: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Persist an unattempted delivery through quiet hours, including restarts."""
    local = now.astimezone(KST)
    end = time.fromisoformat(cfg["end"])
    until = datetime.combine(local.date(), end, KST)
    if until <= local:
        until += timedelta(days=1)
    return {"state": "pending", "deferred_until": until.astimezone(timezone.utc).isoformat()}


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
    """설정에서 끈 종류는 기록하지 않고, 조용한 시간에는 다음 허용 시간까지 보류한다."""
    cfg = get_setting(s, "notifications")
    toggle = NOTIFICATION_TOGGLES[kind]
    if not cfg.get(toggle, True):
        return None
    at = now or utcnow()
    quiet = cfg.get("quiet_hours", {})
    suppressed = "quiet_hours" if _in_quiet_hours(quiet, at) else None
    payload = {"kind": kind, "title": title, "lead_id": lead_id, "source_id": source_id}
    if suppressed:
        payload["native_delivery"] = quiet_delivery(quiet, at)
    row = emit(
        s,
        "notification.created",
        payload,
        dedupe_key=dedupe_key,
        notify=True,
        suppressed_reason=suppressed,
    )
    if row is not None:
        row.created_at = at
    return row


def trim(s: Session) -> None:
    max_seq = s.scalar(select(func.max(EventOutbox.seq))) or 0
    cutoff = max_seq - RETAIN_EVENTS
    if cutoff > 0 and cutoff > int(get_setting(s, "events_trimmed_through")):
        # dedupe_key 가 있는 행은 중복 방지 기록이라 남긴다
        s.execute(delete(EventOutbox).where(EventOutbox.seq <= cutoff, EventOutbox.dedupe_key.is_(None)))
        put_setting(s, "events_trimmed_through", cutoff)
