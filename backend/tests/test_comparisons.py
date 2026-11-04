"""All amounts, dates and accession examples below are synthetic test inputs."""

from copy import deepcopy
from decimal import Decimal

import pytest

from app.services.finance.comparisons import add_comparisons


def metric(key="revenue", value="120", year=2024, **changes):
    row = {
        "metric_key": key,
        "label": key,
        "period_type": "annual",
        "fiscal_year": year,
        "fiscal_quarter": None,
        "period_start": f"{year}-01-01",
        "period_end": f"{year}-12-31",
        "value": value,
        "unit": "USD",
        "calc_method": "direct_annual",
        "missing_reason": None,
        "provenance": {
            "selection_policy": "latest_restated",
            "source_url": f"https://www.sec.gov/Archives/synthetic/{year}",
            "source_facts": [
                {
                    "taxonomy": "us-gaap",
                    "concept": key,
                    "value": value,
                    "unit": "USD",
                    "accn": f"0000000001-{str(year)[-2:]}-000001",
                    "filed": f"{year + 1}-02-01",
                    "start": f"{year}-01-01",
                    "end": f"{year}-12-31",
                }
            ],
        },
    }
    row.update(changes)
    return row


def test_requested_accession_and_cutoff_are_part_of_comparison_policy():
    current, prior = metric(), metric(year=2023)
    current["provenance"].update(selection_policy="as_filed", requested_accession="A")
    prior["provenance"].update(selection_policy="as_filed", requested_accession="B")
    result = add_comparisons([current, prior])
    assert find(result, "revenue")["yoy_reason"] == "INCOMPATIBLE_SELECTION_POLICY"
    prior["provenance"]["requested_accession"] = "A"
    result = add_comparisons([current, prior])
    assert find(result, "revenue")["yoy"] == "0"
    assert (
        find(result, "revenue")["provenance"]["comparison"]["prior_metric"]["requested_accession"]
        == "A"
    )
    current["provenance"]["as_of"] = "2025-01-01"
    assert (
        find(add_comparisons([current, prior]), "revenue")["yoy_reason"]
        == "INCOMPATIBLE_SELECTION_POLICY"
    )


def test_malformed_duration_dates_are_not_treated_as_instant_values():
    current, prior = metric(period_start="invalid"), metric(year=2023, period_start="invalid")
    assert find(add_comparisons([current, prior]), "revenue")["yoy_reason"] == "INCOMPATIBLE_PERIOD"


def test_ratios_do_not_accept_shared_prior_ytd_as_current_basis():
    revenue, net = metric(value="100"), metric("net_income", "10")
    revenue["provenance"]["source_facts"] = [
        {"accn": "A", "start": "2024-01-01", "end": "2024-12-31", "concept": "revenue"},
        {"accn": "B", "start": "2024-01-01", "end": "2024-09-30", "concept": "revenue"},
    ]
    net["provenance"]["source_facts"] = [
        {"accn": "C", "start": "2024-01-01", "end": "2024-12-31", "concept": "net"},
        {"accn": "B", "start": "2024-01-01", "end": "2024-09-30", "concept": "net"},
    ]
    row = find(add_comparisons([revenue, net]), "net_margin")
    assert row["value"] is None
    assert row["missing_reason"] == "INCOMPATIBLE_DISCLOSURE_BASIS"
    net["provenance"]["source_facts"][0]["accn"] = "A"
    row = find(add_comparisons([revenue, net]), "net_margin")
    assert row["value"] == "10"


def find(result, key, year=2024):
    return next(row for row in result if row["metric_key"] == key and row["fiscal_year"] == year)


def test_yoy_uses_decimal_and_preserves_both_inputs():
    current, prior = (
        metric(value="100000000000000003"),
        metric(value="100000000000000000", year=2023),
    )
    result = add_comparisons([current, prior])
    row = find(result, "revenue")
    assert row["yoy"] == "0"
    assert row["provenance"]["comparison"]["absolute_change"] == "3"
    assert row["provenance"]["comparison"]["prior_metric"]["value"] == prior["value"]
    assert row["provenance"]["comparison"]["current_metric"]["value"] == current["value"]


def test_yoy_values_are_percent_not_fraction():
    result = add_comparisons([metric(), metric(value="100", year=2023)])
    assert find(result, "revenue")["yoy"] == "20"
    assert find(result, "revenue")["yoy_reason"] is None
    assert find(result, "revenue", 2023)["yoy_reason"] == "MISSING_PRIOR_PERIOD"


@pytest.mark.parametrize("prior_value", ["0", "-1"])
def test_nonpositive_prior_is_na_not_misleading_growth(prior_value):
    result = add_comparisons([metric(), metric(value=prior_value, year=2023)])
    assert find(result, "revenue")["yoy"] is None
    assert find(result, "revenue")["yoy_reason"] == "NON_POSITIVE_PRIOR_VALUE"


def test_negative_current_can_be_compared_against_positive_prior():
    result = add_comparisons([metric(value="-20"), metric(value="100", year=2023)])
    assert find(result, "revenue")["yoy"] == "-120"


@pytest.mark.parametrize(
    "current_value,prior_value,reason",
    [
        (None, "100", "MISSING_CURRENT_VALUE"),
        ("120", None, "MISSING_PRIOR_VALUE"),
        ("NaN", "100", "MISSING_CURRENT_VALUE"),
        ("120", "Infinity", "MISSING_PRIOR_VALUE"),
    ],
)
def test_missing_or_nonfinite_never_becomes_zero(current_value, prior_value, reason):
    result = add_comparisons([metric(value=current_value), metric(value=prior_value, year=2023)])
    assert find(result, "revenue")["yoy"] is None
    assert find(result, "revenue")["yoy_reason"] == reason


def test_yoy_rejects_currency_concept_policy_and_period_mismatch():
    current, prior = metric(), metric(year=2023)
    cases = []
    wrong_unit = deepcopy(prior)
    wrong_unit["unit"] = "EUR"
    cases.append((wrong_unit, "UNIT_MISMATCH"))
    wrong_concept = deepcopy(prior)
    wrong_concept["provenance"]["source_facts"][0]["concept"] = "other"
    cases.append((wrong_concept, "INCOMPATIBLE_CONCEPT"))
    wrong_policy = deepcopy(prior)
    wrong_policy["provenance"]["selection_policy"] = "as_filed"
    cases.append((wrong_policy, "INCOMPATIBLE_SELECTION_POLICY"))
    wrong_end = deepcopy(prior)
    wrong_end["period_end"] = "2023-06-30"
    cases.append((wrong_end, "INCOMPATIBLE_PERIOD"))
    for changed, reason in cases:
        row = find(add_comparisons([current, changed]), "revenue")
        assert row["yoy"] is None
        assert row["yoy_reason"] == reason
        assert "absolute_change" not in row["provenance"]["comparison"]


def test_nonpositive_growth_base_keeps_same_basis_absolute_change():
    row = find(add_comparisons([metric(value="15"), metric(value="-5", year=2023)]), "revenue")
    assert row["yoy"] is None
    assert row["provenance"]["comparison"]["absolute_change"] == "20"


def test_yoy_requires_same_fiscal_quarter_and_does_not_compare_ytd():
    current = metric(
        period_type="quarterly",
        fiscal_quarter=2,
        period_start="2024-04-01",
        period_end="2024-06-30",
    )
    prior = metric(
        year=2023,
        period_type="quarterly",
        fiscal_quarter=1,
        period_start="2023-01-01",
        period_end="2023-03-31",
    )
    assert (
        find(add_comparisons([current, prior]), "revenue")["yoy_reason"] == "MISSING_PRIOR_PERIOD"
    )
    prior.update(fiscal_quarter=2, period_start="2023-01-01", period_end="2023-06-30")
    assert find(add_comparisons([current, prior]), "revenue")["yoy_reason"] == "INCOMPATIBLE_PERIOD"


def test_53_week_years_are_comparable_with_period_length_warning():
    current = metric(period_start="2023-09-24", period_end="2024-09-28")
    prior = metric(year=2023, period_start="2022-09-25", period_end="2023-09-23")
    row = find(add_comparisons([current, prior]), "revenue")
    assert row["yoy"] == "0"
    assert row["provenance"]["comparison"]["comparison_warning"] == "PERIOD_LENGTH_DIFFERS"


def test_instant_values_can_have_yoy_without_fake_start_dates():
    row = find(
        add_comparisons(
            [metric("assets", period_start=None), metric("assets", "100", 2023, period_start=None)]
        ),
        "assets",
    )
    assert row["yoy"] == "20"


def test_ambiguous_prior_not_arbitrarily_selected():
    row = find(
        add_comparisons([metric(), metric(year=2023), metric(year=2023, value="1")]), "revenue"
    )
    assert row["yoy_reason"] == "AMBIGUOUS_COMPARISON_PERIOD"


def test_all_four_ratios_return_percent_or_ratio_and_source_operands():
    inputs = [
        metric(value="300"),
        metric("gross_profit", "100"),
        metric("net_income", "60"),
        metric("operating_income", "90"),
        metric("operating_cash_flow", "75"),
    ]
    result = add_comparisons(inputs)
    assert find(result, "gross_margin")["value"] == "33.33333333"
    assert find(result, "gross_margin")["unit"] == "percent"
    assert find(result, "net_margin")["value"] == "20"
    assert find(result, "operating_margin")["value"] == "30"
    cash_ratio = find(result, "operating_cash_flow_to_net_income")
    assert cash_ratio["value"] == "1.25"
    assert cash_ratio["unit"] == "ratio"
    assert cash_ratio["yoy"] is None
    assert cash_ratio["yoy_reason"] == "DERIVED_RATIO_YOY_NOT_CALCULATED"
    assert len(cash_ratio["provenance"]["source_metrics"]) == 2
    assert len(cash_ratio["provenance"]["source_facts"]) == 2
    assert cash_ratio["provenance"]["source_metrics"][0]["value"] == "75"


@pytest.mark.parametrize("denominator", ["0", "-1"])
def test_nonpositive_denominator_does_not_produce_ratio(denominator):
    result = add_comparisons([metric(value=denominator), metric("net_income", "50")])
    row = find(result, "net_margin")
    assert row["value"] is None
    assert row["missing_reason"] == "NON_POSITIVE_DENOMINATOR"


def test_negative_numerator_is_a_valid_margin():
    result = add_comparisons([metric(value="100"), metric("net_income", "-10")])
    assert find(result, "net_margin")["value"] == "-10"


def test_ratio_requires_matching_units_periods_and_disclosure_basis():
    cases = []
    wrong_unit = metric("gross_profit", "10", unit="EUR")
    cases.append((wrong_unit, "UNIT_MISMATCH"))
    wrong_period = metric("gross_profit", "10", period_start="2024-02-01")
    cases.append((wrong_period, "INCOMPATIBLE_PERIOD"))
    wrong_accn = metric("gross_profit", "10")
    wrong_accn["provenance"]["source_facts"][0]["accn"] = "0000000001-24-000002"
    cases.append((wrong_accn, "INCOMPATIBLE_DISCLOSURE_BASIS"))
    no_source = metric("gross_profit", "10")
    no_source["provenance"]["source_facts"] = []
    cases.append((no_source, "MISSING_SOURCE_FACTS"))
    for changed, reason in cases:
        row = find(add_comparisons([metric(), changed]), "gross_margin")
        assert row["value"] is None
        assert row["missing_reason"] == reason


def test_missing_operands_ambiguity_and_unknown_year_have_clear_reasons():
    result = add_comparisons([metric("assets", fiscal_year=None)])
    assert result[0]["yoy_reason"] == "UNKNOWN_FISCAL_PERIOD"
    assert result[-1]["missing_reason"] == "MISSING_OPERAND"
    result = add_comparisons([metric(), metric(value="99"), metric("net_income", "1")])
    assert find(result, "net_margin")["missing_reason"] == "AMBIGUOUS_OPERAND"


def test_rounding_is_explicit_and_input_is_unchanged_and_idempotent():
    inputs = [metric(value="3"), metric("gross_profit", "1")]
    original = deepcopy(inputs)
    result = add_comparisons(inputs)
    assert inputs == original
    assert add_comparisons(result) == result
    assert Decimal(find(result, "gross_margin")["value"]) == Decimal("33.33333333")
    assert find(result, "gross_margin")["provenance"]["rounding"] == "half_up_8_decimal_places"
