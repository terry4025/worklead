"""Private native delivery queue. Claim commits before the OS display attempt.

An attempted delivery is never automatically requeued: an interrupted OS call
can have displayed a notification even when no acknowledgement reaches us.
GET eligibility is computed without changing outbox rows.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.orm import Session

from ..context import AppContext
from ..engine.events import NOTIFICATION_TOGGLES, _in_quiet_hours, emit, quiet_delivery
from ..models import EventOutbox, Lead, SourceRecord
from .settings import get_setting, ttl_hours

MAX_AGE = timedelta(hours=24)
TOGGLES = NOTIFICATION_TOGGLES
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


def _review_eligibility(now: datetime, ttl: int):
    """Recheck current facts before a postponed review alert can be delivered."""
    return (
        Lead.recommendation.in_(("needs_review", "recommended")),
        Lead.demand_intent.in_(("buyer_project", "buyer_ongoing")),
        Lead.engagement_type == "project",
        or_(Lead.user_mark.is_(None), Lead.user_mark != "dismissed"),
        Lead.sales_stage.in_(("new", "reviewing")),
        Lead.source_status == "open", Lead.access_status == "accessible",
        SourceRecord.source_status == "open", SourceRecord.access_status == "accessible",
        SourceRecord.published_precision == "exact",
        SourceRecord.published_at >= now - MAX_AGE, SourceRecord.published_at <= now,
        SourceRecord.last_checked_at >= now - timedelta(hours=ttl),
    )


def _review_query(now: datetime, ttl: int):
    return select(Lead.id).join(SourceRecord, SourceRecord.id == Lead.primary_record_id).where(*_review_eligibility(now, ttl))


def _suppression(row: EventOutbox, settings: dict[str, Any], now: datetime) -> str | None:
    deferred = row.suppressed_reason == "quiet_hours" and isinstance(_delivery(row).get("deferred_until"), str)
    if row.suppressed_reason and not deferred:
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
        eligible_review = _review_query(now, ttl_hours(s)).where(Lead.id == EventOutbox.payload["lead_id"].as_string()).exists()
        filters = [
            EventOutbox.type == "notification.created",
            EventOutbox.notify.is_(True),
            EventOutbox.notified_at.is_(None),
            EventOutbox.created_at > now - MAX_AGE,
            or_(EventOutbox.suppressed_reason.is_(None), and_(
                EventOutbox.suppressed_reason == "quiet_hours",
                func.json_type(EventOutbox.payload, "$.native_delivery.deferred_until") == "text",
            )),
            kind.in_(kinds),
            or_(kind != "new_review_lead", eligible_review),
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


def release_quiet_summary(app: AppContext) -> int:
    """Emit one app-only morning summary per enabled new-lead kind.

    The original native queue remains pending. A marker in each original row
    and the summary are committed together, so restarts and concurrent
    housekeeping cannot repeat the app summary. This does not enable OS
    notifications or retry an OS attempt.
    """
    released = 0
    with app.db.session() as s:
        s.execute(text("BEGIN IMMEDIATE"))
        now = app.clock()
        settings = get_setting(s, "notifications")
        if _in_quiet_hours(settings.get("quiet_hours", {}), now):
            return 0
        kinds = [kind for kind in ("new_lead", "new_review_lead") if settings.get(TOGGLES[kind], True)]
        if not kinds:
            return 0
        kind = EventOutbox.payload["kind"].as_string()
        eligible = _review_query(now, ttl_hours(s)).where(Lead.id == EventOutbox.payload["lead_id"].as_string()).exists()
        rows = list(s.scalars(select(EventOutbox).where(
            EventOutbox.type == "notification.created", EventOutbox.notify.is_(True),
            EventOutbox.notified_at.is_(None), EventOutbox.created_at > now - MAX_AGE,
            EventOutbox.suppressed_reason == "quiet_hours", kind.in_(kinds), eligible,
            EventOutbox.payload["native_delivery"]["state"].as_string() == "pending",
            func.json_type(EventOutbox.payload, "$.native_delivery.deferred_until") == "text",
            func.json_type(EventOutbox.payload, "$.native_delivery.app_summary_released").is_(None),
        ).order_by(EventOutbox.created_at, EventOutbox.seq)))
        for current_kind in kinds:
            group = [row for row in rows if _kind(row) == current_kind]
            if not group:
                continue
            digest = hashlib.sha256("\n".join(sorted(row.id for row in group)).encode()).hexdigest()
            title = f"밤사이 새 제작 의뢰 {len(group)}건 · 조건을 확인하세요"
            summary = emit(s, "notification.created", {
                "kind": current_kind, "title": title, "lead_id": None, "source_id": None,
            }, dedupe_key=f"notify:quiet_summary:{current_kind}:{digest}", notify=False)
            # The dedupe key protects recovery even if an older version wrote
            # the event without its markers. Do not re-emit that summary.
            summary_id = summary.id if summary is not None else s.scalar(select(EventOutbox.id).where(
                EventOutbox.dedupe_key == f"notify:quiet_summary:{current_kind}:{digest}"))
            if summary is not None:
                summary.created_at = now
            for row in group:
                row.payload = {**row.payload, "native_delivery": {
                    **_delivery(row), "app_summary_released": summary_id,
                }}
            released += len(group)
    if released:
        app.hub.notify()
    return released


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
        if reason is None and kind == "new_review_lead":
            if s.scalar(_review_query(now, ttl_hours(s)).where(Lead.id == row.payload.get("lead_id"))) is None:
                reason = "lead_not_eligible"
        if reason == "quiet_hours" and (row.suppressed_reason is None or isinstance(_delivery(row).get("deferred_until"), str)):
            row.payload = {**row.payload, "native_delivery": {**_delivery(row), **quiet_delivery(get_setting(s, "notifications")["quiet_hours"], now)}}
            row.suppressed_reason = "quiet_hours"
            return {"allowed": False, "kind": kind, "state": "pending", "claim_token": None, "reason": reason}
        if reason:
            row.payload = {**(row.payload if isinstance(row.payload, dict) else {}), "native_delivery": {"state": "suppressed", "reason": reason, "result_at": now.isoformat()}}
            row.suppressed_reason = reason
            return {"allowed": False, "kind": kind, "state": "suppressed", "claim_token": None, "reason": reason}
        token = secrets.token_hex(16)
        row.suppressed_reason = None
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
