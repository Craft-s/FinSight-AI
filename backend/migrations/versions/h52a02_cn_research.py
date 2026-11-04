"""A-share owner-bound evidence research jobs and reports.

Revision ID: h52a02
Revises: g52a01
"""
import sqlalchemy as sa
from alembic import op

revision = "h52a02"
down_revision = "g52a01"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("cn_research_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(60), nullable=False),
        sa.Column("symbol", sa.String(12), sa.ForeignKey("cn_companies.symbol"), nullable=False),
        sa.Column("task_type", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("config_id", sa.String(36), nullable=False),
        sa.Column("config_version", sa.Integer(), nullable=False),
        sa.Column("config_binding", sa.String(64), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("active_key", sa.String(64), unique=True),
        sa.Column("input_json", sa.JSON(), nullable=False),
        sa.Column("report_id", sa.String(36)),
        sa.Column("parent_job_id", sa.String(36)),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(60)),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)))
    op.create_index("ix_cn_research_history", "cn_research_jobs", ["owner_id", "created_at"])
    op.create_index("ix_cn_research_cache", "cn_research_jobs", ["owner_id", "fingerprint", "status"])
    op.create_table("cn_research_reports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("cn_research_jobs.id"), nullable=False, unique=True),
        sa.Column("owner_id", sa.String(60), nullable=False),
        sa.Column("symbol", sa.String(12), nullable=False),
        sa.Column("task_type", sa.String(20), nullable=False),
        sa.Column("title", sa.String(400), nullable=False),
        sa.Column("language", sa.String(10), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("model_id", sa.String(200), nullable=False),
        sa.Column("source_kind", sa.String(30), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("markdown", sa.Text(), nullable=False),
        sa.Column("usage", sa.JSON(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_cn_research_reports_owner_id", "cn_research_reports", ["owner_id"])


def downgrade():
    op.drop_table("cn_research_reports")
    op.drop_table("cn_research_jobs")
