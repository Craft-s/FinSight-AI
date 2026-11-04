"""Synthetic, isolated accounting cases; none of these amounts are company data."""

from copy import deepcopy
from decimal import Decimal

import pytest

from app.core.errors import AppError
from app.services.cn.normalizer import normalize

SYMBOL = "600519.SH"
KINDS = ("income", "balance", "cashflow", "indicators")


def references():
    return {kind: {
        "source_snapshot_id": "TEST-ONLY-" + kind,
        "source_url": "https://datacenter-web.eastmoney.com/api/data/v1/get?TEST_ONLY=" + kind,
        "sha256": str(index) * 64,
        "fetched_at": "2026-01-01T00:00:00Z",
        "source_kind": "synthetic_fixture",
    } for index, kind in enumerate(KINDS)}


def record(end="2025-12-31", **fields):
    return {
        "SECUCODE": SYMBOL, "SECURITY_CODE": "600519", "CURRENCY": "CNY",
        "ORG_TYPE": "通用", "REPORT_DATE": end + " 00:00:00",
        "NOTICE_DATE": "2026-01-01 00:00:00", "UPDATE_DATE": "2026-01-01 00:00:00",
        **fields,
    }


def metrics(bundle, **options):
    return normalize(bundle, symbol=SYMBOL, source_refs=references(), **options)


def keyed(rows, end="2025-12-31"):
    return {row["metric_key"]: row for row in rows if row["period_end"] == end}


def test_separate_chinese_accounting_concepts_and_preserve_authoritative_precision():
    rows = keyed(metrics({
        "income": [record(
            TOTAL_OPERATE_INCOME="9007199254740993.1200", OPERATE_INCOME="1000.1000",
            OPERATE_COST="300.0250", NETPROFIT="150.20", PARENT_NETPROFIT="110.10",
            OPERATE_PROFIT="170.30", BASIC_EPS="1.2000", DILUTED_EPS="1.1000",
        )],
        "balance": [record(TOTAL_ASSETS="800.00", TOTAL_LIABILITIES="200", TOTAL_EQUITY="600.00", MONETARYFUNDS="50.00")],
        "cashflow": [record(NETCASH_OPERATE="98.0000", END_CCE="75.00")],
    }))
    assert rows["cn_total_revenue"]["value"] == "9007199254740993.1200"
    assert rows["revenue"]["value"] == "1000.1000"
    assert rows["net_income"]["value"] == "150.20"
    assert rows["net_income"]["label"] == "净利润（供应商报表口径）"
    assert rows["net_income"]["provenance"]["statement_scope"] == "provider_reported_not_independently_verified"
    assert rows["parent_net_income"]["value"] == "110.10"
    assert rows["cn_operating_profit"]["value"] == "170.30"
    assert "operating_income" not in rows
    assert rows["gross_profit"]["value"] == "700.0750"
    assert rows["eps_basic"]["value"] == "1.2000"
    assert rows["eps_diluted"]["value"] == "1.1000"
    assert rows["eps_diluted"]["unit"] == "CNY/shares"
    assert rows["monetary_funds"]["value"] == "50.00"
    assert rows["cash"]["value"] == "75.00"
    assert rows["cash"]["period_start"] is None
    assert rows["operating_cash_flow"]["period_start"] == "2025-01-01"
    assert rows["assets"]["period_start"] is None


def test_financial_industry_total_revenue_can_use_reported_indicator_without_cross_statement_ratio():
    rows = keyed(metrics({
        "income": [record(ORG_TYPE="银行", OPERATE_INCOME="500", NETPROFIT="100")],
        "indicators": [record(ORG_TYPE="银行", TOTALOPERATEREVE="500")],
    }))
    total = rows["cn_total_revenue"]
    assert total["value"] == "500"
    assert total["provenance"]["statement_kind"] == "indicators"
    assert total["provenance"]["source_field"] == "TOTALOPERATEREVE"
    assert rows["cn_net_margin"]["value"] is None
    assert rows["cn_net_margin"]["missing_reason"] == "INCOMPATIBLE_DISCLOSURE_BASIS"


def test_missing_net_income_cash_and_diluted_eps_are_not_replaced_by_different_concepts():
    rows = keyed(metrics({
        "income": [record(PARENT_NETPROFIT="100", TOTAL_PROFIT="130", INCOME_TAX="20", BASIC_EPS="1.0")],
        "balance": [record(MONETARYFUNDS="50")],
        "cashflow": [record(NETCASH_OPERATE="30")],
    }))
    for key in ("net_income", "cash", "eps_diluted"):
        assert rows[key]["value"] is None
        assert rows[key]["missing_reason"] == "MISSING_SOURCE_FIELD"
    assert rows["parent_net_income"]["value"] == "100"
    assert rows["monetary_funds"]["value"] == "50"
    assert "operating_cash_flow_to_net_income" not in rows


def test_interim_figures_and_eps_remain_ytd_instead_of_becoming_standalone_quarters():
    bundle = {"income": [
        record("2025-03-31", TOTAL_OPERATE_INCOME="200", BASIC_EPS="1.20"),
        record("2025-06-30", TOTAL_OPERATE_INCOME="600", BASIC_EPS="2.40"),
        record("2025-09-30", TOTAL_OPERATE_INCOME="1000", BASIC_EPS="3.50"),
        record("2025-12-31", TOTAL_OPERATE_INCOME="1400", BASIC_EPS="4.80"),
    ]}
    annual = metrics(bundle)
    assert {row["period_end"] for row in annual} == {"2025-12-31"}
    cumulative = metrics(bundle, frequency="cumulative")
    half = keyed(cumulative, "2025-06-30")["cn_total_revenue"]
    assert half["value"] == "600"
    assert half["period_type"] == "cumulative"
    assert half["fiscal_quarter"] == 2
    assert half["period_start"] == "2025-01-01"
    assert keyed(cumulative, "2025-06-30")["eps_basic"]["value"] == "2.40"
    assert keyed(cumulative, "2025-12-31")["eps_basic"]["value"] == "4.80"


def test_yoy_uses_prior_same_cumulative_period_and_keeps_older_comparison_input():
    bundle = {"income": [
        record("2024-06-30", TOTAL_OPERATE_INCOME="10", BASIC_EPS="0.1"),
        record("2024-09-30", TOTAL_OPERATE_INCOME="800", BASIC_EPS="1.5"),
        record("2025-09-30", TOTAL_OPERATE_INCOME="1000", BASIC_EPS="1.8"),
    ]}
    result = metrics(bundle, frequency="cumulative", years=1)
    rows = keyed(result, "2025-09-30")
    assert {row["fiscal_year"] for row in result} == {2025}
    assert rows["cn_total_revenue"]["yoy"] == "25.00000000"
    assert rows["eps_basic"]["yoy"] == "20.00000000"
    prior = rows["cn_total_revenue"]["provenance"]["yoy_comparison"]["operands"][1]
    assert prior["period_end"] == "2024-09-30"
    assert prior["value"] == "800"


@pytest.mark.parametrize("prior", ["0", "-100"])
def test_yoy_does_not_publish_misleading_growth_from_nonpositive_base(prior):
    result = metrics({"income": [record("2024-12-31", NETPROFIT=prior), record(NETPROFIT="10")]})
    current = keyed(result)["net_income"]
    assert current["yoy"] is None
    assert current["yoy_reason"] == "NON_POSITIVE_PRIOR_VALUE"


def test_deterministic_ratios_use_consistent_statement_operands_and_round_explicitly():
    rows = keyed(metrics({
        "income": [record(TOTAL_OPERATE_INCOME="3", OPERATE_INCOME="3", OPERATE_COST="2", NETPROFIT="1", PARENT_NETPROFIT="0.5", OPERATE_PROFIT="2")],
        "balance": [record(TOTAL_ASSETS="8", TOTAL_LIABILITIES="2")],
    }))
    assert rows["cn_net_margin"]["value"] == "33.33333333"
    assert rows["gross_margin"]["value"] == "33.33333333"
    assert rows["cn_operating_profit_margin"]["value"] == "66.66666667"
    assert rows["debt_to_assets"]["value"] == "25.00000000"
    assert rows["cn_net_margin"]["provenance"]["operands"][0]["value"] == "1"
    assert rows["cn_net_margin"]["yoy"] is None
    assert rows["cn_net_margin"]["yoy_reason"] == "DERIVED_RATIO_YOY_NOT_CALCULATED"
    assert rows["cn_net_margin"]["provenance"]["rounding"] == "ROUND_HALF_UP to 8 decimal places"


@pytest.mark.parametrize("revenue", ["0", "-100"])
def test_ratio_requires_positive_denominator(revenue):
    rows = keyed(metrics({"income": [record(TOTAL_OPERATE_INCOME=revenue, NETPROFIT="10")]}))
    assert rows["cn_net_margin"]["value"] is None
    assert rows["cn_net_margin"]["missing_reason"] == "NON_POSITIVE_DENOMINATOR"


@pytest.mark.parametrize("organization", ["银行", "保险", "证券", None])
def test_gross_profit_is_not_guessed_for_financial_or_unclassified_organizations(organization):
    rows = keyed(metrics({"income": [record(ORG_TYPE=organization, OPERATE_INCOME="100", OPERATE_COST="30")]}))
    assert rows["gross_profit"]["value"] is None
    assert rows["gross_profit"]["missing_reason"] == "FINANCIAL_INDUSTRY_GROSS_PROFIT_UNSUPPORTED"
    assert rows["gross_margin"]["value"] is None


def test_no_cross_period_fill_for_missing_statement_or_year():
    rows = keyed(metrics({
        "income": [record(TOTAL_OPERATE_INCOME="100")],
        "cashflow": [record("2024-12-31", NETCASH_OPERATE="80", END_CCE="40")],
    }))
    assert rows["operating_cash_flow"]["value"] is None
    assert rows["operating_cash_flow"]["missing_reason"] == "MISSING_STATEMENT_PERIOD"
    assert rows["cash"]["value"] is None
    assert rows["cn_total_revenue"]["yoy"] is None


def test_newer_provider_update_wins_without_pretending_to_be_historical_as_filed():
    original = record(TOTAL_OPERATE_INCOME="100", UPDATE_DATE="2026-01-01 00:00:00")
    revised = record(TOTAL_OPERATE_INCOME="110", UPDATE_DATE="2026-02-01 00:00:00")
    for order in ([original, revised], [revised, original]):
        metric = keyed(metrics({"income": order}))["cn_total_revenue"]
        assert metric["value"] == "110"
        assert metric["provenance"]["selection_policy"] == "latest_provider_snapshot"
        assert metric["provenance"]["update_date"] == "2026-02-01 00:00:00"


@pytest.mark.parametrize("other", ["110", None])
def test_equal_version_conflicts_remain_missing_regardless_of_input_order(other):
    first = record(TOTAL_OPERATE_INCOME="100")
    second = record(TOTAL_OPERATE_INCOME=other)
    for order in ([first, second], [second, first]):
        metric = keyed(metrics({"income": order}))["cn_total_revenue"]
        assert metric["value"] is None
        assert metric["missing_reason"] == "CONFLICTING_PROVIDER_RECORDS"


@pytest.mark.parametrize("value", [True, False, 0.1, "NaN", "Infinity", "1,000", "--", "１２３", ""])
def test_invalid_source_numbers_are_not_recast_as_precise_financial_strings(value):
    metric = keyed(metrics({"income": [record(NETPROFIT=value)]}))["net_income"]
    assert metric["value"] is None
    assert metric["missing_reason"] == "INVALID_SOURCE_NUMBER"


def test_zero_and_decimal_json_values_remain_valid_and_distinct_from_missing():
    rows = keyed(metrics({"income": [record(NETPROFIT=Decimal("0.00"), PARENT_NETPROFIT=0, OPERATE_INCOME=Decimal("1.20E+3"))]}))
    assert rows["net_income"]["value"] == "0.00"
    assert rows["parent_net_income"]["value"] == "0"
    assert rows["revenue"]["value"] == "1200"
    assert rows["cn_total_revenue"]["value"] is None


def test_source_page_reference_and_derived_operands_remain_traceable_without_mutation():
    refs = references()
    page_two = {**refs["income"], "source_snapshot_id": "TEST-ONLY-PAGE-2", "sha256": "a" * 64,
                "source_url": refs["income"]["source_url"] + "&pageNumber=2"}
    bundle = {"income": [record(OPERATE_INCOME="123.4500", OPERATE_COST="23.4500", _source_ref=page_two)]}
    before = deepcopy((bundle, refs))
    rows = keyed(normalize(bundle, symbol=SYMBOL, source_refs=refs))
    source = rows["revenue"]["provenance"]
    assert source["source_snapshot_id"] == "TEST-ONLY-PAGE-2"
    assert source["source_field"] == "OPERATE_INCOME"
    assert source["sha256"] == "a" * 64
    assert source["source_facts"][0]["value"] == "123.4500"
    operands = rows["gross_profit"]["provenance"]["operands"]
    assert [item["source_facts"][0]["source_field"] for item in operands] == ["OPERATE_INCOME", "OPERATE_COST"]
    assert all(item["source_facts"][0]["source_snapshot_id"] == "TEST-ONLY-PAGE-2" for item in operands)
    assert (bundle, refs) == before


@pytest.mark.parametrize("changes", [
    {"SECUCODE": "000001.SZ"}, {"SECURITY_CODE": "000001"},
    {"CURRENCY": "USD"}, {"CURRENCY": None}, {"REPORT_DATE": "2025-02-30"},
    {"REPORT_DATE": "2025-10-31"}, {"REPORT_DATE": None},
])
def test_wrong_company_currency_and_unsupported_period_are_rejected(changes):
    with pytest.raises(AppError) as error:
        metrics({"income": [record(**changes)]})
    assert error.value.code == "CN_SOURCE_INVALID"


def test_numbers_without_source_identity_cannot_be_published():
    with pytest.raises(AppError) as error:
        normalize({"income": [record(NETPROFIT="100")]}, symbol=SYMBOL, source_refs={})
    assert error.value.code == "CN_SOURCE_INVALID"


@pytest.mark.parametrize("options", [{"frequency": "quarterly"}, {"years": 0}, {"years": 11}, {"years": True}])
def test_unsupported_views_are_rejected_instead_of_silently_reinterpreted(options):
    with pytest.raises(ValueError):
        metrics({}, **options)
