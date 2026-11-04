import hashlib
import re
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import func, literal_column, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased

from app.core.config import get_settings
from app.core.errors import AppError
from app.db.document_models import Document, DocumentChunk
from app.db.models import Company, Filing
from app.services.documents.client import (
    DocumentClient,
    exhibit_99_urls,
    filing_origin,
    validate_document_url,
)
from app.services.documents.parser import PARSER_VERSION, parse_html

TERMS = {
    "收入": "revenue sales", "营收": "revenue sales", "销售": "sales revenue",
    "净利润": "net income profit", "毛利": "gross margin profit", "利润率": "margin profitability",
    "现金流": "cash flow operating", "现金": "cash liquidity", "经营": "operating operations",
    "风险": "risk uncertainty", "债务": "debt borrowing", "负债": "liabilities debt",
    "资产": "assets", "减值": "impairment", "供应链": "supply chain",
    "研发": "research development", "对比": "comparison change", "比较": "comparison change", "变化": "increase decrease change",
    "增长": "growth increase", "下滑": "decline decrease", "回购": "repurchase stock",
    "事件": "material event", "业绩": "results operations earnings", "投资": "investment capital",
}


def filing_record(session, filing_id):
    filing = session.get(Filing, filing_id)
    if filing is None:
        raise AppError("FILING_NOT_FOUND", "未找到该申报。", 404)
    if filing.form_type not in {"10-K", "10-K/A", "10-Q", "10-Q/A", "8-K", "8-K/A"}:
        raise AppError("FORM_NOT_SUPPORTED", "正文检索仅支持10-K、10-Q和8-K及其修订。", 422)
    return filing


def list_filing_documents(session, filing_id):
    filing_record(session, filing_id)
    return list(session.scalars(select(Document).where(
        Document.filing_id == filing_id, Document.source_kind == get_settings().data_mode,
    ).order_by(Document.created_at.desc(), Document.id.desc())))


def import_document(session, filing, raw_html, source_url, fetched_at, source_kind, metadata=None, role="primary"):
    if source_kind != get_settings().data_mode:
        raise AppError("DATA_MODE_MISMATCH", "文档来源模式与当前数据库模式不一致。", 422)
    company = session.get(Company, filing.company_id)
    if company.source_kind != source_kind:
        raise AppError("DATA_MODE_MISMATCH", "文档来源模式与公司数据不一致。", 422)
    validate_document_url(filing, source_url)
    if role not in {"primary", "exhibit"} or (role == "primary" and source_url != filing.source_url):
        raise AppError("DOCUMENT_URL_REJECTED", "文档类型与所选申报不一致。", 422)
    if role == "exhibit" and not str((metadata or {}).get("exhibit_type", "")).upper().startswith("EX-99.1"):
        raise AppError("DOCUMENT_TYPE_UNSUPPORTED", "仅支持已确认类型的EX-99.1附件。", 422)
    if fetched_at.tzinfo is None:
        raise AppError("DOCUMENT_INVALID", "文档采集时间必须包含时区。", 422)
    if not isinstance(raw_html, bytes) or len(raw_html) > get_settings().document_max_bytes:
        raise AppError("SOURCE_TOO_LARGE", "财报正文或附件超出允许大小。", 422)
    digest = hashlib.sha256(raw_html).hexdigest()
    lookup = select(Document).where(Document.filing_id == filing.id, Document.source_url == source_url,
                                    Document.sha256 == digest, Document.parser_version == PARSER_VERSION)
    existing = session.scalar(lookup)
    if existing:
        return existing
    parsed = parse_html(raw_html, get_settings().document_max_chunks)
    document = Document(filing_id=filing.id, source_url=source_url, sha256=digest, source_kind=source_kind,
                        fetched_at=fetched_at, title=parsed["title"], role=role, parser_version=PARSER_VERSION,
                        raw_bytes=raw_html, raw_html=raw_html.decode("utf-8", errors="replace"), source_metadata=metadata or {})
    try:
        with session.begin_nested():
            session.add(document)
            session.flush()
            for chunk in parsed["chunks"]:
                session.add(DocumentChunk(id=str(uuid5(NAMESPACE_URL, f"{document.id}:{chunk['chunk_index']}")),
                                          document_id=document.id, filing_id=filing.id, **chunk))
            session.flush()
    except IntegrityError:
        existing = session.scalar(lookup)
        if existing is None:
            raise
        return existing
    return document


def ensure_filing_documents(session, filing_id):
    filing = filing_record(session, filing_id)
    existing = list_filing_documents(session, filing_id)
    primary = next((item for item in existing if item.role == "primary"), None)
    if primary and (
        get_settings().data_mode != "sec_live" or not filing.form_type.startswith("8-K")
        or primary.source_metadata.get("attachment_status") in {"loaded", "not_found"}
    ):
        return existing
    if get_settings().data_mode != "sec_live":
        raise AppError("DOCUMENT_NOT_READY", "本地尚无此申报的真实正文，请先导入可信历史文档或在在线模式同步。", 409)
    client = DocumentClient()
    if primary is None:
        raw, fetched = client.fetch_document(filing, filing.source_url)
        primary = import_document(session, filing, raw, filing.source_url, fetched, "sec_live")
    session.flush()
    if filing.form_type.startswith("8-K"):
        # The primary can be insufficient for earnings events. Record partial coverage
        # if exhibit discovery fails instead of silently claiming full coverage.
        coverage = {"attachment_status": "not_loaded", "note": "主文已解析；必要附件可能尚未纳入证据。"}
        try:
            directory, _ = client.fetch_document(filing, filing_origin(filing) + "index.json", index=True)
            accession_index = filing_origin(filing) + filing.accession_number + "-index.html"
            index_raw, _ = client.fetch_document(filing, accession_index)
            urls = exhibit_99_urls(filing, index_raw, directory)
            for url in urls:
                exhibit_raw, exhibit_fetched = client.fetch_document(filing, url)
                import_document(session, filing, exhibit_raw, url, exhibit_fetched, "sec_live",
                                {"exhibit_type": "EX-99.1", "selected_accession": filing.accession_number}, "exhibit")
            coverage = {"attachment_status": "loaded" if urls else "not_found", "note": "仅纳入索引明确列出的EX-99.1 HTML附件。"}
        except AppError as error:
            coverage["error_code"] = error.code
        primary.source_metadata = {**primary.source_metadata, **coverage}
    return list_filing_documents(session, filing_id)


def expanded_terms(query):
    if not isinstance(query, str) or len(query) > 500:
        raise AppError("QUERY_INVALID", "正文检索问题不得超过500字符。", 422)
    expansions = [value for key, value in TERMS.items() if key in query]
    words = re.findall(r"[A-Za-z][A-Za-z0-9_-]{1,40}", query + " " + " ".join(expansions))
    return list(dict.fromkeys(word.lower() for word in words))[:32]


def retrieve_chunks(session, filing_ids, query, limit=8):
    if not 1 <= limit <= 30 or not 1 <= len(filing_ids) <= 10:
        raise AppError("QUERY_INVALID", "检索范围或返回数量无效。", 422)
    for filing_id in filing_ids:
        filing_record(session, filing_id)
    terms = expanded_terms(query)
    newer = aliased(Document)
    latest = ~select(newer.id).where(
        newer.filing_id == Document.filing_id, newer.source_url == Document.source_url,
        or_(newer.created_at > Document.created_at,
            (newer.created_at == Document.created_at) & (newer.id > Document.id)),
    ).exists()
    base = select(DocumentChunk, Document, Filing).join(Document, Document.id == DocumentChunk.document_id).join(Filing, Filing.id == Document.filing_id).where(
        Document.filing_id.in_(filing_ids), Document.source_kind == get_settings().data_mode, latest,
    )
    dialect = session.get_bind().dialect.name
    if not terms and query.strip():
        return []  # Unknown Chinese terms are not treated as proof that no risk exists.
    if not terms:
        mode = "section_fallback"
        rows = session.execute(base.order_by(Document.filing_id, Document.role.desc(), DocumentChunk.chunk_index).limit(limit)).all()
    elif dialect == "postgresql":
        mode = "postgres_fts"
        vector = func.to_tsvector(literal_column("'english'"), DocumentChunk.content)
        ts_query = func.websearch_to_tsquery(literal_column("'english'"), " OR ".join(terms))
        rows = session.execute(base.where(vector.op("@@")(ts_query)).order_by(
            func.ts_rank_cd(vector, ts_query).desc(), DocumentChunk.id,
        ).limit(limit)).all()
    elif get_settings().development and get_settings().data_mode == "synthetic_fixture":
        mode = "test_keyword"
        candidates = session.execute(base.where(or_(*[DocumentChunk.content.icontains(term, autoescape=True) for term in terms])).limit(200)).all()
        rows = sorted(candidates, key=lambda row: sum(row[0].content.lower().count(term) for term in terms), reverse=True)[:limit]
    else:
        raise AppError("POSTGRES_REQUIRED", "生产正文检索需要PostgreSQL全文搜索。", 503)
    return [{"id": chunk.id, "chunk_id": chunk.id, "document_id": document.id,
             "filing_id": filing.id, "accession": filing.accession_number, "form_type": filing.form_type,
             "filing_date": filing.filing_date, "section": chunk.section, "source_url": document.source_url,
             "excerpt": chunk.content, "document_sha256": document.sha256,
             "location": chunk.location, "retrieval_mode": mode,
             "source_kind": document.source_kind, "role": document.role,
             "source_metadata": document.source_metadata} for chunk, document, filing in rows]
