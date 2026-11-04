"""Quarter normalization using actual boundaries and conservative YTD subtraction.

Quarter amounts always come from direct disclosures or an auditable compatible
cumulative pair. EPS, shares and ratios are not additive. No calendar dates are
invented to fill a missing quarterly disclosure.
"""

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, localcontext

from app.services.finance.normalizer import (
    INSTANT_FORMS,
    METRICS,
    _latest,
    _periods,
    _selection_inputs,
)
from app.services.sec.parsers import filing_url

RULE_VERSION = "quarterly-direct-ytd-v1"
QUARTER_RANGES = ((70, 110), (150, 210), (240, 300), (350, 380))


def _quarter_index(days):
    return next(
        (i for i, (minimum, maximum) in enumerate(QUARTER_RANGES, 1) if minimum <= days <= maximum),
        None,
    )


def _calendar_periods(facts, filings):
    annual = [
        period
        for period in _periods(facts, filings)
        if period["start"] and not period["period_error"]
    ]
    calendars = {period["start"]: period.copy() for period in annual}
    # The current incomplete fiscal year can be anchored by the preceding actual
    # year-end, but no future year-end or quarter-end date is synthesized.
    duration_facts = [
        f for rows in facts.values() for f in rows if f["start"] and f["form"] in INSTANT_FORMS
    ]
    for previous in annual:
        next_start = (date.fromisoformat(previous["end"]) + timedelta(days=1)).isoformat()
        if next_start in calendars or previous["fiscal_year"] is None:
            continue
        candidates = [
            f
            for f in duration_facts
            if f["start"] == next_start
            and _quarter_index(
                (date.fromisoformat(f["end"]) - date.fromisoformat(next_start)).days + 1
            )
            in (1, 2, 3)
        ]
        if candidates:
            calendars[next_start] = {
                "start": next_start,
                "end": None,
                "fiscal_year": previous["fiscal_year"] + 1,
                "fiscal_year_basis": "preceding_actual_fiscal_year_end",
                "period_error": None,
            }
    return sorted(calendars.values(), key=lambda period: period["start"])


def _quarter_periods(facts, filings):
    rows = [
        f for values in facts.values() for f in values if f["form"] in INSTANT_FORMS and f["start"]
    ]
    result = []
    for calendar in _calendar_periods(facts, filings):
        year_start = date.fromisoformat(calendar["start"])
        last_date = (
            date.fromisoformat(calendar["end"])
            if calendar["end"]
            else year_start + timedelta(days=379)
        )
        candidates = defaultdict(lambda: defaultdict(set))
        direct_starts = defaultdict(lambda: defaultdict(set))
        for fact in rows:
            begin, end = date.fromisoformat(fact["start"]), date.fromisoformat(fact["end"])
            if begin < year_start or end > last_date:
                continue
            quarter = _quarter_index((end - year_start).days + 1)
            if quarter is None:
                continue
            duration = (end - begin).days + 1
            if begin == year_start or 70 <= duration <= 110:
                candidates[quarter][fact["end"]].add(fact["concept"])
                if 70 <= duration <= 110:
                    direct_starts[fact["end"]][fact["start"]].add(fact["concept"])
        if calendar["end"]:
            candidates[4][calendar["end"]].add("annual_boundary")
        boundaries = {}
        errors = {}
        for quarter, alternatives in candidates.items():
            largest = max(len(concepts) for concepts in alternatives.values())
            best = [end for end, concepts in alternatives.items() if len(concepts) == largest]
            if len(best) == 1:
                boundaries[quarter] = best[0]
            else:
                # Keep each observed boundary visibly missing rather than
                # arbitrarily preferring one of two incompatible calendars.
                for end in best:
                    errors[(quarter, end)] = "AMBIGUOUS_QUARTER_PERIOD"
        entries = [(quarter, end, None) for quarter, end in boundaries.items()]
        entries.extend((quarter, end, error) for (quarter, end), error in errors.items())
        for quarter, end, error in entries:
            previous_end = boundaries.get(quarter - 1)
            start = (
                calendar["start"]
                if quarter == 1
                else (
                    (date.fromisoformat(previous_end) + timedelta(days=1)).isoformat()
                    if previous_end
                    else None
                )
            )
            starts = direct_starts.get(end, {})
            if start is None and starts:
                largest = max(len(concepts) for concepts in starts.values())
                best = [begin for begin, concepts in starts.items() if len(concepts) == largest]
                if len(best) == 1:
                    start = best[0]
                else:
                    error = "AMBIGUOUS_QUARTER_PERIOD"
            if (
                start
                and not 70 <= (date.fromisoformat(end) - date.fromisoformat(start)).days + 1 <= 110
            ):
                error = "AMBIGUOUS_QUARTER_PERIOD"
            result.append(
                {
                    **calendar,
                    "end": end,
                    "start": start,
                    "fiscal_start": calendar["start"],
                    "fiscal_quarter": quarter,
                    "previous_end": previous_end,
                    "period_error": error,
                }
            )
    return sorted(result, key=lambda period: period["end"], reverse=True)


def _compatible_pair(upper, lower, all_rows):
    if upper["accn"] == lower["accn"]:
        return True, "same_accession"
    if upper["form"].endswith("/A") or lower["form"].endswith("/A"):
        return False, "INCOMPATIBLE_RESTATEMENT_BASIS"
    if lower["filed"] > upper["filed"]:
        return False, "INCOMPATIBLE_RESTATEMENT_BASIS"
    # Cross-filing differences are allowed only when neither cumulative operand
    # has a conflicting restatement in the available disclosure history. A
    # matching concept alone does not prove an accounting-basis bridge.
    for operand in (upper, lower):
        values = {
            Decimal(row["value"])
            for row in all_rows
            if row["start"] == operand["start"]
            and row["end"] == operand["end"]
            and row["unit"] == operand["unit"]
        }
        if len(values) > 1:
            return False, "INCOMPATIBLE_RESTATEMENT_BASIS"
    return True, "same_concept_unit_fiscal_start_no_conflicting_restatement_original_filings"


def _select_quarter(metric, period, facts):
    if period["period_error"]:
        return None, [], "unavailable", period["period_error"], None
    matches, wrong_unit, pair_error = [], False, None
    for concept in metric.concepts:
        rows = [f for f in facts.get(concept, []) if f["form"] in INSTANT_FORMS]
        valid_unit = [f for f in rows if f["unit"] == metric.unit]
        wrong_unit |= any(f["end"] == period["end"] and f["unit"] != metric.unit for f in rows)
        if metric.period_kind == "instant":
            direct = [f for f in valid_unit if f["end"] == period["end"] and f["start"] is None]
        else:
            direct = [
                f
                for f in valid_unit
                if period["start"] and f["end"] == period["end"] and f["start"] == period["start"]
            ]
        chosen, error = _latest(direct)
        if error:
            return None, [], "unavailable", error, None
        if chosen:
            matches.append((chosen["value"], [chosen], "direct_quarter", "direct_disclosure"))
            continue
        if metric.period_kind == "instant":
            continue
        if not metric.additive:
            if any(f["end"] == period["end"] for f in valid_unit):
                pair_error = "NON_ADDITIVE_METRIC"
            continue
        if period["fiscal_quarter"] == 1:
            continue
        if not period["previous_end"] or not period["start"]:
            pair_error = pair_error or "MISSING_CUMULATIVE_INPUT"
            continue
        cumulative = [f for f in valid_unit if f["start"] == period["fiscal_start"]]
        upper, error = _latest([f for f in cumulative if f["end"] == period["end"]])
        if error:
            return None, [], "unavailable", error, None
        if upper is None:
            continue
        prior = [f for f in cumulative if f["end"] == period["previous_end"]]
        # Prefer both operands from the very same filing if it actually contains
        # them. Otherwise use the best lower value known when upper was filed.
        same_filing = [f for f in prior if f["accn"] == upper["accn"]]
        lower, error = _latest(same_filing or [f for f in prior if f["filed"] <= upper["filed"]])
        if error:
            return None, [], "unavailable", error, None
        if lower is None:
            pair_error = pair_error or "MISSING_CUMULATIVE_INPUT"
            continue
        compatible, basis = _compatible_pair(upper, lower, cumulative)
        if not compatible:
            pair_error = basis
            continue
        with localcontext() as context:
            context.prec = 80
            value = format(Decimal(upper["value"]) - Decimal(lower["value"]), "f")
        method = "fy_minus_9m" if period["fiscal_quarter"] == 4 else "ytd_difference"
        matches.append((value, [upper, lower], method, basis))
    if not matches:
        reason = pair_error or ("UNIT_MISMATCH" if wrong_unit else "MISSING_CONCEPT_OR_PERIOD")
        return None, [], "unavailable", reason, None
    if len({Decimal(value) for value, *_ in matches}) > 1:
        return None, [], "unavailable", "CONFLICTING_CONCEPTS", None
    value, operands, method, basis = matches[0]
    return value, operands, method, None, basis


def normalize_quarterly(
    companyfacts: dict,
    submissions: dict,
    years: int = 5,
    as_of: str | None = None,
    accession: str | None = None,
) -> list[dict]:
    """Ten metrics per observed fiscal quarter, with missing reasons and inputs."""
    if isinstance(years, bool) or not isinstance(years, int) or not 1 <= years <= 30:
        raise ValueError("years must be an integer between 1 and 30")
    cik, calendar_facts, selected, filings, policy, cutoff = _selection_inputs(
        companyfacts, submissions, as_of, accession
    )
    periods = _quarter_periods(calendar_facts, filings)
    if accession:
        ends = {f["end"] for rows in selected.values() for f in rows}
        periods = [period for period in periods if period["end"] in ends]
    selected_years = sorted(
        {period["fiscal_year"] for period in periods if period["fiscal_year"] is not None},
        reverse=True,
    )[:years]
    periods = [period for period in periods if period["fiscal_year"] in selected_years]
    filing_urls = {row["accession_number"]: row["source_url"] for row in filings}
    result = []
    for period in periods:
        for metric in METRICS:
            value, operands, method, reason, basis = _select_quarter(metric, period, selected)
            urls = [filing_urls.get(f["accn"]) or filing_url(cik, f["accn"]) for f in operands]
            provenance = {
                "rule_version": RULE_VERSION,
                "selection_policy": policy,
                "as_of": cutoff.isoformat() if cutoff else None,
                "requested_accession": accession,
                "period_kind": metric.period_kind,
                "additive": metric.additive,
                "fiscal_year_basis": period["fiscal_year_basis"],
                "fiscal_start": period["fiscal_start"],
                "candidate_concepts": list(metric.concepts),
                "selection_rule": method,
                "basis_check": basis,
                "source_facts": operands,
                "source_url": urls[0] if urls else None,
                "source_urls": list(dict.fromkeys(urls)),
                "filed_at": max((f["filed"] for f in operands), default=None),
                "accession_number": operands[0]["accn"] if operands else None,
                "concept": operands[0]["concept"] if operands else None,
            }
            if len(operands) == 2:
                provenance["calculation"] = {
                    "operation": "subtract",
                    "minuend_fact_key": operands[0]["fact_key"],
                    "subtrahend_fact_key": operands[1]["fact_key"],
                }
            result.append(
                {
                    "metric_key": metric.key,
                    "label": metric.label,
                    "period_type": "quarterly",
                    "fiscal_year": period["fiscal_year"],
                    "fiscal_quarter": period["fiscal_quarter"],
                    "period_start": None if metric.period_kind == "instant" else period["start"],
                    "period_end": period["end"],
                    "value": value,
                    "unit": metric.unit,
                    "calc_method": method,
                    "provenance": provenance,
                    "missing_reason": reason,
                }
            )
    return result
