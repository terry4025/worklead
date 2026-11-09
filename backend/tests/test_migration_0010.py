import sqlite3

from worklead import storage


def test_head_revision_has_ai_tables(ctx):
    assert storage.current_revision(ctx.config.db_path) == "0010_ai_assist"
    with sqlite3.connect(ctx.config.db_path) as c:
        assert "ai_recheck" in {r[1] for r in c.execute("PRAGMA table_info(leads)")}
        cols = {r[1] for r in c.execute("PRAGMA table_info(ai_drafts)")}
    assert {"lead_id", "kind", "cache_key", "text", "status", "attempts", "next_retry_at", "used_at"} <= cols


def test_downgrade_then_upgrade_round_trip(ctx):
    from alembic import command
    ctx.db.dispose()
    command.downgrade(storage.alembic_config(ctx.config.db_path), "0009_auto_apply_retry")
    command.upgrade(storage.alembic_config(ctx.config.db_path), "head")
    assert storage.validate_database(ctx.config.db_path)[0] == "0010_ai_assist"
