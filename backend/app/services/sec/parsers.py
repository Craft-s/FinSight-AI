"""Strict SEC submissions parsing; all rows retain the original accession."""

import re
from datetime import date

SUPPORTED_FORMS = frozenset({"10-K", "10-Q", "8-K", "10-K/A", "10-Q/A", "8-K/A"})
ACCESSION = re.compile(r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")
SAFE_DOCUMENT = re.compile(r"^[A-Za-z0-9_.\-/]+$")


class InvalidSubmissions(ValueError):
    """Invalid or unsafe SEC response; the caller should preserve old cached data."""


def normalize_cik(value: str | int) -> str:
    raw = str(value).strip()
    if not re.fullmatch(r"[0-9]{1,10}", raw) or int(raw) <= 0:
        raise ValueError("CIK must be 1-10 ASCII digits and greater than zero")
    return raw.zfill(10)


def filing_url(cik: str | int, accession: str, primary_document: str | None = None) -> str:
    normalized = normalize_cik(cik)
    if not isinstance(accession, str) or not ACCESSION.fullmatch(accession):
        raise InvalidSubmissions("Invalid SEC accession number")
    base = (
        f"https://www.sec.gov/Archives/edgar/data/{int(normalized)}/{accession.replace('-', '')}/"
    )
    if not primary_document:
        return f"{base}{accession}-index.html"
    if (
        not isinstance(primary_document, str)
        or not SAFE_DOCUMENT.fullmatch(primary_document)
        or primary_document.startswith("/")
        or any(part in {"", ".", ".."} for part in primary_document.split("/"))
    ):
        raise InvalidSubmissions("Unsafe SEC primaryDocument path")
    return base + primary_document


def _parse_date(value: str, field: str, optional: bool = False) -> str | None:
    if optional and not value:
        return None
    if not isinstance(value, str):
        raise InvalidSubmissions(f"Invalid {field}")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise InvalidSubmissions(f"Invalid {field}") from exc


def parse_filings(submissions: dict, cik: str | int) -> list[dict]:
    """Parse recent or historical columnar filings without silent zip truncation.

    SEC historical shards contain the columns at their root. Missing optional
    columns are allowed; present columns must have the same length as ``form``.
    """
    normalized = normalize_cik(cik)
    if submissions.get("cik") is not None and normalize_cik(submissions["cik"]) != normalized:
        raise InvalidSubmissions("Submissions CIK mismatch")
    recent = submissions.get("filings", {}).get("recent", {})
    if "form" in submissions:
        recent = submissions
    if not recent:
        return []
    forms = recent.get("form")
    if not isinstance(forms, list):
        raise InvalidSubmissions("Missing form column")
    count = len(forms)
    for field in ("accessionNumber", "filingDate"):
        if field not in recent:
            raise InvalidSubmissions(f"Missing {field} column")
    for field, column in recent.items():
        if not isinstance(column, list) or len(column) != count:
            raise InvalidSubmissions(f"Misaligned SEC column: {field}")
    result = {}
    for index, form in enumerate(forms):
        if form not in SUPPORTED_FORMS:
            continue
        accession = recent["accessionNumber"][index]
        document = recent.get("primaryDocument", [""] * count)[index]
        items_raw = recent.get("items", [""] * count)[index] or ""
        if not isinstance(items_raw, str):
            raise InvalidSubmissions("Invalid items column value")
        row = {
            "accession_number": accession,
            "form_type": form,
            "filing_date": _parse_date(recent["filingDate"][index], "filingDate"),
            "report_date": _parse_date(
                recent.get("reportDate", [""] * count)[index], "reportDate", optional=True
            ),
            "primary_document": document or None,
            "source_url": filing_url(normalized, accession, document),
            "amended": form.endswith("/A"),
            "items": [item.strip() for item in items_raw.split(",") if item.strip()],
        }
        if accession in result and result[accession] != row:
            raise InvalidSubmissions("Conflicting duplicate accession")
        result[accession] = row
    return sorted(
        result.values(), key=lambda row: (row["filing_date"], row["accession_number"]), reverse=True
    )
