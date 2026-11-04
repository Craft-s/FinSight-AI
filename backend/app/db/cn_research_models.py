"""Owner-bound A-share research; securities are not SEC CIK identities."""
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base, now, uid


class CnResearchJob(Base):
    __tablename__ = "cn_research_jobs"
    __table_args__ = (Index("ix_cn_research_history", "owner_id", "created_at"),
                      Index("ix_cn_research_cache", "owner_id", "fingerprint", "status"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(String(60))
    symbol: Mapped[str] = mapped_column(ForeignKey("cn_companies.symbol"))
    task_type: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    config_id: Mapped[str] = mapped_column(String(36))
    config_version: Mapped[int]
    config_binding: Mapped[str] = mapped_column(String(64))
    fingerprint: Mapped[str] = mapped_column(String(64))
    active_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    input_json: Mapped[dict] = mapped_column(JSON)
    report_id: Mapped[str | None] = mapped_column(String(36))
    parent_job_id: Mapped[str | None] = mapped_column(String(36))
    attempts: Mapped[int] = mapped_column(default=0)
    error_code: Mapped[str | None] = mapped_column(String(60))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CnResearchReport(Base):
    __tablename__ = "cn_research_reports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    job_id: Mapped[str] = mapped_column(ForeignKey("cn_research_jobs.id"), unique=True)
    owner_id: Mapped[str] = mapped_column(String(60), index=True)
    symbol: Mapped[str] = mapped_column(String(12))
    task_type: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(400))
    language: Mapped[str] = mapped_column(String(10))
    provider: Mapped[str] = mapped_column(String(20))
    model_id: Mapped[str] = mapped_column(String(200))
    source_kind: Mapped[str] = mapped_column(String(30))
    content: Mapped[dict] = mapped_column(JSON)
    markdown: Mapped[str] = mapped_column(Text)
    usage: Mapped[dict] = mapped_column(JSON)
    duration_ms: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
