from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class CompanyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    cik: str
    ticker: str | None
    name: str
    exchange: str | None
    market: str
    source_kind: str
    last_synced_at: datetime | None
    fiscal_year_end: str | None = None
    is_stale: bool = True


class SearchOut(BaseModel):
    items: list[CompanyOut]
    source_kind: str


class MetricOut(BaseModel):
    metric_key: str
    label: str
    period_type: str
    fiscal_year: int | None
    fiscal_quarter: int | None = None
    period_start: str | None = None
    period_end: str
    value: str | None
    unit: str
    calc_method: str
    provenance: dict[str, Any]
    missing_reason: str | None = None
    yoy: str | None = None
    yoy_reason: str | None = None


class MetricsOut(BaseModel):
    items: list[MetricOut]
    source_kind: str
    selection_policy: str = "latest_restated"
    currency: str = "USD"
    frequency: Literal["annual", "quarterly"] = "annual"
    as_of: str | None = None
    accession: str | None = None


class FilingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    accession_number: str
    form_type: str
    filing_date: str
    report_date: str | None
    primary_document: str | None
    source_url: str
    amended: bool
    items: str | None
    snapshot_id: str


class FilingsOut(BaseModel):
    items: list[FilingOut]
    total: int
    page: int
    page_size: int
    source_kind: str


class SyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    force: bool = False


class JobAccepted(BaseModel):
    job_id: str
    status: str
    reused: bool


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    job_id: str
    cik: str
    status: Literal["queued", "running", "succeeded", "failed"]
    error_code: str | None
    error_message: str | None
    attempts: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    kind: str
    source_kind: str
    source_url: str
    sha256: str
    fetched_at: datetime
    parser_version: str
    source_metadata: dict[str, Any] = Field(default_factory=dict)


class HealthOut(BaseModel):
    status: str
    source_kind: str
    database: str
    redis: str
    task_mode: str
    ai_available: bool = False


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: ErrorBody
    request_id: str
