"""Synthetic HTML/protocol inputs stay inside this test module and synthetic test database."""
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.errors import AppError
from app.db.document_models import Document, DocumentChunk
from app.db.models import Filing
from app.db.session import session_factory
from app.services.documents.client import (
    DocumentClient,
    exhibit_99_urls,
    filing_origin,
    validate_document_url,
)
from app.services.documents.parser import parse_html
from app.services.documents.service import (
    ensure_filing_documents,
    expanded_terms,
    import_document,
    retrieve_chunks,
)
from tests.test_sec_client import Limiter

HTML = b'''<!doctype html><html><head><title>SYNTHETIC FINANCIAL TEST ONLY</title>
<script>steal_secret()</script><style>body{display:none}</style></head><body>
<nav>navigation</nav><ix:header><ix:hidden>HIDDEN XBRL fact</ix:hidden></ix:header>
<p style="display: none">secret hidden amount</p><span aria-hidden="true">hidden duplicate</span>
<h1 id="risk">Item 1A. Risk Factors</h1><div><span>Supply chain uncertainty affects our operations.</span></div>
<h2>Item 7. Results of Operations</h2><p>Revenue and gross profit improved. Operating cash flow remained positive.</p>
<table id="financial"><caption>Revenue (synthetic amounts, USD millions)</caption>
<tr><th>Metric</th><th>FY2025</th><th>FY2024</th></tr><tr><td>Net sales</td><td>123</td><td>100</td></tr></table>
</body></html>'''


def dummy_filing():
    return SimpleNamespace(source_url="https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/fixture.htm",
                           accession_number="0000320193-25-000079")


def test_parser_visible_content_sections_and_table_context():
    parsed = parse_html(HTML)
    joined = "\n".join(c["content"] for c in parsed["chunks"])
    for forbidden in ("steal_secret", "HIDDEN", "secret hidden", "hidden duplicate", "navigation", "display:none"):
        assert forbidden not in joined
    assert "Supply chain uncertainty" in joined
    sales = next(row for row in parsed["chunks"] if "Net sales" in row["content"])
    assert "Columns: Metric | FY2025 | FY2024" in sales["content"]
    assert "USD millions" in sales["content"]
    assert "123 | 100" in sales["content"]
    assert sales["location"]["row_index"] == 1
    assert sales["section"] == "Item 7. Results of Operations"
    assert len(sales["content_sha256"]) == 64
    assert parsed == parse_html(HTML)


def test_parser_mixed_inline_blocks_not_dropped_or_duplicated():
    parsed = parse_html(b"<body><div>prefix <span>inline</span><p>nested paragraph</p> suffix</div></body>")
    joined = " | ".join(c["content"] for c in parsed["chunks"])
    assert joined == "prefix inline | nested paragraph | suffix"


def test_parser_large_block_splits_and_limits():
    parsed = parse_html(b"<p>" + b"Revenue growth. " * 1000 + b"</p>")
    assert len(parsed["chunks"]) > 1
    assert all(len(c["content"]) <= 2200 for c in parsed["chunks"])
    with pytest.raises(AppError) as error:
        parse_html(b"<p>" + b"Revenue growth. " * 1000 + b"</p>", max_chunks=1)
    assert error.value.code == "DOCUMENT_TOO_COMPLEX"


@pytest.mark.parametrize("raw", [b"", b"\x00", b"<script>only script</script>", b"<ix:header>only hidden</ix:header>"])
def test_parser_empty_or_hidden_document_rejected(raw):
    with pytest.raises(AppError):
        parse_html(raw)


@pytest.mark.parametrize("suffix", ["../secret.htm", "x%2f.htm", "x.htm?token=secret", "x.htm#section", "x.pdf", "x.xml", "index.json", "sub/x.htm", "//evil/x.htm"])
def test_accession_document_path_constraints(suffix):
    filing = dummy_filing()
    with pytest.raises(AppError):
        validate_document_url(filing, filing_origin(filing) + suffix)


@pytest.mark.parametrize("origin", ["http://www.sec.gov", "https://sec.gov", "https://www.sec.gov.evil.test", "https://u:p@www.sec.gov", "https://www.sec.gov:444"])
def test_accession_origin_constraints(origin):
    filing = dummy_filing()
    url = origin + "/Archives/edgar/data/320193/000032019325000079/fixture.htm"
    with pytest.raises(AppError):
        validate_document_url(filing, url)


def test_wrong_accession_source_url_rejected():
    filing = dummy_filing()
    filing.source_url = filing.source_url.replace("000032019325000079", "000032019325000080")
    with pytest.raises(AppError):
        filing_origin(filing)


def test_fetch_content_size_media_and_redirect(monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "FinSightUnitTest unit-test@invalid.test")
    get_settings.cache_clear()
    filing = dummy_filing()
    good = DocumentClient(redis=Limiter(), transport=httpx.MockTransport(lambda _: httpx.Response(200, content=HTML, headers={"Content-Type": "text/html"})))
    assert good.fetch_document(filing, filing.source_url)[0] == HTML
    for response, code in [
        (httpx.Response(302, headers={"Location": "http://127.0.0.1/private"}), "SEC_INVALID_RESPONSE"),
        (httpx.Response(200, content=b"pdf", headers={"Content-Type": "application/pdf"}), "DOCUMENT_TYPE_UNSUPPORTED"),
        (httpx.Response(403), "SEC_FORBIDDEN"),
    ]:
        client = DocumentClient(redis=Limiter(), transport=httpx.MockTransport(lambda _, response=response: response))
        with pytest.raises(AppError) as error:
            client.fetch_document(filing, filing.source_url)
        assert error.value.code == code
    good.settings = get_settings().model_copy(update={"document_max_bytes": 10})
    with pytest.raises(AppError) as error:
        good.fetch_document(filing, filing.source_url)
    assert error.value.code == "SOURCE_TOO_LARGE"


def test_fetch_requires_contact_before_network(monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "")
    get_settings.cache_clear()
    client = DocumentClient(redis=Limiter(), transport=httpx.MockTransport(lambda _: pytest.fail("must not fetch")))
    with pytest.raises(AppError) as error:
        client.fetch_document(dummy_filing(), dummy_filing().source_url)
    assert error.value.code == "SEC_USER_AGENT_REQUIRED"


def test_exhibit_discovery_requires_type_and_directory_listing():
    import json
    filing = dummy_filing()
    index = b'''<table><tr><td><a href="real-exhibit.htm">Earnings release</a></td><td>EX-99.1</td></tr>
    <tr><td><a href="unlisted.htm">Fake</a></td><td>EX-99.1</td></tr>
    <tr><td><a href="other.htm">Other</a></td><td>EX-10.1</td></tr>
    <tr><td><a href="https://evil.test/real-exhibit.htm">Evil</a></td><td>EX-99.1</td></tr></table>'''
    directory = json.dumps({"directory": {"item": [{"name": "real-exhibit.htm"}, {"name": "other.htm"}]}}).encode()
    assert exhibit_99_urls(filing, index, directory) == [filing_origin(filing) + "real-exhibit.htm"]


def test_chinese_financial_query_expansion():
    for chinese, word in [("收入为何变化", "revenue"), ("毛利下降", "margin"), ("现金流", "cash"), ("风险", "risk"), ("两期比较", "comparison")]:
        assert word in expanded_terms(chinese)
    assert expanded_terms("陌生领域") == []


def test_document_versions_mode_guard_and_fts(seeded):
    with session_factory()() as session:
        filing = session.scalar(select(Filing).where(Filing.form_type == "10-K"))
        first = import_document(session, filing, HTML, filing.source_url, datetime(2026, 1, 1, tzinfo=UTC), "synthetic_fixture")
        session.commit()
        original_id = first.id
        chunks_before = list(session.scalars(select(DocumentChunk).where(DocumentChunk.document_id == first.id)))
        second = import_document(session, filing, HTML, filing.source_url, datetime(2026, 1, 2, tzinfo=UTC), "synthetic_fixture")
        assert second.id == first.id
        assert session.scalar(select(func.count()).select_from(Document)) == 1
        for query in ("收入", "毛利", "现金流", "风险"):
            hits = retrieve_chunks(session, [filing.id], query)
            assert hits, query
            assert all(h["document_sha256"] == first.sha256 for h in hits)
            assert all(h["retrieval_mode"] in {"postgres_fts", "test_keyword"} for h in hits)
            assert all(h["filing_id"] == filing.id for h in hits)
        assert retrieve_chunks(session, [filing.id], "utterly_nonexistent_word") == []
        assert retrieve_chunks(session, [filing.id], "陌生领域") == []
        assert retrieve_chunks(session, [filing.id], "")[0]["retrieval_mode"] == "section_fallback"
        changed = import_document(session, filing, HTML.replace(b"positive", b"changed"), filing.source_url,
                                  datetime(2026, 1, 3, tzinfo=UTC), "synthetic_fixture")
        session.commit()
        assert changed.id != original_id
        assert {h["document_id"] for h in retrieve_chunks(session, [filing.id], "revenue")} == {changed.id}
        assert list(session.scalars(select(DocumentChunk.id).where(DocumentChunk.document_id == original_id))) == [c.id for c in chunks_before]
        with pytest.raises(AppError) as error:
            import_document(session, filing, HTML, filing.source_url, datetime(2026, 1, 3, tzinfo=UTC), "sec_snapshot")
        assert error.value.code == "DATA_MODE_MISMATCH"


def test_snapshot_missing_document_never_fetches_network(seeded, monkeypatch):
    monkeypatch.setattr(DocumentClient, "fetch_document", lambda *a, **k: pytest.fail("offline must never fetch"))
    with session_factory()() as session:
        filing = session.scalar(select(Filing))
        with pytest.raises(AppError) as error:
            ensure_filing_documents(session, filing.id)
        assert error.value.code == "DOCUMENT_NOT_READY"


def test_non_utf8_source_bytes_are_preserved_and_hash_replayable(seeded):
    import hashlib
    raw = '<html><meta charset="windows-1252"><body><p>Revenue in caf\xe9 markets.</p></body></html>'.encode("windows-1252")
    with session_factory()() as session:
        filing = session.scalar(select(Filing))
        document = import_document(session, filing, raw, filing.source_url, datetime(2026, 1, 1, tzinfo=UTC), "synthetic_fixture")
        session.commit()
        assert document.raw_bytes == raw
        assert hashlib.sha256(document.raw_bytes).hexdigest() == document.sha256
        assert "café" in session.scalar(select(DocumentChunk.content).where(DocumentChunk.document_id == document.id))


def test_live_event_discovers_and_persists_exhibit(seeded, monkeypatch):
    import json

    from app.db.models import Company
    monkeypatch.setenv("DATA_MODE", "sec_live")
    get_settings.cache_clear()
    with session_factory()() as session:
        filing = session.scalar(select(Filing).where(Filing.form_type == "8-K"))
        company = session.get(Company, filing.company_id)
        company.source_kind = "sec_live"
        session.flush()
        base = filing_origin(filing)
        def fetch(_client, _filing, url, **kwargs):
            if url.endswith("index.json"):
                content = json.dumps({"directory": {"item": [{"name": "earnings.htm"}]}}).encode()
            elif url.endswith("-index.html"):
                content = b'<table><tr><td><a href="earnings.htm">Release</a></td><td>EX-99.1</td></tr></table>'
            else:
                content = HTML
            return content, datetime(2026, 1, 3, tzinfo=UTC)
        monkeypatch.setattr(DocumentClient, "fetch_document", fetch)
        docs = ensure_filing_documents(session, filing.id)
        session.commit()
        assert {d.role for d in docs} == {"primary", "exhibit"}
        assert next(d for d in docs if d.role == "primary").source_metadata["attachment_status"] == "loaded"
        assert next(d for d in docs if d.role == "exhibit").source_url == base + "earnings.htm"


def test_live_event_retry_recovers_attachment_failure_without_refetching_primary(seeded, monkeypatch):
    import json

    from app.db.models import Company
    monkeypatch.setenv("DATA_MODE", "sec_live")
    get_settings.cache_clear()
    calls = []
    fail = [True]
    with session_factory()() as session:
        filing = session.scalar(select(Filing).where(Filing.form_type == "8-K"))
        session.get(Company, filing.company_id).source_kind = "sec_live"
        session.flush()
        def fetch(_client, _filing, url, **kwargs):
            calls.append(url)
            if url.endswith("index.json"):
                if fail[0]:
                    raise AppError("SEC_TIMEOUT", "SEC请求超时。", 503)
                content = json.dumps({"directory": {"item": [{"name": "earnings.htm"}]}}).encode()
            elif url.endswith("-index.html"):
                content = b'<table><tr><td><a href="earnings.htm">Release</a></td><td>EX-99.1</td></tr></table>'
            else:
                content = HTML
            return content, datetime(2026, 1, 3, tzinfo=UTC)
        monkeypatch.setattr(DocumentClient, "fetch_document", fetch)
        first = ensure_filing_documents(session, filing.id)
        session.commit()
        assert len(first) == 1 and first[0].source_metadata["attachment_status"] == "not_loaded"
        fail[0] = False
        second = ensure_filing_documents(session, filing.id)
        session.commit()
        assert {d.role for d in second} == {"primary", "exhibit"}
        assert calls.count(filing.source_url) == 1
