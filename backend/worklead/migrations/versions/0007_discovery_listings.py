"""Public list observation timestamps, separate from publication and detail fetch."""
import sqlalchemy as sa
from alembic import op

revision = "0007_discovery_listings"
down_revision = "0006_daangn_cafe"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("discovery_listings",
        sa.Column("source_id", sa.String(64), sa.ForeignKey("sources.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("ref_key", sa.String(64), primary_key=True),
        sa.Column("source_post_id", sa.String(200)), sa.Column("url", sa.Text(), nullable=False),
        sa.Column("title_hint", sa.Text()), sa.Column("title_digest", sa.String(64), nullable=False),
        sa.Column("first_listed_at", sa.DateTime(), nullable=False),
        sa.Column("last_listed_at", sa.DateTime(), nullable=False),
        sa.Column("source_updated_at", sa.DateTime()), sa.Column("selection", sa.String(16), nullable=False))
    op.create_index("ix_listing_source_post", "discovery_listings", ["source_id", "source_post_id"])
    op.create_index("ix_listing_last_seen", "discovery_listings", ["last_listed_at"])


def downgrade():
    op.drop_table("discovery_listings")
