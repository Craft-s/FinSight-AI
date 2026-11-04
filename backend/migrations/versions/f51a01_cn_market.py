"""Independent A-share companies, immutable sources and asynchronous sync jobs.

Revision ID: f51a01
Revises: e41a02
"""
import sqlalchemy as sa
from alembic import op

revision = "f51a01"
down_revision = "e41a02"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("cn_dataset_metadata",
        sa.Column("id", sa.String(20), primary_key=True),
        sa.Column("source_kind", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("cn_companies",
        sa.Column("symbol", sa.String(12), primary_key=True),
        sa.Column("code", sa.String(6), nullable=False),
        sa.Column("exchange", sa.String(4), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("org_id", sa.String(40)),
        sa.Column("source_kind", sa.String(30), nullable=False),
        sa.Column("source_metadata", sa.JSON(), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True)),
        sa.Column("directory_updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_cn_companies_code", "cn_companies", ["code"])
    op.create_index("ix_cn_companies_exchange", "cn_companies", ["exchange"])
    op.create_table("cn_source_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("scope", sa.String(20), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("source_kind", sa.String(30), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("raw_json", sa.Text(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("parser_version", sa.String(40), nullable=False),
        sa.Column("source_metadata", sa.JSON(), nullable=False),
        sa.UniqueConstraint("scope", "kind", "sha256", name="uq_cn_source_version"))
    op.create_index("ix_cn_source_latest", "cn_source_snapshots", ["scope", "kind", "last_observed_at"])
    op.create_table("cn_sync_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(60), nullable=False),
        sa.Column("scope", sa.String(20), nullable=False),
        sa.Column("active_key", sa.String(20), unique=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error_code", sa.String(60)),
        sa.Column("error_message", sa.Text()),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)))


def downgrade():
    op.drop_table("cn_sync_jobs")
    op.drop_table("cn_source_snapshots")
    op.drop_table("cn_companies")
    op.drop_table("cn_dataset_metadata")
