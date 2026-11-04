from datetime import UTC, datetime

from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.models import Company, FinancialFact, SourceSnapshot, SyncJob
from app.db.session import session_factory
from app.services.sync import import_company
from tests.conftest import FIXTURES, TEST_TOKEN


def test_empty_database_and_auth(api):
    assert api.get("/api/v1/companies/search").json()["items"] == []
    assert api.get("/health").json()["source_kind"] == "synthetic_fixture"
    assert api.post("/api/v1/companies/320193/sync").status_code == 401
    assert api.get("/api/v1/companies/0").status_code == 422


def test_search_candidates_and_no_sql_injection(seeded):
    assert len(seeded.get("/api/v1/companies/search?q=").json()["items"]) == 3
    for term in ("AAPL", "Apple", "320193", "0000320193"):
        body = seeded.get("/api/v1/companies/search", params={"q": term}).json()
        assert body["items"][0]["cik"] == "0000320193"
    assert seeded.get("/api/v1/companies/search", params={"q": "' OR 1=1 --"}).json()["items"] == []
    assert seeded.get("/api/v1/companies/search?market=CN").status_code == 422


def test_metrics_filings_sources_closed_loop(seeded):
    company = seeded.get("/api/v1/companies/320193").json()
    assert company["source_kind"] == "synthetic_fixture"
    assert company["is_stale"] is True
    result = seeded.get("/api/v1/companies/320193/metrics?years=2")
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["selection_policy"] == "latest_restated"
    assert len({m["period_end"] for m in body["items"]}) <= 2
    known = [m for m in body["items"] if m["value"] is not None]
    assert len(known) >= 5
    assert all(isinstance(m["value"], str) for m in known)
    source_id = known[0]["provenance"]["source_snapshot_id"]
    source = seeded.get(f"/api/v1/sources/{source_id}").json()
    assert len(source["sha256"]) == 64
    assert source["source_kind"] == "synthetic_fixture"
    filings = seeded.get("/api/v1/companies/320193/filings?page_size=1").json()
    assert len(filings["items"]) == 1
    assert filings["total"] >= 1
    filing = filings["items"][0]
    assert seeded.get(f"/api/v1/filings/{filing['id']}").json()["source_url"].startswith("https://www.sec.gov/Archives/")
    events = seeded.get("/api/v1/companies/320193/events").json()
    assert all(row["form_type"] in {"8-K", "8-K/A"} for row in events["items"])
    quarterly = seeded.get("/api/v1/companies/320193/metrics?frequency=quarterly")
    assert quarterly.status_code == 200, quarterly.text
    assert quarterly.json()["frequency"] == "quarterly"


def test_reimport_idempotent_and_raw_facts_retained(seeded):
    with session_factory()() as session:
        before = session.scalar(select(func.count()).select_from(FinancialFact))
        snapshots = session.scalar(select(func.count()).select_from(SourceSnapshot))
        import_company(session, (FIXTURES / "aapl_submissions.json").read_bytes(), (FIXTURES / "aapl_companyfacts.json").read_bytes(), datetime(2026, 1, 1, tzinfo=UTC))
        session.commit()
        assert session.scalar(select(func.count()).select_from(FinancialFact)) == before
        assert session.scalar(select(func.count()).select_from(SourceSnapshot)) == snapshots


def test_mode_isolation_blocks_cross_loading(seeded, monkeypatch):
    monkeypatch.setenv("DATA_MODE", "sec_live")
    get_settings.cache_clear()
    response = seeded.get("/api/v1/companies/search")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATA_MODE_MISMATCH"
    monkeypatch.setenv("DATA_MODE", "synthetic_fixture")
    get_settings.cache_clear()


def test_sync_failure_keeps_cache_and_has_queryable_job(seeded):
    response = seeded.post("/api/v1/companies/320193/sync", json={"force": True}, headers={"X-Admin-Token": TEST_TOKEN})
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    result = seeded.get(f"/api/v1/sync/jobs/{job_id}").json()
    assert result["status"] == "failed"
    assert result["error_code"] == "SNAPSHOT_NOT_CONFIGURED"
    assert seeded.get("/api/v1/companies/320193/metrics").json()["items"]


def test_inflight_sync_reused(seeded):
    with session_factory()() as session:
        job = SyncJob(cik="0000320193", active_key="0000320193", status="queued")
        session.add(job)
        session.commit()
        job_id = job.id
    response = seeded.post("/api/v1/companies/320193/sync", headers={"X-Admin-Token": TEST_TOKEN})
    assert response.json() == {"job_id": job_id, "status": "queued", "reused": True}


def test_validation_errors_do_not_echo_sensitive_input(api):
    secret = "must-not-appear-in-error"
    response = api.post("/api/v1/companies/320193/sync", json={"force": secret}, headers={"X-Admin-Token": TEST_TOKEN})
    assert response.status_code == 422
    assert secret not in response.text
    assert response.json()["request_id"]
    assert response.headers["X-Request-ID"]


def test_database_migration_and_company_uniqueness(seeded):
    with session_factory()() as session:
        assert session.scalar(select(func.count()).select_from(Company)) == 3


def test_source_specific_timestamps_and_archive_provenance(api):
    dates = {
        "bundle": datetime(2025, 1, 1, tzinfo=UTC),
        "submissions": datetime(2025, 1, 1, tzinfo=UTC),
        "companyfacts": datetime(2025, 3, 1, tzinfo=UTC),
        "manifest": {"sources": {"companyfacts": {"archive_commit": "synthetic-test-commit"}}},
    }
    with session_factory()() as session:
        import_company(session, (FIXTURES / "aapl_submissions.json").read_bytes(), (FIXTURES / "aapl_companyfacts.json").read_bytes(), dates)
        session.commit()
    company = api.get("/api/v1/companies/320193").json()
    assert company["last_synced_at"].startswith("2025-01-01")
    metric = api.get("/api/v1/companies/320193/metrics").json()["items"][0]
    assert metric["provenance"]["snapshot_fetched_at"].startswith("2025-03-01")
    assert metric["provenance"]["source_metadata"]["archive_commit"] == "synthetic-test-commit"


def test_stale_job_can_be_resubmitted(seeded):
    with session_factory()() as session:
        old = SyncJob(cik="0000320193", active_key="0000320193", status="running", created_at=datetime(2025, 1, 1, tzinfo=UTC))
        session.add(old)
        session.commit()
        old_id = old.id
    response = seeded.post("/api/v1/companies/320193/sync", headers={"X-Admin-Token": TEST_TOKEN})
    assert response.status_code == 202
    assert response.json()["job_id"] != old_id
    old = seeded.get(f"/api/v1/sync/jobs/{old_id}").json()
    assert old["status"] == "failed"
    assert old["error_code"] == "WORKER_TIMEOUT"
