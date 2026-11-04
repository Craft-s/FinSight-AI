"""Durable A-share research over immutable, page-bound PDF evidence only."""
from redis import Redis
from rq import Queue
from rq.serializers import JSONSerializer
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.errors import AppError
from app.db.cn_models import CnCompany
from app.db.cn_research_models import CnResearchJob, CnResearchReport
from app.db.llm_models import LLMConfig
from app.db.models import now
from app.db.session import session_factory
from app.schemas import JobAccepted
from app.services.cn.service import ensure_cn_dataset, mode
from app.services.llm_configs import get_verified_config, provider_for_config
from app.services.research.context import fingerprint
from app.services.research.prompts import PROMPT_VERSION, build_prompt
from app.services.research.validator import SCHEMA_VERSION, context_evidence, validate_result
from app.services.research.writer import write_markdown
from app.services.sync import aware

TITLES = {"summary": ("A股财报总结", "A-share filing summary"),
          "comparison": ("A股两期财报对比", "A-share filing comparison"),
          "question": ("A股财报问答", "A-share filing question")}
PERIODIC = {"annual", "semiannual", "q1", "q3"}
QUERIES = {"summary": "营业 收入 利润 现金 风险 经营 revenue risk cash",
           "comparison": "营业 收入 利润 现金 风险 经营 revenue income cash"}
CN_PROMPT_VERSION = PROMPT_VERSION + ":cn-pdf-v1"
CN_SCHEMA_VERSION = SCHEMA_VERSION + ":cn-pdf-v1"


def checked_context(context, *, symbol=None, task_type=None):
    if (context.get("market") != "CN" or context.get("source_kind") != mode()
            or context.get("prompt_version") != CN_PROMPT_VERSION
            or context.get("schema_version") != CN_SCHEMA_VERSION
            or symbol is not None and context.get("company", {}).get("symbol") != symbol
            or task_type is not None and context.get("task_type") != task_type
            or context.get("task_type") not in TITLES
            or not context.get("citations")
            or len(context.get("filings", [])) != (2 if context.get("task_type") == "comparison" else 1)):
        raise AppError("RESEARCH_CONTEXT_INVALID", "A股研究模式或证据版本已变化，请重新提交研究。", 409)
    context_evidence(context)


def freeze_context(session, symbol, body):
    from app.db.cn_document_models import CnDocument
    from app.services.cn.documents import find_filing, list_documents, retrieve_chunks

    company = session.get(CnCompany, symbol)
    if not company or company.source_kind != mode():
        raise AppError("CN_COMPANY_NOT_FOUND", "未找到该A股公司，请先更新目录。", 404)
    filings, citations, versions = [], [], []
    query = body.question.strip() if body.task_type == "question" else QUERIES[body.task_type]
    for identifier in body.filing_ids:
        filing = find_filing(session, symbol, identifier)
        if body.task_type in {"summary", "comparison"} and filing["form_type"] not in PERIODIC:
            raise AppError("CN_RESEARCH_FILINGS_INVALID", "财报总结和对比仅支持定期报告。", 422)
        if not list_documents(session, symbol, identifier):
            raise AppError("DOCUMENT_NOT_READY", "所选A股公告尚无已解析PDF正文，请先准备文档。", 409)
        chunks = retrieve_chunks(session, symbol, [identifier], query, limit=4 if len(body.filing_ids) == 2 else 8)
        if not chunks and body.task_type != "question":
            chunks = retrieve_chunks(session, symbol, [identifier], "", limit=4 if len(body.filing_ids) == 2 else 8)
        if not chunks:
            raise AppError("EVIDENCE_NOT_FOUND", "已保存PDF中未找到相关证据，请调整问题或准备正文。", 409)
        filings.append({key: filing.get(key) for key in ("id", "title", "form_type", "filing_date", "report_date", "source_url")})
        for chunk in chunks:
            document = session.get(CnDocument, chunk["document_id"])
            if (not document or document.symbol != symbol or document.filing_id != identifier
                    or document.source_kind != mode() or document.sha256 != chunk["document_sha256"]):
                raise AppError("RESEARCH_CONTEXT_INVALID", "A股研究证据版本无效，请重新准备PDF。", 409)
            version = {"id": document.id, "sha256": document.sha256, "parser_version": document.parser_version,
                       "filing_id": identifier, "source_url": document.source_url, "page_count": document.page_count}
            if version not in versions:
                versions.append(version)
            citations.append({**chunk, "citation_id": f"CIT-{len(citations) + 1:03d}", "excerpt": chunk["excerpt"][:1400]})
    english = body.language == "en-US"
    limitations = [
        "Only selected PDF excerpts were retrieved; this is not a complete reading or audit." if english else "仅检索选定PDF片段，不代表完整阅读或审计全文。",
        "PDF text order and table alignment can differ from the rendered document; check the linked pages." if english else "PDF文字顺序与表格对齐可能与原版不同，请核对原文页码。",
        "Latest third-party financial metrics are not treated as facts from these specific PDFs." if english else "最新供应商结构化指标不冒充所选PDF的申报事实，本报告未据此补齐数值。",
        "Quotation identity and text are checked; semantic support of model inference still requires human review." if english else "引用身份和摘录文字已校验，模型推论的语义支持仍需人工核查。",
    ]
    if any("fallback" in str(row.get("retrieval_mode")) for row in citations):
        limitations.append("No query match was found; selected page excerpts are shown as a section fallback." if english else "关键词未命中时使用部分页面片段，并明确标为章节回退。")
    if mode() != "cn_live":
        limitations.append("Sources are saved archive/test material, not a live disclosure update." if english else "来源为已保存归档或隔离测试资料，并非实时披露更新。")
    context = {"market": "CN", "task_type": body.task_type, "language": body.language,
               "question": body.question or "", "company": {"symbol": symbol, "name": company.name},
               "filings": filings, "citations": citations, "facts": [],
               "document_versions": sorted(versions, key=lambda item: item["id"]),
               "source_kind": mode(), "limitations": limitations,
               "retrieval_mode": "section_fallback" if any("fallback" in str(row.get("retrieval_mode")) for row in citations) else "cn_pdf_keyword",
               "prompt_version": CN_PROMPT_VERSION, "schema_version": CN_SCHEMA_VERSION}
    context_evidence(context)
    return company, context


def job_out(job):
    return {"id": job.id, "job_id": job.id, "market": "CN", "symbol": job.symbol,
            "task_type": job.task_type, "status": job.status, "error_code": job.error_code,
            "error_message": job.error_message, "report_id": job.report_id,
            "created_at": aware(job.created_at),
            "started_at": aware(job.started_at) if job.started_at else None,
            "finished_at": aware(job.finished_at) if job.finished_at else None}


def find_job(session, owner, identifier, *, lock=False):
    query = select(CnResearchJob).where(CnResearchJob.id == identifier, CnResearchJob.owner_id == owner)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    job = session.scalar(query)
    if not job:
        raise AppError("JOB_NOT_FOUND", "未找到此A股研究任务。", 404)
    return job


def checked_config(session, job, *, lock=False):
    if lock:
        session.scalar(select(LLMConfig).where(LLMConfig.id == job.config_id,
            LLMConfig.owner_id == job.owner_id).with_for_update().execution_options(populate_existing=True))
    config = get_verified_config(session, job.owner_id, job.config_id)
    if config.version != job.config_version or config.verified_binding != job.config_binding:
        raise AppError("CONFIG_CHANGED", "模型配置已变更，请重新验证并提交研究。", 409)
    return config


def submit(session, background, *, owner, company, task_type, context, config, force=False, parent_job_id=None):
    checked_context(context, symbol=company.symbol, task_type=task_type)
    build_prompt(context)
    digest = fingerprint(context, owner, config)
    existing = session.scalar(select(CnResearchJob).where(CnResearchJob.active_key == digest).with_for_update())
    if existing:
        if (now() - aware(existing.created_at)).total_seconds() < 1200:
            return JobAccepted(job_id=existing.id, status=existing.status, reused=True)
        existing.status, existing.active_key, existing.finished_at = "failed", None, now()
        existing.error_code, existing.error_message = "WORKER_TIMEOUT", "任务超过恢复期限，可重新提交。"
        session.commit()
    if not force:
        cached = session.scalar(select(CnResearchJob).where(CnResearchJob.owner_id == owner,
            CnResearchJob.fingerprint == digest, CnResearchJob.status == "succeeded")
            .order_by(CnResearchJob.created_at.desc()).limit(1))
        if cached:
            return JobAccepted(job_id=cached.id, status="succeeded", reused=True)
    job = CnResearchJob(owner_id=owner, symbol=company.symbol, task_type=task_type,
        input_json=context, fingerprint=digest, active_key=digest, parent_job_id=parent_job_id,
        config_id=config.id, config_version=config.version, config_binding=config.verified_binding)
    session.add(job)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.scalar(select(CnResearchJob).where(CnResearchJob.active_key == digest))
        if existing:
            return JobAccepted(job_id=existing.id, status=existing.status, reused=True)
        raise AppError("RESEARCH_CONFLICT", "A股研究状态变化，请重试。", 409) from None
    if get_settings().task_mode == "inline":
        background.add_task(run_research_job, job.id)
    else:
        try:
            connection = Redis.from_url(get_settings().redis_url, socket_connect_timeout=2, socket_timeout=2)
            Queue("finsight", connection=connection, serializer=JSONSerializer).enqueue(
                "app.services.cn.research.run_research_job", job.id, job_id=job.id,
                job_timeout=240, result_ttl=3600, failure_ttl=86400)
        except Exception:
            job.status, job.active_key, job.finished_at = "failed", None, now()
            job.error_code, job.error_message = "QUEUE_UNAVAILABLE", "任务队列不可用，请稍后重试。"
            session.commit()
            raise AppError("QUEUE_UNAVAILABLE", job.error_message, 503, {"job_id": job.id}) from None
    return JobAccepted(job_id=job.id, status="queued", reused=False)


def run_research_job(identifier):
    with session_factory()() as session:
        ensure_cn_dataset(session)
        job = session.scalar(select(CnResearchJob).where(CnResearchJob.id == identifier).with_for_update())
        if not job or job.status != "queued":
            return
        job.status, job.started_at, job.attempts = "running", now(), job.attempts + 1
        session.commit()
        context = job.input_json
    try:
        checked_context(context, symbol=job.symbol, task_type=job.task_type)
        with session_factory()() as session:
            config = checked_config(session, job)
            provider = provider_for_config(config)
        system, prompt = build_prompt(context)
        generated = provider.generate_structured(config.model_id, system, prompt, options=config.options_json)
        content = validate_result(generated["content"], context)
        markdown = write_markdown(content, {**context, "generation": {
            "provider": config.provider, "model_id": config.model_id, "config_version": config.version}})
        with session_factory()() as session:
            checked_config(session, job, lock=True)
            current = find_job(session, job.owner_id, identifier, lock=True)
            if current.status != "running":
                return
            title = TITLES[current.task_type][1 if context["language"] == "en-US" else 0]
            report = CnResearchReport(job_id=current.id, owner_id=current.owner_id, symbol=current.symbol,
                task_type=current.task_type, title=title, language=context["language"], provider=config.provider,
                model_id=config.model_id, source_kind=context["source_kind"], content=content, markdown=markdown,
                usage=generated.get("usage", {}), duration_ms=generated.get("duration_ms", 0))
            session.add(report)
            session.flush()
            current.status, current.active_key, current.finished_at = "succeeded", None, now()
            current.report_id = report.id
            session.commit()
    except Exception as exc:
        with session_factory()() as session:
            current = session.scalar(select(CnResearchJob).where(CnResearchJob.id == identifier).with_for_update())
            if current and current.status == "running":
                current.status, current.active_key, current.finished_at = "failed", None, now()
                current.error_code = exc.code if isinstance(exc, AppError) else "RESEARCH_FAILED"
                current.error_message = exc.message if isinstance(exc, AppError) else "A股研究失败，请检查PDF和模型配置。"
                session.commit()
