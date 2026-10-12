"""Real local API with synthetic OS-store seams; zero provider network calls."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select

from worklead.models import AppSetting
from worklead.services.credentials import FakeCredentialStore, CredentialStatus, CredentialError
from worklead.services.settings import get_setting, put_setting


SECRET = "synthetic-credential-private-marker-0123456789"


@pytest.fixture
def store(ctx):
    ctx.credential_store = FakeCredentialStore()
    return ctx.credential_store


def expected(client):
    state = client.get("/v1/ai/status")
    assert state.status_code == 200, state.text
    data = state.json()
    return {"expected_revision": data["settings_revision"], "expected_generation": data["database_generation"]}


def test_key_save_delete_disable_and_keep_secret_out_of_db_and_responses(client, ctx, store):
    pre = expected(client)
    response = client.post("/v1/ai/credentials", json={"key": SECRET, **pre})
    assert response.status_code == 200, response.text
    assert response.json()["key_configured"] is True
    assert response.json()["settings_revision"] == pre["expected_revision"] + 1
    assert response.json()["enabled"] is False
    settings = client.get("/v1/settings").json()
    assert settings["ai"]["key_configured"] is True
    with ctx.db.session() as s:
        assert SECRET not in json.dumps([row.value for row in s.scalars(select(AppSetting))])
    assert SECRET not in response.text + json.dumps(settings)
    assert store.get_key(ctx.config.data_dir, "openai") == SECRET
    stale = client.post("/v1/ai/credentials/delete", json=pre)
    assert stale.status_code == 409
    assert store.get_key(ctx.config.data_dir, "openai") == SECRET
    deleted = client.post("/v1/ai/credentials/delete", json=expected(client))
    assert deleted.status_code == 200
    assert deleted.json()["key_configured"] is False
    assert deleted.json()["enabled"] is False


@pytest.mark.parametrize("value", [None, 42, [], {}, "", "private key with spaces", "x\nsecret", "한글", "x" * 2561])
def test_bad_credential_validation_never_reflects_input(client, store, value):
    response = client.post("/v1/ai/credentials", json={"key": value, **expected(client)})
    assert response.status_code == 422
    assert response.json()["error"]["details"] is None
    assert response.json()["error"]["message"] == "키 요청 형식이 올바르지 않습니다"
    assert client.get("/v1/ai/status").json()["key_configured"] is False


def test_secret_property_name_is_not_reflected_in_validation_error(client, store):
    response = client.post("/v1/ai/credentials", json={"key": SECRET, SECRET: SECRET, **expected(client)})
    assert response.status_code == 422
    assert SECRET not in response.text


def test_concurrent_credential_changes_have_one_winner(client, ctx, store):
    pre = expected(client)
    def save(suffix):
        return client.post("/v1/ai/credentials", json={"key": SECRET + suffix, **pre})
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(save, ("A", "B")))
    assert sorted(r.status_code for r in responses) == [200, 409]
    assert client.get("/v1/ai/status").json()["settings_revision"] == pre["expected_revision"] + 1
    assert store.get_key(ctx.config.data_dir, "openai") in (SECRET + "A", SECRET + "B")


def test_restore_generation_rejects_old_key_action(client, ctx, store):
    pre = expected(client)
    ctx.db.gate.restored()
    response = client.post("/v1/ai/credentials", json={"key": SECRET, **pre})
    assert response.status_code == 409
    assert store.get_key(ctx.config.data_dir, "openai") is None


def test_storage_error_is_distinct_from_no_key_and_does_not_leak(client, ctx, store):
    class BrokenStore:
        def status(self, scope, provider):
            return CredentialStatus(supported=True, configured=None, error_code="credential_unavailable")
        def set_key(self, scope, provider, key):
            raise CredentialError("credential_unavailable")
    pre = expected(client)
    with ctx.db.session() as s:
        config = get_setting(s, "ai")
        put_setting(s, "ai", {**config, "enabled": True, "external_transfer_consent": True})
    ctx.credential_store = BrokenStore()
    status = client.get("/v1/ai/status").json()
    assert status["key_configured"] is None
    assert status["ready"] is False
    assert status["blocked_reason"] == "credential_unavailable"
    response = client.post("/v1/ai/credentials", json={"key": SECRET, **pre})
    assert response.status_code == 503
    assert SECRET not in response.text
    assert client.get("/v1/settings").json()["ai"]["enabled"] is False


def test_full_settings_validation_remains_atomic_after_ai_change(client, store):
    before = client.get("/v1/settings").json()
    response = client.patch("/v1/settings", json={"ai": {"daily_cost_cap": 500}, "retention": {"raw_days": 0}})
    assert response.status_code == 422
    assert client.get("/v1/settings").json() == before


@pytest.mark.parametrize("patch", [
    {"provider": None}, {"model": None}, {"provider": "foreign"}, {"model": "unknown"},
    {"pricing_confirmed": 1}, {"usd_to_krw_rate": True}, {"usd_to_krw_rate": 0},
    {"max_output_tokens": 4097}, {"max_output_tokens": None}, {"expected_revision": True},
])
def test_invalid_paid_settings_do_not_change_state(client, store, patch):
    before = client.get("/v1/settings").json()
    assert client.patch("/v1/settings", json={"ai": patch}).status_code == 422
    assert client.get("/v1/settings").json() == before


def test_ai_settings_cas_and_fx_confirmation(client, store):
    pre = expected(client)
    response = client.patch("/v1/settings", json={"ai": {"usd_to_krw_rate": 1500, "pricing_confirmed": True, **pre}})
    assert response.status_code == 200
    assert response.json()["ai"]["pricing_confirmed"] is True
    assert client.patch("/v1/settings", json={"ai": {"daily_cost_cap": 50, **pre}}).status_code == 409
    response = client.patch("/v1/settings", json={"ai": {"usd_to_krw_rate": 1600, **expected(client)}})
    assert response.status_code == 200
    assert response.json()["ai"]["pricing_confirmed"] is False


def test_old_tariff_confirmation_requires_fresh_confirmation(client, ctx, store):
    from worklead.analysis.openai_provider import PRICE_VERSION
    store.set_key(ctx.config.data_dir, "openai", SECRET)
    with ctx.db.session() as s:
        put_setting(s, "ai", {**get_setting(s, "ai"), "enabled": True,
            "external_transfer_consent": True, "pricing_confirmed": True,
            "price_version": "obsolete-tariff", "usd_to_krw_rate": 1500,
            "daily_cost_cap": 100, "monthly_cost_cap": 1000})
    assert client.get("/v1/settings").json()["ai"]["pricing_confirmed"] is False
    assert client.get("/v1/ai/status").json()["blocked_reason"] == "pricing_required"
    response = client.patch("/v1/settings", json={"ai": {"pricing_confirmed": True, **expected(client)}})
    assert response.status_code == 200
    assert response.json()["ai"]["price_version"] == PRICE_VERSION
    assert client.get("/v1/ai/status").json()["ready"] is True


def test_ready_requires_explicit_consent_key_pricing_and_both_caps(client, store):
    assert client.get("/v1/ai/status").json()["ready"] is False
    assert client.patch("/v1/settings", json={"ai": {"enabled": True, **expected(client)}}).status_code == 422
    response = client.post("/v1/ai/credentials", json={"key": SECRET, **expected(client)})
    assert response.status_code == 200
    response = client.patch("/v1/settings", json={"ai": {"enabled": True, "external_transfer_consent": True, **expected(client)}})
    assert response.status_code == 200
    assert client.get("/v1/ai/status").json()["blocked_reason"] == "pricing_required"
    response = client.patch("/v1/settings", json={"ai": {"usd_to_krw_rate": 1500, "pricing_confirmed": True, **expected(client)}})
    assert response.status_code == 200
    assert client.get("/v1/ai/status").json()["blocked_reason"] == "budget_required"
    response = client.patch("/v1/settings", json={"ai": {"daily_cost_cap": 0, "monthly_cost_cap": 1000, **expected(client)}})
    assert response.status_code == 200
    assert client.get("/v1/ai/status").json()["blocked_reason"] == "budget_exhausted"
    response = client.patch("/v1/settings", json={"ai": {"daily_cost_cap": 100, **expected(client)}})
    assert response.status_code == 200
    assert client.get("/v1/ai/status").json()["ready"] is True


def test_uninstall_helper_is_scoped_idempotent_and_does_not_start_server(tmp_path, monkeypatch, capsys):
    from worklead import server
    from worklead.services import credentials
    store = FakeCredentialStore()
    target, foreign = tmp_path / "owned", tmp_path / "foreign"
    store.set_key(target, "openai", SECRET)
    store.set_key(foreign, "openai", SECRET + "foreign")
    monkeypatch.setattr(credentials, "WindowsCredentialStore", lambda: store)
    def forbidden(*args, **kwargs):
        raise AssertionError("credential deletion must not create logs, DB or provider")
    monkeypatch.setattr(server, "setup_logging", forbidden)
    monkeypatch.setattr(server, "build_context", forbidden)
    for flags in (["--remove-ai-credential"], ["--confirm-key-delete"], ["--remove-ai-credential", "--confirm-key-delete", "--demo"]):
        assert server.serve(["--data-dir", str(target), *flags]) == 64
        assert store.get_key(target, "openai") == SECRET
    args = ["--remove-ai-credential", "--confirm-key-delete", "--data-dir", str(target)]
    assert server.serve(args) == 0
    assert server.serve(args) == 0
    assert store.get_key(target, "openai") is None
    assert store.get_key(foreign, "openai") == SECRET + "foreign"
    assert not list(tmp_path.iterdir())
    output = capsys.readouterr().out
    assert SECRET not in output and str(target) not in output


def test_uninstall_helper_os_failure_has_fixed_code_and_retains_key(tmp_path, monkeypatch, capsys):
    from worklead import server
    from worklead.services import credentials
    store = FakeCredentialStore()
    store.set_key(tmp_path, "openai", SECRET)
    store.set_failure("delete", SECRET)
    monkeypatch.setattr(credentials, "WindowsCredentialStore", lambda: store)
    assert server.serve(["--remove-ai-credential", "--confirm-key-delete", "--data-dir", str(tmp_path)]) == 18
    assert store.get_key(tmp_path, "openai") == SECRET
    output = capsys.readouterr().out
    assert json.loads(output)["code"] == "credential_delete_failed"
    assert SECRET not in output and str(tmp_path) not in output


def test_backup_has_no_key_restore_preserves_costs_and_resets_transmission_consent(client, ctx, store):
    assert client.post("/v1/ai/credentials", json={"key": SECRET, **expected(client)}).status_code == 200
    enabled = client.patch("/v1/settings", json={"ai": {
        "enabled": True, "external_transfer_consent": True, "pricing_confirmed": True,
        "usd_to_krw_rate": 1500, "daily_cost_cap": 1000, "monthly_cost_cap": 10000, **expected(client),
    }})
    assert enabled.status_code == 200
    made = client.post("/v1/backups")
    assert made.status_code == 201
    name = made.json()["id"]
    backup = ctx.config.backup_dir / name
    assert SECRET.encode() not in backup.read_bytes()
    # Spending after the chosen backup cannot be rolled back by restoring it.
    ledger = ctx.ai_coordinator.ledger
    claim = ledger.reserve("restore-uncertain-cache", "guard", 15, day_cap=1000, month_cap=10000, now=ctx.clock())
    ledger.sent(claim.attempt_id)
    before = ledger.status(ctx.clock())
    assert before["day"]["uncertain_krw"] == 15
    replacement = SECRET + "replacement"
    store.set_key(ctx.config.data_dir, "openai", replacement)
    check = client.get(f"/v1/backups/{name}")
    assert check.status_code == 200 and check.json()["can_restore"] is True
    restored = client.post(f"/v1/backups/{name}/restore", json={"confirm": True, "sha256": check.json()["sha256"]})
    assert restored.status_code == 200
    assert restored.json()["ai_analysis_paused"] is True
    assert ledger.status(ctx.clock()) == before
    assert store.get_key(ctx.config.data_dir, "openai") == replacement
    current = client.get("/v1/settings").json()["ai"]
    assert current["enabled"] is False
    assert current["external_transfer_consent"] is False
    assert current["pricing_confirmed"] is False


def test_disabled_analysis_is_no_longer_permanently_pending(client, ctx, store):
    from tests.test_source_fields import NOW, sample
    from worklead.engine.pipeline import ingest
    ctx.clock = lambda: NOW
    assert client.post("/v1/ai/credentials", json={"key": SECRET, **expected(client)}).status_code == 200
    assert client.patch("/v1/settings", json={"ai": {"enabled": True, "external_transfer_consent": True, **expected(client)}}).status_code == 200
    with ctx.db.session() as s:
        lead_id = ingest(s, "daangn-alba", sample(), parser_version="synthetic", now=ctx.clock(), provider=ctx.provider).lead_id
    assert client.get(f"/v1/leads/{lead_id}").json()["analysis_status"] == "pending"
    assert client.patch("/v1/settings", json={"ai": {"enabled": False, **expected(client)}}).status_code == 200
    detail = client.get(f"/v1/leads/{lead_id}").json()
    assert detail["analysis_status"] == "rules_only"
    assert detail["body_text"] and detail["evidence"]


def test_budget_store_failure_is_unknown_and_blocks_paid_analysis(client, ctx, store, tmp_path):
    broken = tmp_path / "not-a-ledger"
    broken.mkdir()
    ctx.ai_coordinator.ledger.path = broken
    response = client.get("/v1/ai/status")
    assert response.status_code == 200
    assert response.json()["ledger"] is None
    assert response.json()["blocked_reason"] == "ai_ledger_unavailable"
    assert response.json()["ready"] is False
    assert str(broken) not in response.text


def test_demo_does_not_access_operational_keys(tmp_path):
    from tests.conftest import make_ctx, client_for
    demo = make_ctx(tmp_path, demo=True)
    class ForbiddenStore:
        def __getattr__(self, name):
            raise AssertionError("demo must not access an OS credential")
    demo.credential_store = ForbiddenStore()
    try:
        with client_for(demo) as c:
            assert c.get("/v1/settings").json()["ai"]["key_configured"] is None
            state = c.get("/v1/ai/status")
            assert state.status_code == 200, state.text
            assert state.json()["blocked_reason"] == "demo_credential_unsupported"
            pre = expected(c)
            assert c.post("/v1/ai/credentials", json={"key": SECRET, **pre}).status_code == 409
            assert c.post("/v1/ai/credentials/delete", json=pre).status_code == 409
            assert demo.ai_coordinator.analyze_lead("none") is False
    finally:
        demo.db.dispose()
