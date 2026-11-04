"""Persistent, version-bound jobs for the Phase 3 structured-metric overview."""
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base, now, uid


class OverviewJob(Base):
    __tablename__ = "overview_jobs"
    __table_args__ = (Index("ix_overview_cache", "owner_id", "cache_key", "status"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(String(60))
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    cik: Mapped[str] = mapped_column(String(10))
    config_id: Mapped[str] = mapped_column(String(36))
    config_version: Mapped[int] = mapped_column()
    status: Mapped[str] = mapped_column(String(20), default="queued")
    cache_key: Mapped[str] = mapped_column(String(64))
    active_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    input_json: Mapped[dict] = mapped_column(JSON)
    result_json: Mapped[dict | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(60))
    error_message: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
