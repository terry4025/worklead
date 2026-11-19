"""Telegram alerts: connect once, deliver each eligible alert once, never leak the token."""
from datetime import timedelta

import pytest

from worklead.services import telegram
from worklead.services.credentials import FakeCredentialStore
from worklead.services.native_notifications import NotificationError
from worklead.services.settings import put_setting

from .test_lead_freshness_notifications import NOW, alerts, post, save

TOKEN = "123456:ABC-test_token"


class FakeTelegram:
    def __init__(self, chats=(777,), fail=False):
        self.calls, self.chats, self.fail = [], list(chats), fail

    def __call__(self, method, token, payload):
        self.calls.append((method, token, payload))
        if self.fail:
            raise NotificationError(502, "telegram_unreachable", "x")
        if method == "getUpdates":
            return {"ok": True, "result": [{"message": {"chat": {"id": c, "type": "private"}}} for c in self.chats]}
        return {"ok": True, "result": {}}

    def sent(self):
        return [p["text"] for m, _, p in self.calls if m == "sendMessage"]


@pytest.fixture
def tg(ctx):
    ctx.credential_store = FakeCredentialStore()
    ctx.clock = lambda: NOW
    return ctx


def connect(ctx, fake=None):
    fake = fake or FakeTelegram()
    telegram.save_token(ctx, TOKEN)
    telegram.connect(ctx, call=fake)
    return fake


def test_connect_requires_a_chat_then_enables(tg):
    telegram.save_token(tg, TOKEN)
    with pytest.raises(NotificationError) as exc:
        telegram.connect(tg, call=FakeTelegram(chats=()))
    assert exc.value.code == "telegram_no_chat"
    assert not telegram.status(tg).enabled
    fake = FakeTelegram()
    status = telegram.connect(tg, call=fake)
    assert status.enabled and status.chat_connected
    assert fake.sent() and fake.calls[-1][2]["chat_id"] == "777"


def test_alert_after_connect_is_sent_once_with_link_and_pay(tg):
    fake = connect(tg)
    save(tg)
    assert telegram.deliver_pending(tg, call=fake) == 1
    text = fake.sent()[-1]
    assert "상세페이지" in text and "건당 150,000원" in text and "https://jobs.daangn.com/job-posts/freshness001" in text
    assert telegram.deliver_pending(tg, call=fake) == 0
    assert alerts(tg)[0].payload["telegram_delivery"]["state"] == "accepted"


def test_alerts_before_connect_and_during_quiet_hours_are_not_sent(tg):
    save(tg)
    tg.clock = lambda: NOW + timedelta(seconds=1)
    fake = connect(tg)
    assert telegram.deliver_pending(tg, call=fake) == 0  # created before connection
    save(tg, post(source_post_id="fresh002", original_url="https://jobs.daangn.com/job-posts/fresh002",
                  canonical_url="https://jobs.daangn.com/job-posts/fresh002", title="랜딩페이지 제작해주실 분"), now=NOW + timedelta(seconds=2))
    with tg.db.session() as s:
        put_setting(s, "notifications", {"quiet_hours": {"enabled": True, "start": "00:00", "end": "23:59"}})
    assert telegram.deliver_pending(tg, call=fake) == 0


def test_failed_send_is_recorded_and_not_retried(tg):
    connect(tg)
    save(tg)
    broken = FakeTelegram(fail=True)
    assert telegram.deliver_pending(tg, call=broken) == 0
    assert alerts(tg)[0].payload["telegram_delivery"]["state"] == "failed"
    assert telegram.deliver_pending(tg, call=FakeTelegram()) == 0


def test_new_token_disables_previous_chat(tg):
    connect(tg)
    status = telegram.save_token(tg, "999:other")
    assert not status.enabled and not status.chat_connected


def test_api_never_reflects_the_token(client, ctx):
    ctx.credential_store = FakeCredentialStore()
    bad = client.post("/v1/notifications/telegram/token", json={"token": "bad token with space", "x": TOKEN})
    assert bad.status_code == 422 and TOKEN not in bad.text and "bad token" not in bad.text
    ok = client.post("/v1/notifications/telegram/token", json={"token": TOKEN})
    assert ok.status_code == 200 and TOKEN not in ok.text and ok.json()["key_configured"] is True
    assert client.get("/v1/notifications/telegram").json()["enabled"] is False
