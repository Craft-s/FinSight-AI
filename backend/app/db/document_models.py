"""Immutable SEC document versions; HTML is data and is never served as executable markup."""
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


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("filing_id", "source_url", "sha256", "parser_version", name="uq_document_version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    filing_id: Mapped[str] = mapped_column(ForeignKey("filings.id"), index=True)
    source_url: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    source_kind: Mapped[str] = mapped_column(String(30))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    title: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(20), default="primary")
    parser_version: Mapped[str] = mapped_column(String(40))
    raw_html: Mapped[str] = mapped_column(Text)
    raw_bytes: Mapped[bytes] = mapped_column(LargeBinary)
    source_metadata: Mapped[dict] = mapped_column(JSON, default=dict)


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (UniqueConstraint("document_id", "chunk_index", name="uq_document_chunk_order"),
                      Index("ix_document_chunk_filing", "filing_id", "chunk_index"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    filing_id: Mapped[str] = mapped_column(ForeignKey("filings.id"))
    section: Mapped[str] = mapped_column(Text)
    chunk_index: Mapped[int] = mapped_column()
    content: Mapped[str] = mapped_column(Text)
    content_sha256: Mapped[str] = mapped_column(String(64))
    location: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
