from datetime import datetime
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.auth import require_owner
from app.core.config import get_settings
from app.core.errors import AppError
from app.db.document_models import Document
from app.db.models import Company, Filing, now
from app.db.research_models import ResearchJob, ResearchReport
from app.db.session import get_session
from app.schemas import JobAccepted
from app.services.documents.service import list_filing_documents, retrieve_chunks
from app.services.llm_configs import get_verified_config
from app.services.research.context import fingerprint, freeze_context, public_mode, select_filings
from app.services.research.jobs import find_job, job_out, submit
from app.services.research.prompts import build_prompt
from app.services.sec.client import validate_cik
from app.services.sync import aware

router = APIRouter(prefix="/api/v1", tags=["Filing evidence and research"])


class ResearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cik: str = Field(pattern=r"^[0-9]{1,10}$")
    task_type: Literal["summary", "comparison", "event", "question"]
    filing_ids: list[str] = Field(min_length=1, max_length=2)
    question: str | None = Field(default=None, max_length=500)
    config_id: str = Field(min_length=1, max_length=36)
    language: Literal["zh-CN", "en-US"] = "zh-CN"
    force: bool = False

    @model_validator(mode="after")
    def question_and_filings(self):
        if self.task_type == "question" and (not self.question or not self.question.strip()):
            raise ValueError("Question required")
        if self.task_type != "question" and self.question:
            raise ValueError("Question is only allowed for question research")
        expected = 2 if self.task_type == "comparison" else 1
        if len(self.filing_ids) != expected or len(set(self.filing_ids)) != expected:
            raise ValueError("Select distinct filings for the task type")
        return self


class EmptyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class JobOut(BaseModel):
    id: str
    job_id: str
    cik: str
    task_type: str
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    error_code: str | None
    error_message: str | None
    report_id: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class HistoryOut(BaseModel):
    items: list[JobOut]
    total: int
    page: int
    page_size: int


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    filing_id: str
    title: str
    source_url: str
    sha256: str
    source_kind: str
    fetched_at: datetime
    parser_version: str
    role: str
    source_metadata: dict = Field(default_factory=dict)


class DocumentsOut(BaseModel):
    items: list[DocumentOut]
    retrieval_mode: str


class CitationOut(BaseModel):
    citation_id: str
    filing_id: str
    accession: str
    form_type: str
    filing_date: str
    section: str
    source_url: str
    excerpt: str
    chunk_id: str
    document_sha256: str
    location: dict


class EvidenceOut(BaseModel):
    items: list[CitationOut]
    retrieval_mode: str
    scope_note: str


class ReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    job_id: str
    cik: str
    task_type: str
    title: str
    language: str
    created_at: datetime
    provider: str
    model_id: str
    source_kind: str
    content: dict
    usage: dict
    duration_ms: int


def _filing(session, identifier):
    filing = session.get(Filing, identifier)
    if not filing:
        raise AppError("FILING_NOT_FOUND", "未找到该申报。", 404)
    return filing


@router.get("/filings/{filing_id}/documents", response_model=DocumentsOut)
def documents(filing_id: str, session: Session = Depends(get_session)):
    _filing(session, filing_id)
    return {"items": list_filing_documents(session, filing_id), "retrieval_mode": public_mode(session)}


@router.post("/filings/{filing_id}/documents/sync", status_code=202, response_model=JobAccepted)
def prepare_documents(filing_id: str, background: BackgroundTasks, body: EmptyRequest | None = None,
                      owner: str = Depends(require_owner), session: Session = Depends(get_session)):
    filing = _filing(session, filing_id)
    company = session.get(Company, filing.company_id)
    context = {"filing_ids": [filing.id], "source_url": filing.source_url, "parser_version": "html-v1"}
    return submit(session, background, owner=owner, company=company, task_type="document_sync",
                  context=context, digest=fingerprint(context, owner), force=True)


@router.get("/documents/jobs/{job_id}", response_model=JobOut)
def document_job(job_id: str, owner: str = Depends(require_owner), session: Session = Depends(get_session)):
    job = find_job(session, owner, job_id)
    if job.task_type != "document_sync":
        raise AppError("JOB_NOT_FOUND", "未找到此文档任务。", 404)
    return job_out(job)


@router.get("/companies/{cik}/evidence", response_model=EvidenceOut)
def evidence(cik: str, q: str = Query(min_length=1, max_length=500), filing_id: str | None = None,
             session: Session = Depends(get_session)):
    company = session.scalar(select(Company).where(Company.cik == validate_cik(cik)))
    if not company:
        raise AppError("COMPANY_NOT_FOUND", "本地尚无此公司，请先导入快照或刷新 SEC 映射。", 404)
    query = select(Filing.id).where(Filing.company_id == company.id)
    if filing_id:
        filing = _filing(session, filing_id)
        if filing.company_id != company.id:
            raise AppError("FILING_NOT_FOUND", "未找到所选公司的申报。", 404)
        query = query.where(Filing.id == filing_id)
    else:
        ready = select(Document.filing_id).where(Document.source_kind == get_settings().data_mode)
        query = query.where(Filing.id.in_(ready)).order_by(Filing.filing_date.desc(), Filing.id).limit(10)
    ids = session.scalars(query).all()
    rows = retrieve_chunks(session, ids, q.strip(), limit=8) if ids and q.strip() else []
    for index, row in enumerate(rows):
        row["citation_id"] = f"CIT-{index + 1:03d}"
    mode = "section_fallback" if any("fallback" in str(row.get("retrieval_mode")) for row in rows) else public_mode(session)
    return {"items": rows, "retrieval_mode": mode,
            "scope_note": "仅检索指定申报的已保存正文。" if filing_id else "仅检索最近十份已有正文的申报，可选择指定申报缩小范围。"}


@router.post("/research/jobs", status_code=202, response_model=JobAccepted)
def research(body: ResearchRequest, background: BackgroundTasks,
             owner: str = Depends(require_owner), session: Session = Depends(get_session)):
    company, filings = select_filings(session, body.cik, body.task_type, body.filing_ids)
    config = get_verified_config(session, owner, body.config_id)
    context = freeze_context(session, company, filings, body)
    build_prompt(context)  # Reject oversized contexts before submitting a paid task.
    return submit(session, background, owner=owner, company=company, task_type=body.task_type,
                  context=context, digest=fingerprint(context, owner, config), config=config, force=body.force)


@router.get("/research/jobs/{job_id}", response_model=JobOut)
def research_job(job_id: str, owner: str = Depends(require_owner), session: Session = Depends(get_session)):
    job = find_job(session, owner, job_id)
    if job.task_type == "document_sync":
        raise AppError("JOB_NOT_FOUND", "未找到此研究任务。", 404)
    return job_out(job)


@router.post("/research/jobs/{job_id}/cancel", response_model=JobOut)
def cancel(job_id: str, body: EmptyRequest, owner: str = Depends(require_owner), session: Session = Depends(get_session)):
    job = session.scalar(select(ResearchJob).where(ResearchJob.id == job_id, ResearchJob.owner_id == owner).with_for_update())
    if not job:
        raise AppError("JOB_NOT_FOUND", "未找到此研究任务。", 404)
    if job.status in {"queued", "running"}:
        job.status, job.active_key, job.finished_at = "cancelled", None, now()
        session.commit()
    return job_out(job)


@router.post("/research/jobs/{job_id}/retry", status_code=202, response_model=JobAccepted)
def retry(job_id: str, background: BackgroundTasks, body: EmptyRequest,
          owner: str = Depends(require_owner), session: Session = Depends(get_session)):
    previous = find_job(session, owner, job_id)
    if previous.status not in {"failed", "cancelled"}:
        raise AppError("RESEARCH_NOT_RETRYABLE", "只有失败或已取消的任务可以重试。", 409)
    config = None
    if previous.task_type != "document_sync":
        config = get_verified_config(session, owner, previous.config_id)
        if config.version != previous.config_version or config.verified_binding != previous.config_binding:
            raise AppError("CONFIG_CHANGED", "模型配置已变更，请重新验证并提交研究。", 409)
    company = session.get(Company, previous.company_id)
    return submit(session, background, owner=owner, company=company, task_type=previous.task_type,
                  context=previous.input_json, digest=previous.fingerprint, config=config,
                  force=True, parent_job_id=previous.id)


@router.get("/research/history", response_model=HistoryOut)
def history(page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=50),
            owner: str = Depends(require_owner), session: Session = Depends(get_session)):
    conditions = [ResearchJob.owner_id == owner, ResearchJob.task_type != "document_sync"]
    total = session.scalar(select(func.count()).select_from(ResearchJob).where(*conditions))
    jobs = session.scalars(select(ResearchJob).where(*conditions).order_by(
        ResearchJob.created_at.desc(), ResearchJob.id.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [job_out(job) for job in jobs], "total": total, "page": page, "page_size": page_size}


def find_report(session, owner, report_id):
    report = session.scalar(select(ResearchReport).where(ResearchReport.id == report_id, ResearchReport.owner_id == owner))
    if not report:
        raise AppError("REPORT_NOT_FOUND", "未找到此研究报告。", 404)
    return report


@router.get("/research/reports/{report_id}", response_model=ReportOut)
def report(report_id: str, owner: str = Depends(require_owner), session: Session = Depends(get_session)):
    record = find_report(session, owner, report_id)
    record.created_at = aware(record.created_at)
    return record


@router.get("/research/reports/{report_id}/download")
def download(report_id: str, format: Literal["md"] = "md", owner: str = Depends(require_owner), session: Session = Depends(get_session)):
    record = find_report(session, owner, report_id)
    return Response(record.markdown, media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="finsight-{record.id}.md"'})
