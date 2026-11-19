"""Remove the EXAONE assist: drop its drafts table and recheck column, reset EXAONE settings to disabled OpenAI."""
import json

import sqlalchemy as sa
from alembic import op

revision = "0011_remove_ai_assist"
down_revision = "0010_ai_assist"
branch_labels = None
depends_on = None

OPENAI_MODEL = "gpt-4.1-mini-2025-04-14"
ASSIST_KEYS = ("daily_call_cap", "assist_review", "assist_recheck", "assist_application", "assist_chat", "assist_quote")


def _load(raw):
    return json.loads(raw) if isinstance(raw, str) else raw


def upgrade():
    op.drop_index("ix_ai_draft_due", table_name="ai_drafts")
    op.drop_table("ai_drafts")
    op.drop_column("leads", "ai_recheck")  # native SQLite DROP COLUMN, as 0010's downgrade does

    conn = op.get_bind()
    row = conn.execute(sa.text("SELECT value FROM app_settings WHERE key = 'ai'")).first()
    if row is not None:
        ai = _load(row[0]) or {}
        if ai.get("provider") == "exaone":
            ai.update(provider="openai", model=OPENAI_MODEL, enabled=False)
        for key in ASSIST_KEYS:
            ai.pop(key, None)
        conn.execute(sa.text("UPDATE app_settings SET value = :v WHERE key = 'ai'"), {"v": json.dumps(ai, ensure_ascii=False)})
        # A settings screen opened before the upgrade must not save its stale AI values.
        rev = conn.execute(sa.text("SELECT value FROM app_settings WHERE key = 'ai_revision'")).first()
        if rev is not None:
            conn.execute(sa.text("UPDATE app_settings SET value = :v WHERE key = 'ai_revision'"),
                         {"v": json.dumps(int(_load(rev[0]) or 0) + 1)})
    conn.execute(sa.text("DELETE FROM app_settings WHERE key = 'exaone_calls'"))
    for pid, data in conn.execute(sa.text("SELECT id, data FROM user_profile")).fetchall():
        profile = _load(data) or {}
        if "price_anchor_note" in profile:
            profile.pop("price_anchor_note")
            conn.execute(sa.text("UPDATE user_profile SET data = :d WHERE id = :i"),
                         {"d": json.dumps(profile, ensure_ascii=False), "i": pid})


def downgrade():
    # Structure only; the removed drafts and settings are not restored.
    op.add_column("leads", sa.Column("ai_recheck", sa.JSON()))
    op.create_table(
        "ai_drafts",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("lead_id", sa.String(40), sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("cache_key", sa.String(64), nullable=False),
        sa.Column("text", sa.Text()),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("failure_code", sa.String(64)),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_retry_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime()),
        sa.UniqueConstraint("lead_id", "kind", "cache_key", name="uq_ai_draft_key"),
    )
    op.create_index("ix_ai_draft_due", "ai_drafts", ["kind", "status", "next_retry_at"])
