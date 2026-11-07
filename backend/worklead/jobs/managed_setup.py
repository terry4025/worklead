"""Explicit local enrollment and restart recovery for the app-owned browser."""
from __future__ import annotations

from ..models import AppSetting
from .applications import ApplicationError
from .auto_apply import runtime as auto_runtime
from .auto_apply_models import AutoApplyStartIn
from .auto_pairing import runtime as pairing_runtime

SETTING = "job_auto_managed"


def configuration(app):
    with app.db.session() as session:
        row = session.get(AppSetting, SETTING)
        return dict(row.value) if row and isinstance(row.value, dict) else None


def _save(app, enabled):
    with app.db.session() as session:
        row = session.get(AppSetting, SETTING)
        value = {"enabled": bool(enabled), "provider": "managed_edge", "version": 1}
        if row is None:
            session.add(AppSetting(key=SETTING, value=value, updated_at=app.clock()))
        else:
            row.value, row.updated_at = value, app.clock()


def current_status(app):
    result = auto_runtime(app).status()
    config = configuration(app)
    if config is None:
        return result
    result.provider = "managed_edge"
    managed = app._jobs_managed_auto_runtime
    detail = managed.status() if managed else {}
    result.setup_stage = detail.get("stage", "stopped" if not result.enabled else "starting")
    result.browser_open = detail.get("browser_open", False)
    if result.enabled and result.setup_stage != "ready":
        result.message = detail.get("message", "자동 지원 전용 브라우저를 준비하고 있습니다")
        result.error_code = detail.get("error_code") or result.error_code
        result.needs_user_action = detail.get("needs_user_action", False)
        result.queue_reason = result.setup_stage
    elif result.enabled and result.setup_stage == "ready":
        result.message = "전용 브라우저가 자동 연결되었습니다. 기준에 맞는 공고를 자동 처리합니다"
    return result


def setup(app, body):
    if app.config.demo:
        raise ApplicationError(409, "demo_only", "데모에서는 자동 지원을 등록하지 않습니다")
    from .managed_auto import runtime as managed_runtime
    # Invalidate outstanding authorities before joining the old browser thread.
    auto_runtime(app).shutdown(retry_reason="cancelled")
    if app._jobs_managed_auto_runtime is not None:
        app._jobs_managed_auto_runtime.shutdown()
    pairing_runtime(app).start(AutoApplyStartIn(confirm=True, remember_connection=True,
        min_score=body.min_score, daily_limit=body.daily_limit), managed=True)
    _save(app, True)
    managed_runtime(app).start()
    return current_status(app)


def disable(app):
    """User stop persists; process shutdown must never call this function."""
    if configuration(app) is not None:
        _save(app, False)
    if app._jobs_managed_auto_runtime is not None:
        app._jobs_managed_auto_runtime.shutdown(user_stop=True)


def use_chrome(app):
    """Switch explicitly to the optional extension transport."""
    auto_runtime(app).shutdown(retry_reason="cancelled")
    if app._jobs_managed_auto_runtime is not None:
        app._jobs_managed_auto_runtime.shutdown()
    with app.db.session() as session:
        row = session.get(AppSetting, SETTING)
        if row:
            session.delete(row)


def boot(app):
    if app.config.demo:
        return False
    from .managed_auto import boot as boot_managed
    pair = pairing_runtime(app)
    try:
        config = configuration(app)
        with pair._lock:
            grant = pair._read()
            migrate = (config is None and grant and grant["desired"] and not grant["paused"]
                and not grant["account"])
        # Recover an already enabled but never connected installation. Never opt
        # in a fresh installation, a user stop, or an explicitly paused account.
        if migrate:
            _save(app, True)
            config = configuration(app)
        if config and config.get("enabled") is not True:
            return False
        managed = bool(config and config.get("enabled") is True)
        started = pair.boot(managed=managed)
        if started and managed:
            boot_managed(app)
        return started
    except ApplicationError as exc:
        auto = auto_runtime(app)
        with auto._lock:
            auto._status.error_code, auto._status.message = exc.code, exc.message
        return False
