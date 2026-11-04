"""A-share API namespace. Six-digit tickers never enter SEC CIK endpoints."""
import re
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from pydantic import BaseModel
from redis import Redis
from rq import Queue
from rq.serializers import JSONSerializer
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.auth import require_owner
from app.core.config import get_settings
from app.core.errors import AppError
from app.db.cn_models import CnCompany, CnSnapshot, CnSyncJob
from app.db.models import now
from app.db.session import get_session
from app.schemas import MetricOut
from app.services.cn.service import (
    LIMITATIONS,
    company_out,
    ensure_cn_dataset,
    filings_for,
    metrics_for,
    mode,
    run_cn_sync,
    source_out,
)
from app.services.sync import aware

router = APIRouter(prefix="/api/v1/cn", tags=["A-share"])


class CnCompanyOut(BaseModel):
    symbol: str
    code: str
    name: str
    market: Literal["CN"] = "CN"
    exchange: Literal["SH", "SZ", "BJ"]
    currency: Literal["CNY"] = "CNY"
    source_kind: str
    last_synced_at: datetime | None
    directory_updated_at: datetime
    is_stale: bool
    limitations: list[str]


class CnSearchOut(BaseModel):
    items: list[CnCompanyOut]
    source_kind: str


class CnMetricsOut(BaseModel):
    items: list[MetricOut]
    source_kind: str
    frequency: Literal["annual", "cumulative"]
    currency: Literal["CNY"] = "CNY"
    selection_policy: str = "latest_provider_snapshot"
    limitations: list[str]


def cn_session(session: Session = Depends(get_session)):
    ensure_cn_dataset(session)
    return session


def valid_symbol(value):
    if not re.fullmatch(r"[0-9]{6}\.(SH|SZ|BJ)", value):
        raise AppError("CN_SYMBOL_INVALID", "请选择带交易所后缀的六位A股代码，例如600519.SH。", 422)
    return value


def find_company(session, symbol):
    valid_symbol(symbol)
    record = session.get(CnCompany, symbol)
    if not record:
        raise AppError("CN_COMPANY_NOT_FOUND", "未找到该A股公司，请先更新A股目录。", 404)
    return record


@router.get("/status")
def status(session: Session = Depends(cn_session)):
    count = session.scalar(select(func.count()).select_from(CnCompany))
    updated = session.scalar(select(func.max(CnCompany.directory_updated_at)))
    return {"source_kind": mode(), "directory_count": count,
            "directory_updated_at": aware(updated) if updated else None,
            "providers": ["东方财富（结构化财务）", "巨潮资讯（目录与公告）"],
            "limitations": LIMITATIONS}


@router.get("/companies/search", response_model=CnSearchOut)
def search(q: str = Query(default="", max_length=100),
           exchange: Literal["SH", "SZ", "BJ"] | None = None,
           session: Session = Depends(cn_session)):
    query = select(CnCompany)
    if exchange:
        query = query.where(CnCompany.exchange == exchange)
    term = q.strip()
    if term:
        query = query.where(or_(CnCompany.code == term, CnCompany.symbol == term.upper(),
                                CnCompany.name.icontains(term, autoescape=True)))
    rows = session.scalars(query.order_by(CnCompany.code, CnCompany.exchange).limit(20)).all()
    return {"items": [company_out(row) for row in rows], "source_kind": mode()}


@router.get("/companies/{symbol}", response_model=CnCompanyOut)
def company(symbol: str, session: Session = Depends(cn_session)):
    return company_out(find_company(session, symbol))


@router.get("/companies/{symbol}/metrics", response_model=CnMetricsOut)
def metrics(symbol: str, frequency: Literal["annual", "cumulative"] = "annual",
            years: int = Query(default=5, ge=1, le=10),
            selection_policy: str = "latest_provider_snapshot",
            as_of: str | None = None, accession: str | None = None,
            session: Session = Depends(cn_session)):
    record = find_company(session, symbol)
    if selection_policy != "latest_provider_snapshot" or as_of is not None or accession is not None:
        raise AppError("CN_HISTORY_UNSUPPORTED", "A股当前仅支持已保存的最新供应商口径，不能作为历史当时已知资料。", 422)
    return {"items": metrics_for(session, symbol, frequency, years), "source_kind": record.source_kind,
            "frequency": frequency, "currency": "CNY", "selection_policy": selection_policy,
            "limitations": LIMITATIONS}


@router.get("/companies/{symbol}/filings")
def filings(symbol: str, page: int = Query(default=1, ge=1),
            page_size: int = Query(default=10, ge=1, le=100),
            session: Session = Depends(cn_session)):
    company = find_company(session, symbol)
    rows, limitations = filings_for(session, symbol)
    start = (page - 1) * page_size
    return {"items": rows[start:start + page_size], "total": len(rows), "page": page,
            "page_size": page_size, "source_kind": company.source_kind, "limitations": limitations}


@router.get("/sources/{source_id}")
def source(source_id: str, session: Session = Depends(cn_session)):
    record = session.get(CnSnapshot, source_id)
    if not record:
        raise AppError("CN_SOURCE_NOT_FOUND", "未找到该A股来源版本。", 404)
    return source_out(record)


def enqueue(scope, session, owner, background):
    if mode() != "cn_live":
        raise AppError("CN_LIVE_DISABLED", "当前A股模式不允许在线请求。", 422)
    existing = session.scalar(select(CnSyncJob).where(CnSyncJob.active_key == scope).with_for_update())
    if existing:
        if (now() - aware(existing.created_at)).total_seconds() < 900:
            return {"job_id": existing.id, "status": existing.status, "reused": True}
        existing.status, existing.active_key, existing.finished_at = "failed", None, now()
        existing.error_code, existing.error_message = "WORKER_TIMEOUT", "A股任务超过恢复期限，可重新提交。"
        session.commit()
    job = CnSyncJob(scope=scope, active_key=scope, owner_id=owner)
    session.add(job)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.scalar(select(CnSyncJob).where(CnSyncJob.active_key == scope))
        if existing:
            return {"job_id": existing.id, "status": existing.status, "reused": True}
        raise AppError("CN_SYNC_CONFLICT", "A股同步状态变化，请重试。", 409) from None
    if get_settings().task_mode == "inline":
        background.add_task(run_cn_sync, job.id)
    else:
        try:
            redis = Redis.from_url(get_settings().redis_url, socket_connect_timeout=2, socket_timeout=2)
            Queue("finsight", connection=redis, serializer=JSONSerializer).enqueue(
                "app.services.cn.service.run_cn_sync", job.id, job_id=job.id,
                job_timeout=300, result_ttl=3600, failure_ttl=86400,
            )
        except Exception:
            job.status, job.active_key, job.finished_at = "failed", None, now()
            job.error_code, job.error_message = "QUEUE_UNAVAILABLE", "任务队列不可用，请稍后重试。"
            session.commit()
            raise AppError("QUEUE_UNAVAILABLE", job.error_message, 503, {"job_id": job.id}) from None
    return {"job_id": job.id, "status": "queued", "reused": False}


@router.post("/directory/sync", status_code=202)
def sync_directory(background: BackgroundTasks, owner: str = Depends(require_owner),
                   session: Session = Depends(cn_session)):
    return enqueue("directory", session, owner, background)


@router.post("/companies/{symbol}/sync", status_code=202)
def sync_company(symbol: str, background: BackgroundTasks, owner: str = Depends(require_owner),
                 session: Session = Depends(cn_session)):
    record = find_company(session, symbol)
    return enqueue(record.symbol, session, owner, background)


@router.get("/sync/jobs/{job_id}")
def job_status(job_id: str, owner: str = Depends(require_owner), session: Session = Depends(cn_session)):
    record = session.get(CnSyncJob, job_id)
    if not record or record.owner_id != owner:
        raise AppError("CN_JOB_NOT_FOUND", "未找到该A股同步任务。", 404)
    return {"id": record.id, "job_id": record.id, "status": record.status, "scope": record.scope,
            "attempts": record.attempts, "error_code": record.error_code, "error_message": record.error_message,
            "created_at": aware(record.created_at), "started_at": aware(record.started_at) if record.started_at else None,
            "finished_at": aware(record.finished_at) if record.finished_at else None, "result": record.result}
