from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def uid() -> str:
    return str(uuid4())


def now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Dataset(Base):
    __tablename__ = "dataset_metadata"
    id: Mapped[str] = mapped_column(String(20), primary_key=True, default="singleton")
    source_kind: Mapped[str] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Company(Base):
    __tablename__ = "companies"
    __table_args__ = (UniqueConstraint("market", "cik"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    market: Mapped[str] = mapped_column(String(10), default="US")
    cik: Mapped[str] = mapped_column(String(10), index=True)
    ticker: Mapped[str | None] = mapped_column(String(30))
    name: Mapped[str] = mapped_column(String(300))
    exchange: Mapped[str | None] = mapped_column(String(50))
    fiscal_year_end: Mapped[str | None] = mapped_column(String(10))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_kind: Mapped[str] = mapped_column(String(30))


class TickerAlias(Base):
    __tablename__ = "ticker_aliases"
    __table_args__ = (UniqueConstraint("company_id", "ticker"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    ticker: Mapped[str] = mapped_column(String(30), index=True)
    is_current: Mapped[bool] = mapped_column(default=True)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SourceSnapshot(Base):
    __tablename__ = "source_snapshots"
    __table_args__ = (UniqueConstraint("company_id", "kind", "sha256"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    company_id: Mapped[str | None] = mapped_column(ForeignKey("companies.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    source_url: Mapped[str] = mapped_column(Text)
    source_kind: Mapped[str] = mapped_column(String(30))
    sha256: Mapped[str] = mapped_column(String(64))
    raw_json: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    parser_version: Mapped[str] = mapped_column(String(30), default="sec-v1")
    source_metadata: Mapped[dict] = mapped_column(JSON, default=dict)


class Filing(Base):
    __tablename__ = "filings"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    accession_number: Mapped[str] = mapped_column(String(30), unique=True)
    form_type: Mapped[str] = mapped_column(String(20), index=True)
    filing_date: Mapped[str] = mapped_column(String(10), index=True)
    report_date: Mapped[str | None] = mapped_column(String(10))
    primary_document: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(Text)
    amended: Mapped[bool] = mapped_column(default=False)
    items: Mapped[str | None] = mapped_column(Text)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("source_snapshots.id"))


class FinancialFact(Base):
    __tablename__ = "financial_facts"
    __table_args__ = (Index("ix_fact_lookup", "company_id", "concept", "end_date"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"))
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("source_snapshots.id"))
    taxonomy: Mapped[str] = mapped_column(String(80))
    concept: Mapped[str] = mapped_column(String(200))
    unit: Mapped[str] = mapped_column(String(40))
    value: Mapped[Decimal] = mapped_column(Numeric(38, 10))
    start_date: Mapped[str | None] = mapped_column(String(10))
    end_date: Mapped[str] = mapped_column(String(10))
    filed_at: Mapped[str | None] = mapped_column(String(10))
    accession_number: Mapped[str | None] = mapped_column(String(30))
    fiscal_year: Mapped[int | None]
    fiscal_period: Mapped[str | None] = mapped_column(String(10))
    form_type: Mapped[str | None] = mapped_column(String(20))
    frame: Mapped[str | None] = mapped_column(String(50))
    raw_context_json: Mapped[dict] = mapped_column(JSON)


class NormalizedMetric(Base):
    __tablename__ = "normalized_metrics"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "metric_key", "period_type", "period_end", "rule_version",
            name="uq_normalized_metrics_period",
        ),
        Index("ix_metric_period_lookup", "company_id", "period_type", "period_end"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    metric_key: Mapped[str] = mapped_column(String(60))
    fiscal_year: Mapped[int | None]
    fiscal_quarter: Mapped[int | None]
    period_type: Mapped[str] = mapped_column(String(10), default="annual")
    period_start: Mapped[str | None] = mapped_column(String(10))
    period_end: Mapped[str] = mapped_column(String(10))
    value: Mapped[Decimal | None] = mapped_column(Numeric(38, 10))
    unit: Mapped[str] = mapped_column(String(40))
    rule_version: Mapped[str] = mapped_column(String(40), default="annual-v1")
    payload: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SyncJob(Base):
    __tablename__ = "sync_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(String(50), default="local-owner")
    cik: Mapped[str] = mapped_column(String(10), index=True)
    active_key: Mapped[str | None] = mapped_column(String(10), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    force: Mapped[bool] = mapped_column(default=False)
    error_code: Mapped[str | None] = mapped_column(String(60))
    error_message: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
