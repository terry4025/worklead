"""Keep user decisions on candidate relations without merging lead records."""
import sqlalchemy as sa
from alembic import op

revision = "0004_relation_decisions"
down_revision = "0003_native_notifications"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("duplicate_links", sa.Column("dismissed", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("duplicate_links", sa.Column("revision", sa.Integer(), nullable=False, server_default="0"))


def downgrade():
    with op.batch_alter_table("duplicate_links") as batch:
        batch.drop_column("revision")
        batch.drop_column("dismissed")
