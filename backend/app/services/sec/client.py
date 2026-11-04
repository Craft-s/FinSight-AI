import json
import random
import re
import time
from datetime import UTC, datetime
from decimal import Decimal
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import httpx
from redis import Redis
from redis.exceptions import RedisError

from app.core.config import get_settings
from app.core.errors import AppError


def validate_cik(value: str) -> str:
    if not re.fullmatch(r"[0-9]{1,10}", value) or int(value) == 0:
        raise AppError("INVALID_CIK", "CIK 必须为 1–10 位非零数字。", 422)
    return value.zfill(10)


def validate_sec_url(url: str) -> None:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except (TypeError, ValueError):
        raise AppError("SOURCE_URL_REJECTED", "仅允许指定 SEC 官方来源。", 422) from None
    if (
        not isinstance(url, str) or any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in url)
        or parsed.scheme != "https" or parsed.username is not None or parsed.password is not None
        or port not in (None, 443) or parsed.query or parsed.fragment
        or parsed.hostname not in {"www.sec.gov", "data.sec.gov"}
        or ".." in parsed.path or "%" in parsed.path
    ):
        raise AppError("SOURCE_URL_REJECTED", "仅允许指定 SEC 官方来源。", 422)
    valid = (
        parsed.hostname == "www.sec.gov"
        and parsed.path in {"/files/company_tickers.json", "/files/company_tickers_exchange.json"}
    ) or (
        parsed.hostname == "data.sec.gov"
        and bool(re.fullmatch(
            r"/(?:submissions/CIK[0-9]{10}(?:-submissions-[0-9]{3})?|api/xbrl/companyfacts/CIK[0-9]{10})\.json",
            parsed.path,
        ))
    )
    if not valid:
        raise AppError("SOURCE_URL_REJECTED", "不允许请求此来源路径。", 422)


class SecClient:
    def __init__(self, redis=None, transport=None):
        self.settings = get_settings()
        self.redis = redis if redis is not None else Redis.from_url(self.settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
        self.transport = transport

    def _limit(self):
        deadline = time.monotonic() + 30
        try:
            while time.monotonic() < deadline:
                if self.redis.get("finsight:sec:cooldown"):
                    raise AppError("SEC_COOLDOWN", "SEC 访问处于冷却期，请稍后重试。", 503)
                # Shared across all API and worker processes; 350 ms => below 3 QPS.
                if self.redis.set("finsight:sec:global-rate-slot", "1", nx=True, px=350):
                    return
                time.sleep(0.07)
        except RedisError as exc:
            raise AppError("RATE_LIMITER_UNAVAILABLE", "共享限速服务不可用，已停止 SEC 请求。", 503) from exc
        raise AppError("RATE_LIMITED", "等待 SEC 请求配额超时，请稍后重试。", 503)

    def fetch(self, url: str) -> tuple[bytes, datetime]:
        validate_sec_url(url)
        agent = self.settings.sec_user_agent
        if not re.search(r"[^\s@]+@[^\s@]+\.[^\s@]+", agent) or "example.com" in agent:
            raise AppError("SEC_USER_AGENT_REQUIRED", "请配置包含真实联系邮箱的 SEC_USER_AGENT。", 503)
        with httpx.Client(
            headers={"User-Agent": agent, "Accept": "application/json"},
            timeout=self.settings.sec_timeout_seconds,
            follow_redirects=False, trust_env=False, transport=self.transport,
        ) as client:
            for attempt in range(3):
                self._limit()
                try:
                    with client.stream("GET", url) as response:
                        if response.status_code == 403:
                            self.redis.set("finsight:sec:cooldown", "forbidden", ex=60)
                            raise AppError("SEC_FORBIDDEN", "SEC 拒绝访问，请核对 User-Agent 并稍后重试。", 503)
                        if response.status_code == 404:
                            raise AppError("SEC_NOT_FOUND", "SEC 未找到该申报主体或数据。", 404)
                        if response.status_code == 429 or response.status_code >= 500:
                            if attempt == 2:
                                raise AppError("SEC_UNAVAILABLE", "SEC 暂时不可用，已保留已有缓存。", 503)
                            retry_after = response.headers.get("Retry-After", "")
                            try:
                                delay = float(retry_after)
                            except ValueError:
                                try:
                                    delay = (parsedate_to_datetime(retry_after) - datetime.now(UTC)).total_seconds()
                                except (TypeError, ValueError):
                                    delay = 2 ** attempt + random.random()
                            if delay > 60:
                                self.redis.set("finsight:sec:cooldown", "retry-after", ex=int(delay) + 1)
                                raise AppError("SEC_RETRY_LATER", "SEC 要求较长冷却，请稍后手动重试。", 503)
                            time.sleep(max(0, delay))
                            continue
                        if response.status_code != 200:
                            raise AppError("SEC_INVALID_RESPONSE", "SEC 返回异常状态，未跟随重定向。", 503)
                        chunks, size = [], 0
                        for chunk in response.iter_bytes():
                            size += len(chunk)
                            if size > self.settings.sec_max_bytes:
                                raise AppError("SOURCE_TOO_LARGE", "SEC 数据超出允许大小。", 422)
                            chunks.append(chunk)
                        raw = b"".join(chunks)
                        try:
                            result = json.loads(raw, parse_float=Decimal)
                            if not isinstance(result, dict):
                                raise ValueError("Expected object")
                        except (ValueError, UnicodeError) as exc:
                            raise AppError("SEC_INVALID_RESPONSE", "SEC 返回内容不是有效 JSON 对象。", 503) from exc
                        return raw, datetime.now(UTC)
                except httpx.TimeoutException as exc:
                    if attempt == 2:
                        raise AppError("SEC_TIMEOUT", "SEC 请求超时，已保留已有缓存。", 503) from exc
                except httpx.HTTPError as exc:
                    if attempt == 2:
                        raise AppError("SEC_UNAVAILABLE", "SEC 网络连接失败，已保留已有缓存。", 503) from exc
                time.sleep(2 ** attempt)
        raise AppError("SEC_UNAVAILABLE", "SEC 请求失败。", 503)
