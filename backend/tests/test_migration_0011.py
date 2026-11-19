import json
import sqlite3

from alembic import command

from worklead import storage

HEAD = "0011_remove_ai_assist"
EXAONE_AI = {"enabled": True, "external_transfer_consent": True, "engine_label": None, "monthly_cost_cap": None,
             "daily_cost_cap": None, "provider": "exaone", "model": "k-exaone_v2.5", "usd_to_krw_rate": None,
             "max_output_tokens": 1024, "pricing_confirmed": False, "price_version": None, "daily_call_cap": 300,
             "assist_review": True, "assist_recheck": True, "assist_application": True, "assist_chat": True, "assist_quote": True}


def _setting(c, key):
    row = c.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    # SQLite gives a JSON-affinity column back as int for a bare number.
    return None if row is None else (json.loads(row[0]) if isinstance(row[0], str) else row[0])


def test_head_revision_drops_ai_tables(ctx):
    assert storage.current_revision(ctx.config.db_path) == HEAD
    with sqlite3.connect(ctx.config.db_path) as c:
        assert "ai_recheck" not in {r[1] for r in c.execute("PRAGMA table_info(leads)")}
        assert c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='ai_drafts'").fetchone() is None


def test_live_like_ai_row_is_clean_after_upgrade(ctx, client):
    cfg = storage.alembic_config(ctx.config.db_path)
    ctx.db.dispose()
    command.downgrade(cfg, "0010_ai_assist")
    with sqlite3.connect(ctx.config.db_path) as c:
        for key, value in (("ai", EXAONE_AI), ("ai_revision", 4), ("exaone_calls", {"day": "2026-11-19", "count": 41})):
            c.execute("INSERT OR REPLACE INTO app_settings(key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
                      (key, json.dumps(value)))
        data = json.loads((c.execute("SELECT data FROM user_profile WHERE id = 1").fetchone() or ["{}"])[0])
        c.execute("INSERT OR REPLACE INTO user_profile(id, version, data, updated_at) VALUES (1, 1, ?, CURRENT_TIMESTAMP)",
                  (json.dumps({**data, "price_anchor_note": "x"}),))
    command.upgrade(cfg, "head")
    with sqlite3.connect(ctx.config.db_path) as c:
        ai = _setting(c, "ai")
        assert (ai["provider"], ai["model"], ai["enabled"]) == ("openai", "gpt-4.1-mini-2025-04-14", False)
        assert not {"daily_call_cap", "assist_review", "assist_recheck", "assist_application", "assist_chat", "assist_quote"} & set(ai)
        assert _setting(c, "ai_revision") == 5
        assert _setting(c, "exaone_calls") is None
        assert "price_anchor_note" not in json.loads(c.execute("SELECT data FROM user_profile WHERE id = 1").fetchone()[0])
    body = client.get("/v1/settings")
    assert body.status_code == 200, body.text
    ai = body.json()["ai"]
    patched = client.patch("/v1/settings", json={"ai": {"enabled": False, "expected_revision": ai["revision"],
                                                        "expected_generation": ai["database_generation"]}})
    assert patched.status_code == 200, patched.text


def test_downgrade_then_upgrade_round_trip(ctx):
    cfg = storage.alembic_config(ctx.config.db_path)
    ctx.db.dispose()
    command.downgrade(cfg, "0010_ai_assist")
    command.upgrade(cfg, "head")
    assert storage.validate_database(ctx.config.db_path)[0] == HEAD
