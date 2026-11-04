"""Real PG/Redis/Linux worker orchestration, using explicitly synthetic HTTP responses.

The loopback server is only a protocol fixture, NOT an actual Ollama model. Neither
the HTML nor the generated response enters the production snapshot database.
"""
import hashlib
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import pytest
from redis import Redis
from rq import Queue
from rq.serializers import JSONSerializer
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import Company, Filing, now
from app.db.session import session_factory
from app.services.documents.service import import_document
from tests.conftest import TEST_TOKEN

SYNTHETIC_HTML = b"""<!doctype html><html><head><title>SYNTHETIC TEST FILING</title></head>
<body><h1>Item 7. Management discussion - SYNTHETIC TEST ONLY</h1>
<p>Revenue and cash flow in this synthetic test filing may be affected by uncertain demand.
This is an artificial protocol fixture and does not represent any actual SEC financial statement.</p>
<h2>Item 1A. Risk factors</h2><p>Supply chain disruption could affect operations in this synthetic example.</p>
<script>hidden-test-marker-not-evidence</script></body></html>"""


class ResearchHTTPFixture(BaseHTTPRequestHandler):
    """A test-only local HTTP responder, not a selectable production provider."""
    def do_POST(self):
        if self.path != "/api/chat":
            self.send_error(404)
            return
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if body.get("format") == "json":
            prompt = json.loads(body["messages"][-1]["content"])
            citation = prompt["citations"][0]
            # Exact extraction exercises real model serialization without making
            # a false claim about generative-model quality or actual inference.
            quote = citation["excerpt"][:600]
            content = json.dumps({
                "claims": [{"text": quote, "kind": "disclosed_fact", "citation_ids": [citation["citation_id"]],
                            "metric_refs": [], "reported_values": []}],
                "uncertainties": ["This response is an explicit synthetic protocol fixture."],
                "insufficient_evidence": False,
            })
        else:
            content = "OK"
        raw = json.dumps({"model": "research-test-fixture", "message": {"role": "assistant", "content": content},
                          "done": True, "done_reason": "stop", "prompt_eval_count": 7, "eval_count": 5}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, _format, *args):
        pass


@pytest.mark.skipif(
    not os.getenv("TEST_REDIS_URL") or not os.getenv("TEST_DATABASE_URL", "").startswith("postgresql") or sys.platform == "win32",
    reason="requires isolated PostgreSQL test DB, Redis DB15 and a real Linux RQ worker",
)
def test_real_research_worker_report_history_and_markdown(seeded, monkeypatch):
    url = os.environ["TEST_REDIS_URL"]
    if urlparse(url).path != "/15":
        pytest.fail("TEST_REDIS_URL must use isolated Redis DB15")
    database = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[-1]
    if "_test" not in database:
        pytest.fail("Research integration requires a dedicated PostgreSQL '_test' database")
    queue = Queue("finsight", connection=Redis.from_url(url), serializer=JSONSerializer)
    queue.empty()
    with session_factory()() as session:
        filing = session.scalar(select(Filing).join(Company, Company.id == Filing.company_id).where(
            Company.cik == "0000320193", Filing.form_type == "10-K",
        ).order_by(Filing.filing_date.desc()).limit(1))
        assert filing is not None
        document = import_document(session, filing, SYNTHETIC_HTML, filing.source_url, now(),
                                   "synthetic_fixture", {"test_fixture": True})
        session.commit()
        filing_id, source_url, document_id = filing.id, filing.source_url, document.id
    server = ThreadingHTTPServer(("127.0.0.1", 0), ResearchHTTPFixture)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv("OLLAMA_BASE_URL", f"http://127.0.0.1:{server.server_port}")
        monkeypatch.setenv("TASK_MODE", "rq")
        monkeypatch.setenv("REDIS_URL", url)
        get_settings.cache_clear()
        headers = {"X-Admin-Token": TEST_TOKEN}
        saved = seeded.post("/api/v1/llm/configs", headers=headers,
                            json={"provider": "ollama", "model_id": "research-test-fixture"})
        assert saved.status_code == 200, saved.text
        config = saved.json()
        tested = seeded.post(f"/api/v1/llm/configs/{config['id']}/test", headers=headers,
                             json={"version": config["version"]})
        assert tested.status_code == 200, tested.text
        assert tested.json()["config"]["is_verified"] is True
        activated = seeded.post(f"/api/v1/llm/configs/{config['id']}/activate", headers=headers,
                                json={"version": config["version"]})
        assert activated.status_code == 200 and activated.json()["is_active"] is True
        evidence = seeded.get("/api/v1/companies/320193/evidence", params={"q": "收入 风险", "filing_id": filing_id})
        assert evidence.status_code == 200, evidence.text
        assert evidence.json()["retrieval_mode"] == "postgres_fts"
        assert evidence.json()["items"]
        request = {"cik": "0000320193", "task_type": "summary", "filing_ids": [filing_id],
                   "config_id": config["id"], "language": "en-US"}
        submitted = seeded.post("/api/v1/research/jobs", headers=headers, json=request)
        assert submitted.status_code == 202, submitted.text
        job_id = submitted.json()["job_id"]
        repeated = seeded.post("/api/v1/research/jobs", headers=headers, json=request)
        assert repeated.status_code == 202, repeated.text
        assert repeated.json()["reused"] is True and repeated.json()["job_id"] == job_id
        assert queue.count == 1
        payload = queue.fetch_job(job_id)
        assert list(payload.args) == [job_id]
        assert b"research-test-fixture" not in payload.data
        assert b"SYNTHETIC TEST" not in payload.data
        completed = subprocess.run([sys.executable, "-m", "app.workers.run", "--burst"],
                                   cwd=Path(__file__).parents[1], env=os.environ.copy(),
                                   capture_output=True, text=True, timeout=60)
        assert completed.returncode == 0, completed.stderr
        job = seeded.get(f"/api/v1/research/jobs/{job_id}", headers=headers).json()
        assert job["status"] == "succeeded", job
        assert job["report_id"]
        report_id = job["report_id"]
        response = seeded.get(f"/api/v1/research/reports/{report_id}", headers=headers)
        assert response.status_code == 200, response.text
        report = response.json()
        assert report["source_kind"] == "synthetic_fixture"
        assert report["provider"] == "ollama" and report["usage"]["total_tokens"] == 12
        citation = report["content"]["citations"][0]
        assert citation["source_url"] == source_url
        assert citation["document_id"] == document_id
        assert citation["document_sha256"] == hashlib.sha256(SYNTHETIC_HTML).hexdigest()
        assert isinstance(citation["location"], dict)
        assert report["content"]["claims"][0]["text"] in citation["excerpt"]
        assert report["content"]["claims"][0]["evidence_check"] == "extractive_match"
        assert "hidden-test-marker-not-evidence" not in json.dumps(report)
        markdown = seeded.get(f"/api/v1/research/reports/{report_id}/download", headers=headers)
        assert markdown.status_code == 200 and markdown.headers["content-type"].startswith("text/markdown")
        assert "attachment" in markdown.headers["content-disposition"]
        assert hashlib.sha256(SYNTHETIC_HTML).hexdigest() in markdown.text
        assert source_url in markdown.text
        assert "semantic support" in markdown.text
        history = seeded.get("/api/v1/research/history", headers=headers).json()
        assert history["total"] == 1 and history["items"][0]["report_id"] == report_id
        cached = seeded.post("/api/v1/research/jobs", headers=headers, json=request).json()
        assert cached["job_id"] == job_id and cached["reused"] is True
        assert seeded.get(f"/api/v1/research/reports/{report_id}").status_code == 401
        assert seeded.get(f"/api/v1/research/reports/{report_id}/download").status_code == 401
        assert queue.count == 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
