import logging
from contextlib import asynccontextmanager
from datetime import date
from typing import Literal
from uuid import uuid4

from fastapi import BackgroundTasks, Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from redis import Redis
from rq import Queue
from rq.serializers import JSONSerializer
from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException

from app.api.cn import router as cn_router
from app.api.cn_documents import router as cn_documents_router
from app.api.cn_research import router as cn_research_router
from app.api.llm import router as llm_router
from app.api.overview import router as overview_router
from app.api.research import router as research_router
from app.core.auth import require_owner
from app.core.config import get_settings
from app.core.errors import AppError
from app.db.models import (
    Company,
    Filing,
    SourceSnapshot,
    SyncJob,
    TickerAlias,
    now,
)
from app.db.session import ensure_dataset, get_engine, get_session, session_factory
from app.schemas import (
    CompanyOut,
    ErrorResponse,
    FilingOut,
    FilingsOut,
    HealthOut,
    JobAccepted,
    JobOut,
    MetricsOut,
    SearchOut,
    SourceOut,
    SyncRequest,
)
from app.services.llm_configs import status as llm_status
from app.services.metrics import version_metrics
from app.services.sec.client import validate_cik
from app.services.sync import aware, run_sync

logger = logging.getLogger("finsight.api")


@asynccontextmanager
async def lifespan(_app):
    # Schema is exclusively managed by Alembic; missing migration fails startup explicitly.
    with session_factory()() as session:
        ensure_dataset(session)
    yield


app = FastAPI(
    title="FinSight AI", version="0.1.0", lifespan=lifespan,
    responses={400: {"model": ErrorResponse}, 422: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
app.add_middleware(
    CORSMiddleware, allow_origins=get_settings().cors_origins.split(","),
    allow_methods=["GET", "POST"], allow_headers=["Content-Type", "X-Admin-Token"],
)
app.include_router(llm_router)
app.include_router(overview_router)
app.include_router(research_router)
app.include_router(cn_router)
app.include_router(cn_documents_router)
app.include_router(cn_research_router)


@app.middleware("http")
async def request_id(request: Request, call_next):
    request.state.request_id = str(uuid4())
    try:
        response = await call_next(request)
    except Exception as exc:
        # Starlette re-raises unhandled errors after its 500 handler, allowing
        # the server to log raw exception strings (including SQL parameters or
        # upstream secrets). Handle before that boundary; retain only a type
        # and correlation ID for diagnostics, never exception text/tracebacks.
        logger.error("Request failed request_id=%s error_type=%s", request.state.request_id, type(exc).__name__)
        response = error_response(request, "INTERNAL_ERROR", "服务暂时不可用，请稍后重试。", 500)
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


def error_response(request, code, message, status, details=None):
    return JSONResponse(status_code=status, content={
        "error": {"code": code, "message": message, "details": details or {}},
        "request_id": getattr(request.state, "request_id", str(uuid4())),
    })


@app.exception_handler(AppError)
async def app_error(request: Request, exc: AppError):
    return error_response(request, exc.code, exc.message, exc.status, exc.details)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    # Pydantic's raw errors can contain input secrets; never return them verbatim.
    fields = [{"location": list(e["loc"]), "type": e["type"]} for e in exc.errors()]
    return error_response(request, "VALIDATION_ERROR", "请求参数无效。", 422, {"fields": fields})


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    return error_response(request, "HTTP_ERROR", "请求路径或方法不可用。", exc.status_code)


@app.exception_handler(Exception)
async def internal_error(request: Request, _exc: Exception):
    return error_response(request, "INTERNAL_ERROR", "服务暂时不可用，请稍后重试。", 500)


def find_company(session, cik):
    company = session.scalar(select(Company).where(Company.cik == validate_cik(cik)))
    if company is None:
        raise AppError("COMPANY_NOT_FOUND", "本地尚无此公司，请先导入快照或刷新 SEC 映射。", 404)
    return company


def company_out(company):
    out = CompanyOut.model_validate(company)
    if out.last_synced_at:
        out.last_synced_at = aware(out.last_synced_at)
    out.is_stale = not out.last_synced_at or (now() - out.last_synced_at).total_seconds() > get_settings().cache_ttl_seconds
    return out


@app.get("/health", response_model=HealthOut)
@app.get("/api/v1/health", response_model=HealthOut)
def health():
    settings = get_settings()
    database, redis_status, ai_available = "unavailable", "unavailable", False
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
        database = "ok"
        with session_factory()() as session:
            ai_available = llm_status(session)["available"]
    except Exception:
        pass
    try:
        Redis.from_url(settings.redis_url, socket_connect_timeout=1, socket_timeout=1).ping()
        redis_status = "ok"
    except Exception:
        pass
    return HealthOut(
        status="ok" if database == "ok" and redis_status == "ok" else "degraded",
        source_kind=settings.data_mode, database=database, redis=redis_status, task_mode=settings.task_mode,
        ai_available=ai_available,
    )


@app.get("/health/live")
def live():
    return {"status": "ok"}


@app.get("/health/ready", response_model=HealthOut)
def ready():
    result = health()
    if result.database != "ok" or (get_settings().task_mode == "rq" and result.redis != "ok"):
        raise AppError("NOT_READY", "数据库或任务队列尚未就绪。", 503)
    return result


@app.get("/api/v1/companies/search", response_model=SearchOut)
def search(q: str = Query(default="", max_length=100), market: str = "US", session: Session = Depends(get_session)):
    if market != "US":
        raise AppError("MARKET_NOT_SUPPORTED", "当前仅支持美国 SEC 申报主体。", 422)
    query = select(Company).where(Company.market == market)
    term = q.strip()
    if term:
        alias_ids = select(TickerAlias.company_id).where(TickerAlias.ticker.ilike(term))
        query = query.where(or_(
            Company.name.icontains(term, autoescape=True), Company.ticker.ilike(term),
            Company.cik == term.zfill(10), Company.id.in_(alias_ids),
        ))
    companies = session.scalars(query.order_by(Company.ticker).limit(20)).all()
    return SearchOut(items=[company_out(c) for c in companies], source_kind=get_settings().data_mode)


@app.get("/api/v1/companies/{cik}", response_model=CompanyOut)
def company(cik: str, session: Session = Depends(get_session)):
    return company_out(find_company(session, cik))


@app.get("/api/v1/companies/{cik}/metrics", response_model=MetricsOut)
def metrics(
    cik: str,
    frequency: Literal["annual", "quarterly"] = "annual",
    years: int = Query(default=5, ge=1, le=10),
    selection_policy: Literal["latest_restated", "as_of", "as_filed"] = "latest_restated",
    as_of: date | None = None,
    accession: str | None = Query(default=None, pattern=r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$"),
    session: Session = Depends(get_session),
):
    valid = (
        selection_policy == "latest_restated" and as_of is None and accession is None
        or selection_policy == "as_of" and as_of is not None and accession is None
        or selection_policy == "as_filed" and accession is not None and as_of is None
    )
    if not valid:
        raise AppError("VERSION_PARAMETERS_INVALID", "请选择最新口径，或为截止日期/指定申报口径提供对应参数。", 422)
    record = find_company(session, cik)
    cutoff = as_of.isoformat() if as_of else None
    items = version_metrics(
        session, record, frequency=frequency, years=years,
        selection_policy=selection_policy, as_of=cutoff, accession=accession,
    )
    return MetricsOut(
        items=items, source_kind=record.source_kind, frequency=frequency,
        selection_policy=selection_policy, as_of=cutoff, accession=accession,
    )


def filing_page(session, cik, types, page, page_size):
    company = find_company(session, cik)
    conditions = [Filing.company_id == company.id, Filing.form_type.in_(types)]
    total = session.scalar(select(func.count()).select_from(Filing).where(*conditions))
    rows = session.scalars(select(Filing).where(*conditions).order_by(Filing.filing_date.desc(), Filing.accession_number.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return FilingsOut(items=rows, total=total, page=page, page_size=page_size, source_kind=company.source_kind)


@app.get("/api/v1/companies/{cik}/filings", response_model=FilingsOut)
def filings(cik: str, type: str | None = None, page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100), session: Session = Depends(get_session)):
    allowed = {"10-K", "10-K/A", "10-Q", "10-Q/A"}
    if type and type not in allowed:
        raise AppError("FORM_NOT_SUPPORTED", "财报列表仅支持 10-K/10-Q 及其修订。", 422)
    return filing_page(session, cik, [type] if type else allowed, page, page_size)


@app.get("/api/v1/companies/{cik}/events", response_model=FilingsOut)
def events(cik: str, type: str = "8-K", page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100), session: Session = Depends(get_session)):
    if type not in {"8-K", "8-K/A"}:
        raise AppError("FORM_NOT_SUPPORTED", "事件列表仅支持 8-K。", 422)
    return filing_page(session, cik, ["8-K", "8-K/A"] if type == "8-K" else [type], page, page_size)


@app.get("/api/v1/filings/{filing_id}", response_model=FilingOut)
def filing(filing_id: str, session: Session = Depends(get_session)):
    record = session.get(Filing, filing_id)
    if not record:
        raise AppError("FILING_NOT_FOUND", "未找到该申报。", 404)
    return record


@app.get("/api/v1/sources/{source_id}", response_model=SourceOut)
def source(source_id: str, session: Session = Depends(get_session)):
    record = session.get(SourceSnapshot, source_id)
    if not record:
        raise AppError("SOURCE_NOT_FOUND", "未找到该来源快照。", 404)
    out = SourceOut.model_validate(record)
    out.fetched_at = aware(out.fetched_at)
    return out


@app.post("/api/v1/companies/{cik}/sync", status_code=202, response_model=JobAccepted)
def sync_company(cik: str, background: BackgroundTasks, body: SyncRequest | None = None, owner: str = Depends(require_owner), session: Session = Depends(get_session)):
    cik = validate_cik(cik)
    existing = session.scalar(select(SyncJob).where(SyncJob.active_key == cik))
    if existing:
        if (now() - aware(existing.created_at)).total_seconds() < 900:
            return JobAccepted(job_id=existing.id, status=existing.status, reused=True)
        existing.status, existing.active_key = "failed", None
        existing.error_code, existing.error_message = "WORKER_TIMEOUT", "任务超过恢复期限，可重新提交。"
        existing.finished_at = now()
        session.commit()
    job = SyncJob(cik=cik, active_key=cik, owner_id=owner, force=bool(body and body.force))
    session.add(job)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.scalar(select(SyncJob).where(SyncJob.active_key == cik))
        if existing:
            return JobAccepted(job_id=existing.id, status=existing.status, reused=True)
        raise AppError("SYNC_CONFLICT", "同步状态发生变化，请重试。", 409) from None
    if get_settings().task_mode == "inline":
        background.add_task(run_sync, job.id)
    else:
        try:
            connection = Redis.from_url(get_settings().redis_url, socket_connect_timeout=2, socket_timeout=2)
            Queue("finsight", connection=connection, serializer=JSONSerializer).enqueue(
                "app.services.sync.run_sync", job.id, job_id=job.id,
                job_timeout=300, result_ttl=3600, failure_ttl=86400,
            )
        except Exception:
            job.status, job.active_key, job.finished_at = "failed", None, now()
            job.error_code, job.error_message = "QUEUE_UNAVAILABLE", "任务队列不可用，请稍后重试。"
            session.commit()
            raise AppError("QUEUE_UNAVAILABLE", job.error_message, 503, {"job_id": job.id}) from None
    return JobAccepted(job_id=job.id, status="queued", reused=False)


@app.get("/api/v1/sync/jobs/{job_id}", response_model=JobOut)
def sync_job(job_id: str, session: Session = Depends(get_session)):
    job = session.get(SyncJob, job_id)
    if not job:
        raise AppError("JOB_NOT_FOUND", "未找到该同步任务。", 404)
    return JobOut(
        id=job.id, job_id=job.id, cik=job.cik, status=job.status,
        error_code=job.error_code, error_message=job.error_message, attempts=job.attempts,
        created_at=aware(job.created_at), started_at=aware(job.started_at) if job.started_at else None,
        finished_at=aware(job.finished_at) if job.finished_at else None,
    )
