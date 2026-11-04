import json
from copy import deepcopy
from pathlib import Path

import pytest

from app.services.sec.parsers import InvalidSubmissions, filing_url, normalize_cik, parse_filings

FIXTURE = Path(__file__).parent / "fixtures" / "synthetic" / "aapl_submissions.json"


def submissions():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.mark.parametrize("value", [320193, "320193", "0000320193", " 320193 "])
def test_cik_leading_zero_normalization(value):
    assert normalize_cik(value) == "0000320193"


@pytest.mark.parametrize(
    "value", ["", "0000000000", "1;DROP", "１２３", "-1", "12345678901", None, True]
)
def test_bad_cik_rejected(value):
    with pytest.raises(ValueError):
        normalize_cik(value)


def test_columnar_alignment_and_descending_dates():
    result = parse_filings(submissions(), "320193")
    assert len(result) == 5
    assert [row["filing_date"] for row in result] == sorted(
        [row["filing_date"] for row in result],
        reverse=True,
    )
    annual = next(row for row in result if row["report_date"] == "2024-09-28")
    assert annual["accession_number"] == "0000320193-24-000001"
    assert annual["source_url"].endswith("320193/000032019324000001/synthetic-2024.htm")


def test_form_types_and_event_items():
    result = parse_filings(submissions(), 320193)
    assert {r["form_type"] for r in result} == {"10-K", "10-Q", "8-K"}
    event = next(row for row in result if row["form_type"] == "8-K")
    assert event["items"] == ["2.02", "9.01"]


def test_amendments_are_separate_not_merged():
    data = submissions()
    data["filings"]["recent"]["form"][0] = "10-K/A"
    result = parse_filings(data, 320193)
    amendment = next(r for r in result if r["accession_number"] == "0000320193-22-000001")
    assert amendment["amended"] is True
    assert amendment["form_type"] == "10-K/A"


def test_misaligned_optional_column_rejected_without_zip_truncation():
    data = submissions()
    data["filings"]["recent"]["items"].pop()
    with pytest.raises(InvalidSubmissions, match="Misaligned"):
        parse_filings(data, 320193)


def test_missing_required_column_rejected():
    data = submissions()
    del data["filings"]["recent"]["accessionNumber"]
    with pytest.raises(InvalidSubmissions, match="Missing"):
        parse_filings(data, 320193)


def test_historical_shard_shape():
    data = submissions()["filings"]["recent"]
    assert len(parse_filings(data, 320193)) == 5


def test_optional_columns_absent_are_explicitly_missing():
    data = submissions()
    del data["filings"]["recent"]["reportDate"]
    del data["filings"]["recent"]["primaryDocument"]
    result = parse_filings(data, 320193)
    assert result[0]["report_date"] is None
    assert result[0]["primary_document"] is None
    assert result[0]["source_url"].endswith("-index.html")


@pytest.mark.parametrize(
    "document",
    [
        "https://evil.test/a.htm",
        "//evil.test/a.htm",
        "../a.htm",
        "/a.htm",
        "a/../b.htm",
        "a\\b.htm",
        "a.htm?key=x",
        "a%2F..htm",
        "a.htm#x",
        "a//b.htm",
        "a\n.htm",
    ],
)
def test_unsafe_document_paths_rejected(document):
    with pytest.raises(InvalidSubmissions):
        filing_url(320193, "0000320193-24-000001", document)


def test_original_relative_primary_document_preserved():
    url = filing_url(320193, "0000320193-24-000001", "exhibits/report.htm")
    assert (
        url
        == "https://www.sec.gov/Archives/edgar/data/320193/000032019324000001/exhibits/report.htm"
    )


def test_invalid_accession_and_dates_rejected():
    with pytest.raises(InvalidSubmissions):
        filing_url(320193, "made-up", "a.htm")
    data = submissions()
    data["filings"]["recent"]["filingDate"][0] = "2024-99-01"
    with pytest.raises(InvalidSubmissions):
        parse_filings(data, 320193)


def test_duplicate_accession_conflict_rejected():
    data = submissions()
    recent = data["filings"]["recent"]
    for column in recent.values():
        column.append(deepcopy(column[0]))
    recent["primaryDocument"][-1] = "other.htm"
    with pytest.raises(InvalidSubmissions, match="Conflicting"):
        parse_filings(data, 320193)


def test_unrelated_form_excluded():
    data = submissions()
    data["filings"]["recent"]["form"][0] = "4"
    assert len(parse_filings(data, 320193)) == 4
