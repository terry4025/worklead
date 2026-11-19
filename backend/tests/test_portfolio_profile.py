"""Portfolio profile field after the EXAONE removal: kept, non-scoring, no assist settings left."""
from worklead.engine.pipeline import cache_profile
from worklead.services.settings import get_profile, get_profile_row, update_profile

SCORING_KEYS = {"services", "skills", "excluded_work", "min_contract", "target_hourly", "weekly_hours",
                "onsite", "allow_short_term_employment", "business_registered", "tax_invoice", "version"}


def test_portfolio_must_be_https(client):
    profile = client.get("/v1/settings").json()["profile"]
    r = client.patch("/v1/settings", json={"profile": {**profile, "portfolio_url": "javascript:alert(1)"}})
    assert r.status_code == 422


def test_portfolio_change_does_not_bump_version(ctx):
    with ctx.db.session() as s:
        v = get_profile_row(s).version
        assert update_profile(s, {"portfolio_url": "https://example.com/"}) is False
        row = get_profile_row(s)
        assert row.version == v and row.data["portfolio_url"] == "https://example.com/"
        assert update_profile(s, {"skills": "React"}) is True
        assert get_profile_row(s).version == v + 1


def test_paid_cache_profile_ignores_portfolio(ctx):
    with ctx.db.session() as s:
        before = cache_profile(get_profile(s))
        update_profile(s, {"portfolio_url": "https://example.com/"})
        after = get_profile(s)
        assert after.portfolio_url == "https://example.com/" and cache_profile(after) == before
        assert set(cache_profile(after)) == SCORING_KEYS


def test_settings_have_no_assist_fields(client):
    body = client.get("/v1/settings").json()
    assert "daily_call_cap" not in body["ai"] and "assist_review" not in body["ai"]
    assert "price_anchor_note" not in body["profile"]
    assert body["profile"]["portfolio_url"] == "https://www.seyj.kr/"
