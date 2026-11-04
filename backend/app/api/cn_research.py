"""Owner-bound A-share PDF research routes, independent of SEC identities."""
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.cn import cn_session
from app.core.auth import require_owner
from app.core.errors import AppError
from app.db.cn_models import CnCompany
from app.db.cn_research_models import CnResearchJob, CnResearchReport
from app.db.models import now
from app.services.cn.research import checked_config, find_job, freeze_context, job_out, submit
from app.services.llm_configs import get_verified_config
from app.services.sync import aware

router = APIRouter(prefix="/api/v1/cn", tags=["A-share PDF research"])


class CnResearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(pattern=r"^[0-9]{6}\.(SH|SZ|BJ)$")
    task_type: Literal["summary", "comparison", "question"]
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
        if len(set(self.filing_ids)) != expected or len(self.filing_ids) != expected:
            raise ValueError("Select distinct filings for the task type")
        return self


class EmptyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


@router.post("/research/jobs", status_code=202)
def create(body: CnResearchRequest, background: BackgroundTasks, owner: str = Depends(require_owner),
           session: Session = Depends(cn_session)):
    config = get_verified_config(session, owner, body.config_id)
    company, context = freeze_context(session, body.symbol, body)
    return submit(session, background, owner=owner, company=company, task_type=body.task_type,
                  context=context, config=config, force=body.force)


@router.get("/research/jobs/{job_id}")
def job(job_id: str, owner: str = Depends(require_owner), session: Session = Depends(cn_session)):
    return job_out(find_job(session, owner, job_id))


@router.post("/research/jobs/{job_id}/cancel")
def cancel(job_id: str, body: EmptyRequest, owner: str = Depends(require_owner), session: Session = Depends(cn_session)):
    record = find_job(session, owner, job_id, lock=True)
    if record.status in {"queued", "running"}:
        record.status, record.active_key, record.finished_at = "cancelled", None, now()
        session.commit()
    return job_out(record)


@router.post("/research/jobs/{job_id}/retry", status_code=202)
def retry(job_id: str, body: EmptyRequest, background: BackgroundTasks,
          owner: str = Depends(require_owner), session: Session = Depends(cn_session)):
    previous = find_job(session, owner, job_id)
    if previous.status not in {"failed", "cancelled"}:
        raise AppError("RESEARCH_NOT_RETRYABLE", "只有失败或已取消的任务可以重试。", 409)
    config = checked_config(session, previous)
    company = session.get(CnCompany, previous.symbol)
    if not company:
        raise AppError("CN_COMPANY_NOT_FOUND", "未找到该A股公司。", 404)
    return submit(session, background, owner=owner, company=company, task_type=previous.task_type,
                  context=previous.input_json, config=config, force=True, parent_job_id=previous.id)


@router.get("/research/history")
def history(page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=50),
            owner: str = Depends(require_owner), session: Session = Depends(cn_session)):
    conditions = [CnResearchJob.owner_id == owner]
    total = session.scalar(select(func.count()).select_from(CnResearchJob).where(*conditions))
    jobs = session.scalars(select(CnResearchJob).where(*conditions).order_by(
        CnResearchJob.created_at.desc(), CnResearchJob.id.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [job_out(record) for record in jobs], "total": total, "page": page, "page_size": page_size}


def find_report(session, owner, identifier):
    record = session.scalar(select(CnResearchReport).where(CnResearchReport.id == identifier, CnResearchReport.owner_id == owner))
    if not record:
        raise AppError("REPORT_NOT_FOUND", "未找到此A股研究报告。", 404)
    return record


@router.get("/research/reports/{report_id}")
def report(report_id: str, owner: str = Depends(require_owner), session: Session = Depends(cn_session)):
    record = find_report(session, owner, report_id)
    return {"id": record.id, "job_id": record.job_id, "market": "CN", "symbol": record.symbol,
            "task_type": record.task_type, "title": record.title, "language": record.language,
            "created_at": aware(record.created_at), "provider": record.provider, "model_id": record.model_id,
            "source_kind": record.source_kind, "content": record.content, "usage": record.usage, "duration_ms": record.duration_ms}


@router.get("/research/reports/{report_id}/download")
def download(report_id: str, format: Literal["md"] = "md", owner: str = Depends(require_owner), session: Session = Depends(cn_session)):
    record = find_report(session, owner, report_id)
    return Response(record.markdown, media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="finsight-cn-{record.id}.md"'})
