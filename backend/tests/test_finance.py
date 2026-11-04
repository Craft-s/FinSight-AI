"""All numeric examples in this module are synthetic, never real financial data."""

import json
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest

from app.services.finance.normalizer import METRICS, RULE_VERSION, normalize_annual

FIXTURES = Path(__file__).parent / "fixtures" / "synthetic"


def documents(company="aapl"):
    return (
        json.loads((FIXTURES / f"{company}_companyfacts.json").read_text(encoding="utf-8")),
        json.loads((FIXTURES / f"{company}_submissions.json").read_text(encoding="utf-8")),
    )


def values(facts, submissions, key="revenue"):
    return [r for r in normalize_annual(facts, submissions) if r["metric_key"] == key]


def rows(facts, concept="RevenueFromContractWithCustomerExcludingAssessedTax", unit="USD"):
    return facts["facts"]["us-gaap"][concept]["units"][unit]


@pytest.mark.parametrize(
    "company,fiscal_end",
    [
        ("aapl", "2024-09-28"),
        ("msft", "2024-06-30"),
        ("nvda", "2024-01-28"),
    ],
)
def test_three_fiscal_calendars_and_ten_metrics(company, fiscal_end):
    facts, submissions = documents(company)
    result = normalize_annual(facts, submissions, years=2)
    assert len(result) == 20
    assert {r["metric_key"] for r in result} == {m.key for m in METRICS}
    assert result[0]["period_end"] == fiscal_end
    assert result[0]["fiscal_year"] == 2024
    assert all(r["value"] is not None for r in result)
    assert facts["_fixture"]["source_kind"] == "synthetic_fixture"


def test_53_week_year_retains_actual_interval():
    facts, submissions = documents()
    result = values(facts, submissions)[1]
    assert result["period_start"] == "2022-09-25"
    assert result["period_end"] == "2023-09-30"
    assert result["value"] == "2000000"
    assert result["fiscal_year"] == 2023


def test_comparative_fact_fy_does_not_rename_earlier_year():
    facts, submissions = documents()
    old = deepcopy(rows(facts)[0])
    old.update({"fy": 2024, "filed": "2024-11-01", "accn": "0000320193-24-000001", "val": 1234567})
    rows(facts).append(old)
    result = values(facts, submissions)
    assert result[-1]["period_end"] == "2022-09-24"
    assert result[-1]["fiscal_year"] == 2022
    assert result[-1]["value"] == "1234567"
    assert result[-1]["provenance"]["filed_at"] == "2024-11-01"
    assert result[-1]["provenance"]["source_facts"][0]["fy"] == 2024


def test_latest_amendment_selected_per_fact_without_overwriting_other_metrics():
    facts, submissions = documents()
    correction = deepcopy(rows(facts)[-1])
    correction.update(
        {"form": "10-K/A", "accn": "0000320193-25-000001", "filed": "2025-01-10", "val": 3141592}
    )
    rows(facts).append(correction)
    result = values(facts, submissions)[0]
    assert result["value"] == "3141592"
    assert result["provenance"]["source_facts"][0]["form"] == "10-K/A"
    assert values(facts, submissions, "net_income")[0]["value"] == "300000"


def test_duplicate_facts_do_not_duplicate_metrics():
    facts, submissions = documents()
    rows(facts).append(deepcopy(rows(facts)[-1]))
    result = values(facts, submissions)
    assert len(result) == 3


def test_revenue_concept_fallback():
    facts, submissions = documents()
    gaap = facts["facts"]["us-gaap"]
    gaap["SalesRevenueNet"] = gaap.pop("RevenueFromContractWithCustomerExcludingAssessedTax")
    result = values(facts, submissions)[0]
    assert result["value"] == "3000000"
    assert result["provenance"]["concept"] == "SalesRevenueNet"


def test_conflicting_concepts_are_not_arbitrarily_selected():
    facts, submissions = documents()
    alternate = deepcopy(rows(facts)[-1])
    alternate["val"] = 1
    facts["facts"]["us-gaap"]["Revenues"] = {"units": {"USD": [alternate]}}
    result = values(facts, submissions)[0]
    assert result["value"] is None
    assert result["missing_reason"] == "CONFLICTING_CONCEPTS"


def test_conflicting_values_same_filing_date_are_not_tiebroken_by_accession():
    facts, submissions = documents()
    conflicting = deepcopy(rows(facts)[-1])
    conflicting.update({"val": 2, "accn": "0000320193-24-000002"})
    rows(facts).append(conflicting)
    result = values(facts, submissions)[0]
    assert result["value"] is None
    assert result["missing_reason"] == "CONFLICTING_FACTS"


def test_currency_is_never_silently_converted():
    facts, submissions = documents()
    concept = facts["facts"]["us-gaap"]["NetIncomeLoss"]["units"]
    concept["EUR"] = concept.pop("USD")
    result = values(facts, submissions, "net_income")[0]
    assert result["value"] is None
    assert result["unit"] == "USD"
    assert result["missing_reason"] == "UNIT_MISMATCH"


def test_missing_concept_is_null_not_zero():
    facts, submissions = documents()
    del facts["facts"]["us-gaap"]["Liabilities"]
    result = values(facts, submissions, "liabilities")[0]
    assert result["value"] is None
    assert result["missing_reason"] == "MISSING_CONCEPT_OR_PERIOD"


def test_zero_and_negative_actual_values_are_preserved():
    facts, submissions = documents()
    rows(facts, "NetIncomeLoss")[-1]["val"] = 0
    rows(facts, "OperatingIncomeLoss")[-1]["val"] = -123
    assert values(facts, submissions, "net_income")[0]["value"] == "0"
    assert values(facts, submissions, "operating_income")[0]["value"] == "-123"


def test_point_in_time_is_not_differenced_or_given_a_start():
    facts, submissions = documents()
    result = values(facts, submissions, "assets")[0]
    assert result["value"] == "9000000"
    assert result["period_start"] is None
    assert result["provenance"]["period_kind"] == "instant"
    assert result["provenance"]["additive"] is False


def test_ytd_is_never_treated_as_annual():
    facts, submissions = documents()
    ytd = deepcopy(rows(facts, "NetIncomeLoss")[-1])
    ytd.update(
        {
            "start": "2024-01-01",
            "val": 999,
            "form": "10-Q",
            "filed": "2025-01-01",
            "accn": "0000320193-25-000001",
        }
    )
    rows(facts, "NetIncomeLoss").append(ytd)
    assert values(facts, submissions, "net_income")[0]["value"] == "300000"


def test_eps_only_uses_direct_annual_fact_never_subtracts_or_divides():
    facts, submissions = documents()
    result = values(facts, submissions, "eps_diluted")[0]
    assert result["value"] == "3.25"
    assert result["unit"] == "USD/shares"
    assert result["provenance"]["additive"] is False
    rows(facts, "EarningsPerShareDiluted", "USD/shares")[-1]["start"] = "2024-01-01"
    assert values(facts, submissions, "eps_diluted")[0]["value"] is None


def test_decimal_precision_survives_normalization_and_json():
    facts, submissions = documents()
    rows(facts, "NetIncomeLoss")[-1]["val"] = Decimal("9007199254740993.123456")
    result = values(facts, submissions, "net_income")[0]
    assert result["value"] == "9007199254740993.123456"
    assert json.loads(json.dumps(result))["value"] == result["value"]


def test_provenance_contains_replayable_fact_and_real_url_structure():
    facts, submissions = documents()
    result = values(facts, submissions)[0]
    provenance = result["provenance"]
    assert provenance["rule_version"] == RULE_VERSION
    assert provenance["selection_policy"] == "latest_restated"
    assert provenance["source_url"].endswith("/000032019324000001/synthetic-2024.htm")
    fact = provenance["source_facts"][0]
    assert all(key in fact for key in ("accn", "filed", "start", "end", "concept", "unit", "value"))
    assert len(fact["fact_key"]) == 64


@pytest.mark.parametrize("invalid", [None, True, "NaN", "Infinity", "not-a-number"])
def test_invalid_numeric_values_are_missing(invalid):
    facts, submissions = documents()
    rows(facts, "NetIncomeLoss")[-1]["val"] = invalid
    assert values(facts, submissions, "net_income")[0]["value"] is None


def test_mismatched_cik_rejected():
    facts, submissions = documents()
    facts["cik"] = 789019
    with pytest.raises(ValueError, match="CIK"):
        normalize_annual(facts, submissions)


@pytest.mark.parametrize("years", [0, 31, True, "5"])
def test_invalid_year_limit_rejected(years):
    facts, submissions = documents()
    with pytest.raises(ValueError, match="years"):
        normalize_annual(facts, submissions, years=years)


def test_custom_taxonomy_only_is_unsupported_not_inferred():
    facts, submissions = documents()
    facts["facts"]["aapl"] = facts["facts"].pop("us-gaap")
    assert normalize_annual(facts, submissions) == []


def test_empty_source_has_no_invented_periods():
    assert normalize_annual({"cik": 320193, "facts": {}}, {"cik": "0000320193"}) == []
