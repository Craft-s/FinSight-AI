"""Explicit, hash-verified real archives; never an online-error fallback."""
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from app.core.config import get_settings
from app.core.errors import AppError
from app.services.cn.provider import Resource
from app.services.cn.service import (
    ensure_cn_dataset,
    import_archived_resources,
    import_directory,
    mode,
)


def import_manifest(session, filename):
    ensure_cn_dataset(session)
    if mode() != "cn_snapshot":
        raise AppError("CN_ARCHIVE_MODE_REQUIRED", "A股归档导入必须使用独立cn_snapshot模式。", 422)
    try:
        path = Path(filename).resolve()
        if path.stat().st_size > 100_000:
            raise ValueError("Oversized manifest")
        manifest = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(manifest, dict) or manifest.get("source_kind") != "cn_snapshot":
            raise ValueError("Source kind")
        scope = manifest["scope"]
        if scope != "directory" and not re.fullmatch(r"[0-9]{6}\.(SH|SZ|BJ)", scope):
            raise ValueError("Symbol")
        resources = []
        if not isinstance(manifest["resources"], list) or not 1 <= len(manifest["resources"]) <= 30:
            raise ValueError("Resource count")
        for item in manifest["resources"]:
            if not isinstance(item, dict) or not isinstance(item.get("metadata", {}), dict):
                raise ValueError("Resource metadata")
            resource_path = (path.parent / item["file"]).resolve()
            if not resource_path.is_relative_to(path.parent) or resource_path.stat().st_size > get_settings().cn_max_bytes:
                raise ValueError("Path or size")
            raw = resource_path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != item["sha256"]:
                raise ValueError("Hash")
            url = urlsplit(item["url"])
            if url.scheme != "https" or url.username or url.password or url.port not in (None, 443):
                raise ValueError("Source URL")
            allowed = {"datacenter-web.eastmoney.com": {"/api/data/v1/get"},
                       "www.cninfo.com.cn": {"/new/data/szse_stock.json", "/new/hisAnnouncement/query"}}
            if url.hostname not in allowed or url.path not in allowed[url.hostname]:
                raise ValueError("Source URL")
            stamp = datetime.fromisoformat(item["fetched_at"].replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                raise ValueError("Fetch timezone")
            resources.append(Resource(kind=item["kind"], url=item["url"], raw=raw,
                                      fetched_at=stamp, metadata=item.get("metadata", {})))
        if scope == "directory":
            count = import_directory(session, resources)
            result = {"companies_processed": count, "source_kind": "cn_snapshot"}
        else:
            company = import_archived_resources(session, scope, resources)
            result = {"symbol": company.symbol, "source_kind": "cn_snapshot"}
        session.commit()
        return result
    except AppError:
        session.rollback()
        raise
    except (KeyError, ValueError, TypeError, OSError, UnicodeError):
        session.rollback()
        raise AppError("CN_MANIFEST_INVALID", "A股归档清单、来源、路径或哈希无效。", 422) from None
