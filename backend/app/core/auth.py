import hmac

from fastapi import Header

from app.core.config import get_settings
from app.core.errors import AppError


def require_owner(x_admin_token: str | None = Header(default=None)) -> str:
    expected = get_settings().local_admin_token.get_secret_value()
    if len(expected) < 16:
        raise AppError("ADMIN_NOT_CONFIGURED", "请先配置至少 16 字符的 LOCAL_ADMIN_TOKEN。", 503)
    # compare_digest accepts only ASCII str values; compare bytes so malformed
    # non-ASCII request headers remain authentication failures, not server errors.
    if not x_admin_token or not hmac.compare_digest(x_admin_token.encode("utf-8"), expected.encode("utf-8")):
        raise AppError("AUTH_REQUIRED", "此操作需要本地管理员凭据。", 401)
    return "local-owner"
