"""Conservative, traceable presentation of Eastmoney's A-share statements.

These are the provider's latest available statement records, not historical
as-filed vintages. Amounts in the full statement endpoints are CNY yuan.
Interim income/cash-flow figures remain cumulative from January 1; this module
does not synthesize stand-alone quarters, TTM figures or accounting conversions.
"""

import re
from collections import defaultdict
from copy import deepcopy
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext

from app.core.errors import AppError

RULE_VERSION = "cn-finance-v1"
SELECTION_POLICY = "latest_provider_snapshot"
RATIO_SCALE = Decimal("0.00000001")
SOURCE_KEYS = ("source_snapshot_id", "source_url", "sha256", "fetched_at", "source_kind")
QUARTERS = {"03-31": 1, "06-30": 2, "09-30": 3, "12-31": 4}

# A Chinese operating profit includes items that differ from US operating
# income. Total revenue, consolidated profit and parent-attributable profit
# likewise remain distinct rather than being silently renamed into US concepts.
SPECS = (
    ("cn_total_revenue", "营业总收入", (("income", "TOTAL_OPERATE_INCOME"), ("indicators", "TOTALOPERATEREVE")), "CNY", False),
    ("revenue", "营业收入", (("income", "OPERATE_INCOME"),), "CNY", False),
    ("net_income", "净利润（供应商报表口径）", (("income", "NETPROFIT"),), "CNY", False),
    ("parent_net_income", "归母净利润", (("income", "PARENT_NETPROFIT"),), "CNY", False),
    ("cn_operating_profit", "营业利润（中国报表口径）", (("income", "OPERATE_PROFIT"),), "CNY", False),
    ("operating_cash_flow", "经营活动现金流量净额", (("cashflow", "NETCASH_OPERATE"),), "CNY", False),
    ("assets", "总资产", (("balance", "TOTAL_ASSETS"),), "CNY", True),
    ("liabilities", "总负债", (("balance", "TOTAL_LIABILITIES"),), "CNY", True),
    ("equity", "股东权益（供应商报表口径）", (("balance", "TOTAL_EQUITY"),), "CNY", True),
    ("monetary_funds", "货币资金", (("balance", "MONETARYFUNDS"),), "CNY", True),
    ("cash", "期末现金及现金等价物", (("cashflow", "END_CCE"),), "CNY", True),
    ("eps_basic", "基本每股收益", (("income", "BASIC_EPS"), ("indicators", "EPSJB")), "CNY/shares", False),
    ("eps_diluted", "稀释每股收益", (("income", "DILUTED_EPS"), ("indicators", "EPSXS")), "CNY/shares", False),
)


def _invalid(message):
    raise AppError("CN_SOURCE_INVALID", message, 502)


def _decimal(value):
    # A provider client must decode JSON using parse_float=Decimal. Accepting a
    # Python float here would conceal an earlier, potentially lossy conversion.
    if value is None or isinstance(value, (bool, float)):
        return None
    if not isinstance(value, (str, int, Decimal)):
        return None
    text = str(value).strip()
    if not re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", text):
        return None
    try:
        result = Decimal(text)
        return result if result.is_finite() else None
    except InvalidOperation:
        return None


def _number(value):
    return format(value, "f")


def _date(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}(?:[ T].*)?", value):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _version(row):
    return tuple(str(row.get(key) or "") for key in ("UPDATE_DATE", "NOTICE_DATE"))


def _reference(row, kind, source_refs):
    # The persistence layer overwrites this private key with a trusted reference
    # for each page. It is never copied verbatim from an upstream response.
    reference = row.get("_source_ref", source_refs.get(kind, {}))
    if not isinstance(reference, dict) or not all(reference.get(key) for key in SOURCE_KEYS) or not re.fullmatch(r"[0-9a-fA-F]{64}", str(reference.get("sha256", ""))):
        _invalid("A股财务来源标识不完整，未生成无来源指标。")
    return reference


def _index(bundle, symbol, source_refs):
    index = {}
    for kind in ("income", "balance", "cashflow", "indicators"):
        rows = bundle.get(kind, [])
        if not isinstance(rows, list):
            _invalid("A股财务数据格式无效，已有资料已保留。")
        groups = defaultdict(list)
        for row in rows:
            if not isinstance(row, dict):
                _invalid("A股财务记录格式无效，已有资料已保留。")
            _reference(row, kind, source_refs)
            if row.get("SECUCODE") != symbol or str(row.get("SECURITY_CODE")) != symbol.split(".")[0]:
                _invalid("A股财务来源与所选证券不一致。")
            if row.get("CURRENCY") != "CNY":
                _invalid("A股财务币种未明确为人民币，未进行推测换算。")
            end = _date(row.get("REPORT_DATE"))
            if end is None or end.isoformat()[5:] not in QUARTERS:
                _invalid("A股报告期间无法可靠确定，未归为年度或累计期间。")
            groups[end.isoformat()].append(row)
        # Preserve equal-version candidates so conflicting numbers become N/A,
        # never whichever record happened to be returned last.
        index[kind] = {
            end: [row for row in group if _version(row) == max(map(_version, group))]
            for end, group in groups.items()
        }
    return index


def _base(key, label, unit, end, frequency, instant=False):
    return {
        "metric_key": key, "label": label, "period_type": frequency,
        "fiscal_year": int(end[:4]),
        "fiscal_quarter": QUARTERS[end[5:]] if frequency == "cumulative" else None,
        "period_start": None if instant else end[:4] + "-01-01", "period_end": end,
        "value": None, "unit": unit, "calc_method": "provider_reported",
        "provenance": {
            "rule_version": RULE_VERSION, "selection_policy": SELECTION_POLICY,
            "currency": "CNY", "period_basis": "instant" if instant else "year_to_date",
            "statement_scope": "provider_reported_not_independently_verified",
            "source_facts": [],
        },
        "missing_reason": None, "yoy": None, "yoy_reason": "MISSING_PRIOR_PERIOD",
    }


def _field(index, source_refs, end, frequency, symbol, key, label, choices, unit="CNY", instant=False):
    metric = _base(key, label, unit, end, frequency, instant)
    metric["provenance"]["symbol"] = symbol
    reason = "MISSING_STATEMENT_PERIOD"
    for kind, field in choices:
        rows = index[kind].get(end, [])
        if not rows:
            continue
        reason = "MISSING_SOURCE_FIELD"
        # Conflicts include a missing field in one equal-version record: source
        # ambiguity must not be resolved by input ordering or null filtering.
        values = [_decimal(row.get(field)) for row in rows]
        raw_present = any(row.get(field) is not None for row in rows)
        if not raw_present:
            continue
        if any(value is None for value in values):
            reason = "CONFLICTING_PROVIDER_RECORDS" if len(rows) > 1 else "INVALID_SOURCE_NUMBER"
            break
        if len(set(values)) != 1:
            reason = "CONFLICTING_PROVIDER_RECORDS"
            break
        row = rows[0]
        reference = {name: deepcopy(_reference(row, kind, source_refs)[name]) for name in SOURCE_KEYS}
        source = {
            **reference, "statement_kind": kind, "source_field": field,
            "symbol": symbol, "value": _number(values[0]), "unit": unit,
            "period_start": metric["period_start"], "period_end": end,
            "notice_date": row.get("NOTICE_DATE"), "update_date": row.get("UPDATE_DATE"),
            "organization_type": row.get("ORG_TYPE"),
            "statement_scope": "provider_reported_not_independently_verified",
        }
        metric["value"] = source["value"]
        metric["provenance"].update({**source, "source_facts": [source]})
        return metric
    metric["missing_reason"] = reason
    metric["provenance"]["expected_source_fields"] = [f"{kind}.{field}" for kind, field in choices]
    metric["yoy_reason"] = "MISSING_CURRENT_VALUE"
    return metric


def _operand(metric):
    return {
        **{key: metric[key] for key in ("metric_key", "value", "unit", "period_start", "period_end")},
        "source_facts": deepcopy(metric["provenance"]["source_facts"]),
    }


def _derived(key, label, left, right, *, operation, unit="percent"):
    metric = _base(key, label, unit, left["period_end"], left["period_type"], left["period_start"] is None)
    metric["calc_method"] = "same_statement_difference" if operation == "subtract" else "same_statement_ratio"
    metric["yoy_reason"] = "DERIVED_RATIO_YOY_NOT_CALCULATED"
    metric["provenance"].update({
        "symbol": left["provenance"]["symbol"], "operation": operation,
        "operands": [_operand(left), _operand(right)],
        "source_facts": deepcopy(left["provenance"]["source_facts"] + right["provenance"]["source_facts"]),
    })
    a, b = _decimal(left["value"]), _decimal(right["value"])
    if a is None or b is None:
        metric["missing_reason"] = "MISSING_OPERAND"
        return metric
    # Both inputs must refer to the same response record and reporting basis.
    left_source = left["provenance"]["source_facts"]
    right_source = right["provenance"]["source_facts"]
    def identities(facts):
        return {(f["source_snapshot_id"], f["period_end"], f["notice_date"], f["update_date"], f["organization_type"]) for f in facts}
    if identities(left_source) != identities(right_source):
        metric["missing_reason"] = "INCOMPATIBLE_DISCLOSURE_BASIS"
        return metric
    if operation != "subtract" and b <= 0:
        metric["missing_reason"] = "NON_POSITIVE_DENOMINATOR"
        return metric
    with localcontext() as context:
        context.prec = max(80, len(a.as_tuple().digits) + len(b.as_tuple().digits) + 16)
        value = a - b if operation == "subtract" else (a / b * 100).quantize(RATIO_SCALE, rounding=ROUND_HALF_UP)
    metric["value"] = _number(value)
    if operation != "subtract":
        metric["provenance"]["rounding"] = "ROUND_HALF_UP to 8 decimal places"
    return metric


def _with_derived(by_key, index, references, end, frequency, symbol):
    cost = _field(index, references, end, frequency, symbol, "cn_operating_cost", "营业成本", (("income", "OPERATE_COST"),))
    gross = _derived("gross_profit", "毛利润", by_key["revenue"], cost, operation="subtract", unit="CNY")
    ordinary = all(row.get("ORG_TYPE") == "通用" for row in index["income"].get(end, []))
    if not ordinary or not index["income"].get(end):
        gross.update(value=None, missing_reason="FINANCIAL_INDUSTRY_GROSS_PROFIT_UNSUPPORTED")
    by_key["gross_profit"] = gross
    by_key["gross_margin"] = _derived("gross_margin", "毛利率", gross, by_key["revenue"], operation="ratio")
    by_key["cn_net_margin"] = _derived("cn_net_margin", "报表净利润 / 营业总收入", by_key["net_income"], by_key["cn_total_revenue"], operation="ratio")
    by_key["cn_operating_profit_margin"] = _derived("cn_operating_profit_margin", "营业利润 / 营业总收入", by_key["cn_operating_profit"], by_key["cn_total_revenue"], operation="ratio")
    by_key["debt_to_assets"] = _derived("debt_to_assets", "资产负债率", by_key["liabilities"], by_key["assets"], operation="ratio")


def _yoy(current, previous):
    if current["unit"] in {"percent", "ratio"}:
        return
    a, b = _decimal(current["value"]), _decimal(previous["value"] if previous else None)
    if a is None:
        current["yoy_reason"] = "MISSING_CURRENT_VALUE"
        return
    if previous is None or b is None:
        current["yoy_reason"] = "MISSING_PRIOR_PERIOD" if previous is None else "MISSING_PRIOR_VALUE"
        return
    if b <= 0:
        current["yoy_reason"] = "NON_POSITIVE_PRIOR_VALUE"
        return
    current_facts = current["provenance"]["source_facts"]
    previous_facts = previous["provenance"]["source_facts"]
    def basis(facts):
        return {(f["source_field"], f["statement_kind"], f["organization_type"]) for f in facts}
    if not current_facts or basis(current_facts) != basis(previous_facts):
        current["yoy_reason"] = "INCOMPATIBLE_DISCLOSURE_BASIS"
        return
    with localcontext() as context:
        context.prec = max(80, len(a.as_tuple().digits) + len(b.as_tuple().digits) + 16)
        value = ((a - b) / b * 100).quantize(RATIO_SCALE, rounding=ROUND_HALF_UP)
    current["yoy"], current["yoy_reason"] = _number(value), None
    current["provenance"]["yoy_comparison"] = {
        "formula": "(current - prior_same_period) / prior_same_period * 100",
        "rounding": "ROUND_HALF_UP to 8 decimal places",
        "operands": [_operand(current), _operand(previous)],
    }


def normalize(bundle: dict[str, list[dict]], *, symbol: str, source_refs: dict[str, dict], frequency="annual", years=5) -> list[dict]:
    """Return authoritative decimal strings and explicit missing reasons.

    Equal-version conflicts and absent operands remain missing. Only annual and
    calendar year-to-date views are supported. Historical previous periods are
    used to calculate comparable YoY before limiting the displayed year range.
    """
    if frequency not in {"annual", "cumulative"} or isinstance(years, bool) or not isinstance(years, int) or not 1 <= years <= 10:
        raise ValueError("CN metrics support annual/cumulative frequency and 1–10 years")
    if not isinstance(symbol, str) or not re.fullmatch(r"[0-9]{6}\.(?:SH|SZ|BJ)", symbol):
        raise ValueError("CN symbol must include its exchange")
    index = _index(bundle, symbol, source_refs)
    periods = sorted({end for rows in index.values() for end in rows if frequency == "cumulative" or end.endswith("12-31")})
    if not periods:
        return []
    all_periods = {}
    for end in periods:
        by_key = {key: _field(index, source_refs, end, frequency, symbol, key, label, choices, unit, instant) for key, label, choices, unit, instant in SPECS}
        _with_derived(by_key, index, source_refs, end, frequency, symbol)
        all_periods[end] = by_key
    selected_years = set(sorted({int(end[:4]) for end in periods}, reverse=True)[:years])
    result = []
    for end in periods:
        if int(end[:4]) not in selected_years:
            continue
        prior_end = f"{int(end[:4]) - 1:04d}{end[4:]}"
        for key, metric in all_periods[end].items():
            _yoy(metric, all_periods.get(prior_end, {}).get(key))
            result.append(metric)
    return result
