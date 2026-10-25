"""Durable one-attempt-per-public-job journal for visible-browser applications."""
import sqlalchemy as sa
from alembic import op

revision = "0008_job_auto_attempts"
down_revision = "0007_discovery_listings"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("job_auto_attempts",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("job_key", sa.String(64), nullable=False, unique=True),
        sa.Column("lead_id", sa.String(40), sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("reason_code", sa.String(64)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime()),
        sa.Column("permitted_at", sa.DateTime()),
        sa.Column("finished_at", sa.DateTime()))
    op.create_index("ix_auto_attempt_created", "job_auto_attempts", ["created_at"])


def downgrade():
    op.drop_table("job_auto_attempts")
