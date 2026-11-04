"""AI credential encryption; no secret is returned by a public response schema."""

import ipaddress
import json
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings
from app.core.errors import AppError


def credential_cipher() -> Fernet:
    try:
        return Fernet(get_settings().master_encryption_key.get_secret_value().encode("ascii"))
    except (ValueError, UnicodeError):
        raise AppError("ENCRYPTION_NOT_CONFIGURED", "模型密钥加密尚未就绪，请部署者配置有效的主密钥。", 503) from None


def encryption_ready() -> bool:
    try:
        credential_cipher()
        return True
    except AppError:
        return False


def encrypt_api_key(api_key: str, owner_id: str, provider: str) -> str:
    # Bind the encrypted payload to its recipient to prevent ciphertext swapping
    # from sending one provider's credential to a different provider.
    payload = json.dumps({"key": api_key, "owner": owner_id, "provider": provider})
    return credential_cipher().encrypt(payload.encode()).decode("ascii")


def decrypt_api_key(ciphertext: str, owner_id: str, provider: str) -> str:
    cipher = credential_cipher()
    try:
        payload = json.loads(cipher.decrypt(ciphertext.encode("ascii")))
        if payload["owner"] != owner_id or payload["provider"] != provider:
            raise ValueError("Credential binding mismatch")
        value = payload["key"]
        if not isinstance(value, str) or not value:
            raise ValueError("Empty credential")
        return value
    except (InvalidToken, ValueError, KeyError, TypeError, UnicodeError):
        raise AppError("CREDENTIAL_UNREADABLE", "模型密钥无法解密，请检查主密钥或重新保存服务商密钥。", 503) from None


def ollama_endpoint() -> str:
    """Only an operator-controlled origin is permitted; never accept a request URL."""
    value = get_settings().ollama_base_url.strip()
    try:
        parts = urlsplit(value)
        if (any(ord(character) < 32 or ord(character) == 127 for character in value)
                or parts.scheme not in {"http", "https"} or not parts.hostname
                or parts.username is not None or parts.password is not None or parts.query or parts.fragment
                or parts.path not in {"", "/"} or parts.port == 0):
            raise ValueError("Invalid Ollama origin")
        host = parts.hostname.lower().rstrip(".")
        if host in {"metadata.google.internal", "instance-data", "metadata"}:
            raise ValueError("Metadata origin")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        if address and (address.is_link_local or address.is_multicast or address.is_unspecified):
            raise ValueError("Invalid destination")
        return value.rstrip("/")
    except ValueError:
        raise AppError("OLLAMA_NOT_CONFIGURED", "Ollama 地址尚未配置或不符合受控地址要求，请联系部署者。", 503) from None
