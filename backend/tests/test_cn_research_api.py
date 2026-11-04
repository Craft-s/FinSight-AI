"""TEST ONLY synthetic PDFs and injected model responses in disposable databases."""
import copy
import json
from datetime import UTC, datetime

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.errors import AppError
from app.db.cn_document_models import CnDocument, CnDocumentChunk
from app.db.cn_models import CnCompany
from app.db.cn_research_models import CnResearchJob, CnResearchReport
from app.db.llm_models import LLMConfig
from app.db.session import session_factory
from app.services.cn import documents, research
from app.services.cn.provider import Resource
from app.services.cn.service import ensure_cn_dataset, import_company_resources, import_directory
from tests.conftest import TEST_TOKEN
from tests.test_cn_api import directory, financial_resources
from tests.test_cn_documents import generated_pdf

SYMBOL = "600519.SH"
FIRST, SECOND, EVENT, MISSING = "1220000101", "1220000102", "1220000103", "1220000104"
HEADERS = {"X-Admin-Token": TEST_TOKEN}
STAMP = datetime(2026, 1, 1, tzinfo=UTC)
TEXT = "TEST ONLY synthetic filing. Revenue and operating cash flow depend on demand and working capital."
TEXT_SECOND = "TEST ONLY synthetic comparison filing. Operating cash liquidity and revenue depend on customer demand."


class FixtureProvider:
    calls = []

    def validate_config(self, _model):
        return {}

    def test_connection(self, _model):
        return {"usage": {}, "duration_ms": 1}

    def generate_structured(self, _model, _system, prompt, options=None):
        payload = json.loads(prompt)
        self.calls.append(copy.deepcopy(payload))
        by_filing = {}
        for citation in payload["citations"]:
            by_filing.setdefault(citation["filing_id"], citation)
        return {"content": json.dumps({
            "claims": [{"text": row["excerpt"], "kind": "disclosed_fact",
                        "citation_ids": [row["citation_id"]], "metric_refs": [], "reported_values": []}
                       for row in by_filing.values()],
            "uncertainties": [], "insufficient_evidence": False,
        }), "usage": {"input_tokens": 1, "output_tokens": 1}, "duration_ms": 1}


@pytest.fixture
def prepared_cn(api, monkeypatch):
    from app.services import llm_configs

    FixtureProvider.calls = []
    monkeypatch.setenv("MASTER_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    monkeypatch.setattr(llm_configs, "create_provider", lambda *args, **kwargs: FixtureProvider())
    rows = []
    for identifier, title in [(FIRST, "TEST ONLY 2025年年度报告"),
                              (SECOND, "TEST ONLY 2025年半年度报告"),
                              (EVENT, "TEST ONLY 普通公告"),
                              (MISSING, "TEST ONLY 2024年年度报告")]:
        rows.append({"secCode": "600519", "orgId": "gssh0600519", "announcementId": identifier,
                     "announcementTitle": title, "announcementTime": 1767225600000,
                     "adjunctUrl": f"finalpage/2026-01-01/{identifier}.PDF"})
    announcements = Resource("announcements", "https://www.cninfo.com.cn/new/hisAnnouncement/query",
        json.dumps({"announcements": rows}).encode(), STAMP, {"test_only": True})
    with session_factory()() as session:
        ensure_cn_dataset(session)
        import_directory(session, [directory()])
        company = session.get(CnCompany, SYMBOL)
        import_company_resources(session, company, financial_resources() + [announcements])
        session.add(CnCompany(symbol="000001.SZ", code="000001", exchange="SZ", name="TEST ONLY other company",
                              source_kind="synthetic_fixture", source_metadata={}, directory_updated_at=STAMP))
        session.commit()
        for identifier, text in [(FIRST, TEXT), (SECOND, TEXT_SECOND), (EVENT, TEXT)]:
            documents.import_pdf(session, SYMBOL, identifier, generated_pdf([text]),
                                 fetched_at=STAMP, source_kind="synthetic_fixture")
        session.commit()
    saved = api.post("/api/v1/llm/configs", headers=HEADERS, json={
        "provider": "openai", "model_id": "test-only-cn-model", "api_key": "TEST ONLY disposable model key",
    })
    assert saved.status_code == 200, saved.text
    config = saved.json()
    verified = api.post(f"/api/v1/llm/configs/{config['id']}/test", headers=HEADERS,
                        json={"version": config["version"]})
    assert verified.status_code == 200, verified.text
    return api, config


def body_for(config, task="summary"):
    body = {"symbol": SYMBOL, "task_type": task, "filing_ids": [FIRST, SECOND] if task == "comparison" else [FIRST],
            "config_id": config["id"], "language": "en-US"}
    if task == "question":
        body["question"] = "What revenue and operating cash risks are disclosed?"
    return body


def job_for(api, accepted):
    response = api.get('/api/v1/cn/research/jobs/' + accepted['job_id'], headers=HEADERS)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("task", ["summary", "comparison", "question"])
def test_cn_research_paths_freeze_pages_and_cache_without_latest_metrics(prepared_cn, task):
    api, config = prepared_cn
    body = body_for(config, task)
    accepted = api.post("/api/v1/cn/research/jobs", headers=HEADERS, json=body)
    assert accepted.status_code == 202, accepted.text
    job = job_for(api, accepted.json())
    assert job["status"] == "succeeded", job
    assert job["market"] == "CN" and job["symbol"] == SYMBOL and "cik" not in job
    report = api.get('/api/v1/cn/research/reports/' + job['report_id'], headers=HEADERS)
    assert report.status_code == 200, report.text
    content = report.json()["content"]
    assert content["market"] == "CN" and content["facts"] == []
    assert {row["filing_id"] for row in content["citations"]} == set(body["filing_ids"])
    assert all(row["location"]["page_start"] == 1 and len(row["document_sha256"]) == 64
               and "accession" not in row for row in content["citations"])
    repeated = api.post("/api/v1/cn/research/jobs", headers=HEADERS, json=body).json()
    assert repeated["reused"] and repeated["job_id"] == job["id"]
    assert len(FixtureProvider.calls) == 1
    with session_factory()() as session:
        saved = session.get(CnResearchJob, job["id"])
        assert saved.input_json["prompt_version"] == research.CN_PROMPT_VERSION
        assert saved.input_json["schema_version"] == research.CN_SCHEMA_VERSION
    download = api.get('/api/v1/cn/research/reports/' + job['report_id'] + '/download?format=md', headers=HEADERS)
    assert download.status_code == 200 and "attachment" in download.headers["Content-Disposition"]
    assert "#page=1" in download.text and "static.cninfo.com.cn" in download.text
    assert api.get('/api/v1/cn/research/history', headers=HEADERS).json()["total"] == 1


def test_cn_research_private_resources_and_histories_are_owner_bound(prepared_cn):
    from app.core.auth import require_owner
    from app.main import app

    api, config = prepared_cn
    body = body_for(config)
    assert api.post('/api/v1/cn/research/jobs', json=body).status_code == 401
    accepted = api.post('/api/v1/cn/research/jobs', headers=HEADERS, json=body).json()
    job = job_for(api, accepted)
    for path in ['/research/history', '/research/jobs/' + job['id'],
                 '/research/reports/' + job['report_id'], '/research/reports/' + job['report_id'] + '/download']:
        assert api.get('/api/v1/cn' + path).status_code == 401
    app.dependency_overrides[require_owner] = lambda: 'TEST ONLY another owner'
    try:
        assert api.get('/api/v1/cn/research/history', headers=HEADERS).json()['total'] == 0
        assert api.get('/api/v1/cn/research/jobs/' + job['id'], headers=HEADERS).status_code == 404
        assert api.get('/api/v1/cn/research/reports/' + job['report_id'], headers=HEADERS).status_code == 404
        assert api.get('/api/v1/cn/research/reports/' + job['report_id'] + '/download', headers=HEADERS).status_code == 404
        assert api.post('/api/v1/cn/research/jobs/' + job['id'] + '/cancel', headers=HEADERS, json={}).status_code == 404
        assert api.post('/api/v1/cn/research/jobs/' + job['id'] + '/retry', headers=HEADERS, json={}).status_code == 404
    finally:
        app.dependency_overrides.pop(require_owner)


@pytest.mark.parametrize('changes', [
    {'symbol': '000001.SZ'}, {'symbol': '600519'}, {'task_type': 'event'},
    {'task_type': 'comparison'}, {'task_type': 'question'}, {'question': 'not a question task'},
    {'filing_ids': [EVENT]}, {'filing_ids': ['unknown-filing']},
    {'task_type': 'comparison', 'filing_ids': [FIRST, FIRST]}, {'cik': '0000600519'},
])
def test_cn_request_rejects_wrong_identity_and_invalid_task(prepared_cn, changes):
    api, config = prepared_cn
    response = api.post('/api/v1/cn/research/jobs', headers=HEADERS, json={**body_for(config), **changes})
    assert response.status_code in {404, 422}, response.text
    assert FixtureProvider.calls == []


def test_cn_missing_pdf_and_unknown_question_do_not_call_model(prepared_cn):
    api, config = prepared_cn
    missing = {**body_for(config), 'filing_ids': [MISSING]}
    response = api.post('/api/v1/cn/research/jobs', headers=HEADERS, json=missing)
    assert response.status_code == 409 and response.json()['error']['code'] == 'DOCUMENT_NOT_READY'
    question = {**body_for(config, 'question'), 'question': 'unmatchedfixturekeyword'}
    response = api.post('/api/v1/cn/research/jobs', headers=HEADERS, json=question)
    assert response.status_code == 409 and response.json()['error']['code'] == 'EVIDENCE_NOT_FOUND'
    assert FixtureProvider.calls == []


class DeferredQueue:
    captured = []

    def __init__(self, *args, **kwargs):
        pass

    def enqueue(self, function, identifier, **kwargs):
        self.captured.append((function, identifier, kwargs))


def defer_jobs(monkeypatch):
    DeferredQueue.captured = []
    get_settings().task_mode = 'rq'
    monkeypatch.setattr(research, 'Queue', DeferredQueue)


def test_cn_cancel_queued_duplicate_delivery_and_retry_keep_frozen_context(prepared_cn, monkeypatch):
    api, config = prepared_cn
    defer_jobs(monkeypatch)
    body = body_for(config)
    first = api.post('/api/v1/cn/research/jobs', headers=HEADERS, json=body).json()
    repeated = api.post('/api/v1/cn/research/jobs', headers=HEADERS, json=body).json()
    assert repeated['job_id'] == first['job_id'] and repeated['reused']
    assert len(DeferredQueue.captured) == 1
    function, identifier, _options = DeferredQueue.captured[0]
    assert function == 'app.services.cn.research.run_research_job' and identifier == first['job_id']
    cancelled = api.post('/api/v1/cn/research/jobs/' + identifier + '/cancel', headers=HEADERS, json={})
    assert cancelled.json()['status'] == 'cancelled'
    research.run_research_job(identifier)
    assert FixtureProvider.calls == []
    with session_factory()() as session:
        original = copy.deepcopy(session.get(CnResearchJob, identifier).input_json)
        document = session.scalar(select(CnDocument).where(CnDocument.filing_id == FIRST))
        documents.import_pdf(session, SYMBOL, FIRST, generated_pdf([TEXT + ' TEST ONLY updated disclosure.']),
                             fetched_at=STAMP, source_kind='synthetic_fixture')
        session.commit()
        assert document.sha256 != documents.list_documents(session, SYMBOL, FIRST)[0]['sha256']
    retried = api.post('/api/v1/cn/research/jobs/' + identifier + '/retry', headers=HEADERS, json={})
    assert retried.status_code == 202 and retried.json()['job_id'] != identifier
    with session_factory()() as session:
        second = session.get(CnResearchJob, retried.json()['job_id'])
        assert second.parent_job_id == identifier and second.input_json == original
    research.run_research_job(retried.json()['job_id'])
    assert job_for(api, retried.json())['status'] == 'succeeded'
    research.run_research_job(retried.json()['job_id'])
    assert len(FixtureProvider.calls) == 1
    assert api.post('/api/v1/cn/research/jobs/' + retried.json()['job_id'] + '/retry', headers=HEADERS, json={}).status_code == 409


def test_cn_queue_failure_is_durable_and_does_not_call_model(prepared_cn, monkeypatch):
    api, config = prepared_cn
    get_settings().task_mode = 'rq'
    def unavailable(*args, **kwargs):
        raise RuntimeError('TEST ONLY queue unavailable')
    monkeypatch.setattr(research.Queue, 'enqueue', unavailable)
    response = api.post('/api/v1/cn/research/jobs', headers=HEADERS, json=body_for(config))
    assert response.status_code == 503
    job = job_for(api, {'job_id': response.json()['error']['details']['job_id']})
    assert job['status'] == 'failed' and job['error_code'] == 'QUEUE_UNAVAILABLE'
    with session_factory()() as session:
        assert session.get(CnResearchJob, job['id']).active_key is None
    assert FixtureProvider.calls == []


def test_cn_config_change_before_work_is_rejected(prepared_cn, monkeypatch):
    api, config = prepared_cn
    defer_jobs(monkeypatch)
    accepted = api.post('/api/v1/cn/research/jobs', headers=HEADERS, json=body_for(config)).json()
    with session_factory()() as session:
        saved = session.get(LLMConfig, config['id'])
        saved.version += 1
        saved.verified_version = saved.version
        session.commit()
    research.run_research_job(accepted['job_id'])
    assert job_for(api, accepted)['error_code'] == 'CONFIG_CHANGED'
    assert FixtureProvider.calls == []
    assert api.post('/api/v1/cn/research/jobs/' + accepted['job_id'] + '/retry', headers=HEADERS, json={}).status_code == 409


@pytest.mark.parametrize('change', ['cancel', 'config'])
def test_cn_inflight_cancellation_or_config_change_cannot_publish(prepared_cn, monkeypatch, change):
    from app.services import llm_configs

    api, config = prepared_cn
    class ChangedDuringGeneration(FixtureProvider):
        def generate_structured(self, *args, **kwargs):
            with session_factory()() as session:
                running = session.scalar(select(CnResearchJob).where(CnResearchJob.status == 'running'))
                if change == 'cancel':
                    running.status, running.active_key = 'cancelled', None
                else:
                    saved = session.get(LLMConfig, config['id'])
                    saved.version += 1
                    saved.verified_version = saved.version
                session.commit()
            return super().generate_structured(*args, **kwargs)
    monkeypatch.setattr(llm_configs, 'create_provider', lambda *args, **kwargs: ChangedDuringGeneration())
    response = api.post('/api/v1/cn/research/jobs', headers=HEADERS, json=body_for(config))
    job = job_for(api, response.json())
    assert job['status'] == ('cancelled' if change == 'cancel' else 'failed')
    assert job['report_id'] is None
    with session_factory()() as session:
        assert session.scalar(select(func.count()).select_from(CnResearchReport)) == 0


@pytest.mark.parametrize('field,value', [('market', 'US'), ('prompt_version', 'test-only-old-prompt'),
                                        ('schema_version', 'test-only-old-schema'), ('source_kind', 'sec_live')])
def test_cn_changed_frozen_context_fails_before_model(prepared_cn, monkeypatch, field, value):
    api, config = prepared_cn
    defer_jobs(monkeypatch)
    accepted = api.post('/api/v1/cn/research/jobs', headers=HEADERS, json=body_for(config)).json()
    with session_factory()() as session:
        saved = session.get(CnResearchJob, accepted['job_id'])
        saved.input_json = {**saved.input_json, field: value}
        session.commit()
    research.run_research_job(accepted['job_id'])
    assert job_for(api, accepted)['error_code'] == 'RESEARCH_CONTEXT_INVALID'
    assert FixtureProvider.calls == []


def test_cn_modified_document_identity_and_model_claim_are_rejected(prepared_cn, monkeypatch):
    api, config = prepared_cn
    defer_jobs(monkeypatch)
    accepted = api.post('/api/v1/cn/research/jobs', headers=HEADERS, json=body_for(config)).json()
    with session_factory()() as session:
        job = session.get(CnResearchJob, accepted['job_id'])
        context = copy.deepcopy(job.input_json)
        context['citations'][0]['symbol'] = '000001.SZ'
        job.input_json = context
        session.commit()
    research.run_research_job(accepted['job_id'])
    assert job_for(api, accepted)['error_code'] == 'RESEARCH_CONTEXT_INVALID'
    assert FixtureProvider.calls == []
    get_settings().task_mode = 'inline'
    class InvalidProvider(FixtureProvider):
        def generate_structured(self, *args, **kwargs):
            output = super().generate_structured(*args, **kwargs)
            content = json.loads(output['content'])
            content['claims'][0]['text'] = 'TEST ONLY invented replacement disclosure without support.'
            output['content'] = json.dumps(content)
            return output
    monkeypatch.setattr('app.services.llm_configs.create_provider', lambda *args, **kwargs: InvalidProvider())
    accepted = api.post('/api/v1/cn/research/jobs', headers=HEADERS, json=body_for(config)).json()
    assert job_for(api, accepted)['error_code'] == 'EVIDENCE_VALIDATION_FAILED'


def test_cn_freeze_uses_actual_retrieved_version_and_never_latest_provider_facts(prepared_cn, monkeypatch):
    api, config = prepared_cn
    original = documents.list_documents
    newer_ids = []
    def concurrently_updated(session, symbol, identifier):
        previous = original(session, symbol, identifier)
        newer = documents.import_pdf(session, symbol, identifier, generated_pdf([TEXT + ' TEST ONLY updated source.']),
                                     fetched_at=STAMP, source_kind='synthetic_fixture')
        session.flush()
        newer_ids.append(newer.id)
        return previous
    monkeypatch.setattr(documents, 'list_documents', concurrently_updated)
    created = api.post('/api/v1/cn/research/jobs', headers=HEADERS, json=body_for(config))
    assert created.status_code == 202, created.text
    with session_factory()() as session:
        context = session.get(CnResearchJob, created.json()['job_id']).input_json
        assert {version['id'] for version in context['document_versions']} == set(newer_ids)
        assert {row['document_id'] for row in context['citations']} == set(newer_ids)
        assert context['facts'] == []
        assert session.scalar(select(func.count()).select_from(CnDocumentChunk)) >= 4


def test_cn_submit_rejects_stale_context_without_creating_task(prepared_cn):
    from fastapi import BackgroundTasks

    from app.api.cn_research import CnResearchRequest
    from app.services.llm_configs import get_verified_config

    _api, config = prepared_cn
    with session_factory()() as session:
        company, context = research.freeze_context(session, SYMBOL, CnResearchRequest(**body_for(config)))
        context['prompt_version'] = 'TEST ONLY stale frozen context'
        checked = get_verified_config(session, 'local-owner', config['id'])
        with pytest.raises(AppError) as error:
            research.submit(session, BackgroundTasks(), owner='local-owner', company=company,
                            task_type='summary', context=context, config=checked)
        assert error.value.code == 'RESEARCH_CONTEXT_INVALID'
        assert session.scalar(select(func.count()).select_from(CnResearchJob)) == 0
