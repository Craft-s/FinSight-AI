"""Freeze selected filing evidence and accession-specific metrics before enqueueing."""
import hashlib
import json

from sqlalchemy import select

from app.core.errors import AppError
from app.db.document_models import Document
from app.db.models import Company, Filing
from app.services.documents.service import list_filing_documents, retrieve_chunks
from app.services.metrics import version_metrics
from app.services.research.prompts import PROMPT_VERSION
from app.services.research.validator import SCHEMA_VERSION
from app.services.sec.client import validate_cik

QUERIES = {
    "summary": "revenue income operations cash liquidity risk management",
    "comparison": "revenue income cash liquidity risk comparison",
    "event": "item results agreement acquisition risk event",
}


def public_mode(session):
    return "postgres_fts" if session.bind.dialect.name == "postgresql" else "test_keyword"


def select_filings(session, cik, task_type, ids):
    company = session.scalar(select(Company).where(Company.cik == validate_cik(cik)))
    if not company:
        raise AppError("COMPANY_NOT_FOUND", "本地尚无此公司，请先导入快照或刷新 SEC 映射。", 404)
    required = 2 if task_type == "comparison" else 1
    if len(ids) != required or len(set(ids)) != required:
        raise AppError("RESEARCH_FILINGS_INVALID", "请选择此研究类型所需的一份或两份不同申报。", 422)
    filings = []
    for identifier in ids:
        filing = session.scalar(select(Filing).where(Filing.id == identifier, Filing.company_id == company.id))
        if not filing:
            raise AppError("FILING_NOT_FOUND", "未找到所选公司的申报。", 404)
        if task_type == "event" and filing.form_type not in {"8-K", "8-K/A"}:
            raise AppError("RESEARCH_FILINGS_INVALID", "事件研究仅支持 8-K 及其修订。", 422)
        if task_type in {"summary", "comparison"} and filing.form_type not in {"10-K", "10-K/A", "10-Q", "10-Q/A"}:
            raise AppError("RESEARCH_FILINGS_INVALID", "财报总结和对比仅支持定期报告及其修订。", 422)
        filings.append(filing)
    return company, filings


def freeze_context(session, company, filings, body):
    citations, facts, versions = [], [], []
    modes = []
    query = body.question if body.task_type == "question" else QUERIES[body.task_type]
    for filing in filings:
        documents = list_filing_documents(session, filing.id)
        if not documents:
            raise AppError("DOCUMENT_NOT_READY", "所选申报尚无已解析正文，请先准备文档。", 409)
        chunks = retrieve_chunks(session, [filing.id], query, limit=4 if len(filings) == 2 else 8)
        if not chunks:
            raise AppError("EVIDENCE_NOT_FOUND", "已保存正文中未找到可用于研究的证据。", 409)
        for chunk in chunks:
            document = session.get(Document, chunk["document_id"])
            if not document or document.sha256 != chunk["document_sha256"]:
                raise AppError("RESEARCH_CONTEXT_INVALID", "研究证据上下文无效，请重新同步并检索资料。", 409)
            version = {"id": document.id, "sha256": document.sha256, "parser_version": document.parser_version}
            if version not in versions:
                versions.append(version)
            copied = dict(chunk)
            copied["citation_id"] = f"CIT-{len(citations) + 1:03d}"
            copied["excerpt"] = copied["excerpt"][:1400]
            citations.append(copied)
            modes.append(copied.get("retrieval_mode", public_mode(session)))
        if filing.form_type not in {"10-K", "10-K/A", "10-Q", "10-Q/A"}:
            continue
        frequency = "annual" if filing.form_type.startswith("10-K") else "quarterly"
        try:
            rows = version_metrics(session, company, frequency=frequency, years=1,
                                   selection_policy="as_filed", accession=filing.accession_number)
        except AppError as exc:
            if exc.code not in {"SOURCE_NOT_AVAILABLE", "SOURCE_VERSION_INCOMPLETE"}:
                raise
            rows = []
        for row in rows[:14]:
            facts.append({"id": f"MET-{len(facts) + 1:03d}", "metric_key": row["metric_key"],
                          "label": row["label"], "value": row["value"], "unit": row["unit"],
                          "period": row["period_end"], "filing_id": filing.id,
                          "provenance": row["provenance"]})
    english = body.language == "en-US"
    limitations = [
        "Only selected excerpts were retrieved; this is not a review of every passage or attachment." if english else "仅检索选定片段，不代表已审阅全部正文或附件。",
        "Copied values and quotation matches are checked; relevance and inferential support still require human review." if english else "复制数值及引文文字匹配已校验，相关性和推论支持程度仍需人工核查。",
    ]
    if body.task_type == "event":
        limitations.append("Event evidence may omit attachments; no stock-price direction or causation is established." if english else "事件证据可能缺少附件，不能据此确定股价方向或因果关系。")
    if company.source_kind != "sec_live":
        limitations.append("Sources are historical archives, not a live SEC update." if english else "来源是历史归档，并非实时 SEC 更新。")
    if not facts:
        limitations.append("No accession-specific structured metrics are available; none were estimated." if english else "没有可用的指定申报结构化指标，未作估算补齐。")
    return {
        "task_type": body.task_type, "language": body.language, "question": body.question or "",
        "company": {"cik": company.cik, "name": company.name, "ticker": company.ticker},
        "filings": [{"id": f.id, "accession": f.accession_number, "form_type": f.form_type,
                     "filing_date": f.filing_date, "report_date": f.report_date} for f in filings],
        "citations": citations, "facts": facts, "document_versions": sorted(versions, key=lambda d: d["id"]),
        "source_kind": company.source_kind, "limitations": limitations,
        "retrieval_mode": "section_fallback" if any("fallback" in str(mode) for mode in modes) else public_mode(session),
        "prompt_version": PROMPT_VERSION, "schema_version": SCHEMA_VERSION,
    }


def fingerprint(context, owner, config=None):
    payload = {"context": context, "owner": owner}
    if config:
        payload["config"] = {"id": config.id, "version": config.version,
                             "binding": config.verified_binding, "model": config.model_id}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
