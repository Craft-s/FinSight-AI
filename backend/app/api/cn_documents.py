"""Owner-controlled CN PDF preparation and public, page-addressed evidence."""
import hashlib

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from redis import Redis
from rq import Queue
from rq.serializers import JSONSerializer
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.auth import require_owner
from app.core.config import get_settings
from app.core.errors import AppError
from app.db.cn_document_models import CnDocument, CnDocumentJob
from app.db.models import now
from app.db.session import get_session, session_factory
from app.services.cn.documents import (
    RETRIEVAL_MODE,
    ensure_document,
    find_filing,
    list_documents,
    retrieve_chunks,
)
from app.services.cn.service import ensure_cn_dataset
from app.services.sync import aware

router = APIRouter(prefix="/api/v1/cn", tags=["A-share PDF evidence"])


def cn_session(session: Session = Depends(get_session)):
    ensure_cn_dataset(session)
    return session


def job_out(job):
    return {"id": job.id, "job_id": job.id, "market": "CN", "symbol": job.symbol,
        "filing_id": job.filing_id, "task_type": "document_sync", "status": job.status,
        "error_code": job.error_code, "error_message": job.error_message,
        "result": job.result, "attempts": job.attempts, "created_at": aware(job.created_at),
        "started_at": aware(job.started_at) if job.started_at else None,
        "finished_at": aware(job.finished_at) if job.finished_at else None}


@router.get("/companies/{symbol}/filings/{filing_id}/documents")
def documents(symbol: str, filing_id: str, session: Session = Depends(cn_session)):
    return {"items": list_documents(session, symbol, filing_id), "retrieval_mode": RETRIEVAL_MODE}


@router.post("/companies/{symbol}/filings/{filing_id}/documents/sync", status_code=202)
def prepare(symbol: str, filing_id: str, background: BackgroundTasks,
            owner: str = Depends(require_owner), session: Session = Depends(cn_session)):
    filing = find_filing(session, symbol, filing_id)
    digest = hashlib.sha256(f"{owner}|{symbol}|{filing_id}".encode()).hexdigest()
    existing = session.scalar(select(CnDocumentJob).where(CnDocumentJob.active_key == digest).with_for_update())
    if existing:
        if (now() - aware(existing.created_at)).total_seconds() < 900:
            return {"job_id": existing.id, "status": existing.status, "reused": True}
        existing.status, existing.active_key, existing.finished_at = "failed", None, now()
        existing.error_code, existing.error_message = "WORKER_TIMEOUT", "PDF准备任务超过恢复期限，可重新提交。"
        session.commit()
    job = CnDocumentJob(owner_id=owner, symbol=symbol, filing_id=filing_id, active_key=digest, input_json=filing)
    session.add(job)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.scalar(select(CnDocumentJob).where(CnDocumentJob.active_key == digest))
        if not existing:
            raise AppError("CN_DOCUMENT_CONFLICT", "PDF任务状态变化，请重试。", 409) from None
        return {"job_id": existing.id, "status": existing.status, "reused": True}
    if get_settings().task_mode == "inline":
        background.add_task(run_document_job, job.id)
    else:
        try:
            connection = Redis.from_url(get_settings().redis_url, socket_connect_timeout=2, socket_timeout=2)
            Queue("finsight", connection=connection, serializer=JSONSerializer).enqueue(
                "app.api.cn_documents.run_document_job", job.id, job_id=job.id,
                job_timeout=300, result_ttl=3600, failure_ttl=86400)
        except Exception:
            job.status, job.active_key, job.finished_at = "failed", None, now()
            job.error_code, job.error_message = "QUEUE_UNAVAILABLE", "PDF任务队列不可用，请稍后重试。"
            session.commit()
            raise AppError("QUEUE_UNAVAILABLE", job.error_message, 503, {"job_id": job.id}) from None
    return {"job_id": job.id, "status": "queued", "reused": False}


@router.get("/documents/jobs/{job_id}")
def document_job(job_id: str, owner: str = Depends(require_owner), session: Session = Depends(cn_session)):
    job = session.scalar(select(CnDocumentJob).where(CnDocumentJob.id == job_id, CnDocumentJob.owner_id == owner))
    if not job:
        raise AppError("CN_DOCUMENT_JOB_NOT_FOUND", "未找到该PDF准备任务。", 404)
    return job_out(job)


@router.get("/companies/{symbol}/evidence")
def evidence(symbol: str, q: str = Query(min_length=1, max_length=500),
             filing_id: str | None = None, session: Session = Depends(cn_session)):
    from app.db.cn_models import CnCompany
    from app.services.cn.provider import validate_symbol
    validate_symbol(symbol)
    if not session.get(CnCompany, symbol):
        raise AppError("CN_COMPANY_NOT_FOUND", "请先同步A股公司资料。", 404)
    if filing_id:
        ids = [filing_id]
        find_filing(session, symbol, filing_id)
    else:
        ids = list(dict.fromkeys(session.scalars(select(CnDocument.filing_id).where(CnDocument.symbol == symbol)
            .order_by(CnDocument.created_at.desc(), CnDocument.id.desc()).limit(100)).all()))[:10]
    rows = retrieve_chunks(session, symbol, ids, q.strip(), limit=8) if ids and q.strip() else []
    return {"items": rows, "retrieval_mode": RETRIEVAL_MODE,
        "scope_note": "仅按关键词匹配所选公告的已提取PDF文字；未启用OCR或语义检索。" if filing_id else
                      "仅按关键词匹配最近十份已有正文的公告；候选有界，未启用OCR或语义检索。"}


def run_document_job(identifier):
    with session_factory()() as session:
        ensure_cn_dataset(session)
        job = session.scalar(select(CnDocumentJob).where(CnDocumentJob.id == identifier).with_for_update())
        if not job or job.status != "queued":
            return
        job.status, job.started_at, job.attempts = "running", now(), job.attempts + 1
        symbol, filing_id, expected = job.symbol, job.filing_id, dict(job.input_json)
        session.commit()
    try:
        with session_factory()() as session:
            document = ensure_document(session, symbol, filing_id, refresh=True, expected=expected)
            current = session.scalar(select(CnDocumentJob).where(CnDocumentJob.id == identifier).with_for_update())
            if not current or current.status != "running":
                session.rollback()
                return
            current.status, current.active_key, current.finished_at = "succeeded", None, now()
            current.result = {"document_id": document.id, "document_ids": [document.id],
                "sha256": document.sha256, "page_count": document.page_count,
                "coverage": document.coverage, "parser_version": document.parser_version}
            session.commit()
    except Exception as error:
        with session_factory()() as session:
            current = session.scalar(select(CnDocumentJob).where(CnDocumentJob.id == identifier).with_for_update())
            if current and current.status == "running":
                current.status, current.active_key, current.finished_at = "failed", None, now()
                current.error_code = error.code if isinstance(error, AppError) else "CN_PDF_PREPARATION_FAILED"
                current.error_message = error.message if isinstance(error, AppError) else "PDF正文准备失败，已保存资料保留。"
                session.commit()
