"""Reuse RQ for persistent research work; cancellation prevents publication, not upstream billing."""
from redis import Redis
from rq import Queue
from rq.serializers import JSONSerializer
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.errors import AppError
from app.db.llm_models import LLMConfig
from app.db.models import now
from app.db.research_models import ResearchJob, ResearchReport
from app.db.session import ensure_dataset, session_factory
from app.schemas import JobAccepted
from app.services.llm_configs import get_verified_config, provider_for_config
from app.services.research.prompts import build_prompt
from app.services.research.validator import validate_result
from app.services.research.writer import write_markdown
from app.services.sync import aware

TITLES = {"summary": ("财报总结", "Filing summary"), "comparison": ("两期财报对比", "Filing comparison"),
          "event": ("事件研究", "Event research"), "question": ("财报问答", "Filing question")}


def job_out(job):
    return {"id": job.id, "job_id": job.id, "cik": job.cik, "task_type": job.task_type,
            "status": job.status, "error_code": job.error_code, "error_message": job.error_message,
            "report_id": job.report_id, "created_at": aware(job.created_at),
            "started_at": aware(job.started_at) if job.started_at else None,
            "finished_at": aware(job.finished_at) if job.finished_at else None}


def find_job(session, owner, identifier):
    job = session.scalar(select(ResearchJob).where(ResearchJob.id == identifier, ResearchJob.owner_id == owner))
    if not job:
        raise AppError("JOB_NOT_FOUND", "未找到此研究任务。", 404)
    return job


def submit(session, background, *, owner, company, task_type, context, digest, config=None,
           force=False, parent_job_id=None):
    existing = session.scalar(select(ResearchJob).where(ResearchJob.active_key == digest))
    if existing:
        if (now() - aware(existing.created_at)).total_seconds() < 1200:
            return JobAccepted(job_id=existing.id, status=existing.status, reused=True)
        existing.status, existing.active_key, existing.finished_at = "failed", None, now()
        existing.error_code, existing.error_message = "WORKER_TIMEOUT", "任务超过恢复期限，可重新提交。"
        session.commit()
    if not force:
        cached = session.scalar(select(ResearchJob).where(
            ResearchJob.owner_id == owner, ResearchJob.fingerprint == digest, ResearchJob.status == "succeeded",
        ).order_by(ResearchJob.created_at.desc()).limit(1))
        if cached:
            return JobAccepted(job_id=cached.id, status="succeeded", reused=True)
    job = ResearchJob(owner_id=owner, company_id=company.id, cik=company.cik, task_type=task_type,
                      input_json=context, fingerprint=digest, active_key=digest, parent_job_id=parent_job_id,
                      config_id=config.id if config else None, config_version=config.version if config else None,
                      config_binding=config.verified_binding if config else None)
    session.add(job)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.scalar(select(ResearchJob).where(ResearchJob.active_key == digest))
        if existing:
            return JobAccepted(job_id=existing.id, status=existing.status, reused=True)
        raise AppError("RESEARCH_CONFLICT", "研究任务状态发生变化，请重试。", 409) from None
    if get_settings().task_mode == "inline":
        background.add_task(run_research_job, job.id)
    else:
        try:
            connection = Redis.from_url(get_settings().redis_url, socket_connect_timeout=2, socket_timeout=2)
            Queue("finsight", connection=connection, serializer=JSONSerializer).enqueue(
                "app.services.research.jobs.run_research_job", job.id, job_id=job.id,
                job_timeout=600 if task_type == "document_sync" else 240,
                result_ttl=3600, failure_ttl=86400,
            )
        except Exception:
            job.status, job.active_key, job.finished_at = "failed", None, now()
            job.error_code, job.error_message = "QUEUE_UNAVAILABLE", "任务队列不可用，请稍后重试。"
            session.commit()
            raise AppError("QUEUE_UNAVAILABLE", job.error_message, 503, {"job_id": job.id}) from None
    return JobAccepted(job_id=job.id, status="queued", reused=False)


def _checked_config(session, job, *, lock=False):
    if lock:
        session.scalar(select(LLMConfig).where(LLMConfig.id == job.config_id,
                                             LLMConfig.owner_id == job.owner_id).with_for_update())
    config = get_verified_config(session, job.owner_id, job.config_id)
    if config.version != job.config_version or config.verified_binding != job.config_binding:
        raise AppError("CONFIG_CHANGED", "模型配置已变更，请重新验证并提交研究。", 409)
    return config


def run_research_job(identifier):
    with session_factory()() as session:
        ensure_dataset(session)
        job = session.scalar(select(ResearchJob).where(ResearchJob.id == identifier).with_for_update())
        if not job or job.status != "queued":
            return
        job.status, job.started_at, job.attempts = "running", now(), job.attempts + 1
        session.commit()
        context, task_type = job.input_json, job.task_type
    try:
        if task_type == "document_sync":
            from app.services.documents.service import ensure_filing_documents
            with session_factory()() as session:
                for filing_id in context["filing_ids"]:
                    ensure_filing_documents(session, filing_id)
                job = session.scalar(select(ResearchJob).where(ResearchJob.id == identifier).with_for_update())
                if job.status == "running":
                    job.status, job.active_key, job.finished_at = "succeeded", None, now()
                    session.commit()
                else:
                    session.rollback()
            return
        with session_factory()() as session:
            config = _checked_config(session, job)
            provider = provider_for_config(config)
        system, prompt = build_prompt(context)
        generated = provider.generate_structured(config.model_id, system, prompt, options=config.options_json)
        content = validate_result(generated["content"], context)
        report_context = {**context, "generation": {"provider": config.provider, "model_id": config.model_id,
                                                   "config_version": config.version}}
        markdown = write_markdown(content, report_context)
        with session_factory()() as session:
            _checked_config(session, job, lock=True)
            current = session.scalar(select(ResearchJob).where(ResearchJob.id == identifier).with_for_update())
            if current.status != "running":
                return
            title = TITLES[task_type][1 if context["language"] == "en-US" else 0]
            report = ResearchReport(job_id=current.id, owner_id=current.owner_id, cik=current.cik,
                                    task_type=task_type, title=title, language=context["language"],
                                    provider=config.provider, model_id=config.model_id,
                                    source_kind=context["source_kind"], content=content, markdown=markdown,
                                    usage=generated.get("usage", {}), duration_ms=generated.get("duration_ms", 0))
            session.add(report)
            session.flush()
            current.status, current.active_key, current.finished_at = "succeeded", None, now()
            current.report_id = report.id
            session.commit()
    except Exception as exc:
        with session_factory()() as session:
            current = session.scalar(select(ResearchJob).where(ResearchJob.id == identifier).with_for_update())
            if not current or current.status != "running":
                return
            current.status, current.active_key, current.finished_at = "failed", None, now()
            current.error_code = exc.code if isinstance(exc, AppError) else "RESEARCH_FAILED"
            current.error_message = exc.message if isinstance(exc, AppError) else "研究处理失败，请检查文档和模型配置后重试。"
            session.commit()
