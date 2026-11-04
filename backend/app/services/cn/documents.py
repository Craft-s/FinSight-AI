"""CN-specific immutable PDF evidence; no CIKs or fabricated SEC accessions."""
import hashlib
import re
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased, defer

from app.core.config import get_settings
from app.core.errors import AppError
from app.db.cn_document_models import CnDocument, CnDocumentChunk
from app.db.cn_models import CnCompany, CnSnapshot
from app.services.cn.pdf_client import PdfClient, validate_pdf_url
from app.services.cn.pdf_parser import PARSER_VERSION, parse_isolated
from app.services.cn.provider import parse_filings, validate_symbol
from app.services.cn.service import ensure_cn_dataset, filings_for, mode
from app.services.sync import aware

RETRIEVAL_MODE = "cn_keyword"


def find_filing(session, symbol, filing_id):
    validate_symbol(symbol)
    if not isinstance(filing_id, str) or not re.fullmatch(r"[0-9]{1,30}", filing_id):
        raise AppError("CN_FILING_NOT_FOUND", "未找到所选公司的公告。", 404)
    company = session.get(CnCompany, symbol)
    if not company or company.source_kind != mode():
        raise AppError("CN_COMPANY_NOT_FOUND", "请先同步A股公司资料。", 404)
    rows, _ = filings_for(session, symbol)
    row = next((item for item in rows if item["id"] == filing_id), None)
    if row is None:
        # A saved immutable report remains available when the latest 30-item
        # listing rolls over. Revalidate its original announcement snapshot.
        old = session.scalar(select(CnDocument).options(defer(CnDocument.raw_bytes)).where(CnDocument.symbol == symbol,
            CnDocument.filing_id == filing_id, CnDocument.source_kind == mode())
            .order_by(CnDocument.created_at.desc(), CnDocument.id.desc()).limit(1))
        source = session.get(CnSnapshot, old.source_snapshot_id) if old else None
        if source and source.scope == symbol and source.kind == "announcements" and source.source_kind == mode():
            row = next((item for item in parse_filings(source.raw_json.encode(), symbol, org_id=company.org_id)
                        if item["id"] == filing_id), None)
            if row:
                row = {**row, "source_snapshot_id": source.id}
    if not row:
        raise AppError("CN_FILING_NOT_FOUND", "未找到所选公司的公告，请先同步公告目录。", 404)
    validate_pdf_url(row["source_url"])
    return row


def document_out(document):
    return {"id": document.id, "market": "CN", "symbol": document.symbol,
        "filing_id": document.filing_id, "announcement_id": document.filing_id,
        "title": document.title, "source_url": document.source_url, "sha256": document.sha256,
        "source_kind": document.source_kind, "source_snapshot_id": document.source_snapshot_id,
        "fetched_at": aware(document.fetched_at), "created_at": aware(document.created_at),
        "parser_version": document.parser_version, "page_count": document.page_count,
        "coverage": document.coverage, "source_metadata": document.source_metadata}


def list_documents(session, symbol, filing_id):
    find_filing(session, symbol, filing_id)
    rows = session.scalars(select(CnDocument).options(defer(CnDocument.raw_bytes)).where(CnDocument.symbol == symbol,
        CnDocument.filing_id == filing_id, CnDocument.source_kind == mode())
        .order_by(CnDocument.created_at.desc(), CnDocument.id.desc())).all()
    return [document_out(row) for row in rows]


def import_pdf(session, symbol, filing_id, raw, *, fetched_at, source_kind, expected=None):
    """Trusted import hook for tests/archives; not exposed as an upload API."""
    filing = find_filing(session, symbol, filing_id)
    if source_kind != mode() or fetched_at.tzinfo is None:
        raise AppError("CN_PDF_SOURCE_MISMATCH", "PDF模式或采集时间与资料不一致。", 422)
    if expected and any(expected.get(key) != filing.get(key) for key in ("source_url", "source_snapshot_id")):
        raise AppError("CN_PDF_SOURCE_CHANGED", "PDF准备期间公告来源改变，请重新提交。", 409)
    if not isinstance(raw, bytes) or len(raw) > get_settings().cn_pdf_max_bytes:
        raise AppError("CN_PDF_TOO_LARGE", "PDF超过下载大小上限。", 422)
    digest = hashlib.sha256(raw).hexdigest()
    lookup = select(CnDocument).options(defer(CnDocument.raw_bytes)).where(CnDocument.symbol == symbol, CnDocument.filing_id == filing_id,
        CnDocument.sha256 == digest, CnDocument.parser_version == PARSER_VERSION)
    existing = session.scalar(lookup)
    if existing:
        return existing
    parsed = parse_isolated(raw, settings=get_settings())
    document = CnDocument(symbol=symbol, filing_id=filing_id, title=filing["title"],
        source_url=filing["source_url"], source_snapshot_id=filing["source_snapshot_id"],
        source_kind=source_kind, sha256=digest, parser_version=PARSER_VERSION, raw_bytes=raw,
        page_count=parsed["page_count"], coverage=parsed["coverage"], fetched_at=fetched_at,
        source_metadata={"filing": filing, "provider": "cninfo", "format": "pdf", "table_extraction": "text_layout_only"})
    try:
        with session.begin_nested():
            session.add(document)
            session.flush()
            for chunk in parsed["chunks"]:
                session.add(CnDocumentChunk(id=str(uuid5(NAMESPACE_URL, f"{document.id}:{chunk['chunk_index']}")),
                    document_id=document.id, symbol=symbol, filing_id=filing_id, **chunk))
            session.flush()
    except IntegrityError:
        existing = session.scalar(lookup)
        if not existing:
            raise
        return existing
    return document


def ensure_document(session, symbol, filing_id, *, refresh=False, expected=None):
    ensure_cn_dataset(session)
    filing = find_filing(session, symbol, filing_id)
    if not refresh:
        existing = session.scalar(select(CnDocument).options(defer(CnDocument.raw_bytes)).where(CnDocument.symbol == symbol,
            CnDocument.filing_id == filing_id, CnDocument.source_kind == mode(),
            CnDocument.parser_version == PARSER_VERSION)
            .order_by(CnDocument.created_at.desc(), CnDocument.id.desc()).limit(1))
        if existing:
            return existing
    if mode() != "cn_live":
        raise AppError("CN_PDF_LIVE_DISABLED", "当前A股模式不允许在线下载PDF，已保存正文仍可阅读。", 422)
    if expected and any(expected.get(key) != filing.get(key) for key in ("source_url", "source_snapshot_id")):
        raise AppError("CN_PDF_SOURCE_CHANGED", "公告来源已更新，请重新准备正文。", 409)
    raw, fetched_at = PdfClient().fetch(filing["source_url"])
    return import_pdf(session, symbol, filing_id, raw, fetched_at=fetched_at, source_kind=mode(), expected=expected or filing)


TERMS = ("营业收入", "营业总收入", "净利润", "归母净利润", "现金流", "经营活动", "资产", "负债",
         "风险", "利润", "收入", "现金", "分红", "股东", "经营情况", "管理层", "研发", "关联交易", "担保")


def query_terms(query):
    if not isinstance(query, str) or len(query) > 500:
        raise AppError("QUERY_INVALID", "检索问题不得超过500字符。", 422)
    result = [term for term in TERMS if term in query]
    result += re.findall(r"[A-Za-z][A-Za-z0-9_-]{1,40}", query.lower())
    if not result:
        result = re.findall(r"[\u4e00-\u9fff]{2,12}", query)
    return list(dict.fromkeys(result))[:20]


def retrieve_chunks(session, symbol, filing_ids, q, limit=8):
    if not 1 <= limit <= 30 or not 1 <= len(filing_ids) <= 10:
        raise AppError("QUERY_INVALID", "正文检索范围无效。", 422)
    for identifier in filing_ids:
        find_filing(session, symbol, identifier)
    terms = query_terms(q)
    newer = aliased(CnDocument)
    latest = ~select(newer.id).where(newer.symbol == CnDocument.symbol,
        newer.filing_id == CnDocument.filing_id, newer.source_kind == CnDocument.source_kind,
        or_(newer.created_at > CnDocument.created_at,
            (newer.created_at == CnDocument.created_at) & (newer.id > CnDocument.id))).exists()
    base = select(CnDocumentChunk, CnDocument).options(defer(CnDocument.raw_bytes)).join(CnDocument, CnDocument.id == CnDocumentChunk.document_id).where(
        CnDocument.symbol == symbol, CnDocument.filing_id.in_(filing_ids), CnDocument.source_kind == mode(), latest)
    retrieval_mode = RETRIEVAL_MODE if q.strip() else "section_fallback"
    if q.strip() and not terms:
        return []
    if terms:
        base = base.where(or_(*[CnDocumentChunk.content.icontains(term, autoescape=True) for term in terms]))
    # Fetch a deterministic bounded set; rank only inside this explicit candidate
    # window. This is literal keyword matching, not semantic or exhaustive search.
    candidates = session.execute(base.order_by(CnDocument.filing_id, CnDocumentChunk.chunk_index).limit(300)).all()
    rows = sorted(candidates, key=lambda row: (-sum(row[0].content.lower().count(term) for term in terms),
        row[1].filing_id, row[0].chunk_index))[:limit]
    result = []
    for index, (chunk, document) in enumerate(rows, 1):
        filing = document.source_metadata["filing"]
        result.append({"citation_id": f"CIT-{index:03d}", "market": "CN", "symbol": symbol,
            "filing_id": document.filing_id, "announcement_id": document.filing_id,
            "form_type": filing["form_type"], "filing_date": filing["filing_date"],
            "report_date": filing.get("report_date"), "section": chunk.section,
            "source_url": document.source_url, "excerpt": chunk.content,
            "chunk_id": chunk.id, "document_id": document.id, "document_sha256": document.sha256,
            "location": chunk.location, "retrieval_mode": retrieval_mode,
            "source_kind": document.source_kind, "source_metadata": document.source_metadata,
            "coverage": document.coverage})
    return result
