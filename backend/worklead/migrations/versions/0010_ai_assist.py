"""AI assist: per-lead drafts (application, quote) and the EXAONE recheck verdict."""
import sqlalchemy as sa
from alembic import op

revision = "0010_ai_assist"
down_revision = "0009_auto_apply_retry"
branch_labels = None
depends_on = None


def upgrade():
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


def downgrade():
    op.drop_index("ix_ai_draft_due", table_name="ai_drafts")
    op.drop_table("ai_drafts")
    op.drop_column("leads", "ai_recheck")
