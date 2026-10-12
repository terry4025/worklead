"""Persist per-listing retry pacing and discovery change signals."""
from alembic import op
import sqlalchemy as sa

revision = "0002_detail_fetch_states"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "detail_fetch_states",
        sa.Column("source_id", sa.String(64), sa.ForeignKey("sources.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("ref_key", sa.String(64), primary_key=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("title_hint", sa.Text()),
        sa.Column("source_updated_at", sa.DateTime()),
        sa.Column("outcome", sa.String(64), nullable=False),
        sa.Column("attempted_at", sa.DateTime(), nullable=False),
        sa.Column("retry_at", sa.DateTime()),
        sa.Column("failures", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade():
    op.drop_table("detail_fetch_states")
