"""Only selected accession documents, never user-supplied remote URLs."""
import json
import re
import time
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup

from app.core.errors import AppError
from app.services.sec.client import SecClient


def filing_origin(filing):
    parsed = urlsplit(filing.source_url)
    accession = filing.accession_number.replace("-", "")
    match = re.fullmatch(r"/Archives/edgar/data/([0-9]{1,10})/([0-9]{18})/([A-Za-z0-9][A-Za-z0-9_.-]*\.(?:htm|html))", parsed.path, re.I)
    if (parsed.scheme != "https" or parsed.hostname != "www.sec.gov" or parsed.port not in (None, 443)
            or parsed.username or parsed.password or parsed.query or parsed.fragment or not match
            or match[2] != accession or ".." in match[3]):
        raise AppError("DOCUMENT_URL_REJECTED", "财报地址不属于已选申报的官方目录。", 422)
    return "https://www.sec.gov" + parsed.path.rsplit("/", 1)[0] + "/"


def validate_document_url(filing, url, *, allow_index=False):
    origin = filing_origin(filing)
    if not isinstance(url, str) or not url.startswith(origin):
        raise AppError("DOCUMENT_URL_REJECTED", "财报地址不属于已选申报的官方目录。", 422)
    name = url[len(origin):]
    suffix = r"(?:htm|html)" if not allow_index else r"(?:htm|html|json)"
    if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*\." + suffix, name, re.I)
            or ".." in name or (name.endswith(".json") and name != "index.json")):
        raise AppError("DOCUMENT_URL_REJECTED", "财报正文或附件路径不符合允许范围。", 422)
    return url


class DocumentClient(SecClient):
    def fetch_document(self, filing, url, *, index=False):
        validate_document_url(filing, url, allow_index=index)
        agent = self.settings.sec_user_agent
        if not re.search(r"[^\s@]+@[^\s@]+\.[^\s@]+", agent) or "example.com" in agent:
            raise AppError("SEC_USER_AGENT_REQUIRED", "请配置包含真实联系邮箱的 SEC_USER_AGENT。", 503)
        maximum = min(self.settings.document_max_bytes, 1_000_000) if index else self.settings.document_max_bytes
        with httpx.Client(headers={"User-Agent": agent, "Accept": "application/json" if index else "text/html"},
                          timeout=self.settings.sec_timeout_seconds, follow_redirects=False,
                          trust_env=False, transport=self.transport) as client:
            for attempt in range(3):
                self._limit()
                try:
                    with client.stream("GET", url) as response:
                        code = response.status_code
                        if code == 403:
                            self.redis.set("finsight:sec:cooldown", "forbidden", ex=60)
                            raise AppError("SEC_FORBIDDEN", "SEC 拒绝访问，请核对 User-Agent 并稍后重试。", 503)
                        if code == 404:
                            raise AppError("SEC_NOT_FOUND", "SEC 未找到所选财报正文或附件。", 404)
                        if code == 429 or code >= 500:
                            if attempt < 2:
                                retry = response.headers.get("Retry-After", "")
                                try:
                                    delay = float(retry)
                                except ValueError:
                                    delay = 2 ** attempt
                                if delay > 30:
                                    self.redis.set("finsight:sec:cooldown", "retry-after", ex=min(int(delay) + 1, 3600))
                                    raise AppError("SEC_RETRY_LATER", "SEC 要求较长冷却，请稍后手动重试。", 503)
                                time.sleep(max(delay, 0))
                                continue
                            raise AppError("SEC_UNAVAILABLE", "SEC 正文服务暂时不可用，已保留已有文档。", 503)
                        if code != 200:
                            raise AppError("SEC_INVALID_RESPONSE", "SEC 返回异常状态，未跟随重定向。", 503)
                        media = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                        accepted = {"application/json"} if index else {"text/html", "application/xhtml+xml", "text/plain"}
                        if media not in accepted:
                            raise AppError("DOCUMENT_TYPE_UNSUPPORTED", "当前仅支持所选申报的HTML正文和HTML附件。", 422)
                        raw, size = [], 0
                        for part in response.iter_bytes():
                            size += len(part)
                            if size > maximum:
                                raise AppError("SOURCE_TOO_LARGE", "财报正文或附件超出允许大小。", 422)
                            raw.append(part)
                        return b"".join(raw), datetime.now(UTC)
                except httpx.TimeoutException:
                    if attempt == 2:
                        raise AppError("SEC_TIMEOUT", "SEC 请求超时，已保留已有缓存。", 503) from None
                except httpx.HTTPError:
                    if attempt == 2:
                        raise AppError("SEC_UNAVAILABLE", "SEC 网络连接失败，已保留已有缓存。", 503) from None
                time.sleep(2 ** attempt)
        raise AppError("SEC_UNAVAILABLE", "SEC 正文请求失败。", 503)


def exhibit_99_urls(filing, index_raw, directory_raw):
    """Require both accession index listing and EX-99.1 table metadata, never filename guessing."""
    try:
        entries = json.loads(directory_raw)["directory"]["item"]
        names = {item["name"] for item in entries if isinstance(item, dict) and isinstance(item.get("name"), str)}
    except (ValueError, KeyError, TypeError):
        raise AppError("DOCUMENT_INDEX_INVALID", "所选申报的附件索引无效。", 502) from None
    soup = BeautifulSoup(index_raw, "lxml")
    urls = []
    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if not any(re.fullmatch(r"EX-99\.1(?:\.[0-9]+)?", cell.get_text(" ", strip=True), re.I) for cell in cells):
            continue
        for link in row.find_all("a", href=True):
            href = link["href"]
            if href.startswith(filing_origin(filing)):
                candidate = href
            elif href.startswith("/Archives/"):
                candidate = "https://www.sec.gov" + href
            elif "/" not in href:
                candidate = filing_origin(filing) + href
            else:
                continue
            name = candidate.rsplit("/", 1)[-1]
            if name not in names:
                continue
            try:
                validate_document_url(filing, candidate)
            except AppError:
                continue
            if candidate not in urls:
                urls.append(candidate)
    return urls[:2]
