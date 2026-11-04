"""Materialize latest views and reconstruct explicitly selected filing versions."""

import json
from datetime import UTC
from decimal import Decimal

from sqlalchemy import select

from app.core.errors import AppError
from app.db.models import Filing, FinancialFact, NormalizedMetric, SourceSnapshot
from app.services.finance.comparisons import add_comparisons
from app.services.finance.normalizer import normalize_annual
from app.services.finance.quarterly import normalize_quarterly


def limit_periods(items, frequency, years):
    count = years * 4 if frequency == "quarterly" else years
    periods = set(sorted({row["period_end"] for row in items}, reverse=True)[:count])
    return sorted(
        [row for row in items if row["period_end"] in periods],
        key=lambda row: (row["period_end"], row["metric_key"]), reverse=True,
    )


def with_snapshot_provenance(items, source, *, selection_policy="latest_restated", as_of=None, accession=None):
    def annotate_reference(reference):
        reference["source_snapshot_id"] = source.id
        reference["snapshot_sha256"] = source.sha256
        reference["selection_policy"] = selection_policy

    for row in items:
        provenance = row.setdefault("provenance", {})
        provenance.update({
            "source_snapshot_id": source.id,
            "snapshot_sha256": source.sha256,
            "snapshot_fetched_at": (
                source.fetched_at.replace(tzinfo=UTC) if source.fetched_at.tzinfo is None
                else source.fetched_at
            ).isoformat(),
            "source_metadata": source.source_metadata,
            "selection_policy": selection_policy,
        })
        if as_of:
            provenance["as_of"] = as_of
            provenance["data_version_note"] = "依据已保存快照按披露日期筛选，并非当日抓取的完整档案。"
        if accession:
            provenance["selected_accession"] = accession
        for operand in provenance.get("source_metrics", []):
            annotate_reference(operand)
        comparison = provenance.get("comparison", {})
        for operand_name in ("current_metric", "prior_metric"):
            if comparison.get(operand_name):
                annotate_reference(comparison[operand_name])
    return items


def build_metrics(facts, submissions, *, frequency, years=10, as_of=None, accession=None):
    normalizer = normalize_quarterly if frequency == "quarterly" else normalize_annual
    # One extra fiscal year supplies prior-period inputs for the visible YoY.
    items = normalizer(facts, submissions, years=min(years + 1, 30), as_of=as_of, accession=accession)
    return limit_periods(add_comparisons(items), frequency, years)


def version_metrics(session, company, *, frequency, years, selection_policy, as_of=None, accession=None):
    if selection_policy == "latest_restated":
        rows = session.scalars(select(NormalizedMetric).where(
            NormalizedMetric.company_id == company.id,
            NormalizedMetric.period_type == frequency,
        )).all()
        return limit_periods([r.payload for r in rows], frequency, years)

    if accession:
        filing = session.scalar(select(Filing).where(
            Filing.company_id == company.id, Filing.accession_number == accession,
        ))
        fact_form = session.scalar(select(FinancialFact.form_type).where(
            FinancialFact.company_id == company.id, FinancialFact.accession_number == accession,
        ).limit(1))
        if filing is None and fact_form is None:
            raise AppError("ACCESSION_NOT_FOUND", "该申报编号不属于所选公司或未保存于本地。", 404)
        if (filing.form_type if filing else fact_form) not in {"10-K", "10-K/A", "10-Q", "10-Q/A"}:
            raise AppError("FORM_NOT_SUPPORTED", "财务版本查询仅支持定期报告及其修订。", 422)

    snapshots = {}
    for kind in ("companyfacts", "submissions"):
        snapshots[kind] = session.scalar(select(SourceSnapshot).where(
            SourceSnapshot.company_id == company.id, SourceSnapshot.kind == kind,
            SourceSnapshot.source_kind == company.source_kind,
        ).order_by(SourceSnapshot.fetched_at.desc(), SourceSnapshot.id.desc()).limit(1))
    if not all(snapshots.values()):
        raise AppError("SOURCE_NOT_AVAILABLE", "该公司尚无可重建版本的完整来源快照，请先同步。", 409)
    fact_source = snapshots["companyfacts"]
    try:
        items = build_metrics(
            json.loads(fact_source.raw_json, parse_float=Decimal),
            json.loads(snapshots["submissions"].raw_json, parse_float=Decimal),
            frequency=frequency, years=years, as_of=as_of, accession=accession,
        )
    except ValueError as exc:
        raise AppError("SOURCE_VERSION_INCOMPLETE", "当前来源快照不足以可靠重建所选财务版本。", 409) from exc
    return with_snapshot_provenance(
        items, fact_source, selection_policy=selection_policy, as_of=as_of, accession=accession,
    )
