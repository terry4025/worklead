"""Bound native notification polling by type and the recent time window."""
from alembic import op

revision = "0003_native_notifications"
down_revision = "0002_detail_fetch_states"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_events_native_pending", "event_outbox", ["type", "notify", "notified_at", "created_at", "seq"])


def downgrade():
    op.drop_index("ix_events_native_pending", table_name="event_outbox")
