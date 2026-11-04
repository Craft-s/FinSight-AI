"""Immutable filing documents, chunks and a PostgreSQL English full-text index.

Revision ID: d41a01
Revises: c31d94
"""
import sqlalchemy as sa
from alembic import op

revision = "d41a01"
down_revision = "c31d94"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("filing_id", sa.String(36), sa.ForeignKey("filings.id"), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("source_kind", sa.String(30), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("parser_version", sa.String(40), nullable=False),
        sa.Column("raw_html", sa.Text(), nullable=False),
        sa.Column("raw_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("source_metadata", sa.JSON(), nullable=False),
        sa.UniqueConstraint("filing_id", "source_url", "sha256", "parser_version", name="uq_document_version"))
    op.create_index("ix_documents_filing_id", "documents", ["filing_id"])
    op.create_table("document_chunks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("filing_id", sa.String(36), sa.ForeignKey("filings.id"), nullable=False),
        sa.Column("section", sa.Text(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("location", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("document_id", "chunk_index", name="uq_document_chunk_order"))
    op.create_index("ix_document_chunks_document_id", "document_chunks", ["document_id"])
    op.create_index("ix_document_chunk_filing", "document_chunks", ["filing_id", "chunk_index"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute("CREATE INDEX ix_document_chunk_english_fts ON document_chunks USING gin (to_tsvector('english', content))")


def downgrade():
    op.drop_table("document_chunks")
    op.drop_table("documents")
