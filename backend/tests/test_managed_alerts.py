from datetime import timedelta

from sqlalchemy import select

from worklead.jobs.managed_alerts import OUTAGE_DELAY, update_alert
from worklead.models import AppSetting, EventOutbox
from worklead.services.settings import get_setting, put_setting


def messages(ctx):
    with ctx.db.session() as session:
        return [row.payload["title"] for row in session.scalars(select(EventOutbox).where(
            EventOutbox.type == "notification.created").order_by(EventOutbox.seq))]


def test_login_outage_and_recovery_are_once_across_runtime_recreation(ctx):
    status = {"stage": "login_required", "needs_user_action": True, "error_code": "login_required"}
    for _ in range(3):
        update_alert(ctx, status)
    assert messages(ctx) == ["당근 자동 지원에 로그인이 필요합니다"]
    update_alert(ctx, {"stage": "ready"})
    update_alert(ctx, {"stage": "ready"})
    assert messages(ctx)[1:] == ["당근 자동 지원 연결이 복구되었습니다"]


def test_short_transient_outage_stays_quiet_but_long_outage_is_reported(ctx):
    now = ctx.clock()
    ctx.clock = lambda: now
    recovering = {"stage": "starting", "error_code": "browser_reconnecting", "needs_user_action": False}
    update_alert(ctx, recovering)
    ctx.clock = lambda: now + OUTAGE_DELAY - timedelta(seconds=1)
    update_alert(ctx, recovering)
    assert messages(ctx) == []
    ctx.clock = lambda: now + OUTAGE_DELAY
    update_alert(ctx, recovering)
    update_alert(ctx, recovering)
    assert len(messages(ctx)) == 1
    update_alert(ctx, {"stage": "ready"})
    assert len(messages(ctx)) == 2


def test_explicit_stop_does_not_claim_recovery(ctx):
    update_alert(ctx, {"stage": "blocked", "needs_user_action": True, "error_code": "site_blocked"})
    update_alert(ctx, {"stage": "stopped", "explicit_stop": True})
    update_alert(ctx, {"stage": "ready"})
    assert len(messages(ctx)) == 1


def test_normal_process_shutdown_preserves_outage_and_restart_does_not_repeat(ctx):
    status = {"stage": "login_required", "needs_user_action": True, "error_code": "login_required"}
    update_alert(ctx, status)
    update_alert(ctx, {"stage": "stopped"})
    update_alert(ctx, {"stage": "starting", "error_code": None})
    update_alert(ctx, status)
    assert len(messages(ctx)) == 1
    update_alert(ctx, {"stage": "ready"})
    assert len(messages(ctx)) == 2


def test_disabled_notifications_never_get_issue_or_recovery(ctx):
    with ctx.db.session() as session:
        settings = dict(get_setting(session, "notifications"))
        put_setting(session, "notifications", {**settings, "source_issue": False})
    update_alert(ctx, {"stage": "login_required", "needs_user_action": True})
    update_alert(ctx, {"stage": "ready"})
    assert messages(ctx) == []


def test_quiet_hours_are_preserved_without_enabling_native_delivery(ctx):
    with ctx.db.session() as session:
        settings = dict(get_setting(session, "notifications"))
        put_setting(session, "notifications", {**settings, "quiet_hours": {
            "enabled": True, "start": "00:00", "end": "23:59"}})
    update_alert(ctx, {"stage": "login_required", "needs_user_action": True})
    with ctx.db.session() as session:
        row = session.scalar(select(EventOutbox).where(EventOutbox.type == "notification.created"))
        assert row.suppressed_reason == "quiet_hours"
        assert row.payload["native_delivery"]["state"] == "pending"
        assert row.notified_at is None


def test_bad_saved_timestamp_recovers_without_private_payload(ctx):
    with ctx.db.session() as session:
        session.add(AppSetting(key="job_auto_managed_health", value={"category": "recovering", "since": "broken"}))
    update_alert(ctx, {"stage": "starting", "error_code": "browser_reconnecting"})
    assert messages(ctx) == []
    with ctx.db.session() as session:
        saved = session.get(AppSetting, "job_auto_managed_health").value
        assert set(saved) == {"category", "since", "alerted"}
