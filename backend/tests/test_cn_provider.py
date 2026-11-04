"""Synthetic protocol fixtures only; no A-share data is written to a database."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest
from redis.exceptions import ConnectionError

from app.core.errors import AppError
from app.services.cn.provider import (
    ANNOUNCEMENTS_URL,
    DIRECTORY_URL,
    CnClient,
    Resource,
    directory_entries,
    extract_rows,
    parse_filings,
    validate_symbol,
)


class Limiter:
    def get(self, *_args, **_kwargs):
        return None

    def set(self, *_args, **_kwargs):
        return True


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    monkeypatch.setattr("app.services.cn.provider.get_settings", lambda: SimpleNamespace(
        redis_url="redis://127.0.0.1:1/14", cn_max_bytes=2_000_000, cn_timeout_seconds=3,
    ))


def row(symbol="600001.SH", date="2025-12-31 00:00:00", org_type="通用"):
    return {
        "SECUCODE": symbol, "SECURITY_CODE": symbol[:6], "SECURITY_NAME_ABBR": "TEST ONLY",
        "REPORT_DATE": date, "NOTICE_DATE": "2026-03-31 00:00:00", "CURRENCY": "CNY",
        "ORG_TYPE": org_type, "TOTAL_ASSETS": 0,
    }


def payload(rows, pages=1, count=None):
    return {"success": True, "result": {"pages": pages, "count": len(rows) if count is None else count, "data": rows}}


def directory_row(code="600001", category="A股", org="TESTONLYSH"):
    return {"code": code, "category": category, "orgId": org, "zwjc": "TEST ONLY " + code, "pinyin": "testonly"}


def announcement(**overrides):
    return {
        "secCode": "600001", "orgId": "TESTONLYSH", "announcementId": "1234567890",
        "announcementTitle": "TEST ONLY 2025年年度报告",
        "announcementTime": 1774886400000, "adjunctUrl": "finalpage/2026-03-31/1234567890.PDF",
        **overrides,
    }


def announcements(rows=None):
    return {"announcements": [announcement()] if rows is None else rows, "totalAnnouncement": 1, "hasMore": False}


def client(handler, limiter=None):
    return CnClient(redis=limiter or Limiter(), transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("symbol", ["600001.SH", "000001.SZ", "300001.SZ", "430001.BJ", "830001.BJ", "920001.BJ"])
def test_exchange_qualified_symbol(symbol):
    assert validate_symbol(symbol) == symbol


@pytest.mark.parametrize("symbol", ["600001", "600001.SZ", "000001.SH", "900001.BJ", "200001.SZ", "６００００１.SH", "600001.SH/../", "600001.SH\n", '600001.SH")', None])
def test_rejects_ambiguous_or_injected_symbols(symbol):
    with pytest.raises(AppError) as raised:
        validate_symbol(symbol)
    assert raised.value.status == 422


def test_decimal_parser_preserves_provider_precision_and_zero():
    raw = b'{"success":true,"result":{"data":[{"exact":9007199254740993.1200,"zero":0,"missing":null}]}}'
    value = extract_rows("income", raw)[0]
    assert value["exact"] == Decimal("9007199254740993.1200")
    assert str(value["exact"]) == "9007199254740993.1200"
    assert value["zero"] == 0 and value["missing"] is None


@pytest.mark.parametrize("raw", [b"", b"<html>WAF</html>", b"[]", b'{"success":false,"result":null}', b'{"success":true,"result":{"data":[null]}}', b'{"success":true,"result":{"data":[{"v":NaN}]}}', b'{"success":true,"success":false,"result":{"data":[]}}'])
def test_rejects_empty_waf_and_invalid_financial_json(raw):
    with pytest.raises(AppError):
        extract_rows("income", raw)


def test_explicit_success_empty_rows_are_distinct_from_failure():
    assert extract_rows("income", json.dumps(payload([], pages=0)).encode()) == []
    assert extract_rows("announcements", b'{"announcements":null,"totalAnnouncement":0}') == []
    with pytest.raises(AppError):
        extract_rows("announcements", b'{"announcements":null}')


def test_directory_filters_b_shares_and_cdr_and_keeps_three_exchanges():
    rows = [directory_row(), directory_row("000001"), directory_row("920001"), directory_row("900001", "B股"), directory_row("689009", "CDR")]
    resource = Resource("directory", DIRECTORY_URL, json.dumps({"stockList": rows}).encode(), datetime.now(UTC))
    result = directory_entries([resource])
    assert [r["symbol"] for r in result] == ["600001.SH", "000001.SZ", "920001.BJ"]
    assert result[0]["name"].startswith("TEST ONLY")
    assert result[0]["org_id"] == "TESTONLYSH"


def test_directory_empty_and_conflicting_identity_do_not_erase_old_cache():
    for rows in ([], [directory_row(), {**directory_row(), "orgId": "DIFFERENT"}]):
        with pytest.raises(AppError):
            directory_entries([Resource("directory", DIRECTORY_URL, json.dumps({"stockList": rows}).encode(), datetime.now(UTC))])


@pytest.mark.parametrize(("org_type", "prefix"), [("通用", "G"), ("银行", "B"), ("保险", "I"), ("证券", "S")])
def test_fetch_company_routes_by_verified_org_type_and_keeps_raw_bytes(org_type, prefix):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.headers["User-Agent"].startswith("FinSightAI/")
        if request.url.path == "/api/data/v1/get":
            report = request.url.params["reportName"]
            assert report in {"RPT_F10_FINANCE_MAINFINADATA", *["RPT_F10_FINANCE_" + prefix + x for x in ("INCOME", "BALANCE", "CASHFLOW")]}
            assert request.url.params["pageSize"] == "60"
            assert request.url.params["filter"] == '(SECUCODE="600001.SH")'
            return httpx.Response(200, json=payload([row(org_type=org_type)]))
        assert str(request.url) == ANNOUNCEMENTS_URL
        assert request.method == "POST"
        data = parse_qs(request.content.decode())
        assert data["stock"] == ["600001,TESTONLYSH"]
        assert data["column"] == ["sse"]
        assert data["pageSize"] == ["100"]
        assert "category_ndbg_szsh" in data["category"][0]
        return httpx.Response(200, json=announcements())

    resources = client(handler).fetch_company("600001.SH", "TESTONLYSH")
    assert [r.kind for r in resources] == ["indicators", "income", "balance", "cashflow", "announcements"]
    assert all(isinstance(r.raw, bytes) and r.fetched_at.tzinfo is not None for r in resources)
    assert len(requests) == 5
    assert resources[-1].metadata["coverage"] == "latest_100_financial_announcements"


def test_financial_pagination_is_complete_and_not_last_page_only():
    rows = [row(date=f"{2000+i//4}-{(i%4+1)*3:02d}-{'30' if i%4 in (1,2) else '31'} 00:00:00") for i in range(61)]

    def handler(request):
        page = int(request.url.params["pageNumber"])
        assert page in (1, 2)
        return httpx.Response(200, json=payload(rows[(page-1)*60:page*60], pages=2, count=61))

    resources = client(handler)._financial("income", "RPT_F10_FINANCE_GINCOME", "600001.SH")
    assert sum(len(extract_rows(r.kind, r.raw)) for r in resources) == 61
    assert [r.metadata["page"] for r in resources] == [1, 2]


def test_bse_uses_cninfo_unified_column_and_does_not_hide_real_announcements():
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=payload([row(symbol="920001.BJ")]))
        params = parse_qs(request.content.decode())
        assert params["column"] == ["szse"]
        assert params["stock"] == ["920001,TESTONLYBJ"]
        return httpx.Response(200, json=announcements([announcement(secCode="920001", orgId="TESTONLYBJ")]))

    resources = client(handler).fetch_company("920001.BJ", "TESTONLYBJ")
    assert parse_filings(resources[-1].raw, "920001.BJ")[0]["title"].startswith("TEST ONLY")


def test_bse_historical_code_requires_verified_same_org_not_just_same_name():
    raw = json.dumps(announcements([announcement(secCode="832001", orgId="TESTONLYBJ")])).encode()
    assert parse_filings(raw, "920001.BJ", org_id="TESTONLYBJ")[0]["source_security_code"] == "832001"
    for org_id in (None, "TESTOTHERBJ"):
        with pytest.raises(AppError) as raised:
            parse_filings(raw, "920001.BJ", org_id=org_id)
        assert raised.value.code == "CN_SOURCE_COMPANY_MISMATCH"
    # A matching org does not allow cross-market code substitutions.
    foreign = json.dumps(announcements([announcement(secCode="600001", orgId="TESTONLYBJ")])).encode()
    with pytest.raises(AppError):
        parse_filings(foreign, "920001.BJ", org_id="TESTONLYBJ")


@pytest.mark.parametrize("mode", ["missing", "changed", "duplicate", "over_limit"])
def test_incomplete_racing_or_oversized_pagination_is_explicit(mode):
    rows = [row(date=f"{2000+i//4}-{(i%4+1)*3:02d}-{'30' if i%4 in (1,2) else '31'} 00:00:00") for i in range(60)]

    def handler(request):
        page = int(request.url.params["pageNumber"])
        if mode == "over_limit":
            return httpx.Response(200, json=payload(rows, pages=7, count=400))
        if page == 1:
            return httpx.Response(200, json=payload(rows[:59] if mode == "missing" else rows, pages=2, count=61))
        return httpx.Response(200, json=payload([rows[0]], pages=2, count=62 if mode == "changed" else 61))

    with pytest.raises(AppError):
        client(handler)._financial("income", "RPT_F10_FINANCE_GINCOME", "600001.SH")


@pytest.mark.parametrize("overrides", [{"SECUCODE": "000001.SZ"}, {"SECURITY_CODE": "000001"}, {"CURRENCY": "USD"}, {"CURRENCY": None}, {"REPORT_DATE": "bad"}])
def test_company_currency_and_period_identity_checked(overrides):
    with pytest.raises(AppError):
        client(lambda _: httpx.Response(200, json=payload([{**row(), **overrides}])))._financial("income", "RPT_F10_FINANCE_GINCOME", "600001.SH")


@pytest.mark.parametrize(("title", "kind", "date"), [("TEST ONLY 2025年年度报告", "annual", "2025-12-31"), ("TEST ONLY 2026年半年度报告", "semiannual", "2026-06-30"), ("TEST ONLY 2026年第一季度报告", "q1", "2026-03-31"), ("TEST ONLY 2026年第三季度报告", "q3", "2026-09-30"), ("TEST ONLY 2025年度业绩说明会公告", "announcement", None), ("TEST ONLY 2025年年度报告摘要", "announcement", None)])
def test_actual_pdf_link_and_title_period_are_separate(title, kind, date):
    result = parse_filings(json.dumps(announcements([announcement(announcementTitle=title)])).encode(), "600001.SH")[0]
    assert result["form_type"] == kind and result["report_date"] == date
    assert result["source_url"] == "https://static.cninfo.com.cn/finalpage/2026-03-31/1234567890.PDF"
    assert result["filing_date"] == "2026-03-31"


@pytest.mark.parametrize("overrides", [{"secCode": "000001"}, {"announcementId": "../123"}, {"adjunctUrl": "https://evil.invalid/a.pdf"}, {"adjunctUrl": "finalpage/2026-03-31/1234567890.PDF?url=evil"}, {"adjunctUrl": "finalpage/2026-02-31/1234567890.PDF"}, {"announcementTime": "1774886400000"}])
def test_rejects_wrong_filing_identity_or_untrusted_pdf_path(overrides):
    with pytest.raises(AppError):
        parse_filings(json.dumps(announcements([announcement(**overrides)])).encode(), "600001.SH")


@pytest.mark.parametrize("status", [301, 302, 307, 403, 429, 500])
def test_redirects_access_controls_and_errors_are_not_followed(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={"Location": "http://169.254.169.254/secret", "Retry-After": "120"})

    with pytest.raises(AppError):
        client(handler).fetch_directory()
    assert len(calls) == 1


def test_size_limit_and_network_failures_are_sanitized(monkeypatch):
    monkeypatch.setattr("app.services.cn.provider.get_settings", lambda: SimpleNamespace(cn_max_bytes=16, cn_timeout_seconds=1, redis_url="redis://127.0.0.1:1"))
    with pytest.raises(AppError) as raised:
        client(lambda _: httpx.Response(200, content=b"{" + b"x"*40)).fetch_directory()
    assert raised.value.code == "CN_RESPONSE_TOO_LARGE"

    def handler(request):
        raise httpx.ConnectError("TEST ONLY secret should never appear", request=request)

    with pytest.raises(AppError) as raised:
        client(handler).fetch_directory()
    assert raised.value.code == "CN_NETWORK_ERROR"
    assert "secret" not in raised.value.message


def test_rate_limiter_fails_closed_before_any_network():
    class Broken(Limiter):
        def get(self, *_a, **_kw):
            raise ConnectionError("TEST ONLY offline")

    with pytest.raises(AppError) as raised:
        client(lambda _: pytest.fail("network must not run"), limiter=Broken()).fetch_directory()
    assert raised.value.code == "CN_RATE_LIMITER_UNAVAILABLE"


def test_retry_after_creates_shared_cooldown_and_stops_requests():
    saved = {}

    class Memory(Limiter):
        def get(self, key):
            return saved.get(key)

        def set(self, key, value, **kwargs):
            if key.endswith(":cooldown"):
                assert kwargs["ex"] == 120
                saved[key] = value
            return True

    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "120"})

    source = client(handler, limiter=Memory())
    for _ in range(2):
        with pytest.raises(AppError) as raised:
            source.fetch_directory()
        assert raised.value.code == "CN_RATE_LIMITED"
    assert len(calls) == 1


def test_no_arbitrary_url_fetch_and_no_template_guessing():
    with pytest.raises(AppError) as raised:
        client(lambda _: pytest.fail("network must not run"))._request("directory", "https://www.cninfo.com.cn.evil.invalid/new/data/szse_stock.json")
    assert raised.value.code == "CN_SOURCE_URL_REJECTED"
    with pytest.raises(AppError) as raised:
        client(lambda _: httpx.Response(200, json=payload([row(org_type="TEST ONLY unknown")]))).fetch_company("600001.SH", "TESTONLYSH")
    assert raised.value.code == "CN_UNSUPPORTED_TEMPLATE"
