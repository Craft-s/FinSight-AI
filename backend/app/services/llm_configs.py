"""Private single-owner LLM configuration and revision-bound validation."""

import hashlib
import json
import threading
import time

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.security import decrypt_api_key, encrypt_api_key, encryption_ready, ollama_endpoint
from app.db.llm_models import LLMConfig
from app.db.models import now
from app.services.llm.factory import create_provider

PROVIDERS = {
    "openai": {"mode": "cloud", "label": "OpenAI"},
    "deepseek": {"mode": "cloud", "label": "DeepSeek"},
    "gemini": {"mode": "cloud", "label": "Gemini"},
    "ollama": {"mode": "local", "label": "Ollama"},
}
_models_cache: dict[tuple, tuple[float, list[dict]]] = {}
_cache_lock = threading.Lock()


def verification_binding(config: LLMConfig) -> str:
    endpoint = ollama_endpoint() if config.provider == "ollama" else "official-cloud-origin"
    key = "" if config.provider == "ollama" else get_settings().master_encryption_key.get_secret_value()
    return hashlib.sha256(json.dumps([config.provider, endpoint, key]).encode()).hexdigest()


def is_verified(config: LLMConfig) -> bool:
    if not config.model_id or config.verified_version != config.version:
        return False
    if config.provider != "ollama" and (not config.encrypted_key or not encryption_ready()):
        return False
    try:
        return config.verified_binding == verification_binding(config)
    except AppError:
        return False


def config_out(config: LLMConfig) -> dict:
    verified = is_verified(config)
    return {
        "id": config.id, "provider": config.provider, "model_id": config.model_id,
        "version": config.version, "verified_version": config.verified_version,
        "last_tested_at": config.last_tested_at, "is_verified": verified,
        "is_active": config.is_active and verified, "has_api_key": bool(config.encrypted_key),
        "options": config.options_json, "created_at": config.created_at, "updated_at": config.updated_at,
    }


def find_config(session, owner: str, config_id: str) -> LLMConfig:
    config = session.scalar(select(LLMConfig).where(LLMConfig.id == config_id, LLMConfig.owner_id == owner))
    if config is None:
        raise AppError("LLM_CONFIG_NOT_FOUND", "未找到此模型配置。", 404)
    return config


def get_verified_config(session, owner: str, config_id: str | None = None) -> LLMConfig:
    if config_id:
        config = find_config(session, owner, config_id)
    else:
        config = session.scalar(select(LLMConfig).where(LLMConfig.owner_id == owner, LLMConfig.is_active.is_(True)))
    if config is None or not is_verified(config):
        raise AppError("LLM_NOT_VERIFIED", "请先保存模型配置并通过连接测试，再启用 AI。", 409)
    return config


def provider_for_config(config: LLMConfig):
    if config.provider == "ollama":
        return create_provider("ollama", base_url=ollama_endpoint(), options=config.options_json)
    if not config.encrypted_key:
        raise AppError("API_KEY_REQUIRED", "请先为此服务商保存 API Key。", 422)
    key = decrypt_api_key(config.encrypted_key, config.owner_id, config.provider)
    return create_provider(config.provider, api_key=key, options=config.options_json)


def list_configs(session, owner: str) -> list[dict]:
    return [config_out(row) for row in session.scalars(
        select(LLMConfig).where(LLMConfig.owner_id == owner).order_by(LLMConfig.created_at)
    )]


def status(session) -> dict:
    configs = session.scalars(select(LLMConfig).where(LLMConfig.owner_id == "local-owner")).all()
    try:
        ollama_endpoint()
        ollama_configured = True
    except AppError:
        ollama_configured = False
    ready = encryption_ready()
    return {
        "available": any(row.is_active and is_verified(row) for row in configs),
        "encryption_ready": ready, "ollama_configured": ollama_configured,
        "providers": [
            {"id": key, **metadata,
             "configured": any(row.provider == key for row in configs),
             "options": ["max_output_tokens", "timeout_seconds", "temperature"]
                        + (["reasoning_effort"] if key in {"openai", "deepseek"} else [])}
            for key, metadata in PROVIDERS.items()
        ],
    }


def _conflict():
    return AppError("CONFIG_VERSION_CONFLICT", "配置已被修改，请重新加载后再操作。", 409)


def _version(config: LLMConfig, expected: int):
    if config.version != expected:
        raise _conflict()


def save_config(session, owner: str, body) -> dict:
    previous = find_config(session, owner, body.id) if body.id else None
    if previous:
        if body.version is None:
            raise AppError("CONFIG_VERSION_REQUIRED", "更新配置时必须提供当前版本。", 422)
        _version(previous, body.version)
        if previous.provider != body.provider:
            raise AppError("PROVIDER_IMMUTABLE", "不能更改已有配置的服务商，请选择对应服务商配置。", 422)
    elif session.scalar(select(LLMConfig.id).where(LLMConfig.owner_id == owner, LLMConfig.provider == body.provider)):
        raise AppError("CONFIG_ALREADY_EXISTS", "此服务商已有配置，请重新加载后编辑。", 409)
    if body.provider == "ollama":
        ollama_endpoint()
        if body.api_key and body.api_key.get_secret_value():
            raise AppError("OPTION_NOT_SUPPORTED", "Ollama 配置不接受云端 API Key。", 422)
        ciphertext = None
    else:
        # Even changing a saved model needs usable encryption, rather than
        # presenting a broken credential as a usable configuration.
        if not encryption_ready():
            raise AppError("ENCRYPTION_NOT_CONFIGURED", "模型密钥加密尚未就绪，请部署者配置有效的主密钥。", 503)
        ciphertext = previous.encrypted_key if previous else None
        if body.api_key is not None:
            key = body.api_key.get_secret_value().strip()
            ciphertext = encrypt_api_key(key, owner, body.provider) if key else None
        if not ciphertext:
            raise AppError("API_KEY_REQUIRED", "请先为此服务商保存 API Key。", 422)
    options = body.options.model_dump(exclude_none=True)
    model_id = body.model_id.strip()
    candidate = LLMConfig(owner_id=owner, provider=body.provider, model_id=model_id,
                          encrypted_key=ciphertext, options_json=options)
    provider_for_config(candidate).validate_config(model_id)
    if previous:
        changed = (previous.model_id != model_id or previous.encrypted_key != ciphertext
                   or previous.options_json != options)
        if not changed:
            return config_out(previous)
        result = session.execute(update(LLMConfig).where(
            LLMConfig.id == previous.id, LLMConfig.owner_id == owner, LLMConfig.version == body.version,
        ).values(model_id=model_id, encrypted_key=ciphertext, options_json=options,
                 version=body.version + 1, verified_version=None, verified_binding=None,
                 last_tested_at=None, is_active=False, updated_at=now()))
        if result.rowcount != 1:
            session.rollback()
            raise _conflict()
        config_id = previous.id
    else:
        session.add(candidate)
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            raise AppError("CONFIG_ALREADY_EXISTS", "此服务商已有配置，请重新加载后编辑。", 409) from None
        config_id = candidate.id
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise AppError("CONFIG_ALREADY_EXISTS", "此服务商已有配置，请重新加载后编辑。", 409) from None
    session.expire_all()
    return config_out(find_config(session, owner, config_id))


def models_for_config(session, owner: str, config_id: str, refresh: bool = False) -> dict:
    config = find_config(session, owner, config_id)
    version, binding = config.version, verification_binding(config)
    cache_key = (owner, config.id, version, binding)
    with _cache_lock:
        cached = _models_cache.get(cache_key)
        if not refresh and cached and cached[0] > time.monotonic():
            return {"items": cached[1], "cached": True}
    provider = provider_for_config(config)
    session.commit()  # Do not retain DB locks while waiting for a vendor.
    items = provider.list_models()
    session.expire_all()
    current = find_config(session, owner, config_id)
    if current.version != version or verification_binding(current) != binding:
        raise _conflict()
    ttl = get_settings().llm_model_cache_seconds
    if ttl:
        with _cache_lock:
            if len(_models_cache) >= 128:
                _models_cache.clear()
            _models_cache[cache_key] = (time.monotonic() + ttl, items)
    return {"items": items, "cached": False}


def test_config(session, owner: str, config_id: str, expected: int) -> dict:
    config = find_config(session, owner, config_id)
    _version(config, expected)
    if not config.model_id:
        raise AppError("MODEL_REQUIRED", "请选择或手动填写精确模型 ID。", 422)
    binding = verification_binding(config)
    provider, model_id = provider_for_config(config), config.model_id
    session.commit()
    error = None
    try:
        result = provider.test_connection(model_id)
    except AppError as exc:
        error = exc
    except Exception:
        error = AppError("PROVIDER_UNAVAILABLE", "模型服务暂时不可用，请检查配置后重试。", 503)
    session.expire_all()
    current = find_config(session, owner, config_id)
    if current.version != expected or verification_binding(current) != binding:
        raise _conflict()
    values = {"last_tested_at": now(), "updated_at": now(),
              "verified_version": None if error else expected,
              "verified_binding": None if error else binding}
    if error:
        values["is_active"] = False
    mutation = session.execute(update(LLMConfig).where(
        LLMConfig.id == config_id, LLMConfig.owner_id == owner, LLMConfig.version == expected,
    ).values(**values))
    if mutation.rowcount != 1:
        session.rollback()
        raise _conflict()
    session.commit()
    if error:
        raise error
    session.expire_all()
    return {"config": config_out(find_config(session, owner, config_id)),
            "usage": result.get("usage", {}), "duration_ms": result.get("duration_ms", 0)}


def activate_config(session, owner: str, config_id: str, expected: int) -> dict:
    config = get_verified_config(session, owner, config_id)
    _version(config, expected)
    session.execute(update(LLMConfig).where(LLMConfig.owner_id == owner).values(is_active=False))
    activated = session.execute(update(LLMConfig).where(
        LLMConfig.id == config_id, LLMConfig.owner_id == owner,
        LLMConfig.version == expected, LLMConfig.verified_version == expected,
    ).values(is_active=True, updated_at=now()))
    if activated.rowcount != 1:
        session.rollback()
        raise _conflict()
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise _conflict() from None
    session.expire_all()
    return config_out(find_config(session, owner, config_id))


def delete_config(session, owner: str, config_id: str, expected: int) -> dict:
    config = find_config(session, owner, config_id)
    _version(config, expected)
    result = session.execute(delete(LLMConfig).where(
        LLMConfig.id == config_id, LLMConfig.owner_id == owner, LLMConfig.version == expected,
    ))
    if result.rowcount != 1:
        session.rollback()
        raise _conflict()
    session.commit()
    with _cache_lock:
        for key in list(_models_cache):
            if key[:2] == (owner, config_id):
                del _models_cache[key]
    return {"deleted": True}
