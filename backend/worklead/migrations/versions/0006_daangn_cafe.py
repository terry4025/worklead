"""Dedicated cafe session, approvals, durable work and user records."""
import sqlalchemy as sa
from alembic import op

revision = "0006_daangn_cafe"
down_revision = "0005_page_checkpoints"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("cafe",
        sa.Column("id", sa.String(40), primary_key=True), sa.Column("url", sa.Text(), nullable=False, unique=True),
        sa.Column("name", sa.Text(), nullable=False), sa.Column("region", sa.Text()), sa.Column("category", sa.Text()),
        sa.Column("member_count", sa.Integer()), sa.Column("post_count", sa.Integer()), sa.Column("joined", sa.Boolean()),
        sa.Column("status", sa.String(24), nullable=False), sa.Column("status_reason", sa.Text()), sa.Column("rule_evidence", sa.Text()),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False), sa.Column("last_checked_at", sa.DateTime()), sa.Column("approved_at", sa.DateTime()))
    op.create_table("cafe_board",
        sa.Column("id", sa.String(40), primary_key=True), sa.Column("cafe_id", sa.String(40), sa.ForeignKey("cafe.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False), sa.Column("url", sa.Text(), nullable=False, unique=True),
        sa.Column("kind", sa.String(24), nullable=False), sa.Column("post_allowed", sa.Boolean(), nullable=False),
        sa.Column("approved", sa.Boolean(), nullable=False), sa.Column("evidence", sa.Text()))
    op.create_table("cafe_post_attempt",
        sa.Column("id", sa.String(40), primary_key=True), sa.Column("cafe_id", sa.String(40), sa.ForeignKey("cafe.id"), nullable=False),
        sa.Column("board_id", sa.String(40), sa.ForeignKey("cafe_board.id"), nullable=False), sa.Column("template_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False), sa.Column("post_url", sa.Text()), sa.Column("error_code", sa.String(64)),
        sa.Column("error_message", sa.Text()), sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("verified_at", sa.DateTime()))
    op.create_table("cafe_comment",
        sa.Column("id", sa.String(40), primary_key=True), sa.Column("attempt_id", sa.String(40), sa.ForeignKey("cafe_post_attempt.id"), nullable=False),
        sa.Column("external_id", sa.String(200), nullable=False), sa.Column("author_nickname", sa.Text(), nullable=False), sa.Column("body", sa.Text(), nullable=False),
        sa.Column("written_at", sa.DateTime()), sa.Column("written_raw", sa.Text()), sa.Column("is_reply", sa.Boolean(), nullable=False),
        sa.Column("classification", sa.String(16), nullable=False), sa.Column("read_at", sa.DateTime()), sa.Column("memo", sa.Text(), nullable=False),
        sa.Column("lead_id", sa.String(40), sa.ForeignKey("leads.id", ondelete="SET NULL")), sa.UniqueConstraint("attempt_id", "external_id"))
    op.create_table("cafe_session",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("state", sa.String(16), nullable=False), sa.Column("checked_at", sa.DateTime()),
        sa.Column("reason", sa.String(200)), sa.Column("usage_day", sa.String(10), nullable=False), sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("post_count", sa.Integer(), nullable=False), sa.Column("last_navigation_at", sa.DateTime()), sa.Column("last_post_at", sa.DateTime()))
    op.create_table("cafe_consent", sa.Column("version", sa.String(40), primary_key=True), sa.Column("accepted_at", sa.DateTime(), nullable=False))
    op.create_table("cafe_job",
        sa.Column("id", sa.String(40), primary_key=True), sa.Column("kind", sa.String(24), nullable=False), sa.Column("state", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("started_at", sa.DateTime()), sa.Column("finished_at", sa.DateTime()),
        sa.Column("error_code", sa.String(64)), sa.Column("error_message", sa.Text()), sa.Column("input", sa.JSON(), nullable=False), sa.Column("result", sa.JSON(), nullable=False))
    op.execute("INSERT OR IGNORE INTO sources(id,name,kind,adapter_version,scope_note,auto_collect_enabled,interval_minutes,health_status,created_at,updated_at) VALUES ('daangn_cafe','당근 카페','site','cafe-1','전용 Edge 세션을 사용하는 카페 수집',0,180,'unknown',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")
    op.execute("CREATE UNIQUE INDEX cafe_job_single_active ON cafe_job ((1)) WHERE state IN ('queued','running')")
    op.execute("INSERT OR IGNORE INTO source_policies(source_id,status,reviewed_by,robots_status) VALUES ('daangn_cafe','permission_pending','system','unchecked')")


def downgrade():
    for name in ("cafe_job", "cafe_consent", "cafe_session", "cafe_comment", "cafe_post_attempt", "cafe_board", "cafe"):
        op.drop_table(name)
    # Existing lead records can reference the source. Keep that source and its policy.
