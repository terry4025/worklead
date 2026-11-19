"""Bounded pre-permit retry metadata; public job uniqueness remains unchanged."""
import sqlalchemy as sa
from alembic import op

revision = "0009_auto_apply_retry"
down_revision = "0008_job_auto_attempts"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("job_auto_attempts", sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("job_auto_attempts", sa.Column("retry_after", sa.DateTime()))


def downgrade():
    op.drop_column("job_auto_attempts", "retry_after")
    op.drop_column("job_auto_attempts", "retry_count")
