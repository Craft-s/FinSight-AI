"""TEST ONLY: synthetic A-share evidence and injected model output, never live data."""
import copy
import json

import pytest

from app.core.errors import AppError
from app.services.research.prompts import build_prompt
from app.services.research.validator import context_evidence, safe_citation_url, validate_result
from app.services.research.writer import write_markdown

URL = "https://static.cninfo.com.cn/finalpage/2026-01-01/1220000001.PDF"
QUOTE = "TEST ONLY synthetic disclosure. Operating risk depends on customer demand."


@pytest.fixture
def cn_context():
    return {"market": "CN", "task_type": "summary", "language": "en-US", "question": "",
            "company": {"symbol": "600001.SH", "name": "TEST ONLY synthetic company"},
            "source_kind": "synthetic_fixture", "retrieval_mode": "cn_pdf_keyword",
            "filings": [{"id": "1220000001", "form_type": "annual", "filing_date": "2026-01-01",
                         "report_date": "2025-12-31", "source_url": URL}],
            "document_versions": [{"id": "test-document", "filing_id": "1220000001", "sha256": "a" * 64,
                                  "parser_version": "test-only-pdf", "page_count": 12, "source_url": URL}],
            "citations": [{"market": "CN", "symbol": "600001.SH", "citation_id": "CIT-001",
                           "filing_id": "1220000001", "announcement_id": "1220000001", "form_type": "annual",
                           "filing_date": "2026-01-01", "section": "TEST ONLY risks", "source_url": URL,
                           "excerpt": QUOTE, "chunk_id": "test-chunk", "document_id": "test-document",
                           "document_sha256": "a" * 64, "location": {"page_start": 3, "page_end": 3}}],
            "facts": [], "limitations": ["TEST ONLY synthetic evidence."]}


def response(text=QUOTE):
    return json.dumps({"claims": [{"kind": "disclosed_fact", "text": text, "citation_ids": ["CIT-001"],
                                  "metric_refs": [], "reported_values": []}],
                       "uncertainties": [], "insufficient_evidence": False})


def test_cn_prompt_and_result_keep_market_pages_and_source_identity(cn_context):
    result = validate_result(response(), cn_context)
    assert result["market"] == "CN" and result["symbol"] == "600001.SH"
    assert result["citations"][0]["location"] == {"page_start": 3, "page_end": 3}
    system, prompt = build_prompt(cn_context)
    payload = json.loads(prompt)
    assert "DATA" in system and payload["market"] == "CN"
    assert payload["citations"][0]["announcement_id"] == "1220000001"
    assert payload["citations"][0]["location"]["page_start"] == 3
    cn_context["citations"][0]["excerpt"] = "Changed after generation"
    assert result["citations"][0]["excerpt"] == QUOTE


@pytest.mark.parametrize("field,value", [
    ("market", "US"), ("symbol", "000001.SZ"), ("symbol", "600001"),
    ("announcement_id", "1220000002"), ("filing_id", "1220000002"),
    ("document_id", "another-document"), ("document_sha256", "b" * 64),
    ("form_type", "q1"), ("filing_date", "2026-02-01"),
    ("source_url", "https://static.cninfo.com.cn/finalpage/2026-01-01/1220000002.PDF"),
    ("accession", "0000000001-26-000001"),
])
def test_cn_cross_market_security_filing_document_identity_rejected(cn_context, field, value):
    cn_context["citations"][0][field] = value
    with pytest.raises(AppError) as error:
        context_evidence(cn_context)
    assert error.value.code == "RESEARCH_CONTEXT_INVALID"


@pytest.mark.parametrize("start,end", [(0, 1), (-1, 3), (4, 3), (3, 13), (True, 3), (3, False), ("3", 3)])
def test_cn_invalid_or_forged_page_ranges_rejected(cn_context, start, end):
    cn_context["citations"][0]["location"] = {"page_start": start, "page_end": end}
    with pytest.raises(AppError):
        context_evidence(cn_context)


@pytest.mark.parametrize("url", [
    "http://static.cninfo.com.cn/finalpage/2026-01-01/1220000001.PDF",
    "https://static.cninfo.com.cn.evil.test/finalpage/2026-01-01/1220000001.PDF",
    "https://user@static.cninfo.com.cn/finalpage/2026-01-01/1220000001.PDF",
    "https://static.cninfo.com.cn:8443/finalpage/2026-01-01/1220000001.PDF",
    URL + "?token=test-only", URL + "#page=3",
    "https://static.cninfo.com.cn/finalpage/2026-01-01/report.PDF",
    "https://static.cninfo.com.cn/finalpage/2026-01-01/../1220000001.PDF",
    "https://www.sec.gov/Archives/edgar/data/1/000000000126000001/test.htm",
])
def test_cn_link_boundary_does_not_broaden_sec_allowlist(url):
    assert safe_citation_url(url, market="CN") is None
    assert safe_citation_url(URL) is None


def test_cn_frozen_versions_and_source_mode_cannot_be_swapped(cn_context):
    for change in ("duplicate-version", "duplicate-filing", "sec-mode", "latest-metric"):
        context = copy.deepcopy(cn_context)
        if change == "duplicate-version":
            context["document_versions"].append(copy.deepcopy(context["document_versions"][0]))
        elif change == "duplicate-filing":
            context["filings"].append(copy.deepcopy(context["filings"][0]))
        elif change == "sec-mode":
            context["source_kind"] = "sec_live"
        else:
            context["facts"] = [{"id": "latest-provider-metric", "value": "123"}]
        with pytest.raises(AppError):
            context_evidence(context)


@pytest.mark.parametrize("text", ["TEST ONLY synthetic disclosure. Risk depends on demand.",
                                  "Operating risk depends on changed customer demand.",
                                  "TEST ONLY synthetic disclosure. customer demand."])
def test_cn_altered_or_stitched_quotations_rejected(cn_context, text):
    with pytest.raises(AppError) as error:
        validate_result(response(text), cn_context)
    assert error.value.code == "EVIDENCE_VALIDATION_FAILED"


def test_cn_markdown_uses_pdf_page_link_and_escaped_identity(cn_context):
    cn_context["company"]["name"] = "TEST ONLY <script> & [company]"
    result = validate_result(response(), cn_context)
    markdown = write_markdown(result, cn_context)
    assert URL + "#page=3" in markdown
    assert "Original CNINFO PDF" in markdown and "Original SEC filing" not in markdown
    assert "<script>" not in markdown and "600001" in markdown
    assert "test-document" not in markdown  # source hash/chunk/page are public, DB identity is not required.


@pytest.mark.parametrize("page", [True, 0, -1, 10001, "3"])
def test_cn_export_link_does_not_accept_unvalidated_page_fragment(page):
    assert safe_citation_url(URL, market="CN", page=page) is None
