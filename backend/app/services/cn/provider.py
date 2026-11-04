"""Bounded, read-only A-share sources; never accepts a user-provided fetch URL.

Financial rows are Eastmoney's third-party aggregation, not exchange-certified
facts. CNINFO supplies the securities directory and actual announcement links.
Raw response bytes and the exact request parameters belong in SourceSnapshot.
"""

import json
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime
from html import unescape
from urllib.parse import urlparse

import httpx
from redis import Redis
from redis.exceptions import RedisError

from app.core.config import get_settings
from app.core.errors import AppError

FINANCE_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
DIRECTORY_URL = "https://www.cninfo.com.cn/new/data/szse_stock.json"
ANNOUNCEMENTS_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
_ENDPOINTS = {FINANCE_URL, DIRECTORY_URL, ANNOUNCEMENTS_URL}
_TEMPLATES = {"通用": "G", "银行": "B", "保险": "I", "证券": "S"}
_REPORT_SUFFIX = {"income": "INCOME", "balance": "BALANCE", "cashflow": "CASHFLOW"}
_PAGE_SIZE = 60
_MAX_PAGES = 6
_ANNOUNCEMENT_LIMIT = 100
_FINANCIAL_CATEGORIES = (
    "category_ndbg_szsh;category_bndbg_szsh;category_sjdbg_szsh;category_yjdbg_szsh"
)


@dataclass(frozen=True)
class Resource:
    kind: str
    url: str
    raw: bytes
    fetched_at: datetime
    metadata: dict = field(default_factory=dict)


def validate_symbol(value: str) -> str:
    """Canonical exchange-qualified symbol; never confuse e.g. SZ000001 with US CIK 1."""
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{6}\.(?:SH|SZ|BJ)", value):
        raise AppError("CN_INVALID_SYMBOL", "A股代码必须包含六位数字和.SH/.SZ/.BJ市场后缀。", 422)
    code, exchange = value.split(".")
    valid = (
        exchange == "SH" and code.startswith("6")
        or exchange == "SZ" and code.startswith(("0", "3"))
        or exchange == "BJ" and code.startswith(("4", "8", "920"))
    )
    if not valid:
        raise AppError("CN_INVALID_SYMBOL", "股票代码与A股市场后缀不匹配。", 422)
    return value


def _invalid_response():
    return AppError("CN_INVALID_RESPONSE", "A股数据源返回格式异常，旧数据已保留。", 502)


def _reject_constant(_value):
    raise ValueError("Nonfinite JSON number")


def _pairs(values):
    result = {}
    for key, value in values:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _decode(raw: bytes):
    try:
        return json.loads(
            raw.decode("utf-8-sig"), parse_float=Decimal,
            parse_constant=_reject_constant, object_pairs_hook=_pairs,
        )
    except (ValueError, UnicodeError, TypeError, AttributeError, RecursionError):
        raise _invalid_response() from None


def extract_rows(kind: str, raw: bytes) -> list[dict]:
    """Decode exact decimal JSON without float conversion or WAF/empty-body fallback."""
    data = _decode(raw)
    if not isinstance(data, dict):
        raise _invalid_response()
    if kind == "directory":
        rows = data.get("stockList")
    elif kind in {"announcements", "events"}:
        rows = data.get("announcements")
        # CNINFO returns null only for a genuinely empty announcement result.
        if rows is None and data.get("totalAnnouncement") == 0:
            rows = []
    elif kind in {"indicators", "income", "balance", "cashflow"}:
        if data.get("success") is not True or not isinstance(data.get("result"), dict):
            raise AppError("CN_SOURCE_ERROR", "A股财务数据源未返回有效结果，旧数据已保留。", 502)
        rows = data["result"].get("data")
    else:
        raise _invalid_response()
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise _invalid_response()
    return rows


def directory_entries(resources: list[Resource]) -> list[dict]:
    result = {}
    for resource in resources:
        if resource.kind != "directory":
            continue
        for row in extract_rows("directory", resource.raw):
            if row.get("category") != "A股":
                continue
            code, name, org_id = row.get("code"), row.get("zwjc"), row.get("orgId")
            if not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code):
                raise _invalid_response()
            if not isinstance(name, str) or not name.strip() or len(name) > 200:
                raise _invalid_response()
            exchange = "SH" if code.startswith("6") else "SZ" if code.startswith(("0", "3")) else "BJ"
            symbol = validate_symbol(f"{code}.{exchange}")
            if not isinstance(org_id, str) or not re.fullmatch(r"[A-Za-z0-9]{1,40}", org_id):
                raise _invalid_response()
            entry = {
                "symbol": symbol, "code": code, "exchange": exchange, "name": name.strip(),
                "org_id": org_id, "pinyin": str(row.get("pinyin") or "")[:100],
            }
            if symbol in result and result[symbol] != entry:
                raise _invalid_response()
            result[symbol] = entry
    if not result:
        raise AppError("CN_DIRECTORY_EMPTY", "A股公司目录为空，旧目录已保留。", 502)
    return list(result.values())


def _pdf_url(path: str) -> str:
    if not isinstance(path, str) or not re.fullmatch(
        r"finalpage/[0-9]{4}-[0-9]{2}-[0-9]{2}/[0-9]{6,30}\.[Pp][Dd][Ff]", path,
    ):
        raise AppError("CN_SOURCE_URL_REJECTED", "公告未提供可核查的巨潮PDF来源。", 422)
    try:
        datetime.strptime(path.split("/")[1], "%Y-%m-%d")
    except ValueError:
        raise _invalid_response() from None
    return "https://static.cninfo.com.cn/" + path


def _report_period(title):
    match = re.search(r"((?:19|20)[0-9]{2})\s*年?\s*(半年度|年度|第一季度|第三季度|一季度|三季度)", title)
    if not match:
        return "announcement", None
    year, label = match.groups()
    # Do not classify forecasts, explanations or meeting notices as financial reports.
    if "报告" not in title or any(word in title for word in ("说明会", "业绩预告", "摘要", "取消", "延期")):
        return "announcement", None
    kind, end = {
        "年度": ("annual", "12-31"), "半年度": ("semiannual", "06-30"),
        "第一季度": ("q1", "03-31"), "一季度": ("q1", "03-31"),
        "第三季度": ("q3", "09-30"), "三季度": ("q3", "09-30"),
    }[label]
    return kind, f"{year}-{end}"


def parse_filings(raw: bytes, symbol: str, org_id: str | None = None) -> list[dict]:
    symbol = validate_symbol(symbol)
    result = {}
    for row in extract_rows("announcements", raw):
        code = row.get("secCode")
        same_org = org_id is not None and row.get("orgId") == org_id
        historical_bse_code = (
            symbol.endswith(".BJ") and same_org and isinstance(code, str)
            and re.fullmatch(r"[0-9]{6}", code) and code.startswith(("4", "8", "920"))
        )
        if (org_id is not None and not same_org) or (code != symbol[:6] and not historical_bse_code):
            raise AppError("CN_SOURCE_COMPANY_MISMATCH", "公告公司身份不匹配，未导入该响应。", 422)
        identifier, title = row.get("announcementId"), row.get("announcementTitle")
        if not isinstance(identifier, str) or not re.fullmatch(r"[0-9]{1,30}", identifier):
            raise _invalid_response()
        if not isinstance(title, str) or not title.strip() or len(title) > 2000:
            raise _invalid_response()
        title = unescape(re.sub(r"<[^>]*>", "", title)).strip()
        moment = row.get("announcementTime")
        if type(moment) is not int or moment < 0:
            raise _invalid_response()
        try:
            filed = datetime.fromtimestamp(moment / 1000, UTC).astimezone(timezone(timedelta(hours=8))).date()
        except (ValueError, OverflowError, OSError):
            raise _invalid_response() from None
        form_type, report_date = _report_period(title)
        url = _pdf_url(row.get("adjunctUrl"))
        entry = {
            "id": identifier, "title": title, "filing_date": filed.isoformat(),
            "report_date": report_date, "form_type": form_type, "source_url": url,
            "source_security_code": code, "source_org_id": row.get("orgId"),
        }
        if identifier in result and result[identifier] != entry:
            raise _invalid_response()
        result[identifier] = entry
    return list(result.values())


class CnClient:
    def __init__(self, redis=None, transport=None):
        self.settings = get_settings()
        self.redis = redis if redis is not None else Redis.from_url(
            self.settings.redis_url, socket_connect_timeout=3, socket_timeout=3,
        )
        self.transport = transport

    def _limit(self, host: str):
        key = f"finsight:cn:rate:{host}"
        try:
            if self.redis.get(key + ":cooldown"):
                raise AppError("CN_RATE_LIMITED", "A股数据源处于冷却期，请稍后重试。", 503)
            for _ in range(30):
                if self.redis.set(key, "1", nx=True, px=1000):
                    return
                time.sleep(.15)
        except RedisError:
            raise AppError("CN_RATE_LIMITER_UNAVAILABLE", "共享限速服务不可用，已停止A股请求。", 503) from None
        raise AppError("CN_RATE_LIMITED", "等待A股请求配额超时，请稍后重试。", 503)

    def _request(self, kind: str, url: str, params: dict | None = None, *, method="GET", metadata=None):
        # Parameters are constructed internally; no generic URL fetch endpoint exists.
        if url not in _ENDPOINTS or method not in {"GET", "POST"}:
            raise AppError("CN_SOURCE_URL_REJECTED", "仅允许已配置的A股公开数据端点。", 422)
        host = urlparse(url).hostname
        self._limit(host)
        maximum = getattr(self.settings, "cn_max_bytes", 10_000_000)
        timeout = getattr(self.settings, "cn_timeout_seconds", 20)
        try:
            with httpx.Client(
                timeout=timeout, follow_redirects=False, trust_env=False, transport=self.transport,
                headers={"User-Agent": "FinSightAI/0.1 (public financial research)",
                         "Referer": "https://www.cninfo.com.cn/" if host == "www.cninfo.com.cn" else "https://data.eastmoney.com/"},
            ) as client, client.stream(
                method, url, params=params if method == "GET" else None,
                data=params if method == "POST" else None,
            ) as response:
                if response.status_code in (403, 429):
                    cooldown = 60
                    retry_after = response.headers.get("Retry-After", "")
                    try:
                        requested = int(retry_after) if retry_after.isdigit() else int(
                            (parsedate_to_datetime(retry_after) - datetime.now(UTC)).total_seconds()
                        )
                        cooldown = max(cooldown, requested)
                    except (ValueError, TypeError, OverflowError):
                        pass
                    try:
                        self.redis.set(f"finsight:cn:rate:{host}:cooldown", "1", ex=cooldown)
                    except RedisError:
                        pass
                    raise AppError("CN_RATE_LIMITED", "A股数据源限制访问，已停止请求并保留旧资料。", 503)
                if response.status_code != 200:
                    raise AppError("CN_UPSTREAM_UNAVAILABLE", "A股数据源暂时不可用，旧资料已保留。", 502)
                length = response.headers.get("Content-Length")
                if length and length.isdigit() and int(length) > maximum:
                    raise AppError("CN_RESPONSE_TOO_LARGE", "A股数据响应超过允许大小。", 502)
                body, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > maximum:
                        raise AppError("CN_RESPONSE_TOO_LARGE", "A股数据响应超过允许大小。", 502)
                    body.append(chunk)
                raw = b"".join(body)
                # Also reject 200 HTML/WAF, empty bodies and malformed structures.
                extract_rows(kind, raw)
                return Resource(kind, str(response.request.url), raw, datetime.now(UTC), {
                    "provider": "cninfo" if kind in {"directory", "announcements", "events"} else "eastmoney",
                    "request_method": method, "request_parameters": params or {}, **(metadata or {}),
                })
        except httpx.HTTPError:
            raise AppError("CN_NETWORK_ERROR", "无法连接A股数据源，旧资料已保留。", 503) from None

    def fetch_directory(self) -> list[Resource]:
        resource = self._request("directory", DIRECTORY_URL, metadata={
            "coverage": "CNINFO A-share directory; may include historical codes; active listing not guaranteed",
        })
        directory_entries([resource])
        return [resource]

    def _financial(self, kind: str, report: str, symbol: str) -> list[Resource]:
        resources, seen = [], set()
        expected_count = expected_pages = None
        for page in range(1, _MAX_PAGES + 1):
            resource = self._request(kind, FINANCE_URL, {
                "reportName": report, "columns": "ALL", "filter": f'(SECUCODE="{symbol}")',
                "pageNumber": page, "pageSize": _PAGE_SIZE, "sortColumns": "REPORT_DATE",
                "sortTypes": -1, "source": "HSF10", "client": "PC",
            }, metadata={"symbol": symbol, "report_name": report, "basis": "latest_provider_snapshot"})
            result = _decode(resource.raw)["result"]
            count, pages = result.get("count"), result.get("pages")
            if type(count) is not int or type(pages) is not int or count < 0 or pages < 0:
                raise _invalid_response()
            if pages > _MAX_PAGES or count > _MAX_PAGES * _PAGE_SIZE:
                raise AppError("CN_COVERAGE_LIMIT", "财务历史超过本次完整同步上限，未导入截断资料。", 502)
            if expected_count is None:
                expected_count, expected_pages = count, pages
            elif (count, pages) != (expected_count, expected_pages):
                raise AppError("CN_SOURCE_CHANGED", "分页期间上游资料发生变化，请重新同步。", 502)
            rows = extract_rows(kind, resource.raw)
            if len(rows) > _PAGE_SIZE or (page < pages and len(rows) != _PAGE_SIZE):
                raise _invalid_response()
            for row in rows:
                if row.get("SECUCODE") != symbol or row.get("SECURITY_CODE") != symbol[:6]:
                    raise AppError("CN_SOURCE_COMPANY_MISMATCH", "财务公司身份不匹配，未导入该响应。", 422)
                if row.get("CURRENCY") != "CNY":
                    raise AppError("CN_UNSUPPORTED_CURRENCY", "当前A股规范化仅支持明确标识的人民币报表。", 422)
                date = row.get("REPORT_DATE")
                if not isinstance(date, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2} 00:00:00", date):
                    raise _invalid_response()
                if date in seen:
                    raise AppError("CN_SOURCE_CHANGED", "财务分页存在重复期间，请重新同步。", 502)
                seen.add(date)
            resource.metadata.update({"page": page, "pages": pages, "total": count, "page_size": _PAGE_SIZE})
            resources.append(resource)
            if page >= max(pages, 1):
                if len(seen) != count:
                    raise _invalid_response()
                return resources
        raise AppError("CN_COVERAGE_LIMIT", "未完整取得A股财务分页，旧资料已保留。", 502)

    def fetch_company(self, symbol: str, org_id: str | None = None) -> list[Resource]:
        symbol = validate_symbol(symbol)
        indicators = self._financial("indicators", "RPT_F10_FINANCE_MAINFINADATA", symbol)
        rows = [row for resource in indicators for row in extract_rows(resource.kind, resource.raw)]
        if not rows:
            raise AppError("CN_NO_DATA", "该公司尚无可用的A股财务资料。", 404)
        org_type = rows[0].get("ORG_TYPE")
        prefix = _TEMPLATES.get(org_type)
        if prefix is None:
            raise AppError("CN_UNSUPPORTED_TEMPLATE", "该公司的财务模板暂未支持。", 422)
        resources = list(indicators)
        for kind, suffix in _REPORT_SUFFIX.items():
            resources.extend(self._financial(kind, "RPT_F10_FINANCE_" + prefix + suffix, symbol))
        if org_id is None:
            entry = next((entry for entry in directory_entries(self.fetch_directory()) if entry["symbol"] == symbol), None)
            if entry:
                org_id = entry["org_id"]
        if not isinstance(org_id, str) or not re.fullmatch(r"[A-Za-z0-9]{1,40}", org_id):
            raise AppError("CN_ORG_ID_REQUIRED", "请先更新A股目录以确认巨潮公司身份。", 422)
        params = {
            "pageNum": "1", "pageSize": str(_ANNOUNCEMENT_LIMIT),
            # CNINFO's public query uses its szse column for BSE as well. A bse
            # column returns an apparently valid zero and would hide real reports.
            "column": "sse" if symbol.endswith(".SH") else "szse",
            "tabName": "fulltext", "stock": f"{symbol[:6]},{org_id}",
            "category": _FINANCIAL_CATEGORIES, "isHLtitle": "false",
            "sortName": "time", "sortType": "desc",
        }
        announcements = self._request("announcements", ANNOUNCEMENTS_URL, params, method="POST", metadata={
            "symbol": symbol, "coverage": "latest_100_financial_announcements", "limit": _ANNOUNCEMENT_LIMIT,
        })
        parsed = _decode(announcements.raw)
        announcements.metadata.update({"total": parsed.get("totalAnnouncement"), "has_more": parsed.get("hasMore")})
        parse_filings(announcements.raw, symbol, org_id=org_id)
        resources.append(announcements)
        return resources
