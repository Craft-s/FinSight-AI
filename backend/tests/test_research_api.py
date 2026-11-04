"""Research integration against explicitly synthetic documents and injected test providers."""
import json
from datetime import UTC, datetime

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.models import Company, Filing
from app.db.research_models import ResearchJob, ResearchReport
from app.db.session import session_factory
from app.services.documents.service import import_document
from tests.conftest import TEST_TOKEN

HEADERS = {"X-Admin-Token": TEST_TOKEN}
HTML = b"""<!doctype html><html><head><title>SYNTHETIC TEST FILING</title></head><body>
<h1>Item Risk Factors</h1><p>Revenue and net income are exposed to supply chain uncertainty and operating risks.</p>
<h2>Liquidity and Operations</h2><p>Cash flow and liquidity depend on operating performance and working capital.</p>
</body></html>"""


class ResearchFixtureProvider:
    def validate_config(self, _model):
        return {}

    def test_connection(self, _model):
        return {"usage": {"input_tokens": 1}, "duration_ms": 1}

    def generate_structured(self, _model, _system, prompt, options=None):
        context = json.loads(prompt)
        by_filing = {}
        for citation in context["citations"]:
            by_filing.setdefault(citation["filing_id"], citation)
        claims = [{"text": citation["excerpt"], "kind": "disclosed_fact",
                   "citation_ids": [citation["citation_id"]], "metric_refs": [], "reported_values": []}
                  for citation in by_filing.values()]
        return {"content": json.dumps({"claims": claims, "uncertainties": [], "insufficient_evidence": False}),
                "usage": {"input_tokens": 3, "output_tokens": 3}, "duration_ms": 1}


@pytest.fixture
def prepared(seeded, monkeypatch):
    from app.services import llm_configs
    monkeypatch.setenv("MASTER_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    monkeypatch.setattr(llm_configs, "create_provider", lambda *args, **kwargs: ResearchFixtureProvider())
    with session_factory()() as session:
        company = session.scalar(select(Company).where(Company.cik == "0000320193"))
        filings = session.scalars(select(Filing).where(Filing.company_id == company.id)).all()
        for filing in filings:
            import_document(session, filing, HTML, filing.source_url, datetime.now(UTC), "synthetic_fixture")
        session.commit()
        rows = [{"id": f.id, "form": f.form_type, "url": f.source_url} for f in filings]
    saved = seeded.post("/api/v1/llm/configs", headers=HEADERS, json={
        "provider": "openai", "model_id": "fixture-research", "api_key": "test-only-never-production",
    })
    assert saved.status_code == 200, saved.text
    config = saved.json()
    tested = seeded.post(f"/api/v1/llm/configs/{config['id']}/test", headers=HEADERS, json={"version": config["version"]})
    assert tested.status_code == 200, tested.text
    return seeded, config, rows


def request_for(config, filings, task_type="summary"):
    periodic = [f for f in filings if f["form"] in {"10-K", "10-Q", "10-K/A", "10-Q/A"}]
    events = [f for f in filings if f["form"] in {"8-K", "8-K/A"}]
    chosen = periodic[:2] if task_type == "comparison" else events[:1] if task_type == "event" else periodic[:1]
    body = {"cik": "0000320193", "config_id": config["id"], "task_type": task_type,
            "filing_ids": [f["id"] for f in chosen], "language": "en-US"}
    if task_type == "question":
        body["question"] = "What operating risks are disclosed?"
    return body


@pytest.mark.parametrize("task_type", ["summary", "comparison", "event", "question"])
def test_four_research_paths_and_frozen_citations(prepared, task_type):
    api, config, filings = prepared
    body = request_for(config, filings, task_type)
    response = api.post("/api/v1/research/jobs", headers=HEADERS, json=body)
    assert response.status_code == 202, response.text
    job = api.get(f"/api/v1/research/jobs/{response.json()['job_id']}", headers=HEADERS).json()
    assert job["status"] == "succeeded", job
    report = api.get(f"/api/v1/research/reports/{job['report_id']}", headers=HEADERS)
    assert report.status_code == 200, report.text
    content = report.json()["content"]
    assert content["source_kind"] == "synthetic_fixture"
    assert all(c["excerpt"] and len(c["document_sha256"]) == 64 for c in content["citations"])
    assert {c["filing_id"] for c in content["citations"]} == set(body["filing_ids"])
    repeated = api.post("/api/v1/research/jobs", headers=HEADERS, json=body).json()
    assert repeated["job_id"] == job["id"] and repeated["reused"] is True
    download = api.get(f"/api/v1/research/reports/{job['report_id']}/download?format=md", headers=HEADERS)
    assert download.status_code == 200 and "attachment" in download.headers["Content-Disposition"]
    assert "https://www.sec.gov/Archives/" in download.text
    assert api.get("/api/v1/research/history", headers=HEADERS).json()["total"] == 1
    assert api.get(f"/api/v1/research/reports/{job['report_id']}").status_code == 401


def test_public_document_and_chinese_evidence_without_model_access(prepared):
    api, _config, filings = prepared
    filing_id = filings[0]["id"]
    documents = api.get(f"/api/v1/filings/{filing_id}/documents")
    assert documents.status_code == 200, documents.text
    assert documents.json()["items"]
    assert "raw_html" not in documents.text and "raw_bytes" not in documents.text
    result = api.get("/api/v1/companies/320193/evidence", params={"filing_id": filing_id, "q": "收入风险"})
    assert result.status_code == 200, result.text
    assert result.json()["items"] and result.json()["scope_note"]
    assert isinstance(result.json()["items"][0]["location"], dict)
    assert api.get("/api/v1/companies/320193/metrics").json()["items"]


def test_wrong_company_and_invalid_task_parameters_rejected(prepared):
    api, config, filings = prepared
    body = request_for(config, filings)
    body["cik"] = "0000789019"
    assert api.post("/api/v1/research/jobs", headers=HEADERS, json=body).status_code == 404
    body["cik"] = "0000320193"
    body["task_type"] = "comparison"
    assert api.post("/api/v1/research/jobs", headers=HEADERS, json=body).status_code == 422
    body["task_type"] = "question"
    assert api.post("/api/v1/research/jobs", headers=HEADERS, json=body).status_code == 422
    body["question"] = "x" * 501
    assert api.post("/api/v1/research/jobs", headers=HEADERS, json=body).status_code == 422


def test_missing_documents_and_sync_failure_are_explicit(prepared):
    api, config, _filings = prepared
    with session_factory()() as session:
        company = session.scalar(select(Company).where(Company.cik == "0000320193"))
        filing = Filing(company_id=company.id, accession_number="0000320193-26-000999", form_type="10-K",
                        filing_date="2026-01-01", report_date="2025-12-31", primary_document="missing.htm",
                        source_url="https://www.sec.gov/Archives/edgar/data/320193/000032019326000999/missing.htm",
                        snapshot_id=session.scalar(select(Filing.snapshot_id).where(Filing.company_id == company.id)))
        session.add(filing)
        session.commit()
        identifier = filing.id
    body = {"cik": "320193", "task_type": "summary", "filing_ids": [identifier], "config_id": config["id"]}
    response = api.post("/api/v1/research/jobs", headers=HEADERS, json=body)
    assert response.status_code == 409 and response.json()["error"]["code"] == "DOCUMENT_NOT_READY"
    prepared_job = api.post(f"/api/v1/filings/{identifier}/documents/sync", headers=HEADERS, json={})
    assert prepared_job.status_code == 202
    job = api.get(f"/api/v1/documents/jobs/{prepared_job.json()['job_id']}", headers=HEADERS).json()
    assert job["status"] == "failed" and job["error_code"] == "DOCUMENT_NOT_READY"


def test_cancelled_queued_job_never_calls_model_and_can_retry(prepared, monkeypatch):
    from app.services.research import jobs
    api, config, filings = prepared
    class QueueFixture:
        def __init__(self, *args, **kwargs):
            pass
        def enqueue(self, *args, **kwargs):
            return None
    monkeypatch.setenv("TASK_MODE", "rq")
    get_settings.cache_clear()
    monkeypatch.setattr(jobs, "Queue", QueueFixture)
    response = api.post("/api/v1/research/jobs", headers=HEADERS, json=request_for(config, filings))
    identifier = response.json()["job_id"]
    cancelled = api.post(f"/api/v1/research/jobs/{identifier}/cancel", headers=HEADERS, json={})
    assert cancelled.json()["status"] == "cancelled"
    jobs.run_research_job(identifier)
    with session_factory()() as session:
        assert session.scalar(select(func.count()).select_from(ResearchReport)) == 0
    retried = api.post(f"/api/v1/research/jobs/{identifier}/retry", headers=HEADERS, json={})
    assert retried.status_code == 202 and retried.json()["job_id"] != identifier
    assert api.post(f"/api/v1/research/jobs/{retried.json()['job_id']}/retry", headers=HEADERS, json={}).status_code == 409


def test_running_cancellation_discards_upstream_result(prepared, monkeypatch):
    from app.services import llm_configs
    api, config, filings = prepared
    class CancelDuringCall(ResearchFixtureProvider):
        def generate_structured(self, *args, **kwargs):
            with session_factory()() as session:
                job = session.scalar(select(ResearchJob).where(ResearchJob.status == "running"))
                job.status, job.active_key = "cancelled", None
                session.commit()
            return super().generate_structured(*args, **kwargs)
    monkeypatch.setattr(llm_configs, "create_provider", lambda *args, **kwargs: CancelDuringCall())
    response = api.post("/api/v1/research/jobs", headers=HEADERS, json=request_for(config, filings))
    job = api.get(f"/api/v1/research/jobs/{response.json()['job_id']}", headers=HEADERS).json()
    assert job["status"] == "cancelled" and job["report_id"] is None


def test_frozen_document_versions_match_actual_retrieved_revision(prepared, monkeypatch):
    from app.services.research import context
    api, config, filings = prepared
    original = context.list_filing_documents
    imported = []

    def concurrent_import(session, identifier):
        previous = original(session, identifier)
        filing = session.get(Filing, identifier)
        changed = HTML.replace(b"working capital", b"updated working capital disclosure")
        newer = import_document(session, filing, changed, filing.source_url, datetime.now(UTC), "synthetic_fixture")
        session.flush()
        imported.append(newer.id)
        return previous

    monkeypatch.setattr(context, "list_filing_documents", concurrent_import)
    response = api.post("/api/v1/research/jobs", headers=HEADERS, json=request_for(config, filings))
    assert response.status_code == 202, response.text
    with session_factory()() as session:
        job = session.get(ResearchJob, response.json()["job_id"])
        assert {v["id"] for v in job.input_json["document_versions"]} == set(imported)
        assert {c["document_id"] for c in job.input_json["citations"]} == set(imported)


def test_reports_and_history_do_not_cross_owner_boundary(prepared):
    from app.core.auth import require_owner
    from app.main import app
    api, config, filings = prepared
    created = api.post("/api/v1/research/jobs", headers=HEADERS, json=request_for(config, filings)).json()
    job = api.get(f"/api/v1/research/jobs/{created['job_id']}", headers=HEADERS).json()
    app.dependency_overrides[require_owner] = lambda: "other-test-owner"
    try:
        assert api.get("/api/v1/research/history", headers=HEADERS).json()["total"] == 0
        assert api.get(f"/api/v1/research/jobs/{job['id']}", headers=HEADERS).status_code == 404
        assert api.get(f"/api/v1/research/reports/{job['report_id']}", headers=HEADERS).status_code == 404
        assert api.get(f"/api/v1/research/reports/{job['report_id']}/download", headers=HEADERS).status_code == 404
    finally:
        app.dependency_overrides.pop(require_owner)
