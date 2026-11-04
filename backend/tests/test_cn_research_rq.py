"""Real PG/Redis/Linux RQ over TEST ONLY PDFs and a loopback protocol responder.

The HTTP server is not an Ollama model. No synthetic report enters production.
"""
import hashlib
import json
import os
import subprocess
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import pytest
from redis import Redis
from rq import Queue
from rq.serializers import JSONSerializer
from sqlalchemy import select

from app.core.config import get_settings
from app.db.cn_document_models import CnDocument
from app.db.session import session_factory
from app.services import llm_configs
from app.services.llm.factory import create_provider
from tests.test_cn_research_api import (
    HEADERS,
    SYMBOL,
    body_for,
)
from tests.test_cn_research_api import (
    prepared_cn as prepared_cn,
)
from tests.test_research_rq import ResearchHTTPFixture


class CnResearchHTTPFixture(ResearchHTTPFixture):
    """TEST ONLY: return exact quotations for every selected filing."""
    def do_POST(self):
        if self.path != '/api/chat':
            self.send_error(404)
            return
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if body.get('format') == 'json':
            payload = json.loads(body['messages'][-1]['content'])
            by_filing = {}
            for row in payload['citations']:
                by_filing.setdefault(row['filing_id'], row)
            content = json.dumps({
                'claims': [{'text': row['excerpt'][:600], 'kind': 'disclosed_fact',
                            'citation_ids': [row['citation_id']], 'metric_refs': [], 'reported_values': []}
                           for row in by_filing.values()],
                'uncertainties': ['This response is an explicit synthetic protocol fixture.'],
                'insufficient_evidence': False,
            })
        else:
            content = 'OK'
        raw = json.dumps({'model': 'cn-test-protocol-fixture',
                          'message': {'role': 'assistant', 'content': content},
                          'done': True, 'done_reason': 'stop',
                          'prompt_eval_count': 7, 'eval_count': 5}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@pytest.mark.skipif(
    not os.getenv('TEST_REDIS_URL') or not os.getenv('TEST_DATABASE_URL', '').startswith('postgresql')
    or sys.platform == 'win32', reason='requires dedicated PostgreSQL test DB, Redis DB15 and Linux RQ',
)
def test_cn_real_research_worker_three_tasks_pages_history_and_export(prepared_cn, monkeypatch):
    api, _config = prepared_cn
    redis_url = os.environ['TEST_REDIS_URL']
    if urlparse(redis_url).path != '/15':
        pytest.fail('CN research integration requires isolated Redis DB15')
    if '_test' not in os.environ['TEST_DATABASE_URL'].rsplit('/', 1)[-1]:
        pytest.fail('CN research integration requires an isolated test database')
    queue = Queue('finsight', connection=Redis.from_url(redis_url), serializer=JSONSerializer)
    queue.empty()
    with session_factory()() as session:
        expected_hashes = {row.filing_id: hashlib.sha256(row.raw_bytes).hexdigest()
                           for row in session.scalars(select(CnDocument).where(CnDocument.symbol == SYMBOL))}
    server = ThreadingHTTPServer(('127.0.0.1', 0), CnResearchHTTPFixture)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        # Restore the actual provider factory; only the upstream HTTP server is fake.
        monkeypatch.setattr(llm_configs, 'create_provider', create_provider)
        monkeypatch.setenv('OLLAMA_BASE_URL', f'http://127.0.0.1:{server.server_port}')
        monkeypatch.setenv('TASK_MODE', 'rq')
        monkeypatch.setenv('REDIS_URL', redis_url)
        get_settings.cache_clear()
        saved = api.post('/api/v1/llm/configs', headers=HEADERS,
                         json={'provider': 'ollama', 'model_id': 'cn-test-protocol-fixture'})
        assert saved.status_code == 200, saved.text
        config = saved.json()
        tested = api.post(f"/api/v1/llm/configs/{config['id']}/test", headers=HEADERS,
                          json={'version': config['version']})
        assert tested.status_code == 200 and tested.json()['config']['is_verified']
        requests, identifiers = [], []
        for task in ('summary', 'comparison', 'question'):
            body = body_for(config, task)
            response = api.post('/api/v1/cn/research/jobs', headers=HEADERS, json=body)
            assert response.status_code == 202, response.text
            identifier = response.json()['job_id']
            identifiers.append(identifier)
            requests.append(body)
            queued = queue.fetch_job(identifier)
            assert list(queued.args) == [identifier]
            assert b'cn-test-protocol-fixture' not in queued.data
            assert b'TEST ONLY synthetic' not in queued.data
        repeated = api.post('/api/v1/cn/research/jobs', headers=HEADERS, json=requests[0]).json()
        assert repeated['reused'] and repeated['job_id'] == identifiers[0]
        assert queue.count == 3
        finished = subprocess.run([sys.executable, '-m', 'app.workers.run', '--burst'],
            cwd=Path(__file__).parents[1], env=os.environ.copy(), capture_output=True, text=True, timeout=60)
        assert finished.returncode == 0, finished.stderr
        for identifier, body in zip(identifiers, requests, strict=True):
            job = api.get('/api/v1/cn/research/jobs/' + identifier, headers=HEADERS).json()
            assert job['status'] == 'succeeded' and job['report_id'], job
            response = api.get('/api/v1/cn/research/reports/' + job['report_id'], headers=HEADERS)
            assert response.status_code == 200, response.text
            report = response.json()
            assert report['market'] == 'CN' and report['symbol'] == SYMBOL
            assert report['source_kind'] == 'synthetic_fixture' and report['provider'] == 'ollama'
            assert report['content']['facts'] == [] and report['usage']['total_tokens'] == 12
            citations = report['content']['citations']
            assert {row['filing_id'] for row in citations} == set(body['filing_ids'])
            assert all(row['location']['page_start'] == 1 and len(row['document_sha256']) == 64
                       and 'accession' not in row for row in citations)
            assert all(row['document_sha256'] == expected_hashes[row['filing_id']]
                       for row in citations)
            for claim in report['content']['claims']:
                for citation_id in claim['citation_ids']:
                    assert claim['text'] in next(row['excerpt'] for row in citations if row['citation_id'] == citation_id)
            markdown = api.get('/api/v1/cn/research/reports/' + job['report_id'] + '/download', headers=HEADERS)
            assert markdown.status_code == 200 and '#page=1' in markdown.text
            assert 'static.cninfo.com.cn' in markdown.text and 'semantic support' in markdown.text
            assert api.get('/api/v1/cn/research/reports/' + job['report_id']).status_code == 401
        assert api.get('/api/v1/cn/research/history', headers=HEADERS).json()['total'] == 3
        assert queue.count == 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
