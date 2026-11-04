import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import delete, select

from app.core.config import get_settings
from app.core.errors import AppError
from app.db.models import (
    Company,
    Filing,
    FinancialFact,
    NormalizedMetric,
    SourceSnapshot,
    SyncJob,
    TickerAlias,
    now,
)
from app.db.session import ensure_dataset, session_factory
from app.services.metrics import build_metrics, with_snapshot_provenance
from app.services.sec.client import SecClient, validate_cik
from app.services.sec.parsers import parse_filings


def json_safe(value):
    return json.loads(json.dumps(value, default=str, ensure_ascii=False))


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def source_urls(cik):
    return {
        "submissions": f"https://data.sec.gov/submissions/CIK{cik}.json",
        "companyfacts": f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
    }


def snapshot(session, company_id, kind, raw, fetched_at, source_url, metadata=None):
    digest = hashlib.sha256(raw).hexdigest()
    existing = session.scalar(select(SourceSnapshot).where(
        SourceSnapshot.company_id == company_id, SourceSnapshot.kind == kind,
        SourceSnapshot.sha256 == digest,
    ))
    if existing:
        if metadata and not existing.source_metadata:
            existing.source_metadata = metadata
            existing.fetched_at = fetched_at
        return existing
    record = SourceSnapshot(
        company_id=company_id, kind=kind, sha256=digest,
        source_url=source_url, source_kind=get_settings().data_mode,
        raw_json=raw.decode("utf-8-sig"), fetched_at=fetched_at,
        source_metadata=metadata or {},
    )
    session.add(record)
    session.flush()
    return record


def import_company(session, submissions_raw, facts_raw, fetched_at):
    source_times = fetched_at if isinstance(fetched_at, dict) else {"bundle": fetched_at}
    bundle_time = source_times["bundle"]
    source_metadata = source_times.get("manifest", {})
    submissions = json.loads(submissions_raw, parse_float=Decimal)
    facts = json.loads(facts_raw, parse_float=Decimal)
    cik = validate_cik(str(submissions["cik"]))
    if validate_cik(str(facts["cik"])) != cik:
        raise AppError("SOURCE_COMPANY_MISMATCH", "申报与财务事实 CIK 不一致。", 422)
    company = session.scalar(select(Company).where(Company.cik == cik))
    tickers, exchanges = submissions.get("tickers", []), submissions.get("exchanges", [])
    if company is None:
        company = Company(cik=cik, name=submissions["name"], source_kind=get_settings().data_mode)
        session.add(company)
        session.flush()
    company.name = submissions["name"]
    company.ticker = tickers[0] if tickers else None
    company.exchange = exchanges[0] if exchanges else None
    company.fiscal_year_end = submissions.get("fiscalYearEnd")
    aliases = {a.ticker: a for a in session.scalars(select(TickerAlias).where(TickerAlias.company_id == company.id))}
    for alias in aliases.values():
        if alias.ticker not in tickers:
            alias.is_current, alias.valid_to = False, now()
    for ticker in tickers:
        if ticker not in aliases:
            session.add(TickerAlias(company_id=company.id, ticker=ticker))
        else:
            aliases[ticker].is_current, aliases[ticker].valid_to = True, None
    urls = source_urls(cik)
    sub_snapshot = snapshot(session, company.id, "submissions", submissions_raw, source_times.get("submissions", bundle_time), urls["submissions"], source_metadata.get("sources", {}).get("submissions", {}))
    fact_snapshot = snapshot(session, company.id, "companyfacts", facts_raw, source_times.get("companyfacts", bundle_time), urls["companyfacts"], source_metadata.get("sources", {}).get("companyfacts", {}))
    existing_filings = {
        f.accession_number: f for f in session.scalars(select(Filing).where(Filing.company_id == company.id))
    }
    for row in parse_filings(submissions, cik):
        record = existing_filings.get(row["accession_number"])
        fields = {k: row.get(k) for k in (
            "accession_number", "form_type", "filing_date", "report_date", "primary_document",
            "source_url", "amended", "items",
        )}
        if isinstance(fields["items"], list):
            fields["items"] = ", ".join(fields["items"])
        if record is None:
            session.add(Filing(company_id=company.id, snapshot_id=sub_snapshot.id, **fields))
        else:
            for key, value in fields.items():
                setattr(record, key, value)
            record.snapshot_id = sub_snapshot.id
    existing_facts = set(session.scalars(select(FinancialFact.fingerprint).where(FinancialFact.company_id == company.id)))
    for taxonomy, concepts in facts.get("facts", {}).items():
        for concept, body in concepts.items():
            for unit, values in body.get("units", {}).items():
                for value in values:
                    if "val" not in value or "end" not in value:
                        continue
                    numeric = Decimal(str(value["val"]))
                    if not numeric.is_finite():
                        raise AppError("INVALID_FACT", "SEC 数值不是有限数。", 422)
                    safe = json_safe(value)
                    canonical = json.dumps([cik, taxonomy, concept, unit, safe], sort_keys=True)
                    fingerprint = hashlib.sha256(canonical.encode()).hexdigest()
                    if fingerprint in existing_facts:
                        continue
                    session.add(FinancialFact(
                        fingerprint=fingerprint, company_id=company.id, snapshot_id=fact_snapshot.id,
                        taxonomy=taxonomy, concept=concept, unit=unit, value=numeric,
                        start_date=value.get("start"), end_date=value["end"], filed_at=value.get("filed"),
                        accession_number=value.get("accn"), fiscal_year=value.get("fy"),
                        fiscal_period=value.get("fp"), form_type=value.get("form"), frame=value.get("frame"),
                        raw_context_json=safe,
                    ))
                    existing_facts.add(fingerprint)
    session.flush()
    # Keep all original disclosures; replace only the materialized latest view atomically.
    metrics = [
        metric
        for frequency in ("annual", "quarterly")
        for metric in build_metrics(facts, submissions, frequency=frequency, years=10)
    ]
    with_snapshot_provenance(metrics, fact_snapshot)
    session.execute(delete(NormalizedMetric).where(NormalizedMetric.company_id == company.id))
    for metric in metrics:
        payload = json_safe(metric)
        session.add(NormalizedMetric(
            company_id=company.id, metric_key=payload["metric_key"],
            fiscal_year=payload.get("fiscal_year"), fiscal_quarter=payload.get("fiscal_quarter"),
            period_type=payload["period_type"], period_start=payload.get("period_start"),
            period_end=payload["period_end"],
            value=Decimal(payload["value"]) if payload.get("value") is not None else None,
            unit=payload["unit"], payload=payload,
            rule_version=payload["provenance"]["rule_version"],
        ))
    company.last_synced_at = bundle_time
    session.flush()
    return company


def load_snapshot_directory(directory: str | Path):
    folder = Path(directory)
    try:
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8-sig"))
        if manifest["source_kind"] != get_settings().data_mode:
            raise AppError("DATA_MODE_MISMATCH", "快照模式与当前数据库模式不一致。", 422)
        fetched_at = datetime.fromisoformat(manifest["fetched_at"].replace("Z", "+00:00"))
        if fetched_at.tzinfo is None:
            raise ValueError("fetched_at requires timezone")
        raw = {}
        for kind in ("submissions", "companyfacts"):
            raw[kind] = (folder / f"{kind}.json").read_bytes()
            if len(raw[kind]) > get_settings().sec_max_bytes:
                raise AppError("SOURCE_TOO_LARGE", "快照超出大小限制。", 422)
            expected = manifest.get("sha256", {}).get(kind)
            if expected and expected != hashlib.sha256(raw[kind]).hexdigest():
                raise AppError("SNAPSHOT_HASH_MISMATCH", "快照哈希校验失败。", 422)
        times = {"bundle": fetched_at, "manifest": manifest}
        for kind in ("submissions", "companyfacts"):
            timestamp = manifest.get("sources", {}).get(kind, {}).get("fetched_at")
            if timestamp:
                parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    raise ValueError("source fetched_at requires timezone")
                times[kind] = parsed
        return raw["submissions"], raw["companyfacts"], times
    except (OSError, ValueError, KeyError) as exc:
        raise AppError("SNAPSHOT_INVALID", "快照缺失或 manifest 无效。", 422) from exc


def run_sync(job_id: str):
    settings = get_settings()
    with session_factory()() as session:
        ensure_dataset(session)
        job = session.get(SyncJob, job_id)
        if not job or job.status != "queued":
            return
        job.status, job.started_at = "running", now()
        job.attempts += 1
        session.commit()
        try:
            company = session.scalar(select(Company).where(Company.cik == job.cik))
            fresh = company and company.last_synced_at and (now() - aware(company.last_synced_at)).total_seconds() < settings.cache_ttl_seconds
            if not fresh or job.force:
                if settings.data_mode == "sec_live":
                    client = SecClient()
                    urls = source_urls(job.cik)
                    sub, _ = client.fetch(urls["submissions"])
                    facts, fetched_at = client.fetch(urls["companyfacts"])
                else:
                    if not settings.snapshot_directory:
                        raise AppError("SNAPSHOT_NOT_CONFIGURED", "离线模式需配置快照目录。", 503)
                    sub, facts, fetched_at = load_snapshot_directory(Path(settings.snapshot_directory) / job.cik)
                import_company(session, sub, facts, fetched_at)
            job.status, job.finished_at, job.active_key = "succeeded", now(), None
            session.commit()
        except Exception as exc:
            session.rollback()
            job = session.get(SyncJob, job_id)
            job.status, job.finished_at, job.active_key = "failed", now(), None
            job.error_code = exc.code if isinstance(exc, AppError) else "SYNC_FAILED"
            job.error_message = exc.message if isinstance(exc, AppError) else "同步处理失败，旧数据已保留。请查看输入格式和服务配置。"
            session.commit()


def refresh_tickers(session):
    if get_settings().data_mode != "sec_live":
        raise AppError("DATA_MODE_MISMATCH", "离线模式不能导入在线映射。", 422)
    raw, fetched_at = SecClient().fetch("https://www.sec.gov/files/company_tickers_exchange.json")
    data = json.loads(raw)
    fields = data["fields"]
    count = 0
    for entry in data["data"]:
        row = dict(zip(fields, entry, strict=True))
        cik = validate_cik(str(row["cik"]))
        company = session.scalar(select(Company).where(Company.cik == cik))
        if company is None:
            company = Company(cik=cik, name=row["name"], ticker=row["ticker"], exchange=row.get("exchange"), source_kind="sec_live")
            session.add(company)
            session.flush()
        alias = session.scalar(select(TickerAlias).where(TickerAlias.company_id == company.id, TickerAlias.ticker == row["ticker"]))
        if not alias:
            session.add(TickerAlias(company_id=company.id, ticker=row["ticker"]))
        count += 1
    snapshot(session, None, "ticker_mapping", raw, fetched_at, "https://www.sec.gov/files/company_tickers_exchange.json")
    session.commit()
    return count
