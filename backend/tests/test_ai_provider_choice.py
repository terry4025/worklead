from datetime import datetime

from worklead.services.credentials import FakeCredentialStore
from worklead.services.settings import get_profile, put_setting


def state(client):
    return client.get("/v1/ai/status").json()


def switch_to_exaone(client):
    s = client.get("/v1/settings").json()["ai"]
    body = client.patch("/v1/settings", json={"ai": {"provider": "exaone", "expected_revision": s["revision"],
                                                     "expected_generation": s["database_generation"]}}).json()["ai"]
    assert body["provider"] == "exaone" and body["model"] == "k-exaone_v2.5"
    return body


def test_exaone_key_is_stored_under_its_own_provider(ctx, client):
    ctx.credential_store = FakeCredentialStore()
    ai = switch_to_exaone(client)
    status = state(client)
    r = client.post("/v1/ai/credentials", json={"key": "fixture-not-real", "expected_revision": status["settings_revision"],
                                                "expected_generation": status["database_generation"]})
    assert r.status_code == 200
    assert ctx.credential_store.get_key(ctx.config.data_dir, "exaone") == "fixture-not-real"
    assert ctx.credential_store.get_key(ctx.config.data_dir, "openai") is None


def test_exaone_needs_no_pricing_or_budget_but_has_a_call_cap(ctx, client):
    ctx.credential_store = FakeCredentialStore()
    ctx.credential_store.set_key(ctx.config.data_dir, "exaone", "fixture-not-real")
    ai = switch_to_exaone(client)
    status = state(client)
    patch = {"ai": {"enabled": True, "external_transfer_consent": True, "assist_application": True, "daily_call_cap": 50,
                    "expected_revision": status["settings_revision"], "expected_generation": status["database_generation"]}}
    body = client.patch("/v1/settings", json=patch).json()["ai"]
    assert body["assist_application"] is True and body["daily_call_cap"] == 50 and body["assist_chat"] is False
    status = state(client)
    assert status["provider"] == "exaone" and status["ready"] is True and status["calls_today"] == 0


def test_feature_switches_default_off_and_profile_has_portfolio(ctx, client):
    ai = client.get("/v1/settings").json()["ai"]
    assert all(ai[k] is False for k in ("assist_review", "assist_recheck", "assist_application", "assist_chat", "assist_quote"))
    profile = client.get("/v1/settings").json()["profile"]
    assert profile["portfolio_url"] == "https://www.seyj.kr/" and profile["price_anchor_note"] == ""
    with ctx.db.session() as s:
        assert get_profile(s).portfolio_url == "https://www.seyj.kr/"


def test_portfolio_must_be_https(client):
    profile = client.get("/v1/settings").json()["profile"]
    r = client.patch("/v1/settings", json={"profile": {**profile, "portfolio_url": "javascript:alert(1)"}})
    assert r.status_code == 422


def test_calls_today_counts_only_the_same_kst_day(ctx):
    from worklead.analysis.assist import calls_today
    now = datetime(2026, 11, 9, 16, 0)  # 01:00 KST on 11-10
    with ctx.db.session() as s:
        put_setting(s, "exaone_calls", {"day": "2026-11-09", "count": 7})
        assert calls_today(s, now) == 0
        put_setting(s, "exaone_calls", {"day": "2026-11-10", "count": 7})
        assert calls_today(s, now) == 7


def test_non_scoring_profile_fields_do_not_bump_version(ctx):
    from worklead.services.settings import get_profile_row, update_profile
    with ctx.db.session() as s:
        v = get_profile_row(s).version
        assert update_profile(s, {"portfolio_url": "https://example.com/"}) is False
        assert update_profile(s, {"price_anchor_note": "랜딩 30만원부터"}) is False
        row = get_profile_row(s)
        assert row.version == v and row.data["portfolio_url"] == "https://example.com/"
        assert update_profile(s, {"skills": "React"}) is True
        assert get_profile_row(s).version == v + 1


def test_paid_cache_profile_ignores_non_scoring_fields(ctx):
    from worklead.engine.pipeline import cache_profile
    from worklead.services.settings import update_profile
    with ctx.db.session() as s:
        before = cache_profile(get_profile(s))
        update_profile(s, {"price_anchor_note": "시간당 5만원"})
        after = get_profile(s)
        assert after.price_anchor_note == "시간당 5만원" and cache_profile(after) == before
        # Same key set as the pre-upgrade Profile, so existing paid cache keys stay byte-identical.
        assert set(cache_profile(after)) == {"services", "skills", "excluded_work", "min_contract", "target_hourly", "weekly_hours",
                                             "onsite", "allow_short_term_employment", "business_registered", "tax_invoice", "version"}


def test_model_only_patch_cannot_mismatch_provider(client):
    s = client.get("/v1/settings").json()["ai"]
    body = client.patch("/v1/settings", json={"ai": {"model": "k-exaone_v2.5", "expected_revision": s["revision"],
                                                     "expected_generation": s["database_generation"]}}).json()["ai"]
    assert (body["provider"], body["model"]) == ("openai", "gpt-4.1-mini-2025-04-14")
    ex = switch_to_exaone(client)
    body = client.patch("/v1/settings", json={"ai": {"model": "gpt-4.1-mini-2025-04-14", "expected_revision": ex["revision"],
                                                     "expected_generation": ex["database_generation"]}}).json()["ai"]
    assert (body["provider"], body["model"]) == ("exaone", "k-exaone_v2.5")
