"""Security/fault acceptance against isolated fixtures, never production data."""

import logging
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from fastapi import BackgroundTasks
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.models import Company, now
from app.db.research_models import ResearchJob, ResearchReport
from app.db.session import get_session, session_factory
from tests.conftest import TEST_TOKEN
from tests.test_research_api import prepared as prepared
from tests.test_research_api import request_for

HEADERS = {"X-Admin-Token": TEST_TOKEN}
SECRET = "synthetic-only-sensitive-error-value"


@pytest.mark.parametrize("token", [None, b"wrong-test-token", b"\xff-invalid-header"])
def test_private_endpoint_matrix_rejects_bad_tokens_before_side_effects(api, token):
    headers = {} if token is None else {b"X-Admin-Token": token}
    paths = [
        ("POST", "/companies/320193/sync"),
        ("POST", "/companies/320193/overview"),
        ("GET", "/overview/jobs/unknown"),
        ("GET", "/llm/configs"),
        ("POST", "/llm/configs"),
        *[("POST", f"/llm/configs/unknown/{action}") for action in ("models", "test", "activate", "delete")],
        ("POST", "/filings/unknown/documents/sync"),
        ("GET", "/documents/jobs/unknown"),
        ("POST", "/research/jobs"),
        ("GET", "/research/jobs/unknown"),
        ("POST", "/research/jobs/unknown/cancel"),
        ("POST", "/research/jobs/unknown/retry"),
        ("GET", "/research/history"),
        ("GET", "/research/reports/unknown"),
        ("GET", "/research/reports/unknown/download"),
    ]
    for method, path in paths:
        response = api.request(method, "/api/v1" + path, headers=headers, **({"json": {}} if method == "POST" else {}))
        assert response.status_code == 401, (path, response.text)
        assert response.json()["error"]["code"] == "AUTH_REQUIRED"
        assert response.headers["Cache-Control"] == "no-store"
    with session_factory()() as session:
        assert session.scalar(select(func.count()).select_from(ResearchJob)) == 0


def test_unexpected_endpoint_exception_is_sanitized_before_server_logging(api, monkeypatch, caplog):
    from app.main import app, logger

    def failed_dependency():
        raise RuntimeError(SECRET)

    app.dependency_overrides[get_session] = failed_dependency
    try:
        # Alembic fileConfig in repeated test migrations disables existing
        # loggers; restore this one locally to observe real emitted records.
        monkeypatch.setattr(logger, "disabled", False)
        # raise_server_exceptions=True catches the Starlette re-raise boundary:
        # merely replacing a 500 response body does not prevent raw server logs.
        with caplog.at_level(logging.ERROR, logger="finsight.api"):
            response = TestClient(app, raise_server_exceptions=True).get("/api/v1/companies/search")
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "INTERNAL_ERROR"
        assert response.json()["request_id"] == response.headers["X-Request-ID"]
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["Cache-Control"] == "no-store"
        assert SECRET not in response.text + caplog.text
        records = [record for record in caplog.records if record.name == "finsight.api"]
        assert records and "RuntimeError" in records[-1].getMessage()
        assert not records[-1].exc_info
    finally:
        app.dependency_overrides.pop(get_session)


def test_health_reports_dependency_failure_and_recovers_without_details(api, monkeypatch):
    from app import main

    class Ping:
        def ping(self):
            return True

    class Unavailable:
        def connect(self):
            raise RuntimeError(SECRET)

        def ping(self):
            raise RuntimeError(SECRET)

    healthy_redis = SimpleNamespace(from_url=lambda *args, **kwargs: Ping())
    monkeypatch.setattr(main, "Redis", healthy_redis)
    monkeypatch.setenv("TASK_MODE", "rq")
    get_settings.cache_clear()
    assert api.get("/health/ready").status_code == 200
    with monkeypatch.context() as fault:
        fault.setattr(main, "get_engine", lambda: Unavailable())
        fault.setattr(main, "Redis", SimpleNamespace(from_url=lambda *args, **kwargs: Unavailable()))
        health = api.get("/health")
        assert health.json()["status"] == "degraded"
        assert health.json()["database"] == health.json()["redis"] == "unavailable"
        assert health.json()["ai_available"] is False
        ready = api.get("/health/ready")
        assert ready.status_code == 503 and ready.json()["error"]["code"] == "NOT_READY"
        assert api.get("/health/live").status_code == 200
        assert SECRET not in health.text + ready.text
    assert api.get("/health/ready").status_code == 200


def test_untrusted_company_metadata_is_returned_as_json_text(seeded):
    payload = '<img src=x onerror="window.fixtureXss=true"><script>alert(1)</script>'
    with session_factory()() as session:
        company = session.scalar(select(Company).where(Company.cik == "0000320193"))
        company.name = payload
        session.commit()
    response = seeded.get("/api/v1/companies/320193")
    assert response.status_code == 200
    assert response.json()["name"] == payload
    assert response.headers["Content-Type"].startswith("application/json")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    # Actual DOM escaping is a separate browser acceptance check.


def test_research_queue_failure_is_private_queryable_and_retryable(prepared, monkeypatch):
    from app.services.research import jobs

    api, config, filings = prepared
    body = request_for(config, filings)
    monkeypatch.setenv("TASK_MODE", "rq")
    get_settings.cache_clear()

    def unavailable(*args, **kwargs):
        raise RuntimeError(SECRET)

    with monkeypatch.context() as fault:
        fault.setattr(jobs, "Redis", SimpleNamespace(from_url=unavailable))
        failed = api.post("/api/v1/research/jobs", headers=HEADERS, json=body)
    assert failed.status_code == 503, failed.text
    identifier = failed.json()["error"]["details"]["job_id"]
    job = api.get(f"/api/v1/research/jobs/{identifier}", headers=HEADERS)
    assert job.json()["status"] == "failed"
    assert job.json()["error_code"] == "QUEUE_UNAVAILABLE"
    assert job.json()["report_id"] is None
    assert SECRET not in failed.text + job.text
    assert api.get(f"/api/v1/research/jobs/{identifier}").status_code == 401
    assert api.get("/api/v1/companies/320193/metrics").json()["items"]
    monkeypatch.setenv("TASK_MODE", "inline")
    get_settings.cache_clear()
    retry = api.post(f"/api/v1/research/jobs/{identifier}/retry", headers=HEADERS, json={})
    assert retry.status_code == 202, retry.text
    recovered = api.get(f"/api/v1/research/jobs/{retry.json()['job_id']}", headers=HEADERS).json()
    assert recovered["status"] == "succeeded" and recovered["report_id"]
    with session_factory()() as session:
        assert session.get(ResearchJob, recovered["id"]).parent_job_id == identifier


def test_expired_research_attempt_cannot_publish_after_replacement(prepared, monkeypatch):
    from app.services.research.jobs import run_research_job

    api, config, filings = prepared
    body = request_for(config, filings)
    monkeypatch.setattr(BackgroundTasks, "add_task", lambda *_args, **_kwargs: None)
    original = api.post("/api/v1/research/jobs", headers=HEADERS, json=body).json()["job_id"]
    with session_factory()() as session:
        job = session.get(ResearchJob, original)
        job.created_at, job.status = now() - timedelta(seconds=1300), "running"
        session.commit()
    replacement = api.post("/api/v1/research/jobs", headers=HEADERS, json=body)
    assert replacement.status_code == 202, replacement.text
    new_id = replacement.json()["job_id"]
    assert new_id != original
    old_job = api.get(f"/api/v1/research/jobs/{original}", headers=HEADERS).json()
    assert old_job["status"] == "failed" and old_job["error_code"] == "WORKER_TIMEOUT"
    run_research_job(original)
    run_research_job(new_id)
    with session_factory()() as session:
        assert session.get(ResearchJob, original).report_id is None
        assert session.get(ResearchJob, new_id).status == "succeeded"
        assert session.scalar(select(func.count()).select_from(ResearchReport)) == 1


def test_untrusted_cors_preflight_cannot_read_private_headers(api):
    response = api.options("/api/v1/llm/configs", headers={
        "Origin": "https://untrusted.invalid", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "x-admin-token,content-type",
    })
    assert response.status_code == 400
    assert "Access-Control-Allow-Origin" not in response.headers
    assert "Access-Control-Allow-Credentials" not in response.headers


def test_sec_client_does_not_adopt_environment_proxy(monkeypatch):
    from app.services.sec import client as sec
    from tests.test_sec_client import Limiter

    monkeypatch.setenv("SEC_USER_AGENT", "FinSightSecurityTest fixture@invalid.test")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy-with-credentials.invalid:8080")
    get_settings.cache_clear()
    options = []
    original = httpx.Client

    class InspectingClient(original):
        def __init__(self, *args, **kwargs):
            options.append(kwargs)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(sec.httpx, "Client", InspectingClient)
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, json={"cik": 320193}))
    raw, _ = sec.SecClient(redis=Limiter(), transport=transport).fetch("https://data.sec.gov/submissions/CIK0000320193.json")
    assert b"320193" in raw
    assert options[0]["trust_env"] is False and options[0]["follow_redirects"] is False
