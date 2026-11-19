"""Telegram push for new lead alerts, so a phone hears about a lead within a minute.

The bot token lives in Windows Credential Manager (provider "telegram"); only the
chat id, enabled flag and connection time are stored in app_settings. Each
notification row is claimed (committed as attempted) before the network call and
is never resent, matching the native queue. Network errors are reported as fixed
codes: the token is part of the API URL, so no exception text is logged.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from sqlalchemy import func, or_, select, text

from ..context import AppContext
from ..engine.events import NOTIFICATION_TOGGLES, _in_quiet_hours
from ..models import EventOutbox, Lead, SourceRecord
from .native_notifications import MAX_AGE, NotificationError, _review_query
from .settings import get_setting, put_setting, ttl_hours

log = logging.getLogger("worklead.telegram")
PROVIDER = "telegram"
API = "https://api.telegram.org"
KINDS = ("new_lead", "new_review_lead", "cafe_request", "daangn_message")
BATCH = 10
PAY_UNITS = {"project": "건당", "hourly": "시급", "daily": "일급", "monthly": "월급", "yearly": "연봉"}

# (method, token, payload) -> Telegram JSON response. Replaced in tests.
Call = Callable[[str, str, dict[str, Any]], dict[str, Any]]


class TelegramStatus(BaseModel):
    key_supported: bool
    key_configured: bool | None
    chat_connected: bool
    enabled: bool
    error_code: str | None = None


class TelegramTokenIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: SecretStr = Field(json_schema_extra={"writeOnly": True})

    @field_validator("token", mode="before")
    @classmethod
    def validate_secret(cls, value: Any) -> Any:
        # Fixed text; the submitted token is never reflected.
        if not isinstance(value, str) or not value or len(value) > 200 or any(not 33 <= ord(c) <= 126 for c in value):
            raise ValueError("invalid credential")
        return value


def _http_call(method: str, token: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        response = httpx.post(f"{API}/bot{token}/{method}", json=payload, timeout=10)
    except httpx.HTTPError:
        raise NotificationError(502, "telegram_unreachable", "텔레그램 서버에 연결하지 못했습니다") from None
    if response.status_code == 401:
        raise NotificationError(422, "telegram_token_rejected", "텔레그램이 봇 토큰을 거부했습니다. 토큰을 다시 확인하세요")
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code != 200 or not body.get("ok"):
        raise NotificationError(502, "telegram_rejected", "텔레그램이 요청을 처리하지 않았습니다")
    return body


def _config(s) -> dict[str, Any]:
    return get_setting(s, "telegram")


def status(app: AppContext) -> TelegramStatus:
    with app.db.session() as s:
        cfg = _config(s)
    if app.config.demo:
        return TelegramStatus(key_supported=False, key_configured=None, chat_connected=False, enabled=False, error_code="demo_credential_unsupported")
    key = app.credential_store.status(app.config.data_dir, PROVIDER)
    return TelegramStatus(key_supported=key.supported, key_configured=key.configured,
                          chat_connected=bool(cfg.get("chat_id")), enabled=bool(cfg.get("enabled")) and bool(key.configured),
                          error_code=key.error_code)


def _demo_guard(app: AppContext) -> None:
    if app.config.demo:
        raise NotificationError(409, "demo_credential_unsupported", "데모에서는 텔레그램 토큰을 저장하지 않습니다")


def save_token(app: AppContext, token: str) -> TelegramStatus:
    _demo_guard(app)
    # Disable first so a new token never delivers to the previous chat.
    with app.db.session() as s:
        put_setting(s, "telegram", {"enabled": False, "chat_id": None, "connected_at": None})
    app.credential_store.set_key(app.config.data_dir, PROVIDER, token)
    return status(app)


def delete(app: AppContext) -> TelegramStatus:
    _demo_guard(app)
    with app.db.session() as s:
        put_setting(s, "telegram", {"enabled": False, "chat_id": None, "connected_at": None})
    app.credential_store.delete_key(app.config.data_dir, PROVIDER)
    return status(app)


def connect(app: AppContext, call: Call = _http_call) -> TelegramStatus:
    """Find the private chat that last messaged the bot, send a test, enable."""
    _demo_guard(app)
    token = app.credential_store.get_key(app.config.data_dir, PROVIDER)
    if not token:
        raise NotificationError(409, "telegram_token_missing", "먼저 봇 토큰을 저장하세요")
    updates = call("getUpdates", token, {"allowed_updates": ["message"]}).get("result") or []
    chats = [u["message"]["chat"]["id"] for u in updates
             if isinstance(u.get("message"), dict) and (u["message"].get("chat") or {}).get("type") == "private"]
    if not chats:
        raise NotificationError(409, "telegram_no_chat", "휴대폰 텔레그램에서 봇에게 아무 메시지나 보낸 뒤 다시 연결하세요")
    chat_id = str(chats[-1])
    call("sendMessage", token, {"chat_id": chat_id, "text": "Worklead 알림이 연결되었습니다. 새 제작 의뢰를 이 채팅으로 보냅니다."})
    with app.db.session() as s:
        put_setting(s, "telegram", {"enabled": True, "chat_id": chat_id, "connected_at": app.clock().isoformat()})
    return status(app)


def _message(title: str, lead: Lead | None, url: str | None) -> str:
    lines = [title]
    if lead is not None and lead.pay_min:
        amount = f"{lead.pay_min:,}원" + (f"~{lead.pay_max:,}원" if lead.pay_max and lead.pay_max != lead.pay_min else "")
        lines.append(f"보수: {PAY_UNITS.get(lead.pay_unit, '')} {amount}".replace("  ", " "))
    if url:
        lines.append(url)
    return "\n".join(lines)


def deliver_pending(app: AppContext, call: Call = _http_call) -> int:
    """Send unsent new-lead alerts created after the chat was connected."""
    if app.config.demo:
        return 0
    with app.db.session() as s:
        s.execute(text("BEGIN IMMEDIATE"))
        cfg = _config(s)
        if not cfg.get("enabled") or not cfg.get("chat_id") or not cfg.get("connected_at"):
            return 0
        now = app.clock()
        settings = get_setting(s, "notifications")
        if _in_quiet_hours(settings.get("quiet_hours", {}), now):
            return 0
        kinds = [k for k in KINDS if settings.get(NOTIFICATION_TOGGLES[k], True)]
        if not kinds:
            return 0
        kind = EventOutbox.payload["kind"].as_string()
        eligible_review = _review_query(now, ttl_hours(s)).where(Lead.id == EventOutbox.payload["lead_id"].as_string()).exists()
        since = max(datetime.fromisoformat(cfg["connected_at"]), now - MAX_AGE)
        rows = list(s.scalars(select(EventOutbox).where(
            EventOutbox.type == "notification.created", EventOutbox.notify.is_(True),
            EventOutbox.created_at >= since, kind.in_(kinds),
            or_(kind != "new_review_lead", eligible_review),
            func.json_type(EventOutbox.payload, "$.telegram_delivery").is_(None),
        ).order_by(EventOutbox.created_at, EventOutbox.seq).limit(BATCH)))
        outgoing = []
        for row in rows:
            lead = s.get(Lead, row.payload.get("lead_id")) if row.payload.get("lead_id") else None
            rec = s.get(SourceRecord, lead.primary_record_id) if lead is not None and lead.primary_record_id else None
            outgoing.append((row.id, _message(str(row.payload.get("title") or "Worklead 알림"), lead, rec.original_url if rec else None)))
            row.payload = {**row.payload, "telegram_delivery": {"state": "attempted", "at": now.isoformat()}}
        chat_id = cfg["chat_id"]
    if not outgoing:
        return 0
    token = app.credential_store.get_key(app.config.data_dir, PROVIDER)
    sent = 0
    for id_, message in outgoing:
        state = "failed"
        if token:
            try:
                call("sendMessage", token, {"chat_id": chat_id, "text": message, "disable_web_page_preview": True})
                state, sent = "accepted", sent + 1
            except NotificationError as exc:
                log.warning("telegram delivery failed code=%s", exc.code)
        with app.db.session() as s:
            row = s.scalar(select(EventOutbox).where(EventOutbox.id == id_))
            if row is not None:
                row.payload = {**row.payload, "telegram_delivery": {**row.payload.get("telegram_delivery", {}), "state": state, "result_at": app.clock().isoformat()}}
    return sent
