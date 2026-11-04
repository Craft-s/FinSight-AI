"""Synthetic accounting examples only; none of the amounts are real company data."""

import json
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.services.finance.normalizer import METRICS, normalize_annual
from app.services.finance.quarterly import normalize_quarterly


def example():
    data = {"cik": 320193, "_fixture": "synthetic_fixture", "facts": {"us-gaap": {}}}
    for metric in METRICS:
        unit = metric.unit
        values = []
        for year, start, end in (
            (2023, "2022-09-25", "2023-09-30"),
            (2024, "2023-10-01", "2024-09-28"),
        ):
            row = {
                "end": end,
                "val": 1000 if unit == "USD" else "3.25",
                "filed": f"{year}-11-01",
                "accn": f"0000320193-{year % 100:02d}-000001",
                "form": "10-K",
                "fy": year,
                "fp": "FY",
            }
            if metric.period_kind == "duration":
                row["start"] = start
            values.append(row)
        for q, end, filed in (
            (1, "2023-12-30", "2024-02-01"),
            (2, "2024-03-30", "2024-05-01"),
            (3, "2024-06-29", "2024-08-01"),
        ):
            row = {
                "end": end,
                "val": q * 100 if unit == "USD" else str(Decimal(q) / 2),
                "filed": filed,
                "accn": f"0000320193-24-00010{q}",
                "form": "10-Q",
                "fy": 2024,
                "fp": f"Q{q}",
            }
            if metric.period_kind == "duration":
                row["start"] = "2023-10-01"
            values.append(row)
        data["facts"]["us-gaap"][metric.concepts[0]] = {"units": {unit: values}}
    return data, {"cik": "0000320193"}


def rows(data, concept="RevenueFromContractWithCustomerExcludingAssessedTax", unit="USD"):
    return data["facts"]["us-gaap"][concept]["units"][unit]


def result(data, submissions, metric="revenue", quarter=2, **options):
    return next(
        row
        for row in normalize_quarterly(data, submissions, years=1, **options)
        if row["metric_key"] == metric and row["fiscal_quarter"] == quarter
    )


def test_all_observed_quarters_and_ten_metrics():
    data, sub = example()
    normalized = normalize_quarterly(data, sub, years=1)
    assert len(normalized) == 40
    assert {row["fiscal_quarter"] for row in normalized} == {1, 2, 3, 4}
    assert {row["fiscal_year"] for row in normalized} == {2024}


@pytest.mark.parametrize(
    "quarter,expected,method",
    [
        (1, "100", "direct_quarter"),
        (2, "100", "ytd_difference"),
        (3, "100", "ytd_difference"),
        (4, "700", "fy_minus_9m"),
    ],
)
def test_actual_ytd_conversions(quarter, expected, method):
    data, sub = example()
    selected = result(data, sub, quarter=quarter)
    assert selected["value"] == expected
    assert selected["calc_method"] == method
    assert selected["provenance"]["source_facts"]
    if quarter > 1:
        assert len(selected["provenance"]["source_facts"]) == 2
        assert selected["provenance"]["calculation"]["operation"] == "subtract"


def test_direct_quarter_precedes_ytd_difference():
    data, sub = example()
    direct = deepcopy(rows(data)[-2])
    direct.update({"start": "2023-12-31", "val": 111})
    rows(data).append(direct)
    selected = result(data, sub)
    assert selected["value"] == "111"
    assert selected["calc_method"] == "direct_quarter"
    assert len(selected["provenance"]["source_facts"]) == 1


@pytest.mark.parametrize("quarter", [2, 3, 4])
def test_eps_is_never_cumulative_difference(quarter):
    data, sub = example()
    selected = result(data, sub, metric="eps_diluted", quarter=quarter)
    assert selected["value"] is None
    assert selected["missing_reason"] == "NON_ADDITIVE_METRIC"
    assert selected["unit"] == "USD/shares"


def test_direct_quarter_eps_is_allowed():
    data, sub = example()
    eps = deepcopy(rows(data, "EarningsPerShareDiluted", "USD/shares")[-2])
    eps.update({"start": "2023-12-31", "val": "0.73"})
    rows(data, "EarningsPerShareDiluted", "USD/shares").append(eps)
    assert result(data, sub, metric="eps_diluted")["value"] == "0.73"


def test_instant_balance_is_never_subtracted():
    data, sub = example()
    selected = result(data, sub, metric="assets")
    assert selected["value"] == "200"
    assert selected["period_start"] is None
    assert selected["provenance"]["source_facts"][0]["start"] is None
    assert selected["provenance"]["additive"] is False


def test_cross_currency_does_not_form_difference():
    data, sub = example()
    concept = data["facts"]["us-gaap"]["RevenueFromContractWithCustomerExcludingAssessedTax"][
        "units"
    ]
    concept["EUR"] = concept.pop("USD")
    selected = result(data, sub)
    assert selected["value"] is None
    assert selected["missing_reason"] == "UNIT_MISMATCH"


def test_different_concepts_cannot_be_differenced():
    data, sub = example()
    prior = rows(data)[2]
    rows(data).remove(prior)
    data["facts"]["us-gaap"]["Revenues"] = {"units": {"USD": [prior]}}
    selected = result(data, sub)
    assert selected["value"] is None
    assert selected["missing_reason"] == "MISSING_CUMULATIVE_INPUT"


def test_restatement_blocks_cross_filing_ytd_pair():
    data, sub = example()
    changed = deepcopy(rows(data)[2])
    changed.update({"val": 99, "filed": "2025-02-01", "accn": "0000320193-25-000101"})
    rows(data).append(changed)
    selected = result(data, sub)
    assert selected["value"] is None
    assert selected["missing_reason"] == "INCOMPATIBLE_RESTATEMENT_BASIS"


def test_same_filing_pair_supports_consistent_amendment():
    data, sub = example()
    upper, lower = deepcopy(rows(data)[3]), deepcopy(rows(data)[2])
    for operand in (upper, lower):
        operand.update({"filed": "2024-06-01", "accn": "0000320193-24-000500", "form": "10-Q/A"})
    lower["val"] = 90
    upper["val"] = 210
    rows(data).extend([upper, lower])
    selected = result(data, sub)
    assert selected["value"] == "120"
    assert selected["provenance"]["basis_check"] == "same_accession"


def test_separate_amendment_does_not_silently_mix_basis():
    data, sub = example()
    upper = deepcopy(rows(data)[3])
    upper.update(
        {"val": 210, "filed": "2024-06-01", "accn": "0000320193-24-000500", "form": "10-Q/A"}
    )
    rows(data).append(upper)
    assert result(data, sub)["missing_reason"] == "INCOMPATIBLE_RESTATEMENT_BASIS"


def test_annual_as_of_and_accession_views_do_not_take_future_restatement():
    data, sub = example()
    amended = deepcopy(rows(data)[1])
    amended.update(
        {"val": 1200, "filed": "2025-01-15", "accn": "0000320193-25-000500", "form": "10-K/A"}
    )
    rows(data).append(amended)

    def revenue(**options):
        return next(
            row
            for row in normalize_annual(data, sub, years=1, **options)
            if row["metric_key"] == "revenue"
        )

    assert revenue()["value"] == "1200"
    assert revenue(as_of="2024-12-31")["value"] == "1000"
    scoped = revenue(accession="0000320193-24-000001")
    assert scoped["value"] == "1000"
    assert scoped["provenance"]["selection_policy"] == "as_filed"
    assert scoped["provenance"]["as_of"] == "2024-11-01"


def test_accession_scope_never_borrows_prior_ytd_from_another_filing():
    data, sub = example()
    selected = result(data, sub, accession="0000320193-24-000102")
    assert selected["value"] is None
    assert selected["missing_reason"] == "MISSING_CUMULATIVE_INPUT"


def test_incomplete_fiscal_year_uses_previous_real_year_end():
    data, sub = example()
    partial = deepcopy(rows(data)[2])
    partial.update(
        {
            "start": "2024-09-29",
            "end": "2024-12-28",
            "filed": "2025-02-01",
            "accn": "0000320193-25-000101",
            "val": 123,
            "fy": 2025,
        }
    )
    rows(data).append(partial)
    selected = normalize_quarterly(data, sub, years=1)[0]
    assert selected["fiscal_year"] == 2025
    assert selected["fiscal_quarter"] == 1
    assert selected["period_start"] == "2024-09-29"
    assert selected["period_end"] == "2024-12-28"


def test_53_week_fiscal_quarter_keeps_fourteen_actual_weeks():
    data, sub = example()
    # Add actual synthetic FY2023 boundaries; the first quarter has fourteen weeks.
    for quarter, end, filed in (
        (1, "2022-12-31", "2023-02-01"),
        (2, "2023-04-01", "2023-05-01"),
        (3, "2023-07-01", "2023-08-01"),
    ):
        rows(data).append(
            {
                "start": "2022-09-25",
                "end": end,
                "filed": filed,
                "val": quarter * 100,
                "accn": f"0000320193-23-00010{quarter}",
                "fy": 2023,
                "fp": f"Q{quarter}",
                "form": "10-Q",
            }
        )
    selected = next(
        row
        for row in normalize_quarterly(data, sub, years=2)
        if row["metric_key"] == "revenue"
        and row["fiscal_year"] == 2023
        and row["fiscal_quarter"] == 1
    )
    assert (
        date.fromisoformat(selected["period_end"]) - date.fromisoformat(selected["period_start"])
    ).days + 1 == 98


def test_non_december_year_is_not_calendar_quarter():
    data, sub = example()
    # Shift all facts 91 days: June annual end and September fiscal Q1.
    for concept in data["facts"]["us-gaap"].values():
        for values in concept["units"].values():
            for row in values:
                for key in ("start", "end", "filed"):
                    if key in row:
                        row[key] = (date.fromisoformat(row[key]) - timedelta(days=91)).isoformat()
    selected = result(data, sub, quarter=1)
    assert selected["fiscal_quarter"] == 1
    assert selected["period_end"] == "2023-09-30"
    assert selected["fiscal_year"] == 2024


def test_later_comparative_fy_tag_does_not_change_original_fiscal_quarter():
    data, sub = example()
    comparative = deepcopy(rows(data)[2])
    comparative.update({"fy": 2026, "filed": "2026-02-01", "accn": "0000320193-26-000101"})
    rows(data).append(comparative)
    selected = result(data, sub, quarter=1)
    assert selected["fiscal_year"] == 2024
    assert selected["period_end"] == "2023-12-30"
    assert selected["provenance"]["source_facts"][0]["fy"] == 2026


def test_quarter_difference_keeps_decimal_precision():
    data, sub = example()
    rows(data)[2]["val"] = Decimal("9007199254740993.123456789")
    rows(data)[3]["val"] = Decimal("9007199254740994.123456790")
    assert result(data, sub)["value"] == "1.000000001"


@pytest.mark.parametrize("bad", ["not-a-date", "2025-02-30", 10])
def test_invalid_as_of_rejected(bad):
    data, sub = example()
    with pytest.raises(ValueError, match="as_of"):
        normalize_quarterly(data, sub, as_of=bad)


@pytest.mark.parametrize("accession", ["bad", "0000320193-29-999999"])
def test_invalid_or_unknown_accession_rejected(accession):
    data, sub = example()
    with pytest.raises(ValueError, match="accession"):
        normalize_annual(data, sub, accession=accession)


@pytest.mark.parametrize("years", [0, 31, True, "5"])
def test_quarterly_year_limit_rejected(years):
    data, sub = example()
    with pytest.raises(ValueError, match="years"):
        normalize_quarterly(data, sub, years=years)


@pytest.mark.parametrize("cik", ["0000320193", "0000789019", "0001045810"])
def test_real_archived_snapshots_have_quarters_without_mutating_sources(cik):
    folder = Path(__file__).parents[2] / "data" / "snapshots" / cik
    if not folder.exists():
        pytest.skip("Optional real historical snapshots not present")
    data = json.loads(
        (folder / "companyfacts.json").read_text(encoding="utf-8"), parse_float=Decimal
    )
    sub = json.loads((folder / "submissions.json").read_text(encoding="utf-8"))
    normalized = normalize_quarterly(data, sub, years=2)
    assert normalized
    assert len({row["fiscal_year"] for row in normalized}) <= 2
    assert any(row["metric_key"] == "revenue" and row["value"] is not None for row in normalized)
    assert all(row["value"] is None or row["provenance"]["source_facts"] for row in normalized)
    assert all(
        row["calc_method"] == "direct_quarter"
        for row in normalized
        if row["metric_key"] == "eps_diluted" and row["value"] is not None
    )


@pytest.mark.parametrize("cik", ["0000320193", "0000789019", "0001045810"])
def test_complete_real_fiscal_year_revenue_reconciles_with_quarters(cik):
    folder = Path(__file__).parents[2] / "data" / "snapshots" / cik
    if not folder.exists():
        pytest.skip("Optional real historical snapshots not present")
    data = json.loads(
        (folder / "companyfacts.json").read_text(encoding="utf-8"), parse_float=Decimal
    )
    sub = json.loads((folder / "submissions.json").read_text(encoding="utf-8"))
    annual = next(
        row for row in normalize_annual(data, sub, years=1) if row["metric_key"] == "revenue"
    )
    quarters = [
        row
        for row in normalize_quarterly(data, sub, years=3)
        if row["metric_key"] == "revenue" and row["fiscal_year"] == annual["fiscal_year"]
    ]
    assert {row["fiscal_quarter"] for row in quarters} == {1, 2, 3, 4}
    assert all(row["value"] is not None for row in quarters)
    assert sum(Decimal(row["value"]) for row in quarters) == Decimal(annual["value"])
