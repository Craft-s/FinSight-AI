"""Versioned A-share cache and RQ tasks, separate from all SEC tables."""
import hashlib

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.errors import AppError
from app.db.cn_models import CnCompany, CnDataset, CnSnapshot, CnSyncJob
from app.db.models import now
from app.db.session import session_factory
from app.services.cn.normalizer import normalize
from app.services.cn.provider import CnClient, directory_entries, extract_rows, parse_filings
from app.services.sync import aware

KINDS = ("indicators", "income", "balance", "cashflow", "announcements")
LIMITATIONS = [
    "结构化财务来自东方财富第三方整理，并非交易所原始XBRL；请与原始报告核对。",
    "仅提供当前保存的供应商口径，不支持历史当时已知口径或指定申报重述选择。",
    "年初至今为累计值，不是单季度；未对累计EPS或比率做差分。",
    "供应商报表的合并范围尚未逐份与原始PDF核对；归母净利润单独展示，不替代报表净利润。",
    "A股PDF正文解析、AI研究和事件证据验证尚未接入。",
]


def mode():
    settings = get_settings()
    return "synthetic_fixture" if settings.data_mode == "synthetic_fixture" else settings.cn_data_mode


def ensure_cn_dataset(session):
    expected = mode()
    record = session.get(CnDataset, "singleton")
    if record and record.source_kind != expected:
        raise AppError("CN_DATA_MODE_MISMATCH", "A股数据模式与已绑定资料不一致，请使用独立A股数据表所在数据库。", 503)
    if not record:
        session.add(CnDataset(id="singleton", source_kind=expected))
        try:
            session.commit()
        except IntegrityError:
            # The first concurrent search and status requests may both bind the
            # empty CN namespace. Accept only the same winning source mode.
            session.rollback()
            record = session.get(CnDataset, "singleton")
            if not record or record.source_kind != expected:
                raise AppError("CN_DATA_MODE_MISMATCH", "A股数据模式绑定冲突，请检查独立数据库配置。", 503) from None


def latest_snapshot(session, scope, kind):
    return session.scalar(select(CnSnapshot).where(
        CnSnapshot.scope == scope, CnSnapshot.kind == kind,
    ).order_by(CnSnapshot.last_observed_at.desc(), CnSnapshot.id.desc()).limit(1))


def store_resource(session, scope, resource, *, source_kind=None):
    digest = hashlib.sha256(resource.raw).hexdigest()
    record = session.scalar(select(CnSnapshot).where(
        CnSnapshot.scope == scope, CnSnapshot.kind == resource.kind, CnSnapshot.sha256 == digest,
    ))
    if record:
        # The source bytes and first fetch date remain immutable. Re-observation
        # can select an older version again without inventing a new source date.
        record.last_observed_at = resource.fetched_at
    else:
        record = CnSnapshot(scope=scope, kind=resource.kind, source_kind=source_kind or mode(),
                            source_url=resource.url, sha256=digest,
                            raw_json=resource.raw.decode("utf-8"), fetched_at=resource.fetched_at,
                            last_observed_at=resource.fetched_at, source_metadata=resource.metadata)
        session.add(record)
        session.flush()
    return record


def source_out(record):
    return {"id": record.id, "kind": record.kind, "source_kind": record.source_kind,
            "source_url": record.source_url, "sha256": record.sha256,
            "fetched_at": aware(record.fetched_at), "last_observed_at": aware(record.last_observed_at),
            "parser_version": record.parser_version, "source_metadata": record.source_metadata}


def company_out(record):
    stale = not record.last_synced_at or (now() - aware(record.last_synced_at)).total_seconds() > 86400
    return {"symbol": record.symbol, "code": record.code, "name": record.name,
            "market": "CN", "exchange": record.exchange, "currency": "CNY",
            "source_kind": record.source_kind, "last_synced_at": aware(record.last_synced_at) if record.last_synced_at else None,
            "directory_updated_at": aware(record.directory_updated_at), "is_stale": stale,
            "limitations": LIMITATIONS}


def import_directory(session, resources):
    entries = directory_entries(resources)
    if not entries:
        raise AppError("CN_DIRECTORY_EMPTY", "A股目录为空，旧目录已保留。", 502)
    sources = [store_resource(session, "directory", resource) for resource in resources]
    timestamp = max(resource.fetched_at for resource in resources)
    for entry in sorted(entries, key=lambda row: row["symbol"]):
        record = session.scalar(select(CnCompany).where(CnCompany.symbol == entry["symbol"])
                                .with_for_update().execution_options(populate_existing=True))
        if record is None:
            record = CnCompany(symbol=entry["symbol"], code=entry["code"],
                               exchange=entry["exchange"], name=entry["name"], source_kind=mode())
            session.add(record)
        record.name, record.org_id = entry["name"], entry.get("org_id")
        record.directory_updated_at = timestamp
        record.source_metadata = {**(record.source_metadata or {}),
                                  "directory_source_ids": [source.id for source in sources]}
    session.flush()
    return len(entries)


def import_company_resources(session, company, resources):
    company = session.scalar(select(CnCompany).where(CnCompany.symbol == company.symbol)
                             .with_for_update().execution_options(populate_existing=True))
    if company is None:
        raise AppError("CN_COMPANY_NOT_FOUND", "请先更新A股目录并选择公司。", 404)
    required = {"indicators", "income", "balance", "cashflow"}
    present = {resource.kind for resource in resources}
    if not required.issubset(present):
        raise AppError("CN_FINANCIAL_DATA_INCOMPLETE", "A股财务响应不完整，旧资料已保留。", 502)
    for kind in required:
        if not any(extract_rows(kind, resource.raw) for resource in resources if resource.kind == kind):
            raise AppError("CN_FINANCIAL_DATA_INCOMPLETE", "A股财务报表为空，旧资料已保留。", 502)
    manifest = {}
    for resource in resources:
        if resource.kind not in KINDS:
            raise AppError("CN_INVALID_SOURCE", "A股响应类型不受支持。", 502)
        if resource.kind == "announcements":
            parse_filings(resource.raw, company.symbol, org_id=company.org_id)
        source = store_resource(session, company.symbol, resource)
        manifest.setdefault(resource.kind, []).append(source.id)
    # All pages form one published bundle. A query must not select only the last
    # page, or mix the income table from one sync with a balance from another.
    company.source_metadata = {**(company.source_metadata or {}), "current_source_ids": manifest}
    # Validate the entire normalized result before publishing any source version.
    if not metrics_for(session, company.symbol, "cumulative", 10):
        raise AppError("CN_FINANCIAL_DATA_EMPTY", "未取得可用A股财务期间，旧资料已保留。", 502)
    company.last_synced_at = now()


def metrics_for(session, symbol, frequency, years):
    bundle, refs = {}, {}
    company = session.get(CnCompany, symbol)
    manifest = (company.source_metadata or {}).get("current_source_ids", {}) if company else {}
    for kind in KINDS[:-1]:
        for source_id in manifest.get(kind, []):
            record = session.get(CnSnapshot, source_id)
            if record is None or record.scope != symbol or record.kind != kind or record.source_kind != mode():
                raise AppError("CN_SOURCE_MANIFEST_INVALID", "A股来源版本不完整，不能发布混合口径。", 503)
            ref = {"source_snapshot_id": record.id, "source_url": record.source_url,
                   "sha256": record.sha256, "fetched_at": aware(record.fetched_at).isoformat(),
                   "source_kind": record.source_kind}
            refs.setdefault(kind, ref)
            bundle.setdefault(kind, []).extend(
                {**row, "_source_ref": ref} for row in extract_rows(kind, record.raw_json.encode("utf-8"))
            )
    if not bundle:
        return []
    return normalize(bundle, symbol=symbol, source_refs=refs, frequency=frequency, years=years)


def filings_for(session, symbol):
    company = session.get(CnCompany, symbol)
    manifest = (company.source_metadata or {}).get("current_source_ids", {}) if company else {}
    ids = manifest.get("announcements", [])
    if not ids:
        return [], ["尚无已保存的公告，请同步公司数据；财务指标不代表已解析报告正文。"]
    rows = []
    for source_id in ids:
        record = session.get(CnSnapshot, source_id)
        if record is None or record.scope != symbol or record.kind != "announcements" or record.source_kind != mode():
            raise AppError("CN_SOURCE_MANIFEST_INVALID", "A股公告来源版本不完整。", 503)
        for row in parse_filings(record.raw_json.encode("utf-8"), symbol, org_id=company.org_id):
            rows.append({**row, "source_snapshot_id": record.id})
    rows = sorted({row["id"]: row for row in rows}.values(), key=lambda row: (row["filing_date"], row["id"]), reverse=True)
    return rows, ["公告列表来自保存的公开响应；只展示已取得的条目，不保证历史公告全覆盖。",
                  "原文链接跳转至披露站点，PDF尚未在本应用解析或验证。"]


def run_cn_sync(job_id):
    # The worker receives an ID only. No URL, key, code expression or token is
    # serialized in Redis. A duplicate delivery never replays a running job.
    with session_factory()() as session:
        ensure_cn_dataset(session)
        job = session.scalar(select(CnSyncJob).where(CnSyncJob.id == job_id).with_for_update())
        if not job or job.status != "queued":
            return
        if mode() != "cn_live":
            job.status, job.active_key, job.finished_at = "failed", None, now()
            job.error_code, job.error_message = "CN_LIVE_DISABLED", "当前A股模式不允许在线请求。"
            session.commit()
            return
        job.status, job.started_at, job.attempts = "running", now(), job.attempts + 1
        scope = job.scope
        company = session.get(CnCompany, scope) if scope != "directory" else None
        org_id = company.org_id if company else None
        session.commit()
    try:
        client = CnClient()
        resources = client.fetch_directory() if scope == "directory" else client.fetch_company(scope, org_id=org_id)
        with session_factory()() as session:
            job = session.scalar(select(CnSyncJob).where(CnSyncJob.id == job_id).with_for_update())
            if job is None or job.status != "running":
                return
            if scope == "directory":
                job.result = {"companies_processed": import_directory(session, resources)}
            else:
                company = session.scalar(select(CnCompany).where(CnCompany.symbol == scope)
                                         .with_for_update().execution_options(populate_existing=True))
                if company is None:
                    raise AppError("CN_COMPANY_NOT_FOUND", "请先更新A股目录并选择公司。", 404)
                import_company_resources(session, company, resources)
                job.result = {"symbol": scope, "resources": len(resources), "limitations": LIMITATIONS}
            job.status, job.active_key, job.finished_at = "succeeded", None, now()
            session.commit()
    except Exception as exc:
        with session_factory()() as session:
            job = session.scalar(select(CnSyncJob).where(CnSyncJob.id == job_id).with_for_update())
            if job and job.status == "running":
                job.status, job.active_key, job.finished_at = "failed", None, now()
                job.error_code = exc.code if isinstance(exc, AppError) else "CN_SYNC_FAILED"
                job.error_message = exc.message if isinstance(exc, AppError) else "A股同步失败，旧资料已保留。"
                session.commit()


def import_archived_resources(session, symbol, resources):
    """Used only by the manifest CLI in an explicitly bound archive database."""
    ensure_cn_dataset(session)
    if mode() != "cn_snapshot":
        raise AppError("CN_ARCHIVE_MODE_REQUIRED", "真实A股归档导入需使用独立cn_snapshot数据模式。", 422)
    company = session.scalar(select(CnCompany).where(CnCompany.symbol == symbol)
                             .with_for_update().execution_options(populate_existing=True))
    if company is None:
        raise AppError("CN_COMPANY_NOT_FOUND", "请先导入对应真实公司目录归档。", 404)
    import_company_resources(session, company, resources)
    return company
