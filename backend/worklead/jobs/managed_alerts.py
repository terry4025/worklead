"""Durable, private outage transitions for unattended application monitoring."""
from datetime import datetime, timedelta

from ..engine.events import notify
from ..models import AppSetting

SETTING = "job_auto_managed_health"
OUTAGE_DELAY = timedelta(minutes=15)


def update_alert(app, status):
    """Report an outage once and its recovery once, respecting alert settings.

    Called outside the runner's status lock. No account, page or exception text
    enters the notification. Persisting the transition prevents restart spam.
    """
    stage = status.get("stage")
    code = status.get("error_code")
    if stage == "ready":
        category = "ready"
    elif stage == "stopped" and status.get("explicit_stop"):
        category = "stopped"
    elif status.get("needs_user_action"):
        category = "login_required" if stage == "login_required" else "action_required"
    elif code in {"browser_reconnecting", "browser_timeout", "identity_not_ready", "navigation_failed"}:
        category = "recovering"
    else:
        return
    now = app.clock()
    changed = False
    with app.db.gate.operation(), app.db.session() as session:
        row = session.get(AppSetting, SETTING)
        old = dict(row.value) if row and isinstance(row.value, dict) else {}
        if category in {"ready", "stopped"}:
            if not old.get("category"):
                return
            if category == "ready" and old.get("delivered"):
                notify(session, "source_issue", "당근 자동 지원 연결이 복구되었습니다",
                       dedupe_key=f"managed-health:recovered:{old['since']}", source_id="daangn-alba", now=now)
            value = {}
        else:
            # Browser restarts are one outage. Moving from an outage to a login
            # or identity intervention is a new, immediately actionable event.
            value = old if old.get("category") == category else {
                "category": category, "since": now.isoformat(), "alerted": False}
            try:
                since = datetime.fromisoformat(value["since"])
                due = now - since >= OUTAGE_DELAY
            except (ValueError, TypeError, KeyError):
                value = {"category": category, "since": now.isoformat(), "alerted": False}
                due = False
            if not value.get("alerted") and (category != "recovering" or due):
                title = {"login_required": "당근 자동 지원에 로그인이 필요합니다",
                         "action_required": "당근 자동 지원이 멈췄습니다. 연결 상태를 확인하세요",
                         "recovering": "당근 자동 지원 연결이 지연되어 자동 복구 중입니다"}[category]
                notification = notify(session, "source_issue", title,
                    dedupe_key=f"managed-health:{category}:{value['since']}", source_id="daangn-alba", now=now)
                # Remember evaluation even when this alert type is disabled.
                # Do not create a recovery message for a suppressed issue.
                value = {**value, "alerted": True, "delivered": notification is not None}
        if value != old:
            if row is None:
                session.add(AppSetting(key=SETTING, value=value))
            else:
                row.value = value
            changed = True
    if changed:
        app.hub.notify()
