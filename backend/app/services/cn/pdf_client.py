"""CNINFO PDF fetches restricted to a saved announcement's canonical URL."""
import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from time import monotonic

import httpx
from redis.exceptions import RedisError

from app.core.errors import AppError
from app.services.cn.provider import CnClient


def validate_pdf_url(url):
    if not isinstance(url, str) or not re.fullmatch(
        r"https://static\.cninfo\.com\.cn/finalpage/[0-9]{4}-[0-9]{2}-[0-9]{2}/[0-9]{6,30}\.[Pp][Dd][Ff]", url,
    ):
        raise AppError("CN_PDF_URL_REJECTED", "仅允许所选公告的巨潮官方PDF地址。", 422)
    try:
        datetime.strptime(url.split("/")[-2], "%Y-%m-%d")
    except ValueError:
        raise AppError("CN_PDF_URL_REJECTED", "PDF来源日期无效。", 422) from None
    return url


class PdfClient(CnClient):
    def fetch(self, url):
        from app.services.cn.service import mode
        validate_pdf_url(url)
        if mode() != "cn_live":
            raise AppError("CN_PDF_LIVE_DISABLED", "当前A股模式不允许在线下载PDF。", 422)
        host = "static.cninfo.com.cn"
        self._limit(host)
        maximum = self.settings.cn_pdf_max_bytes
        deadline = monotonic() + self.settings.cn_pdf_timeout_seconds
        try:
            with httpx.Client(timeout=self.settings.cn_pdf_timeout_seconds, follow_redirects=False,
                trust_env=False, transport=self.transport,
                headers={"User-Agent": "FinSightAI/0.1 (public financial research)",
                         "Referer": "https://www.cninfo.com.cn/", "Accept-Encoding": "identity"}) as client:
                with client.stream("GET", url) as response:
                    if response.status_code in {403, 429}:
                        delay = 60
                        retry = response.headers.get("Retry-After", "")
                        try:
                            requested = int(retry) if retry.isdigit() else int((parsedate_to_datetime(retry) - datetime.now(UTC)).total_seconds())
                            delay = min(86400, max(delay, requested))
                        except (ValueError, TypeError, OverflowError):
                            pass
                        try:
                            self.redis.set(f"finsight:cn:rate:{host}:cooldown", "1", ex=delay)
                        except RedisError:
                            pass
                        raise AppError("CN_RATE_LIMITED", "巨潮PDF来源限制访问，请等待冷却后重试。", 503)
                    if response.status_code != 200:
                        raise AppError("CN_PDF_UNAVAILABLE", "PDF来源暂时不可用，已保存正文不受影响。", 502)
                    if response.headers.get("Content-Encoding", "identity").strip().lower() not in {"", "identity"}:
                        # PDFs already compress their own content. Reject HTTP
                        # compression before httpx can inflate a response beyond
                        # the download budget, including a mislabeled WAF body.
                        raise AppError("CN_PDF_INVALID", "PDF来源使用了不支持的传输编码。", 502)
                    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                    if content_type and content_type not in {"application/pdf", "application/octet-stream", "binary/octet-stream"}:
                        raise AppError("CN_PDF_INVALID", "来源未返回PDF内容。", 502)
                    length = response.headers.get("Content-Length", "")
                    if length.isdigit() and int(length) > maximum:
                        raise AppError("CN_PDF_TOO_LARGE", "PDF超过下载大小上限。", 422)
                    body, size = [], 0
                    for piece in response.iter_bytes(chunk_size=65_536):
                        if monotonic() > deadline:
                            raise AppError("CN_PDF_DOWNLOAD_TIMEOUT", "PDF下载超过时间上限，请稍后重试。", 503)
                        size += len(piece)
                        if size > maximum:
                            raise AppError("CN_PDF_TOO_LARGE", "PDF超过下载大小上限。", 422)
                        body.append(piece)
                    raw = b"".join(body)
                    if not raw.startswith(b"%PDF-"):
                        raise AppError("CN_PDF_INVALID", "来源未返回有效PDF，未保存正文。", 422)
                    return raw, datetime.now(UTC)
        except httpx.HTTPError:
            raise AppError("CN_PDF_NETWORK_ERROR", "无法连接巨潮PDF来源，请稍后重试。", 503) from None
