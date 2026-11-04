"""TEST ONLY generated PDFs and disposable CN records; no production finance."""
import hashlib
import io
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pypdf import PdfWriter
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
    TextStringObject,
)
from sqlalchemy import func, select

from app.api import cn_documents
from app.core.config import get_settings
from app.core.errors import AppError
from app.db.cn_document_models import CnDocument, CnDocumentChunk, CnDocumentJob
from app.db.cn_models import CnCompany
from app.db.session import session_factory
from app.services.cn import documents
from app.services.cn.pdf_client import PdfClient, validate_pdf_url
from app.services.cn.pdf_parser import parse_isolated, parse_pdf, section_heading
from app.services.cn.provider import Resource
from app.services.cn.service import ensure_cn_dataset, import_company_resources, import_directory
from tests.conftest import TEST_TOKEN
from tests.test_cn_api import directory, financial_resources

SYMBOL, FILING = "600519.SH", "12345678"
URL = "https://static.cninfo.com.cn/finalpage/2026-01-01/12345678.PDF"
STAMP = datetime(2026, 1, 1, tzinfo=UTC)
TEXT = "TEST ONLY synthetic disclosure. Revenue and operating cash flow are supplied for parser testing. No production financial data. "


def generated_pdf(texts=None, *, encrypted=False, chinese=False):
    writer = PdfWriter()
    for text in texts or [TEXT]:
        page = writer.add_blank_page(width=600, height=800)
        if chinese:
            descendant = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/CIDFontType0"), NameObject("/BaseFont"): NameObject("/STSong-Light"),
                NameObject("/CIDSystemInfo"): DictionaryObject({NameObject("/Registry"): TextStringObject("Adobe"),
                    NameObject("/Ordering"): TextStringObject("GB1"), NameObject("/Supplement"): NumberObject(4)})})
            font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type0"),
                NameObject("/BaseFont"): NameObject("/STSong-Light"), NameObject("/Encoding"): NameObject("/UniGB-UCS2-H"),
                NameObject("/DescendantFonts"): ArrayObject([writer._add_object(descendant)])})
            operand = "<" + text.encode("utf-16-be").hex() + ">"
        else:
            font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                                     NameObject("/BaseFont"): NameObject("/Helvetica")})
            operand = "(" + text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ")"
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 40 700 Td {operand} Tj ET".encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    if encrypted:
        writer.encrypt("TEST ONLY encryption")
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.fixture
def cn_docs(api):
    announcements = Resource("announcements", "https://www.cninfo.com.cn/new/hisAnnouncement/query",
        json.dumps({"announcements": [{"secCode": "600519", "orgId": "gssh0600519", "announcementId": FILING,
            "announcementTitle": "TEST ONLY 2025年年度报告", "announcementTime": 1767225600000,
            "adjunctUrl": "finalpage/2026-01-01/12345678.PDF"}]}).encode(), STAMP, {"test_only": True})
    with session_factory()() as session:
        ensure_cn_dataset(session)
        import_directory(session, [directory()])
        import_company_resources(session, session.get(CnCompany, SYMBOL), financial_resources() + [announcements])
        session.commit()
    return api


def import_fixture(session, raw=None, *, stamp=STAMP):
    return documents.import_pdf(session, SYMBOL, FILING, raw or generated_pdf(),
                                fetched_at=stamp, source_kind="synthetic_fixture")


def test_parser_page_provenance_and_content_hashes():
    parsed = parse_pdf(generated_pdf([TEXT, TEXT + " Second page."]))
    assert parsed["page_count"] == 2 and parsed["coverage"]["text_pages"] == 2
    assert parsed["coverage"]["ocr"] is False
    for chunk in parsed["chunks"]:
        assert chunk["location"]["page_start"] in (1, 2)
        assert chunk["location"]["page_start"] == chunk["location"]["page_end"]
        assert hashlib.sha256(chunk["content"].encode()).hexdigest() == chunk["content_sha256"]
    assert parsed["coverage"]["all_pages_have_text"] is True
    assert parsed["coverage"]["text_extraction_quality"] == "not_independently_verified"


def test_financial_table_rows_do_not_replace_the_narrative_section():
    heading = "第一节 公司经营情况和风险因素说明"
    table_row = "二、营业总成本 44,486,918,820.21 41,426,154,052.85"
    parsed = parse_pdf(generated_pdf([heading, table_row,
        "TEST ONLY 合成披露，仅用于验证段落继承，不代表真实财务内容。" * 4], chinese=True))
    assert all(chunk["section"] == heading for chunk in parsed["chunks"])
    assert any(table_row in chunk["content"] for chunk in parsed["chunks"])
    assert section_heading("二、2026年度财务报告")
    assert section_heading("1. 2026年与2025年经营情况说明")
    assert not section_heading("三、营业利润 123456 123450")


def test_blank_pages_are_explicit_without_claiming_extraction_completeness():
    parsed = parse_pdf(generated_pdf([TEXT, " "]))
    assert parsed["page_count"] == 2 and parsed["coverage"]["text_pages"] == 1
    assert parsed["coverage"]["empty_or_image_pages"] == [2]
    assert parsed["coverage"]["all_pages_have_text"] is False
    assert "complete_text_extraction" not in parsed["coverage"]


def test_isolated_parser_unicode_and_arbitrary_working_directory(tmp_path, monkeypatch):
    chinese = "TEST ONLY 合成报告，营业收入与经营现金流仅供解析测试，不是真实财务资料。" * 4
    monkeypatch.chdir(tmp_path)
    parsed = parse_isolated(generated_pdf([chinese], chinese=True), settings=get_settings())
    assert "营业收入" in parsed["chunks"][0]["content"]
    assert parsed["page_count"] == 1


@pytest.mark.parametrize(("raw", "code"), [
    (b"<html>WAF</html>", "CN_PDF_INVALID"),
    (b"%PDF-broken", "CN_PDF_INVALID"),
])
def test_non_pdf_and_damaged_pdf_are_explicit(raw, code):
    with pytest.raises(AppError) as failure:
        parse_pdf(raw)
    assert failure.value.code == code


def test_encrypted_and_scanned_pdf_do_not_create_fake_text():
    with pytest.raises(AppError, match="加密") as encrypted:
        parse_pdf(generated_pdf(encrypted=True))
    assert encrypted.value.code == "CN_PDF_ENCRYPTED"
    with pytest.raises(AppError) as scanned:
        parse_pdf(generated_pdf([" "]))
    assert scanned.value.code == "CN_PDF_OCR_REQUIRED"


@pytest.mark.parametrize(("limits", "code"), [
    ({"max_pages": 1}, "CN_PDF_PAGE_LIMIT"),
    ({"max_chars": 20}, "CN_PDF_TEXT_LIMIT"),
    ({"max_chunks": 1}, "CN_PDF_CHUNK_LIMIT"),
])
def test_parser_limits_reject_incomplete_document(limits, code):
    with pytest.raises(AppError) as failure:
        parse_pdf(generated_pdf([TEXT * 20, TEXT]), **limits)
    assert failure.value.code == code


def test_timeout_is_explicit_and_child_command_has_fixed_cwd(monkeypatch):
    import subprocess
    def timeout(command, **kwargs):
        assert kwargs["cwd"].name == "backend"
        assert kwargs["input"].startswith(b"%PDF-")
        raise subprocess.TimeoutExpired(command, 1)
    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(AppError) as failure:
        parse_isolated(generated_pdf(), settings=get_settings())
    assert failure.value.code == "CN_PDF_PARSE_TIMEOUT"


@pytest.mark.parametrize("url", [
    "http://static.cninfo.com.cn/finalpage/2026-01-01/12345678.PDF",
    URL + "?url=http://127.0.0.1", URL + "#page=1", URL.replace(".cn/", ".cn:443/"),
    URL.replace("static.cninfo.com.cn", "static.cninfo.com.cn.attacker.invalid"),
    URL.replace("/2026-01-01/", "/2026-99-99/"),
    "https://static.cninfo.com.cn/finalpage/../12345678.PDF",
])
def test_pdf_url_rejects_arbitrary_destinations(url):
    with pytest.raises(AppError):
        validate_pdf_url(url)


class RateStore:
    def __init__(self):
        self.values = {}
    def get(self, key):
        return self.values.get(key)
    def set(self, key, value, **kwargs):
        self.values[key] = value
        return True


def test_pdf_fetch_boundaries_redirect_waf_and_cooldown(monkeypatch):
    monkeypatch.setattr("app.services.cn.service.mode", lambda: "cn_live")
    for status, headers, body, code in [
        (302, {"Location": "http://127.0.0.1"}, b"", "CN_PDF_UNAVAILABLE"),
        (200, {"Content-Type": "text/html"}, b"WAF", "CN_PDF_INVALID"),
        (429, {"Retry-After": "120"}, b"", "CN_RATE_LIMITED"),
        (200, {"Content-Type": "application/pdf", "Content-Length": "999999999"}, b"", "CN_PDF_TOO_LARGE"),
    ]:
        store = RateStore()
        client = PdfClient(redis=store, transport=httpx.MockTransport(lambda request: httpx.Response(status, headers=headers, content=body)))
        with pytest.raises(AppError) as failure:
            client.fetch(URL)
        assert failure.value.code == code
        if status == 429:
            assert store.get("finsight:cn:rate:static.cninfo.com.cn:cooldown")


def test_fixture_mode_never_performs_network(monkeypatch):
    monkeypatch.setattr("app.services.cn.service.mode", lambda: "synthetic_fixture")
    client = PdfClient(redis=RateStore(), transport=httpx.MockTransport(lambda request: pytest.fail("fixture must not fetch")))
    with pytest.raises(AppError) as failure:
        client.fetch(URL)
    assert failure.value.code == "CN_PDF_LIVE_DISABLED"


def test_pdf_download_success_preserves_original_bytes(monkeypatch):
    monkeypatch.setattr("app.services.cn.service.mode", lambda: "cn_live")
    raw = generated_pdf()
    client = PdfClient(redis=RateStore(), transport=httpx.MockTransport(lambda request: httpx.Response(200, content=raw, headers={"Content-Type": "application/pdf"})))
    downloaded, fetched = client.fetch(URL)
    assert downloaded == raw and fetched.tzinfo is not None


def test_pdf_download_rejects_transport_compression_before_decoding(monkeypatch):
    import gzip
    monkeypatch.setattr("app.services.cn.service.mode", lambda: "cn_live")
    def compressed(request):
        assert request.headers["Accept-Encoding"] == "identity"
        return httpx.Response(200, headers={"Content-Encoding": "gzip", "Content-Type": "application/pdf"},
                              stream=httpx.ByteStream(gzip.compress(generated_pdf())))
    client = PdfClient(redis=RateStore(), transport=httpx.MockTransport(compressed))
    with pytest.raises(AppError) as failure:
        client.fetch(URL)
    assert failure.value.code == "CN_PDF_INVALID"


def test_pdf_download_checks_actual_stream_size_without_content_length(monkeypatch):
    monkeypatch.setattr("app.services.cn.service.mode", lambda: "cn_live")
    client = PdfClient(redis=RateStore(), transport=httpx.MockTransport(lambda request: httpx.Response(
        200, headers={"Content-Type": "application/pdf"}, stream=httpx.ByteStream(b"%PDF-" + b"x" * 100_000))))
    monkeypatch.setattr(client.settings, "cn_pdf_max_bytes", 100_000)
    with pytest.raises(AppError) as failure:
        client.fetch(URL)
    assert failure.value.code == "CN_PDF_TOO_LARGE"


def test_pdf_download_has_a_total_budget_in_addition_to_read_timeout(monkeypatch):
    monkeypatch.setattr("app.services.cn.service.mode", lambda: "cn_live")
    clock = iter([0, 31])
    monkeypatch.setattr("app.services.cn.pdf_client.monotonic", lambda: next(clock))
    client = PdfClient(redis=RateStore(), transport=httpx.MockTransport(lambda request: httpx.Response(
        200, headers={"Content-Type": "application/pdf"}, content=generated_pdf())))
    with pytest.raises(AppError) as failure:
        client.fetch(URL)
    assert failure.value.code == "CN_PDF_DOWNLOAD_TIMEOUT"


def test_immutable_versions_hashes_and_failure_preserves_old_document(cn_docs):
    raw = generated_pdf()
    with session_factory()() as session:
        first = import_fixture(session, raw)
        session.commit()
        identifier, fetched = first.id, first.fetched_at
        again = import_fixture(session, raw, stamp=STAMP + timedelta(days=1))
        assert again.id == identifier and again.fetched_at == fetched
        second = import_fixture(session, generated_pdf([TEXT + " A changed original response."]))
        session.commit()
        assert second.id != identifier and first.raw_bytes == raw
        assert first.sha256 == hashlib.sha256(raw).hexdigest()
        with pytest.raises(AppError):
            import_fixture(session, b"%PDF-invalid")
        session.rollback()
        assert session.scalar(select(func.count()).select_from(CnDocument)) == 2
        assert session.scalar(select(func.count()).select_from(CnDocumentChunk)) >= 2


def test_documents_and_evidence_are_company_scoped_without_sec_identity(cn_docs):
    with session_factory()() as session:
        import_fixture(session)
        session.commit()
    url = f"/api/v1/cn/companies/{SYMBOL}/filings/{FILING}/documents"
    body = cn_docs.get(url).json()
    assert len(body["items"]) == 1 and body["retrieval_mode"] == "cn_keyword"
    document = body["items"][0]
    assert document["page_count"] == 1 and "raw_bytes" not in document
    evidence = cn_docs.get(f"/api/v1/cn/companies/{SYMBOL}/evidence", params={"q": "revenue", "filing_id": FILING}).json()
    citation = evidence["items"][0]
    assert citation["market"] == "CN" and citation["announcement_id"] == FILING
    assert citation["document_sha256"] == document["sha256"] and "accession" not in citation
    assert citation["location"]["page_start"] == 1
    assert cn_docs.get(url.replace(SYMBOL, "000001.SZ")).status_code == 404
    assert cn_docs.get(url.replace(FILING, "87654321")).status_code == 404


def test_cn_keyword_question_preserves_literal_chinese_and_unknown_empty(cn_docs):
    raw = generated_pdf(["TEST ONLY 营业收入与经营活动现金流量净额测试，真实数据禁止使用本夹具。" * 4], chinese=True)
    with session_factory()() as session:
        import_fixture(session, raw)
        session.commit()
        rows = documents.retrieve_chunks(session, SYMBOL, [FILING], "公司的营业收入和经营现金流如何？")
        assert rows and "营业收入" in rows[0]["excerpt"]
        assert rows[0]["retrieval_mode"] == "cn_keyword"
        assert documents.retrieve_chunks(session, SYMBOL, [FILING], "unrelatedkeyword") == []
        assert documents.retrieve_chunks(session, SYMBOL, [FILING], "")[0]["retrieval_mode"] == "section_fallback"


def test_saved_pdf_survives_latest_announcement_listing_rollover(cn_docs):
    with session_factory()() as session:
        import_fixture(session)
        company = session.get(CnCompany, SYMBOL)
        company.source_metadata = {**company.source_metadata, "current_source_ids": {}}
        session.commit()
        assert documents.find_filing(session, SYMBOL, FILING)["id"] == FILING
        assert documents.retrieve_chunks(session, SYMBOL, [FILING], "revenue")


def test_wrong_source_mode_and_changed_snapshot_cannot_import(cn_docs):
    with session_factory()() as session:
        with pytest.raises(AppError):
            documents.import_pdf(session, SYMBOL, FILING, generated_pdf(), fetched_at=STAMP, source_kind="cn_live")
        expected = {**documents.find_filing(session, SYMBOL, FILING), "source_snapshot_id": "other"}
        with pytest.raises(AppError) as failure:
            import_fixture_with_expected(session, expected)
        assert failure.value.code == "CN_PDF_SOURCE_CHANGED"


def import_fixture_with_expected(session, expected):
    return documents.import_pdf(session, SYMBOL, FILING, generated_pdf(), fetched_at=STAMP,
                                source_kind="synthetic_fixture", expected=expected)


def test_document_prepare_requires_owner_and_success_records_real_parse(cn_docs, monkeypatch):
    url = f"/api/v1/cn/companies/{SYMBOL}/filings/{FILING}/documents/sync"
    assert cn_docs.post(url, json={}).status_code == 401
    monkeypatch.setattr(cn_documents, "ensure_document", lambda session, symbol, filing_id, **kwargs: import_fixture(session))
    accepted = cn_docs.post(url, json={}, headers={"X-Admin-Token": TEST_TOKEN})
    assert accepted.status_code == 202
    identifier = accepted.json()["job_id"]
    task = f"/api/v1/cn/documents/jobs/{identifier}"
    assert cn_docs.get(task).status_code == 401
    result = cn_docs.get(task, headers={"X-Admin-Token": TEST_TOKEN}).json()
    assert result["status"] == "succeeded" and result["result"]["page_count"] == 1
    with session_factory()() as session:
        job = session.get(CnDocumentJob, identifier)
        job.owner_id = "another-owner"
        session.commit()
    assert cn_docs.get(task, headers={"X-Admin-Token": TEST_TOKEN}).status_code == 404


def test_failed_preparation_keeps_saved_versions_and_retry_is_safe(cn_docs, monkeypatch):
    with session_factory()() as session:
        original = import_fixture(session)
        session.commit()
        original_id = original.id
    def unavailable(*args, **kwargs):
        raise AppError("CN_PDF_NETWORK_ERROR", "TEST ONLY upstream failure", 503)
    monkeypatch.setattr(cn_documents, "ensure_document", unavailable)
    url = f"/api/v1/cn/companies/{SYMBOL}/filings/{FILING}/documents/sync"
    accepted = cn_docs.post(url, json={}, headers={"X-Admin-Token": TEST_TOKEN}).json()
    result = cn_docs.get('/api/v1/cn/documents/jobs/' + accepted['job_id'], headers={"X-Admin-Token": TEST_TOKEN}).json()
    assert result["status"] == "failed" and result["error_code"] == "CN_PDF_NETWORK_ERROR"
    cn_documents.run_document_job(accepted["job_id"])
    with session_factory()() as session:
        assert session.get(CnDocument, original_id)
        assert session.get(CnDocumentJob, accepted["job_id"]).attempts == 1


def test_prepare_queue_failure_persists_error_and_releases_active_key(cn_docs, monkeypatch):
    get_settings().task_mode = "rq"
    monkeypatch.setattr("rq.Queue.enqueue", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("TEST ONLY queue offline")))
    response = cn_docs.post(f"/api/v1/cn/companies/{SYMBOL}/filings/{FILING}/documents/sync", json={}, headers={"X-Admin-Token": TEST_TOKEN})
    assert response.status_code == 503
    with session_factory()() as session:
        job = session.scalar(select(CnDocumentJob))
        assert job.status == "failed" and job.active_key is None


def test_retrieval_never_loads_raw_binary_for_each_chunk(cn_docs):
    with session_factory()() as session:
        import_fixture(session)
        session.commit()
        rows = documents.retrieve_chunks(session, SYMBOL, [FILING], "revenue")
        assert rows
        # LargeBinary is intentionally deferred from the joined candidate scan.
        assert all("raw_bytes" not in item for item in rows)
