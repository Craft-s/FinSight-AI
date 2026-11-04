import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from app.core.config import get_settings
from app.core.errors import AppError
from app.db.models import Company, Filing
from app.db.session import ensure_dataset, session_factory
from app.services.sync import import_company, load_snapshot_directory, refresh_tickers


def import_document_manifest(session, path):
    from app.services.documents.service import import_document
    try:
        manifest_path = Path(path).resolve()
        body = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        source_path = (manifest_path.parent / body["file"]).resolve()
        if not source_path.is_relative_to(manifest_path.parent) or source_path.stat().st_size > get_settings().document_max_bytes:
            raise ValueError("Unsafe or oversized source file")
        raw = source_path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != body["sha256"]:
            raise ValueError("Hash mismatch")
        filing = session.scalar(select(Filing).join(Company).where(
            Company.cik == str(body["cik"]).zfill(10), Filing.accession_number == body["accession"],
        ))
        if not filing:
            raise AppError("FILING_NOT_FOUND", "请先同步该公司与指定申报元数据。", 404)
        timestamp = datetime.fromisoformat(body["fetched_at"].replace("Z", "+00:00"))
        document = import_document(session, filing, raw, body["source_url"], timestamp,
                                   body["source_kind"], body.get("source_metadata", {}), body.get("role", "primary"))
        session.commit()
        return {"document_id": document.id, "filing_id": filing.id, "sha256": document.sha256,
                "source_kind": document.source_kind}
    except (KeyError, ValueError, OSError, TypeError):
        raise AppError("DOCUMENT_MANIFEST_INVALID", "文档清单、路径或原始文件哈希无效。", 422) from None


def main():
    parser = argparse.ArgumentParser(description="FinSight 数据管理（请先 alembic upgrade head）")
    commands = parser.add_subparsers(dest="command", required=True)
    snapshot = commands.add_parser("import-snapshot")
    snapshot.add_argument("--directory", required=True)
    commands.add_parser("refresh-tickers")
    document = commands.add_parser("import-document")
    document.add_argument("--manifest", required=True)
    commands.add_parser("cn-refresh-directory")
    cn_sync = commands.add_parser("cn-sync")
    cn_sync.add_argument("--symbol", required=True)
    cn_archive = commands.add_parser("cn-import-archive")
    cn_archive.add_argument("--manifest", required=True)
    args = parser.parse_args()
    with session_factory()() as session:
        ensure_dataset(session)
        if args.command.startswith("cn-"):
            from app.api.cn import find_company, valid_symbol
            from app.db.cn_models import CnSyncJob
            from app.services.cn.archive import import_manifest
            from app.services.cn.service import ensure_cn_dataset, run_cn_sync
            ensure_cn_dataset(session)
            if args.command == "cn-import-archive":
                result = import_manifest(session, args.manifest)
            else:
                scope = "directory" if args.command == "cn-refresh-directory" else valid_symbol(args.symbol)
                if scope != "directory":
                    find_company(session, scope)
                active = session.scalar(select(CnSyncJob).where(CnSyncJob.active_key == scope))
                if active:
                    raise AppError("CN_SYNC_CONFLICT", "该A股范围已有任务，请先查询现有任务。", 409)
                job = CnSyncJob(owner_id="local-owner", scope=scope, active_key=scope)
                session.add(job)
                session.commit()
                run_cn_sync(job.id)
                session.refresh(job)
                if job.status != "succeeded":
                    raise AppError(job.error_code or "CN_SYNC_FAILED", job.error_message or "A股同步失败。", 503)
                result = {"job_id": job.id, "status": job.status, **job.result}
            print(json.dumps(result, ensure_ascii=False))
        elif args.command == "import-snapshot":
            company = import_company(session, *load_snapshot_directory(args.directory))
            session.commit()
            print(json.dumps({"cik": company.cik, "name": company.name, "source_kind": company.source_kind}, ensure_ascii=False))
        elif args.command == "refresh-tickers":
            print(json.dumps({"companies_processed": refresh_tickers(session)}))
        else:
            print(json.dumps(import_document_manifest(session, args.manifest)))


if __name__ == "__main__":
    main()
