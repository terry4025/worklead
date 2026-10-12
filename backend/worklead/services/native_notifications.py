"""Private native delivery queue. Claim commits before the OS display attempt.

An attempted delivery is never automatically requeued: an interrupted OS call
can have displayed a notification even when no acknowledgement reaches us.
GET eligibility is computed without changing outbox rows.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.orm import Session

from ..context import AppContext
from ..engine.events import _in_quiet_hours
from ..models import EventOutbox
from .settings import get_setting

MAX_AGE = timedelta(hours=24)
TOGGLES = {"new_lead": "new_recommended", "lead_changed": "meaningful_change", "source_issue": "source_issue", "cafe_request": "new_recommended"}
TERMINAL = {"accepted", "failed", "unknown", "suppressed"}


class NotificationError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def _delivery(row: EventOutbox) -> dict[str, Any]:
    payload = row.payload if isinstance(row.payload, dict) else {}
    if "native_delivery" not in payload:
        return {"state": "pending"}
    delivery = payload["native_delivery"]
    return delivery if isinstance(delivery, dict) else {"state": "unknown"}


def _kind(row: EventOutbox) -> str | None:
    kind = row.payload.get("kind") if isinstance(row.payload, dict) else None
    return kind if isinstance(kind, str) and kind in TOGGLES else None


def _state(row: EventOutbox) -> str:
    state = _delivery(row).get("state")
    return state if isinstance(state, str) and state in {"pending", "attempted", *TERMINAL} else "unknown"


def _suppression(row: EventOutbox, settings: dict[str, Any], now: datetime) -> str | None:
    if row.suppressed_reason:
        return row.suppressed_reason if row.suppressed_reason in {"quiet_hours", "expired", "type_disabled", "not_native", "unsupported_kind"} else "creation_suppressed"
    if not row.notify:
        return "not_native"
    if _kind(row) is None:
        return "unsupported_kind"
    if row.created_at <= now - MAX_AGE:
        return "expired"
    if not settings.get(TOGGLES[_kind(row)], True):
        return "type_disabled"
    if _in_quiet_hours(settings.get("quiet_hours", {}), now):
        return "quiet_hours"
    return None


def list_pending(app: AppContext, *, limit: int = 20, created_after: datetime | None = None) -> dict[str, Any]:
    with app.db.session() as s:
        now = app.clock()
        settings = get_setting(s, "notifications")
        if _in_quiet_hours(settings.get("quiet_hours", {}), now):
            return {"items": [], "backlog_count": 0}
        kinds = [kind for kind, toggle in TOGGLES.items() if settings.get(toggle, True)]
        if not kinds:
            return {"items": [], "backlog_count": 0}
        kind = EventOutbox.payload["kind"].as_string()
        state = EventOutbox.payload["native_delivery"]["state"].as_string()
        filters = [
            EventOutbox.type == "notification.created",
            EventOutbox.notify.is_(True),
            EventOutbox.notified_at.is_(None),
            EventOutbox.created_at > now - MAX_AGE,
            EventOutbox.suppressed_reason.is_(None),
            kind.in_(kinds),
            or_(
                func.json_type(EventOutbox.payload, "$.native_delivery").is_(None),
                and_(func.json_type(EventOutbox.payload, "$.native_delivery") == "object", state == "pending"),
            ),
        ]
        if created_after is not None:
            filters.append(EventOutbox.created_at >= created_after)
        backlog = s.scalar(select(func.count()).select_from(EventOutbox).where(*filters)) or 0
        rows = s.execute(select(EventOutbox.id, kind, EventOutbox.created_at).where(*filters).order_by(EventOutbox.created_at, EventOutbox.seq).limit(limit))
        return {"items": [{"id": id_, "kind": kind_, "created_at": at} for id_, kind_, at in rows], "backlog_count": backlog}


def _row(s: Session, id_: str) -> EventOutbox:
    row = s.scalar(select(EventOutbox).where(EventOutbox.id == id_, EventOutbox.type == "notification.created"))
    if row is None:
        raise NotificationError(404, "notification_not_found", "알림을 찾을 수 없습니다")
    return row


def claim(app: AppContext, id_: str) -> dict[str, Any]:
    # Reserve the SQLite writer before reading settings or delivery state. This
    # also prevents concurrent claims from both reading an unattempted row.
    with app.db.session() as s:
        s.execute(text("BEGIN IMMEDIATE"))
        row = _row(s, id_)
        kind, state = _kind(row), _state(row)
        if row.notified_at is not None or state != "pending":
            return {"allowed": False, "kind": kind, "state": "accepted" if row.notified_at else state, "claim_token": None, "reason": "already_attempted"}
        now = app.clock()
        reason = _suppression(row, get_setting(s, "notifications"), now)
        if reason:
            row.payload = {**(row.payload if isinstance(row.payload, dict) else {}), "native_delivery": {"state": "suppressed", "reason": reason, "result_at": now.isoformat()}}
            row.suppressed_reason = reason
            return {"allowed": False, "kind": kind, "state": "suppressed", "claim_token": None, "reason": reason}
        token = secrets.token_hex(16)
        row.payload = {**row.payload, "native_delivery": {"state": "attempted", "claim_token": token, "attempted_at": now.isoformat()}}
        result = {"allowed": True, "kind": kind, "state": "attempted", "claim_token": token, "reason": None}
    # The session commits before allowed:true can reach the OS consumer.
    return result


def record_result(app: AppContext, id_: str, *, claim_token: str, state: str) -> dict[str, Any]:
    with app.db.session() as s:
        s.execute(text("BEGIN IMMEDIATE"))
        row = _row(s, id_)
        delivery = _delivery(row)
        token = delivery.get("claim_token")
        if not isinstance(token, str) or not secrets.compare_digest(token, claim_token):
            raise NotificationError(409, "notification_claim_mismatch", "복원 또는 다른 처리로 알림 승인 상태가 바뀌었습니다")
        current = _state(row)
        if current == state:
            return {"id": row.id, "kind": _kind(row), "state": current}
        if current != "attempted":
            raise NotificationError(409, "notification_result_conflict", "이미 기록한 알림 결과는 변경할 수 없습니다")
        now = app.clock()
        row.payload = {**row.payload, "native_delivery": {**delivery, "state": state, "result_at": now.isoformat()}}
        if state == "accepted":
            row.notified_at = now
        result = {"id": row.id, "kind": _kind(row), "state": state}
    return result
