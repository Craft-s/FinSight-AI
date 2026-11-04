"""A-share records are independent of SEC CIKs and the SEC dataset binding."""
from datetime import datetime

from sqlalchemy import JSON, DateTime, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base, now, uid


class CnDataset(Base):
    __tablename__ = "cn_dataset_metadata"
    id: Mapped[str] = mapped_column(String(20), primary_key=True, default="singleton")
    source_kind: Mapped[str] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class CnCompany(Base):
    __tablename__ = "cn_companies"
    symbol: Mapped[str] = mapped_column(String(12), primary_key=True)
    code: Mapped[str] = mapped_column(String(6), index=True)
    exchange: Mapped[str] = mapped_column(String(4), index=True)
    name: Mapped[str] = mapped_column(String(200))
    org_id: Mapped[str | None] = mapped_column(String(40))
    source_kind: Mapped[str] = mapped_column(String(30))
    source_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    directory_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class CnSnapshot(Base):
    __tablename__ = "cn_source_snapshots"
    __table_args__ = (
        UniqueConstraint("scope", "kind", "sha256", name="uq_cn_source_version"),
        Index("ix_cn_source_latest", "scope", "kind", "last_observed_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    scope: Mapped[str] = mapped_column(String(20))
    kind: Mapped[str] = mapped_column(String(30))
    source_kind: Mapped[str] = mapped_column(String(30))
    source_url: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    raw_json: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    parser_version: Mapped[str] = mapped_column(String(40), default="cn-v1")
    source_metadata: Mapped[dict] = mapped_column(JSON, default=dict)


class CnSyncJob(Base):
    __tablename__ = "cn_sync_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(String(60))
    scope: Mapped[str] = mapped_column(String(20))
    active_key: Mapped[str | None] = mapped_column(String(20), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    error_code: Mapped[str | None] = mapped_column(String(60))
    error_message: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(default=0)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
