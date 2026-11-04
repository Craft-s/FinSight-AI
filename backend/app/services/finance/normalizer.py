"""Normalize directly reported annual SEC facts with auditable selection rules.

This module deliberately does not derive quarters or subtract EPS. The SEC API's
fy/fp fields describe the source filing, not necessarily the fact's own period.
"""

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from typing import Any

from app.services.sec.parsers import normalize_cik, parse_filings

RULE_VERSION = "annual-direct-v2"
ANNUAL_FORMS = frozenset({"10-K", "10-K/A"})
INSTANT_FORMS = ANNUAL_FORMS | {"10-Q", "10-Q/A"}


@dataclass(frozen=True)
class MetricDefinition:
    key: str
    label: str
    concepts: tuple[str, ...]
    unit: str = "USD"
    period_kind: str = "duration"
    additive: bool = True


METRICS = (
    MetricDefinition(
        "revenue",
        "营业收入",
        (
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
            "SalesRevenueNet",
        ),
    ),
    MetricDefinition("net_income", "净利润", ("NetIncomeLoss",)),
    MetricDefinition("gross_profit", "毛利润", ("GrossProfit",)),
    MetricDefinition("operating_income", "营业利润", ("OperatingIncomeLoss",)),
    MetricDefinition(
        "operating_cash_flow", "经营现金流", ("NetCashProvidedByUsedInOperatingActivities",)
    ),
    MetricDefinition("assets", "总资产", ("Assets",), period_kind="instant", additive=False),
    MetricDefinition(
        "liabilities", "总负债", ("Liabilities",), period_kind="instant", additive=False
    ),
    MetricDefinition(
        "equity", "股东权益", ("StockholdersEquity",), period_kind="instant", additive=False
    ),
    MetricDefinition(
        "cash",
        "现金及等价物",
        ("CashAndCashEquivalentsAtCarryingValue",),
        period_kind="instant",
        additive=False,
    ),
    MetricDefinition(
        "eps_diluted",
        "稀释每股收益",
        ("EarningsPerShareDiluted",),
        unit="USD/shares",
        additive=False,
    ),
)


def _date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _number(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
        return format(parsed, "f") if parsed.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def _fact(row: dict, concept: str, unit: str) -> dict | None:
    end, filed = _date(row.get("end")), _date(row.get("filed"))
    start = _date(row.get("start"))
    value = _number(row.get("val"))
    if end is None or filed is None or filed < end or value is None:
        return None
    if row.get("start") is not None and (start is None or start > end):
        return None
    accn = row.get("accn", "")
    if (
        not isinstance(accn, str)
        or len(accn) != 20
        or not (
            accn[:10].isascii()
            and accn[:10].isdigit()
            and accn[10] == "-"
            and accn[11:13].isascii()
            and accn[11:13].isdigit()
            and accn[13] == "-"
            and accn[14:].isascii()
            and accn[14:].isdigit()
        )
    ):
        return None
    result = {
        "taxonomy": "us-gaap",
        "concept": concept,
        "unit": unit,
        "value": value,
        "start": start.isoformat() if start else None,
        "end": end.isoformat(),
        "filed": filed.isoformat(),
        "accn": accn,
        "form": row.get("form"),
        "fy": row.get("fy"),
        "fp": row.get("fp"),
        "frame": row.get("frame"),
    }
    result["fact_key"] = sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
    return result


def _is_annual(fact: dict) -> bool:
    if fact["form"] not in ANNUAL_FORMS or not fact["start"]:
        return False
    # Inclusive durations include normal 365/366 day and 52/53 week calendars.
    days = (date.fromisoformat(fact["end"]) - date.fromisoformat(fact["start"])).days + 1
    return 350 <= days <= 380


def _facts(companyfacts: dict) -> dict[str, list[dict]]:
    taxonomy = companyfacts.get("facts", {}).get("us-gaap", {})
    result: dict[str, list[dict]] = {}
    for metric in METRICS:
        for concept in metric.concepts:
            result[concept] = []
            for unit, rows in taxonomy.get(concept, {}).get("units", {}).items():
                for row in rows:
                    fact = _fact(row, concept, unit)
                    if fact is not None:
                        result[concept].append(fact)
    return result


def _periods(all_facts: dict[str, list[dict]], filings: list[dict]) -> list[dict]:
    annual = [f for rows in all_facts.values() for f in rows if _is_annual(f)]
    # A source 10-K's current annual end, rather than every comparative fact's fy.
    accession_ends: dict[str, str] = {}
    for fact in annual:
        accession_ends[fact["accn"]] = max(
            accession_ends.get(fact["accn"], ""),
            fact["end"],
        )
    for filing in filings:
        if filing["form_type"] in ANNUAL_FORMS and filing["report_date"]:
            accession_ends[filing["accession_number"]] = filing["report_date"]
    labels: dict[str, set[int]] = defaultdict(set)
    for fact in annual:
        fy = fact["fy"]
        if (
            accession_ends.get(fact["accn"]) == fact["end"]
            and fact["fp"] == "FY"
            and isinstance(fy, int)
            and not isinstance(fy, bool)
            and abs(fy - date.fromisoformat(fact["end"]).year) <= 1
        ):
            labels[fact["end"]].add(fy)
    ends: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for fact in annual:
        # Different units do not turn a nonannual record into an annual one.
        ends[fact["end"]][fact["start"]].add(fact["concept"])
    result = []
    for end, starts in ends.items():
        max_support = max(len(concepts) for concepts in starts.values())
        best_starts = [start for start, concepts in starts.items() if len(concepts) == max_support]
        label_set = labels.get(end, set())
        label_basis = "current_annual_filing"
        if len(label_set) == 1:
            fiscal_year = next(iter(label_set))
        else:
            # Older comparative facts may lack their original filing. Infer only
            # from an unambiguous nearby year-end with the same calendar season.
            inferred = set()
            end_date = date.fromisoformat(end)
            for anchor_end, anchor_labels in labels.items():
                anchor = date.fromisoformat(anchor_end)
                month_difference = min(
                    abs(anchor.month - end_date.month), 12 - abs(anchor.month - end_date.month)
                )
                if len(anchor_labels) == 1 and month_difference <= 1:
                    inferred.add(next(iter(anchor_labels)) - (anchor.year - end_date.year))
            if not label_set and len(inferred) == 1:
                fiscal_year = next(iter(inferred))
                label_basis = "annual_calendar_anchor"
            else:
                fiscal_year = None
                label_basis = "unknown_or_conflicting_fiscal_year"
        result.append(
            {
                "start": best_starts[0] if len(best_starts) == 1 else None,
                "end": end,
                "fiscal_year": fiscal_year,
                "fiscal_year_basis": label_basis,
                "period_error": "AMBIGUOUS_ANNUAL_PERIOD" if len(best_starts) != 1 else None,
            }
        )
    return sorted(result, key=lambda p: p["end"], reverse=True)


def _latest(rows: list[dict]) -> tuple[dict | None, str | None]:
    if not rows:
        return None, None
    newest_date = max(row["filed"] for row in rows)
    newest = [row for row in rows if row["filed"] == newest_date]
    if len({Decimal(row["value"]) for row in newest}) != 1:
        return None, "CONFLICTING_FACTS"
    # Same-value duplicates may occur across filings. Accession is a stable tie
    # breaker, not a way to hide different values on the same filing date.
    return max(newest, key=lambda row: (row["accn"], row["fact_key"])), None


def _select(metric: MetricDefinition, period: dict, facts: dict) -> tuple[dict | None, str | None]:
    if period["period_error"]:
        return None, period["period_error"]
    matches, wrong_unit, invalid_period = [], False, False
    for concept in metric.concepts:
        rows = []
        for fact in facts.get(concept, []):
            if fact["end"] != period["end"]:
                continue
            valid_period = (
                fact["start"] is None and fact["form"] in INSTANT_FORMS
                if metric.period_kind == "instant"
                else fact["start"] == period["start"] and _is_annual(fact)
            )
            if not valid_period:
                invalid_period = True
                continue
            if fact["unit"] != metric.unit:
                wrong_unit = True
                continue
            rows.append(fact)
        selected, error = _latest(rows)
        if error:
            return None, error
        if selected:
            matches.append(selected)
    if not matches:
        reason = (
            "UNIT_MISMATCH"
            if wrong_unit
            else ("NO_COMPARABLE_ANNUAL_FACT" if invalid_period else "MISSING_CONCEPT_OR_PERIOD")
        )
        return None, reason
    if len({Decimal(row["value"]) for row in matches}) != 1:
        return None, "CONFLICTING_CONCEPTS"
    return matches[0], None


def _selection_inputs(companyfacts, submissions, as_of=None, accession=None):
    """Filter before selecting values; filing-scoped views cannot see later facts.

    Unselected, date-filtered facts may establish calendar boundaries only. They
    never supply an amount to an accession-scoped result.
    """
    from app.services.sec.parsers import ACCESSION

    cutoff = None
    if as_of is not None:
        cutoff = as_of if isinstance(as_of, date) else _date(as_of)
        if cutoff is None:
            raise ValueError("as_of must be an ISO calendar date")
    if accession is not None and (
        not isinstance(accession, str) or not ACCESSION.fullmatch(accession)
    ):
        raise ValueError("Invalid accession")
    cik = normalize_cik(companyfacts.get("cik", submissions.get("cik")))
    if submissions.get("cik") is not None and normalize_cik(submissions["cik"]) != cik:
        raise ValueError("companyfacts and submissions CIK do not match")
    facts = _facts(companyfacts)
    filings = parse_filings(submissions, cik)
    if accession:
        dates = {
            _date(row["filed"])
            for rows in facts.values()
            for row in rows
            if row["accn"] == accession
        }
        dates.update(
            _date(row["filing_date"]) for row in filings if row["accession_number"] == accession
        )
        dates.discard(None)
        if not dates:
            raise ValueError("accession is not present in the available company sources")
        filing_date = min(dates)
        cutoff = min(cutoff, filing_date) if cutoff else filing_date
    eligible = {
        concept: [row for row in rows if cutoff is None or _date(row["filed"]) <= cutoff]
        for concept, rows in facts.items()
    }
    selected = {
        concept: [row for row in rows if accession is None or row["accn"] == accession]
        for concept, rows in eligible.items()
    }
    filings = [row for row in filings if cutoff is None or _date(row["filing_date"]) <= cutoff]
    policy = (
        "as_filed" if accession else ("as_of" if as_of is not None else "latest_restated")
    )
    return cik, eligible, selected, filings, policy, cutoff


def normalize_annual(
    companyfacts: dict,
    submissions: dict,
    years: int = 5,
    as_of: str | None = None,
    accession: str | None = None,
) -> list[dict]:
    """Return ten directly disclosed metrics per annual period, newest first.

    Missing values are ``None`` plus a reason. Unsupported/custom concepts,
    transition years, and unclear periods are never filled with guessed values.
    Every chosen fact is retained in JSON-safe provenance. Callers must persist
    the raw SEC snapshot and its hash separately and tag its data source mode.
    """
    if isinstance(years, bool) or not isinstance(years, int) or not 1 <= years <= 30:
        raise ValueError("years must be an integer between 1 and 30")
    cik, calendar_facts, facts, filings, policy, cutoff = _selection_inputs(
        companyfacts, submissions, as_of, accession
    )
    filing_urls = {row["accession_number"]: row["source_url"] for row in filings}
    periods = _periods(calendar_facts, filings)
    if accession:
        selected_ends = {f["end"] for rows in facts.values() for f in rows if _is_annual(f)}
        periods = [period for period in periods if period["end"] in selected_ends]
    periods = periods[:years]
    result = []
    for period in periods:
        for metric in METRICS:
            selected, missing_reason = _select(metric, period, facts)
            provenance = {
                "rule_version": RULE_VERSION,
                "selection_policy": policy,
                "as_of": cutoff.isoformat() if cutoff else None,
                "requested_accession": accession,
                "selection_rule": "direct_annual_same_unit_latest_filed_concept_consensus",
                "period_kind": metric.period_kind,
                "additive": metric.additive,
                "fiscal_year_basis": period["fiscal_year_basis"],
                "candidate_concepts": list(metric.concepts),
                "source_facts": [],
                "source_url": None,
            }
            if selected:
                selected_accession = selected["accn"]
                source_url = filing_urls.get(selected_accession) or (
                    f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                    f"{selected_accession.replace('-', '')}/{selected_accession}-index.html"
                )
                provenance.update(
                    {
                        "source_facts": [selected],
                        "source_url": source_url,
                        "filed_at": selected["filed"],
                        "accession_number": selected_accession,
                        "concept": selected["concept"],
                    }
                )
            result.append(
                {
                    "metric_key": metric.key,
                    "label": metric.label,
                    "period_type": "annual",
                    "fiscal_year": period["fiscal_year"],
                    "fiscal_quarter": None,
                    "period_start": period["start"] if metric.period_kind == "duration" else None,
                    "period_end": period["end"],
                    "value": selected["value"] if selected else None,
                    "unit": metric.unit,
                    "calc_method": "direct_annual" if selected else "unavailable",
                    "provenance": provenance,
                    "missing_reason": missing_reason,
                }
            )
    return result
