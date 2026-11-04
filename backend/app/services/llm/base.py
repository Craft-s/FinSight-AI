"""Bounded, synchronous adapters for use inside the existing RQ workers."""

import json
import math
import re
from time import perf_counter
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.core.errors import AppError

ERROR_MESSAGES = {
    "AUTH_INVALID": "模型服务凭据无效或没有访问权限。",
    "MODEL_NOT_FOUND": "模型不存在或当前账户不可访问，请检查精确模型 ID。",
    "MODEL_NOT_SUPPORTED": "模型或所选参数不支持当前文本请求。",
    "RATE_LIMITED": "模型服务请求受限，请稍后重试。",
    "PROVIDER_UNAVAILABLE": "模型服务暂时不可用，请检查部署网络。",
    "TIMEOUT": "模型服务请求超时，请稍后重试。",
    "INVALID_RESPONSE": "模型返回拒答、截断或无效响应，结果未被采用。",
}


def provider_error(code: str, status: int = 502) -> AppError:
    return AppError(code, ERROR_MESSAGES[code], status)


def normalize_error(exc: Exception) -> AppError:
    """Never include exception text, upstream response bodies, or credentials."""
    if isinstance(exc, AppError):
        return exc
    if isinstance(exc, httpx.TimeoutException) or type(exc).__name__ == "APITimeoutError":
        return provider_error("TIMEOUT", 504)
    if type(exc).__name__ == "APIResponseValidationError":
        return provider_error("INVALID_RESPONSE")
    details = getattr(exc, "details", None)
    if isinstance(details, dict):
        upstream_error = details.get("error")
        if isinstance(upstream_error, dict):
            for detail in upstream_error.get("details", []) or []:
                if isinstance(detail, dict) and detail.get("reason") in {
                    "API_KEY_INVALID", "API_KEY_EXPIRED", "API_KEY_SERVICE_BLOCKED",
                }:
                    return provider_error("AUTH_INVALID", 422)
    code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
    if code in (401, 403):
        return provider_error("AUTH_INVALID", 422)
    if code == 404:
        return provider_error("MODEL_NOT_FOUND", 422)
    if code == 429:
        return provider_error("RATE_LIMITED", 429)
    if code in (408, 504):
        return provider_error("TIMEOUT", 504)
    if code in (400, 405, 409, 422):
        return provider_error("MODEL_NOT_SUPPORTED", 422)
    if isinstance(exc, (ValueError, KeyError, TypeError, AttributeError, IndexError)):
        return provider_error("INVALID_RESPONSE")
    return provider_error("PROVIDER_UNAVAILABLE", 503)


def validate_options(options: dict | None) -> dict:
    options = dict(options or {})
    if set(options) - {"max_output_tokens", "timeout_seconds", "temperature", "reasoning_effort"}:
        raise provider_error("MODEL_NOT_SUPPORTED", 422)
    maximum = options.get("max_output_tokens", 2048)
    timeout = options.get("timeout_seconds", 30)
    temperature = options.get("temperature")
    effort = options.get("reasoning_effort")
    if type(maximum) is not int or not 16 <= maximum <= 8192:
        raise provider_error("MODEL_NOT_SUPPORTED", 422)
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 5 <= timeout <= 120:
        raise provider_error("MODEL_NOT_SUPPORTED", 422)
    if temperature is not None and (
        type(temperature) not in (int, float) or not math.isfinite(temperature)
        or not 0 <= temperature <= 2
    ):
        raise provider_error("MODEL_NOT_SUPPORTED", 422)
    if effort is not None and effort not in {"none", "minimal", "low", "medium", "high", "max", "xhigh"}:
        raise provider_error("MODEL_NOT_SUPPORTED", 422)
    return {"max_output_tokens": maximum, "timeout_seconds": timeout,
            **({"temperature": temperature} if temperature is not None else {}),
            **({"reasoning_effort": effort} if effort is not None else {})}


def validate_model_id(model_id: str) -> str:
    if not isinstance(model_id, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,199}", model_id):
        raise provider_error("MODEL_NOT_FOUND", 422)
    if ".." in model_id or "//" in model_id:
        raise provider_error("MODEL_NOT_FOUND", 422)
    return model_id


def usage_counts(**values: Any) -> dict:
    return {key: value for key, value in values.items() if type(value) is int and value >= 0}


def model_record(provider: str, model_id: str, name: str | None = None,
                 supports_text: bool | None = None, context: int | None = None) -> dict:
    return {"id": model_id, "display_name": name or model_id, "provider": provider,
            "supports_text_generation": supports_text,
            "context_window": context if type(context) is int and context > 0 else None}


def reject_json_constant(_value):
    raise ValueError("Non-finite JSON constants are invalid")


class OriginTransport(httpx.BaseTransport):
    """Pin every SDK request, including pagination URLs, to the chosen origin."""

    def __init__(self, origin: str, transport: httpx.BaseTransport | None = None):
        self.origin = httpx.URL(origin)
        self.transport = transport or httpx.HTTPTransport(retries=0, trust_env=False)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        target = request.url
        expected = self.origin
        if (target.scheme, target.host, target.port) != (expected.scheme, expected.host, expected.port):
            raise provider_error("PROVIDER_UNAVAILABLE", 503)
        prefix = expected.path.rstrip("/")
        if prefix and not target.path.startswith(prefix + "/"):
            raise provider_error("PROVIDER_UNAVAILABLE", 503)
        return self.transport.handle_request(request)

    def close(self):
        self.transport.close()


def local_base_url(base_url: str | None) -> str:
    """The caller must obtain this URL from the operator's allowlist, never UI input."""
    try:
        parsed = urlsplit(base_url or "")
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
            raise ValueError
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError
    except (ValueError, TypeError):
        raise provider_error("PROVIDER_UNAVAILABLE", 503) from None
    return (base_url or "").rstrip("/")


class BaseProvider:
    provider = ""
    base_url = ""

    def __init__(self, api_key: str | None = None, base_url: str | None = None,
                 options: dict | None = None, *, transport: httpx.BaseTransport | None = None):
        if self.provider != "ollama" and not api_key:
            raise provider_error("AUTH_INVALID", 422)
        if base_url and base_url.rstrip("/") != self.base_url.rstrip("/"):
            raise provider_error("PROVIDER_UNAVAILABLE", 503)
        self.api_key = api_key
        self.options = validate_options(options)
        self.transport = transport

    def _http_client(self, options: dict | None = None) -> httpx.Client:
        timeout = (options or self.options)["timeout_seconds"]
        return httpx.Client(transport=OriginTransport(self.base_url, self.transport),
                            timeout=httpx.Timeout(timeout, connect=min(10, timeout)),
                            follow_redirects=False, trust_env=False)

    def _generate(self, model_id: str, system: str, prompt: str, options: dict, structured: bool) -> dict:
        raise NotImplementedError

    def list_models(self) -> list[dict]:
        raise NotImplementedError

    def validate_config(self, model_id: str, options: dict | None = None) -> dict:
        resolved = validate_options(options if options is not None else self.options)
        if model_id:
            validate_model_id(model_id)
        effort = resolved.get("reasoning_effort")
        if effort is not None and (
            self.provider not in {"openai", "deepseek"}
            or self.provider == "openai" and effort not in {"none", "minimal", "low", "medium", "high", "xhigh"}
            or self.provider == "deepseek" and effort not in {"none", "low", "high", "max"}
        ):
            raise provider_error("MODEL_NOT_SUPPORTED", 422)
        if "temperature" in resolved and self.provider == "deepseek" and effort != "none":
            # DeepSeek's protocol ignores temperature in thinking mode. Do not silently ignore it.
            raise provider_error("MODEL_NOT_SUPPORTED", 422)
        return resolved

    def generate_structured(self, model_id: str, system: str, prompt: str, options: dict | None = None) -> dict:
        result = self._checked_generate(model_id, system, prompt, options, structured=True)
        try:
            parsed = json.loads(result["content"], parse_constant=reject_json_constant)
            if not isinstance(parsed, dict):
                raise ValueError
        except (ValueError, TypeError):
            raise provider_error("INVALID_RESPONSE") from None
        return result

    def test_connection(self, model_id: str) -> dict:
        result = self._checked_generate(
            model_id, "You are a connection test. Reply with the word OK only.",
            "Reply OK.", {"max_output_tokens": 512}, structured=False,
        )
        # A model ID being listed does not count as a successful connection test.
        return {"model_id": model_id, "usage": result["usage"], "duration_ms": result["duration_ms"]}

    def _checked_generate(self, model_id: str, system: str, prompt: str,
                          options: dict | None, structured: bool) -> dict:
        validate_model_id(model_id)
        resolved = validate_options({**self.options, **(options or {})})
        self.validate_config(model_id, resolved)
        if not isinstance(system, str) or not isinstance(prompt, str) or len(system) + len(prompt) > 24_000:
            raise AppError("CONTEXT_TOO_LARGE", "模型输入超出当前长度限制。", 422)
        started = perf_counter()
        try:
            result = self._generate(model_id, system, prompt, resolved, structured)
            content = result["content"]
            if not isinstance(content, str) or not content.strip() or len(content) > 65_536:
                raise provider_error("INVALID_RESPONSE")
            result["duration_ms"] = round((perf_counter() - started) * 1000)
            return result
        except Exception as exc:
            raise normalize_error(exc) from None
