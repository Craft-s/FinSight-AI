"""API/SQL contracts for explicit financial periods and disclosure versions."""

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.models import Company, NormalizedMetric
from app.db.session import session_factory

BASE = "/api/v1/companies/320193/metrics"


def find_metric(body, key, year):
    return next(row for row in body["items"] if row["metric_key"] == key and row["fiscal_year"] == year)


def test_latest_metrics_include_derived_values_and_yoy(seeded):
    response = seeded.get(BASE)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["frequency"] == "annual"
    assert body["as_of"] is None and body["accession"] is None
    revenue = find_metric(body, "revenue", 2024)
    assert revenue["value"] == "3000000"
    assert Decimal(revenue["yoy"]) == Decimal("50")
    assert revenue["fiscal_quarter"] is None
    margin = find_metric(body, "net_margin", 2024)
    assert margin["unit"] == "percent"
    assert Decimal(margin["value"]) == Decimal("10")
    assert margin["provenance"]["source_snapshot_id"]
    assert all(
        item["source_snapshot_id"] == margin["provenance"]["source_snapshot_id"]
        for item in margin["provenance"]["source_metrics"]
    )
    assert revenue["provenance"]["comparison"]["prior_metric"]["source_snapshot_id"]


def test_as_of_hides_later_disclosures_and_keeps_source_trace(seeded):
    response = seeded.get(BASE, params={"selection_policy": "as_of", "as_of": "2023-11-01"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["selection_policy"] == "as_of"
    assert body["as_of"] == "2023-11-01"
    assert all(row["period_end"] <= "2023-09-30" for row in body["items"])
    for row in body["items"]:
        assert row["provenance"]["selection_policy"] == "as_of"
        assert row["provenance"]["source_snapshot_id"]
        for fact in row["provenance"].get("source_facts", []):
            assert fact["filed"] <= "2023-11-01"
    assert seeded.get(BASE, params={"selection_policy": "as_of", "as_of": "2020-01-01"}).json()["items"] == []


def test_as_filed_is_accession_bound_without_replacing_latest_view(seeded):
    accession = "0000320193-22-000001"
    response = seeded.get(BASE, params={"selection_policy": "as_filed", "accession": accession})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["accession"] == accession
    revenue = find_metric(body, "revenue", 2022)
    assert revenue["value"] == "1000000"
    assert revenue["provenance"]["selected_accession"] == accession
    for row in body["items"]:
        for fact in row["provenance"].get("source_facts", []):
            assert fact["accn"] == accession
    latest = seeded.get(BASE).json()
    assert find_metric(latest, "revenue", 2024)["value"] == "3000000"


@pytest.mark.parametrize("params", [
    {"selection_policy": "as_of"},
    {"selection_policy": "as_filed"},
    {"as_of": "2024-01-01"},
    {"accession": "0000320193-22-000001"},
    {"selection_policy": "as_of", "as_of": "2024-02-30"},
    {"selection_policy": "as_of", "as_of": "2024-01-01", "accession": "0000320193-22-000001"},
    {"selection_policy": "as_filed", "accession": "invalid"},
    {"selection_policy": "unsupported"},
    {"frequency": "monthly"},
])
def test_version_parameters_are_explicit_and_validated(seeded, params):
    response = seeded.get(BASE, params=params)
    assert response.status_code == 422, response.text
    assert response.json()["request_id"]


def test_selected_accession_must_belong_to_company_and_supported_form(seeded):
    foreign = seeded.get(BASE, params={"selection_policy": "as_filed", "accession": "0000789019-22-000001"})
    assert foreign.status_code == 404
    assert foreign.json()["error"]["code"] == "ACCESSION_NOT_FOUND"
    event = seeded.get(BASE, params={"selection_policy": "as_filed", "accession": "0000320193-24-000003"})
    assert event.status_code == 422
    assert event.json()["error"]["code"] == "FORM_NOT_SUPPORTED"


def test_annual_and_quarterly_rows_share_end_without_collision(seeded):
    with session_factory()() as session:
        company = session.scalar(select(Company).where(Company.cik == "0000320193"))
        for frequency in ("annual", "quarterly"):
            session.add(NormalizedMetric(
                company_id=company.id, metric_key="uniqueness_test", fiscal_year=1999,
                fiscal_quarter=4 if frequency == "quarterly" else None,
                period_type=frequency, period_start="1999-01-01" if frequency == "annual" else "1999-10-01",
                period_end="1999-12-31", value=Decimal("1.2345678901"), unit="USD",
                rule_version="test-only-v1", payload={},
            ))
        session.commit()
        rows = session.scalars(select(NormalizedMetric).where(NormalizedMetric.metric_key == "uniqueness_test")).all()
        assert {row.period_type for row in rows} == {"annual", "quarterly"}
        assert all(row.value == Decimal("1.2345678901") for row in rows)
        session.add(NormalizedMetric(
            company_id=company.id, metric_key="uniqueness_test", fiscal_year=1999,
            period_type="annual", period_end="1999-12-31", value=Decimal("2"), unit="USD",
            rule_version="test-only-v1", payload={},
        ))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
