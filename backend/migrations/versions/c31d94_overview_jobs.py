"""Versioned structured-metric overview jobs.

Revision ID: c31d94
Revises: b31c93
"""
import sqlalchemy as sa
from alembic import op

revision = "c31d94"
down_revision = "b31c93"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "overview_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(60), nullable=False),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("cik", sa.String(10), nullable=False),
        sa.Column("config_id", sa.String(36), nullable=False),
        sa.Column("config_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("cache_key", sa.String(64), nullable=False),
        sa.Column("active_key", sa.String(64), unique=True),
        sa.Column("input_json", sa.JSON(), nullable=False),
        sa.Column("result_json", sa.JSON()),
        sa.Column("error_code", sa.String(60)),
        sa.Column("error_message", sa.Text()),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_overview_jobs_company_id", "overview_jobs", ["company_id"])
    op.create_index("ix_overview_cache", "overview_jobs", ["owner_id", "cache_key", "status"])


def downgrade():
    op.drop_table("overview_jobs")
