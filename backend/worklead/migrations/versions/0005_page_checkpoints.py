"""Retain discovered page references when collection is interrupted."""
import sqlalchemy as sa
from alembic import op

revision = "0005_page_checkpoints"
down_revision = "0004_relation_decisions"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("collection_tasks", sa.Column("checkpoint", sa.JSON(), nullable=False, server_default="{}"))


def downgrade():
    with op.batch_alter_table("collection_tasks") as batch:
        batch.drop_column("checkpoint")
