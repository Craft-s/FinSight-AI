"""Immutable, page-addressed CNINFO PDF documents and durable preparation jobs."""
from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base, now, uid


class CnDocument(Base):
    __tablename__ = "cn_documents"
    __table_args__ = (UniqueConstraint("symbol", "filing_id", "sha256", "parser_version", name="uq_cn_document_version"),
                      Index("ix_cn_document_filing", "symbol", "filing_id", "created_at"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    symbol: Mapped[str] = mapped_column(ForeignKey("cn_companies.symbol"))
    filing_id: Mapped[str] = mapped_column(String(30))
    title: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(Text)
    source_snapshot_id: Mapped[str] = mapped_column(ForeignKey("cn_source_snapshots.id"))
    source_kind: Mapped[str] = mapped_column(String(30))
    sha256: Mapped[str] = mapped_column(String(64))
    parser_version: Mapped[str] = mapped_column(String(40))
    raw_bytes: Mapped[bytes] = mapped_column(LargeBinary)
    page_count: Mapped[int]
    coverage: Mapped[dict] = mapped_column(JSON)
    source_metadata: Mapped[dict] = mapped_column(JSON)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class CnDocumentChunk(Base):
    __tablename__ = "cn_document_chunks"
    __table_args__ = (UniqueConstraint("document_id", "chunk_index", name="uq_cn_document_chunk_order"),
                      Index("ix_cn_document_chunk_scope", "symbol", "filing_id", "chunk_index"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    document_id: Mapped[str] = mapped_column(ForeignKey("cn_documents.id"))
    symbol: Mapped[str] = mapped_column(String(12))
    filing_id: Mapped[str] = mapped_column(String(30))
    chunk_index: Mapped[int]
    section: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    content_sha256: Mapped[str] = mapped_column(String(64))
    location: Mapped[dict] = mapped_column(JSON)


class CnDocumentJob(Base):
    __tablename__ = "cn_document_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(String(60), index=True)
    symbol: Mapped[str] = mapped_column(ForeignKey("cn_companies.symbol"))
    filing_id: Mapped[str] = mapped_column(String(30))
    active_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    input_json: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    attempts: Mapped[int] = mapped_column(default=0)
    error_code: Mapped[str | None] = mapped_column(String(60))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
