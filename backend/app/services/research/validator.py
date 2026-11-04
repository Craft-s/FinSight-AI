"""Deterministic evidence checks; an extractive match is not semantic verification."""
import copy
import json
import re
import unicodedata
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.errors import AppError

SCHEMA_VERSION = "research-claims-v1"
MAX_OUTPUT_CHARS = 60_000
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}")
_DECIMAL = re.compile(r"[+-]?[0-9]+(?:\.[0-9]+)?")
_ACCESSION = re.compile(r"[0-9]{10}-[0-9]{2}-[0-9]{6}")
_NUMBER_WORDS = re.compile(
    r"\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|"
    r"forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|billion|"
    r"trillion|percent|percentage)\b|"
    r"[〇零一二两三四五六七八九十百千万亿兆壹贰叁肆伍陆柒捌玖拾佰仟]+"
    r"(?=美元|美金|元|股|倍|成|个百分点|百分|万|亿|兆)|百分之|千分之|万分之|分之",
    re.IGNORECASE,
)
_ADVICE = re.compile(
    r"\b(?:you should (?:buy|sell)|recommend (?:buying|selling)|strong buy|"
    r"guaranteed (?:return|profit)|stock (?:will|must) (?:rise|fall))\b|"
    r"建议(?:买入|卖出)|强烈买入|保本收益|保证(?:盈利|回报)|股价(?:必然|一定|必定)(?:上涨|下跌)",
    re.IGNORECASE,
)


class ReportedValue(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    metric_ref: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=120)
    unit: str = Field(min_length=1, max_length=40)


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    text: str = Field(max_length=1200)
    kind: Literal["disclosed_fact", "inference"]
    citation_ids: list[str] = Field(default_factory=list, max_length=8)
    metric_refs: list[str] = Field(default_factory=list, max_length=8)
    reported_values: list[ReportedValue] = Field(default_factory=list, max_length=8)


class ResearchOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    claims: list[Claim] = Field(max_length=12)
    uncertainties: list[str] = Field(max_length=8)
    insufficient_evidence: bool


def normalized_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).split())


def safe_citation_url(value: object, accession: str | None = None, *, market="US", page=None) -> str | None:
    """Market-specific fixed hosts; never infer a market from an untrusted URL."""
    if (not isinstance(value, str) or len(value) > 1500
            or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)):
        return None
    try:
        parts = urlsplit(value)
        if market == "CN":
            from app.services.cn.pdf_client import validate_pdf_url
            try:
                validate_pdf_url(value)
            except AppError:
                return None
            if page is not None and (type(page) is not int or not 1 <= page <= 10000):
                return None
            return value + (f"#page={page}" if page is not None else "")
        if market != "US":
            return None
        if (parts.scheme != "https" or parts.hostname != "www.sec.gov" or parts.port not in (None, 443)
                or parts.username is not None or parts.password is not None or parts.query or parts.fragment):
            return None
    except ValueError:
        return None
    match = re.fullmatch(r"/Archives/edgar/data/[0-9]{1,10}/([0-9]{18})/[A-Za-z0-9][A-Za-z0-9_.-]{0,249}", parts.path)
    if not match or ".." in parts.path:
        return None
    if accession and (not _ACCESSION.fullmatch(accession) or match[1] != accession.replace("-", "")):
        return None
    return value


def _context_error():
    return AppError("RESEARCH_CONTEXT_INVALID", "研究证据上下文无效，请重新同步并检索资料。", 409)


def _cn_citation(row, context):
    """Bind announcement, security, source hash and page range to frozen records."""
    symbol = context["company"]["symbol"]
    if not isinstance(symbol, str) or not re.fullmatch(r"[0-9]{6}\.(SH|SZ|BJ)", symbol):
        return False
    if not 1 <= len(context["filings"]) <= 2 or not 1 <= len(context["document_versions"]) <= 16:
        return False
    filings = {item["id"]: item for item in context["filings"]}
    versions = {item["id"]: item for item in context["document_versions"]}
    if len(filings) != len(context["filings"]) or len(versions) != len(context["document_versions"]):
        return False
    filing = filings.get(row["filing_id"])
    version = versions.get(row["document_id"])
    location = row["location"]
    start, end = location["page_start"], location["page_end"]
    return (
        row.get("market") == "CN" and row.get("symbol") == symbol
        and row.get("announcement_id") == row["filing_id"]
        and not row.get("accession") and bool(filing) and bool(version)
        and safe_citation_url(row["source_url"], market="CN") is not None
        and filing["source_url"] == row["source_url"]
        and filing["form_type"] == row["form_type"] and filing["filing_date"] == row["filing_date"]
        and version["filing_id"] == row["filing_id"] and version["source_url"] == row["source_url"]
        and version["sha256"] == row["document_sha256"]
        and type(start) is int and type(end) is int and type(version["page_count"]) is int
        and 1 <= start <= end <= version["page_count"] <= 10000
    )


def context_evidence(context: dict) -> tuple[dict, dict]:
    """Validate identities before creating a model prompt or trusting copied results.

    The supplied hashes identify frozen server records, not a claim that an excerpt
    alone can reconstitute or independently prove the original document hash.
    """
    try:
        citations, facts = context.get("citations", []), context.get("facts", [])
        market = context.get("market", "US")
        if market not in {"US", "CN"} or market == "CN" and facts:
            raise ValueError
        if market == "CN" and context.get("source_kind") not in {"cn_live", "cn_snapshot", "synthetic_fixture"}:
            raise ValueError
        if not isinstance(citations, list) or not isinstance(facts, list) or len(citations) > 32 or len(facts) > 120:
            raise ValueError
        citation_map, fact_map = {}, {}
        for row in citations:
            identifier = row["citation_id"]
            if (not isinstance(identifier, str) or not _ID.fullmatch(identifier) or identifier in citation_map
                    or not isinstance(row["document_sha256"], str)
                    or not re.fullmatch(r"[a-fA-F0-9]{64}", row["document_sha256"])
                    or not isinstance(row["excerpt"], str) or not 1 <= len(row["excerpt"]) <= 12_000
                    or not normalized_text(row["excerpt"])):
                raise ValueError
            if market == "CN":
                if not _cn_citation(row, context):
                    raise ValueError
            elif (row.get("market", "US") != "US" or not isinstance(row.get("accession"), str)
                  or not _ACCESSION.fullmatch(row["accession"])
                  or not safe_citation_url(row["source_url"], row["accession"])):
                raise ValueError
            for field in ("filing_id", "chunk_id", "form_type", "filing_date", "section"):
                if not isinstance(row[field], str) or not row[field] or len(row[field]) > 500:
                    raise ValueError
            citation_map[identifier] = row
        for row in facts:
            identifier, value = row["id"], row["value"]
            if (not isinstance(identifier, str) or not _ID.fullmatch(identifier) or identifier in fact_map
                    or value is not None and (not isinstance(value, str) or len(value) > 120 or not _DECIMAL.fullmatch(value))):
                raise ValueError
            for field in ("metric_key", "label", "unit", "period"):
                if not isinstance(row[field], str) or not row[field] or len(row[field]) > 500:
                    raise ValueError
            if not isinstance(row.get("provenance", {}), dict):
                raise ValueError
            fact_map[identifier] = row
        return citation_map, fact_map
    except (KeyError, TypeError, ValueError, AttributeError):
        raise _context_error() from None


def _unverified_number(text: str) -> bool:
    return any(unicodedata.category(char) in {"Nd", "Nl", "No"} for char in text) or bool(_NUMBER_WORDS.search(text))


def _no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError("non-finite JSON")


def _metric_sentence(refs, facts, english):
    pieces = [f"{facts[ref]['label']} · {facts[ref]['period']}: {facts[ref]['value']} {facts[ref]['unit']}" for ref in refs]
    return ("Saved structured facts: " if english else "已保存的结构化事实：") + "; ".join(pieces)


def validate_result(content: str, context: dict) -> dict:
    citations, facts = context_evidence(context)
    english = context.get("language") == "en-US"
    try:
        if not isinstance(content, str) or not 1 <= len(content) <= MAX_OUTPUT_CHARS:
            raise ValueError
        data = json.loads(content, object_pairs_hook=_no_duplicates, parse_constant=_reject_constant)
        parsed = ResearchOutput.model_validate(data)
        if not parsed.claims and not parsed.insufficient_evidence:
            raise ValueError("an empty answer must declare insufficient evidence")
        claims = []
        for original in parsed.claims:
            refs, metric_refs = original.citation_ids, original.metric_refs
            if (not refs and not metric_refs or len(set(refs)) != len(refs)
                    or len(set(metric_refs)) != len(metric_refs)
                    or any(ref not in citations for ref in refs) or any(ref not in facts for ref in metric_refs)):
                raise ValueError("unbound reference")
            reported = original.reported_values
            if len(reported) != len(metric_refs) or {value.metric_ref for value in reported} != set(metric_refs):
                raise ValueError("reported values do not match references")
            for value in reported:
                fact = facts[value.metric_ref]
                if fact["value"] is None or value.value != fact["value"] or value.unit != fact["unit"]:
                    raise ValueError("changed copied value")
            claim = original.model_dump()
            text = normalized_text(original.text)
            if original.kind == "disclosed_fact":
                if refs:
                    # Matching a whole contiguous passage prevents paraphrases or
                    # stitched fragments from being presented as source quotations.
                    if metric_refs or len(text) < 12 or not all(text in normalized_text(citations[ref]["excerpt"]) for ref in refs):
                        raise ValueError("fact is not an extractive quotation")
                    claim["text"] = text
                    claim["evidence_check"] = "extractive_match"
                else:
                    if text or not metric_refs:
                        raise ValueError("metric facts are rendered by the server")
                    claim["text"] = _metric_sentence(metric_refs, facts, english)
                    claim["evidence_check"] = "backend_metric_template"
            else:
                if not text or _unverified_number(text) or _ADVICE.search(text):
                    raise ValueError("unchecked numeric or advice prose")
                claim["text"] = text
                claim["evidence_check"] = "references_only"
            claim["semantic_support"] = "not_automatically_verified"
            claims.append(claim)
        uncertainties = []
        for text in parsed.uncertainties:
            if not 1 <= len(text) <= 600 or not text.strip() or _unverified_number(text) or _ADVICE.search(text):
                raise ValueError("unchecked uncertainty prose")
            uncertainties.append(normalized_text(text))
        if parsed.insufficient_evidence and not uncertainties:
            uncertainties.append("The retrieved material does not provide sufficient evidence." if english else "未在已检索资料中找到足够依据。")
    except (ValidationError, ValueError, TypeError, KeyError, RecursionError):
        raise AppError("EVIDENCE_VALIDATION_FAILED", "研究结果的引用、数值或结构未通过校验，请重试。", 502) from None
    limitations = copy.deepcopy(context.get("limitations", []))
    if not isinstance(limitations, list) or any(not isinstance(item, str) for item in limitations):
        raise _context_error()
    limitations.append(
        "Quotations match retrieved excerpts and copied metric values match the frozen facts; semantic support of model interpretation was not automatically verified."
        if english else "引用摘录与检索片段匹配，复制指标值与冻结事实一致；模型解读的语义支持尚未自动验证。"
    )
    return {
        "claims": claims, "uncertainties": uncertainties, "insufficient_evidence": parsed.insufficient_evidence,
        "citations": copy.deepcopy(list(citations.values())), "facts": copy.deepcopy(list(facts.values())),
        "validation": {"citation_ids": "context_bound", "copied_fact_values": "verified",
                       "extractive_claims": "substring_matched", "semantic_support": "not_automatically_verified"},
        "limitations": list(dict.fromkeys(limitations)), "language": context.get("language", "zh-CN"),
        "filings": copy.deepcopy(context.get("filings", [])),
        "task_type": context.get("task_type"), "retrieval_mode": context.get("retrieval_mode", "not_specified"),
        "source_kind": context.get("source_kind", "unknown"), "schema_version": SCHEMA_VERSION,
        **({"market": "CN", "symbol": context["company"]["symbol"],
            "schema_version": context.get("schema_version", SCHEMA_VERSION)} if context.get("market") == "CN" else {}),
        "disclaimer": "For research and education only. Not investment advice." if english else "仅供研究和教育，不构成投资建议。",
    }
