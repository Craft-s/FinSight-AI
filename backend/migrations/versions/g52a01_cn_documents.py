"""Immutable CNINFO PDF documents, page chunks and preparation jobs."""
import sqlalchemy as sa
from alembic import op

revision = "g52a01"
down_revision = "f51a01"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("cn_documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("symbol", sa.String(12), sa.ForeignKey("cn_companies.symbol"), nullable=False),
        sa.Column("filing_id", sa.String(30), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("source_snapshot_id", sa.String(36), sa.ForeignKey("cn_source_snapshots.id"), nullable=False),
        sa.Column("source_kind", sa.String(30), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("parser_version", sa.String(40), nullable=False),
        sa.Column("raw_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("coverage", sa.JSON(), nullable=False),
        sa.Column("source_metadata", sa.JSON(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("symbol", "filing_id", "sha256", "parser_version", name="uq_cn_document_version"))
    op.create_index("ix_cn_document_filing", "cn_documents", ["symbol", "filing_id", "created_at"])
    op.create_table("cn_document_chunks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("cn_documents.id"), nullable=False),
        sa.Column("symbol", sa.String(12), nullable=False),
        sa.Column("filing_id", sa.String(30), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("section", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("location", sa.JSON(), nullable=False),
        sa.UniqueConstraint("document_id", "chunk_index", name="uq_cn_document_chunk_order"))
    op.create_index("ix_cn_document_chunk_scope", "cn_document_chunks", ["symbol", "filing_id", "chunk_index"])
    op.create_table("cn_document_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(60), nullable=False),
        sa.Column("symbol", sa.String(12), sa.ForeignKey("cn_companies.symbol"), nullable=False),
        sa.Column("filing_id", sa.String(30), nullable=False),
        sa.Column("active_key", sa.String(64), unique=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("input_json", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(60)),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)))
    op.create_index("ix_cn_document_jobs_owner_id", "cn_document_jobs", ["owner_id"])


def downgrade():
    op.drop_table("cn_document_jobs")
    op.drop_table("cn_document_chunks")
    op.drop_table("cn_documents")
