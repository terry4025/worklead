from worklead.engine.power import ES_CONTINUOUS, ES_SYSTEM_REQUIRED, SleepGuard, wanted
from worklead.models import Source
from worklead.services.settings import put_setting


def test_guard_sets_and_releases_once():
    calls = []
    guard = SleepGuard(lambda flags: calls.append(flags) or True)
    guard.update(True); guard.update(True); guard.update(False)
    assert calls == [ES_CONTINUOUS | ES_SYSTEM_REQUIRED, ES_CONTINUOUS]


def test_failed_call_keeps_previous_state():
    guard = SleepGuard(lambda flags: False)
    guard.update(True)
    assert guard.held is False


def test_wanted_needs_opt_in_and_auto_collect(ctx):
    with ctx.db.session() as s:
        s.get(Source, "daangn-alba").auto_collect_enabled = True
    assert wanted(ctx) is False
    with ctx.db.session() as s:
        put_setting(s, "collection", {"prevent_sleep": True})
    assert wanted(ctx) is True


def test_settings_api_round_trip(client):
    assert client.get("/v1/settings").json()["collection"] == {"prevent_sleep": False}
    body = client.patch("/v1/settings", json={"collection": {"prevent_sleep": True}}).json()
    assert body["collection"]["prevent_sleep"] is True
