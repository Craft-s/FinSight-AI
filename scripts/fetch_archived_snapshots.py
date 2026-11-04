"""Extract unchanged historical SEC JSON bodies from public VCR recordings.

This helper contacts raw.githubusercontent.com only; it does not contact SEC.
The recorded SEC response Date and the mirror download time are kept separate.
Run with Python 3.12 and PyYAML installed (development dependency only).
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from decimal import Decimal
from email.utils import parsedate_to_datetime
from hashlib import sha256
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

import yaml

REPOSITORY = "https://github.com/dgunning/edgartools"
COMMIT = "3931b22077eee1e0d2b77a8c5a61ab415d51ae0b"
ARCHIVE_ROOT = f"https://raw.githubusercontent.com/dgunning/edgartools/{COMMIT}/tests/cassettes/"
SOURCES = {
    "0000320193": {
        "submissions": "test_get_entity_by_ticker.yaml",
        "companyfacts": "test_issue_910_aapl_company_facts.yaml",
    },
    "0000789019": {
        "submissions": "test_company_repr.yaml",
        "companyfacts": "TestCompanyTTMIntegration.test_msft_fiscal_year_labels_match_fiscal_periods.yaml",
    },
    "0001045810": {
        "submissions": "test_company_repr.yaml",
        "companyfacts": "test_issue_910_nvda_company_facts.yaml",
    },
}


def iso_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def sec_url(cik: str, kind: str) -> str:
    prefix = "submissions" if kind == "submissions" else "api/xbrl/companyfacts"
    return f"https://data.sec.gov/{prefix}/CIK{cik}.json"


def extract_recording(payload: bytes, cik: str, kind: str) -> tuple[bytes, dict]:
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    recording = yaml.load(payload, Loader=loader)
    matches = []
    expected_url = sec_url(cik, kind)
    for interaction in recording.get("interactions", []):
        if interaction.get("request", {}).get("uri") != expected_url:
            continue
        response = interaction.get("response", {})
        if response.get("status", {}).get("code") != 200:
            continue
        body = response.get("body", {}).get("string")
        if not isinstance(body, (str, bytes)):
            raise TypeError("Recording contains no extractable response body")
        body_bytes = body.encode("utf-8") if isinstance(body, str) else body
        # Validation only: never serialize or alter the archived financial body.
        parsed = json.loads(body_bytes, parse_float=Decimal)
        if str(parsed.get("cik", "")).zfill(10) != cik:
            raise ValueError("Archived body CIK does not match original SEC request")
        if kind == "companyfacts" and "us-gaap" not in parsed.get("facts", {}):
            raise ValueError("Companyfacts recording lacks standard financial facts")
        if kind == "submissions" and "recent" not in parsed.get("filings", {}):
            raise ValueError("Submissions recording lacks recent columnar filings")
        headers = {name.lower(): values for name, values in response.get("headers", {}).items()}
        date_header = headers.get("date")
        if not date_header:
            raise ValueError("Missing original response Date; refusing to invent acquisition time")
        if isinstance(date_header, list):
            date_header = date_header[0]
        recorded_at = parsedate_to_datetime(date_header)
        if recorded_at.tzinfo is None:
            raise ValueError("Response Date must include timezone")
        matches.append(
            (
                recorded_at,
                body_bytes,
                {
                    "original_url": expected_url,
                    "http_status": 200,
                    "fetched_at": iso_utc(recorded_at),
                    "original_response_date": date_header,
                    "body_encoding": "utf-8",
                    "body_sha256": sha256(body_bytes).hexdigest(),
                    "body_bytes": len(body_bytes),
                },
            )
        )
    if not matches:
        raise ValueError(f"No successful archived response for {expected_url}")
    _, body_bytes, metadata = max(matches, key=lambda item: item[0])
    return body_bytes, metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/snapshots"))
    args = parser.parse_args()
    cache: dict[str, tuple[bytes, str]] = {}
    for cik, kinds in SOURCES.items():
        directory = args.output / cik
        directory.mkdir(parents=True, exist_ok=True)
        manifest = {
            "source_kind": "sec_snapshot",
            "cik": cik,
            "origin": "third_party_archival_mirror",
            "repository": REPOSITORY,
            "commit": COMMIT,
            "downloaded_at": iso_utc(datetime.now(UTC)),
            "fetched_at": None,
            "sha256": {},
            "sources": {},
            "notice": (
                "Historical SEC HTTP response bodies extracted unchanged from public VCR "
                "recordings. Downloaded from a third-party archive, not fetched directly "
                "from SEC by FinSight AI. Source response dates differ by document. "
                "Top-level fetched_at is the earliest source date and is a conservative "
                "bundle timestamp, not a claim that all financial facts were known then."
            ),
        }
        for kind, filename in kinds.items():
            archive_url = ARCHIVE_ROOT + quote(filename, safe="")
            if filename not in cache:
                request = Request(
                    archive_url, headers={"User-Agent": "FinSightAI archival-snapshot-import"}
                )
                with urlopen(request, timeout=90) as response:
                    payload = response.read(15_000_001)
                if len(payload) > 15_000_000:
                    raise ValueError("Archive exceeds 15 MB import limit")
                cache[filename] = payload, sha256(payload).hexdigest()
            payload, archive_hash = cache[filename]
            body_bytes, metadata = extract_recording(payload, cik, kind)
            metadata.update(
                {
                    "archive_url": archive_url,
                    "archive_path": f"tests/cassettes/{filename}",
                    "archive_sha256": archive_hash,
                    "archive_commit": COMMIT,
                }
            )
            (directory / f"{kind}.json").write_bytes(body_bytes)
            manifest["sha256"][kind] = metadata["body_sha256"]
            manifest["sources"][kind] = metadata
            print(
                f"{cik} {kind}: {metadata['body_bytes']} bytes, SEC response {metadata['fetched_at']}",
                flush=True,
            )
        manifest["fetched_at"] = min(
            source["fetched_at"] for source in manifest["sources"].values()
        )
        (directory / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
