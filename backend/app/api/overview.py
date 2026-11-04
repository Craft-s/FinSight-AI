from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, ConfigDict, Field, model_validator
from redis import Redis
from rq import Queue
from rq.serializers import JSONSerializer
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.auth import require_owner
from app.core.config import get_settings
from app.core.errors import AppError
from app.db.models import Company, now
from app.db.overview_models import OverviewJob
from app.db.session import get_session
from app.schemas import JobAccepted
from app.services.llm_configs import get_verified_config
from app.services.overview import context_digest, frozen_context, run_overview
from app.services.sec.client import validate_cik
from app.services.sync import aware

router = APIRouter(prefix="/api/v1", tags=["AI metric overview"])


class OverviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config_id: str = Field(min_length=1, max_length=36)
    frequency: Literal["annual", "quarterly"] = "annual"
    selection_policy: Literal["latest_restated", "as_of", "as_filed"] = "latest_restated"
    as_of: date | None = None
    accession: str | None = Field(default=None, pattern=r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")
    language: Literal["zh-CN", "en-US"] = "zh-CN"

    @model_validator(mode="after")
    def version_parameters(self):
        valid = (
            self.selection_policy == "latest_restated" and self.as_of is None and self.accession is None
            or self.selection_policy == "as_of" and self.as_of is not None and self.accession is None
            or self.selection_policy == "as_filed" and self.accession is not None and self.as_of is None
        )
        if not valid:
            raise ValueError("inconsistent version parameters")
        return self


class OverviewJobOut(BaseModel):
    id: str
    job_id: str
    cik: str
    status: Literal["queued", "running", "succeeded", "failed"]
    error_code: str | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    result: dict | None


@router.post("/companies/{cik}/overview", status_code=202, response_model=JobAccepted)
def create_overview(cik: str, body: OverviewRequest, background: BackgroundTasks,
                    owner: str = Depends(require_owner), session: Session = Depends(get_session)):
    cik = validate_cik(cik)
    company = session.scalar(select(Company).where(Company.cik == cik))
    if not company:
        raise AppError("COMPANY_NOT_FOUND", "本地尚无此公司，请先导入快照或刷新 SEC 映射。", 404)
    config = get_verified_config(session, owner, body.config_id)
    context = frozen_context(session, company, body)
    context["config_binding"] = config.verified_binding
    digest = context_digest(context, config)
    existing = session.scalar(select(OverviewJob).where(OverviewJob.active_key == digest))
    if existing:
        if (now() - aware(existing.created_at)).total_seconds() < 900:
            return JobAccepted(job_id=existing.id, status=existing.status, reused=True)
        existing.status, existing.active_key, existing.finished_at = "failed", None, now()
        existing.error_code, existing.error_message = "WORKER_TIMEOUT", "任务超过恢复期限，可重新提交。"
        session.commit()
    cached = session.scalar(select(OverviewJob).where(
        OverviewJob.owner_id == owner, OverviewJob.cache_key == digest, OverviewJob.status == "succeeded",
    ).order_by(OverviewJob.created_at.desc()).limit(1))
    if cached:
        return JobAccepted(job_id=cached.id, status="succeeded", reused=True)
    job = OverviewJob(owner_id=owner, company_id=company.id, cik=cik, config_id=config.id,
                      config_version=config.version, cache_key=digest, active_key=digest, input_json=context)
    session.add(job)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.scalar(select(OverviewJob).where(OverviewJob.active_key == digest))
        if existing:
            return JobAccepted(job_id=existing.id, status=existing.status, reused=True)
        raise AppError("OVERVIEW_CONFLICT", "概览状态发生变化，请重试。", 409) from None
    if get_settings().task_mode == "inline":
        background.add_task(run_overview, job.id)
    else:
        try:
            connection = Redis.from_url(get_settings().redis_url, socket_connect_timeout=2, socket_timeout=2)
            Queue("finsight", connection=connection, serializer=JSONSerializer).enqueue(
                "app.services.overview.run_overview", job.id, job_id=job.id,
                job_timeout=240, result_ttl=3600, failure_ttl=86400,
            )
        except Exception:
            job.status, job.active_key, job.finished_at = "failed", None, now()
            job.error_code, job.error_message = "QUEUE_UNAVAILABLE", "任务队列不可用，请稍后重试。"
            session.commit()
            raise AppError("QUEUE_UNAVAILABLE", job.error_message, 503, {"job_id": job.id}) from None
    return JobAccepted(job_id=job.id, status="queued", reused=False)


@router.get("/overview/jobs/{job_id}", response_model=OverviewJobOut)
def get_overview(job_id: str, owner: str = Depends(require_owner), session: Session = Depends(get_session)):
    job = session.scalar(select(OverviewJob).where(OverviewJob.id == job_id, OverviewJob.owner_id == owner))
    if not job:
        raise AppError("JOB_NOT_FOUND", "未找到该概览任务。", 404)
    return OverviewJobOut(
        id=job.id, job_id=job.id, cik=job.cik, status=job.status,
        error_code=job.error_code, error_message=job.error_message,
        created_at=aware(job.created_at), started_at=aware(job.started_at) if job.started_at else None,
        finished_at=aware(job.finished_at) if job.finished_at else None, result=job.result_json,
    )
