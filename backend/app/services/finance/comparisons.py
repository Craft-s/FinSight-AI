"""Deterministic comparisons of one company's already normalized metrics.

Values remain Decimal until decimal-string serialization. Growth percentages and
margins are rounded half-up to eight decimal places; input values are retained in
provenance. A known source is required: lack of evidence never means zero.
"""

import json
from collections import defaultdict
from copy import deepcopy
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext

RULE_VERSION = "comparisons-v1"
OUTPUT_SCALE = Decimal("0.00000001")
RATIOS = (
    ("gross_margin", "毛利率", "gross_profit", "revenue", "percent"),
    ("net_margin", "净利率", "net_income", "revenue", "percent"),
    ("operating_margin", "营业利润率", "operating_income", "revenue", "percent"),
    (
        "operating_cash_flow_to_net_income",
        "经营现金流 / 净利润",
        "operating_cash_flow",
        "net_income",
        "ratio",
    ),
)
RATIO_KEYS = frozenset(row[0] for row in RATIOS)


def _decimal(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def _serialized(value: Decimal) -> str:
    rounded = value.quantize(OUTPUT_SCALE, rounding=ROUND_HALF_UP)
    return "0" if not rounded else format(rounded, "f").rstrip("0").rstrip(".")


def _date(value):
    try:
        return date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _fiscal_key(row):
    year = row.get("fiscal_year")
    quarter = row.get("fiscal_quarter")
    frequency = row.get("period_type")
    if isinstance(year, bool) or not isinstance(year, int):
        return None
    if frequency == "annual" and quarter is None:
        return row["metric_key"], frequency, year, None
    if frequency == "quarterly" and not isinstance(quarter, bool) and quarter in (1, 2, 3, 4):
        return row["metric_key"], frequency, year, quarter
    return None


def _policy(row):
    provenance = row.get("provenance", {})
    # A different historical cutoff/accession must not silently mix versions.
    return (
        provenance.get("selection_policy"),
        provenance.get("as_of"),
        provenance.get("requested_accession") or provenance.get("accession"),
    )


def _source_facts(row):
    return row.get("provenance", {}).get("source_facts", [])


def _concepts(row):
    return {(fact.get("taxonomy"), fact.get("concept")) for fact in _source_facts(row)}


def _metric_reference(row):
    provenance = row.get("provenance", {})
    return {
        **{
            key: row.get(key)
            for key in (
                "metric_key",
                "period_type",
                "fiscal_year",
                "fiscal_quarter",
                "period_start",
                "period_end",
                "value",
                "unit",
                "calc_method",
            )
        },
        "selection_policy": provenance.get("selection_policy"),
        "as_of": provenance.get("as_of"),
        "requested_accession": _policy(row)[2],
        "source_snapshot_id": provenance.get("source_snapshot_id"),
        "source_url": provenance.get("source_url"),
        "source_facts": deepcopy(_source_facts(row)),
    }


def _duration(row):
    start, end = _date(row.get("period_start")), _date(row.get("period_end"))
    return (end - start).days + 1 if start and end else None


def _yoy_error(current, prior):
    if _decimal(current.get("value")) is None:
        return "MISSING_CURRENT_VALUE"
    if _decimal(prior.get("value")) is None:
        return "MISSING_PRIOR_VALUE"
    if current.get("unit") != prior.get("unit"):
        return "UNIT_MISMATCH"
    if _policy(current) != _policy(prior) or _policy(current)[0] is None:
        return "INCOMPATIBLE_SELECTION_POLICY"
    if not _source_facts(current) or not _source_facts(prior):
        return "MISSING_SOURCE_FACTS"
    if _concepts(current) != _concepts(prior):
        return "INCOMPATIBLE_CONCEPT"
    current_end, prior_end = _date(current.get("period_end")), _date(prior.get("period_end"))
    if not current_end or not prior_end or not 350 <= (current_end - prior_end).days <= 380:
        return "INCOMPATIBLE_PERIOD"
    current_days, prior_days = _duration(current), _duration(prior)
    if any(
        row.get("period_start") is not None
        and not _date(row["period_start"])
        or row.get("provenance", {}).get("period_kind") == "duration"
        and row.get("period_start") is None
        for row in (current, prior)
    ):
        return "INCOMPATIBLE_PERIOD"
    if (current_days is None) != (prior_days is None):
        return "INCOMPATIBLE_PERIOD"
    if current_days is not None:
        minimum, maximum = (350, 380) if current["period_type"] == "annual" else (70, 110)
        if not minimum <= current_days <= maximum or not minimum <= prior_days <= maximum:
            return "INCOMPATIBLE_PERIOD"
    if _decimal(prior["value"]) <= 0:
        return "NON_POSITIVE_PRIOR_VALUE"
    return None


def _add_yoy(rows):
    index = defaultdict(list)
    for row in rows:
        key = _fiscal_key(row)
        if key is not None:
            index[key].append(row)
    for row in rows:
        row["yoy"], row["yoy_reason"] = None, "MISSING_PRIOR_PERIOD"
        key = _fiscal_key(row)
        if key is None:
            row["yoy_reason"] = "UNKNOWN_FISCAL_PERIOD"
            continue
        prior_key = key[:2] + (key[2] - 1, key[3])
        priors = index.get(prior_key, [])
        if len(index[key]) != 1 or len(priors) > 1:
            row["yoy_reason"] = "AMBIGUOUS_COMPARISON_PERIOD"
            continue
        if not priors:
            continue
        prior = priors[0]
        reason = _yoy_error(row, prior)
        row["yoy_reason"] = reason
        comparison = {
            "rule_version": RULE_VERSION,
            "method": "(current - prior) / prior * 100",
            "output_unit": "percent",
            "rounding": "half_up_8_decimal_places",
            "prior_metric": _metric_reference(prior),
            "current_metric": _metric_reference(row),
            "reason": reason,
        }
        current_value, prior_value = _decimal(row.get("value")), _decimal(prior.get("value"))
        if (
            current_value is not None
            and prior_value is not None
            and reason in (None, "NON_POSITIVE_PRIOR_VALUE")
        ):
            with localcontext() as context:
                context.prec = 80
                comparison["absolute_change"] = format(current_value - prior_value, "f")
                if reason is None:
                    row["yoy"] = _serialized((current_value - prior_value) / prior_value * 100)
        current_days, prior_days = _duration(row), _duration(prior)
        if current_days is not None and prior_days is not None and current_days != prior_days:
            comparison["comparison_warning"] = "PERIOD_LENGTH_DIFFERS"
            comparison["current_days"], comparison["prior_days"] = current_days, prior_days
        row.setdefault("provenance", {})["comparison"] = comparison


def _ratio_error(numerator, denominator):
    if numerator is None or denominator is None:
        return "MISSING_OPERAND"
    if _decimal(numerator.get("value")) is None or _decimal(denominator.get("value")) is None:
        return "MISSING_OPERAND"
    if numerator.get("unit") != denominator.get("unit"):
        return "UNIT_MISMATCH"
    if any(
        numerator.get(key) != denominator.get(key)
        for key in (
            "period_type",
            "fiscal_year",
            "fiscal_quarter",
            "period_start",
            "period_end",
        )
    ):
        return "INCOMPATIBLE_PERIOD"
    if not _date(numerator.get("period_start")) or not _date(numerator.get("period_end")):
        return "INCOMPATIBLE_PERIOD"
    if _policy(numerator) != _policy(denominator) or _policy(numerator)[0] is None:
        return "INCOMPATIBLE_SELECTION_POLICY"
    numerator_facts, denominator_facts = _source_facts(numerator), _source_facts(denominator)
    if not numerator_facts or not denominator_facts:
        return "MISSING_SOURCE_FACTS"
    # Sharing only a prior YTD subtraction operand does not prove the current
    # quarter's numerators use the same disclosure basis. The facts covering the
    # upper boundary must share a source filing. Each normalized difference is
    # responsible for separately proving its own lower-bound compatibility.
    numerator_accessions = {
        fact.get("accn") for fact in numerator_facts if fact.get("end") == numerator["period_end"]
    } - {None, ""}
    denominator_accessions = {
        fact.get("accn")
        for fact in denominator_facts
        if fact.get("end") == denominator["period_end"]
    } - {None, ""}
    if not numerator_accessions.intersection(denominator_accessions):
        return "INCOMPATIBLE_DISCLOSURE_BASIS"
    if _decimal(denominator["value"]) <= 0:
        return "NON_POSITIVE_DENOMINATOR"
    return None


def _ratios(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[
            tuple(
                row.get(key)
                for key in (
                    "period_type",
                    "fiscal_year",
                    "fiscal_quarter",
                    "period_end",
                )
            )
        ].append(row)
    result = []
    for group in groups.values():
        metrics = defaultdict(list)
        for row in group:
            metrics[row["metric_key"]].append(row)
        for key, label, numerator_key, denominator_key, unit in RATIOS:
            numerator_rows, denominator_rows = metrics[numerator_key], metrics[denominator_key]
            numerator = numerator_rows[0] if len(numerator_rows) == 1 else None
            denominator = denominator_rows[0] if len(denominator_rows) == 1 else None
            template = denominator or numerator or group[0]
            reason = _ratio_error(numerator, denominator)
            if len(numerator_rows) > 1 or len(denominator_rows) > 1:
                reason = "AMBIGUOUS_OPERAND"
            operands = [row for row in (numerator, denominator) if row is not None]
            source_facts = {}
            for operand in operands:
                for fact in _source_facts(operand):
                    source_facts[json.dumps(fact, sort_keys=True, default=str)] = deepcopy(fact)
            source_urls = list(
                dict.fromkeys(
                    row.get("provenance", {}).get("source_url")
                    for row in operands
                    if row.get("provenance", {}).get("source_url")
                )
            )
            template_provenance = template.get("provenance", {})
            value = None
            if reason is None:
                with localcontext() as context:
                    context.prec = 80
                    value = _serialized(
                        _decimal(numerator["value"])
                        / _decimal(denominator["value"])
                        * (100 if unit == "percent" else 1)
                    )
            result.append(
                {
                    "metric_key": key,
                    "label": label,
                    **{
                        field: template.get(field)
                        for field in (
                            "period_type",
                            "fiscal_year",
                            "fiscal_quarter",
                            "period_start",
                            "period_end",
                        )
                    },
                    "value": value,
                    "unit": unit,
                    "calc_method": "ratio" if value is not None else "unavailable",
                    "missing_reason": reason,
                    "yoy": None,
                    "yoy_reason": "DERIVED_RATIO_YOY_NOT_CALCULATED",
                    "provenance": {
                        "rule_version": RULE_VERSION,
                        **{
                            field: template_provenance.get(field)
                            for field in (
                                "selection_policy",
                                "as_of",
                            )
                        },
                        "requested_accession": _policy(template)[2],
                        "selection_rule": "same_period_unit_and_shared_upper_disclosure_ratio",
                        "formula": f"{numerator_key} / {denominator_key}"
                        + (" * 100" if unit == "percent" else ""),
                        "rounding": "half_up_8_decimal_places",
                        "source_metrics": [_metric_reference(row) for row in operands],
                        "source_facts": list(source_facts.values()),
                        "source_url": source_urls[0] if source_urls else None,
                        "source_urls": source_urls,
                        "additive": False,
                    },
                }
            )
    return result


def add_comparisons(metrics: list[dict]) -> list[dict]:
    """Enrich a single company's normalized values; never mutate the input.

    Existing recognized ratio entries are rebuilt, making this operation
    idempotent. Ratios are separate metrics, with percentage values expressed as
    e.g. ``"25"`` for 25%. Nonpositive growth bases and ratio denominators return
    N/A with reasons. Ratios themselves do not have a second-order YoY figure.
    """
    rows = deepcopy([row for row in metrics if row.get("metric_key") not in RATIO_KEYS])
    _add_yoy(rows)
    return rows + _ratios(rows)
