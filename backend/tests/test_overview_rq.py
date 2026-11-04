"""Real PostgreSQL/Redis/RQ + actual HTTP adapter, against an explicit TEST-ONLY server.

This verifies orchestration and protocol, not an actual Ollama model inference.
"""
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

from app.core.config import get_settings
from tests.conftest import TEST_TOKEN
from tests.test_overview import model_json


class FixtureOllamaHandler(BaseHTTPRequestHandler):
    """Synthetic protocol response, reachable only within the isolated test run."""
    def do_POST(self):
        if self.path != "/api/chat":
            self.send_error(404)
            return
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if body.get("format") == "json":
            facts = json.loads(body["messages"][-1]["content"])["facts"]
            fact = next(row for row in facts if row["value"] is not None)
            content = json.dumps(model_json({"facts": [fact]}))
        else:
            content = "OK"
        raw = json.dumps({"model": "test-fixture-only", "message": {"role": "assistant", "content": content},
                          "done": True, "done_reason": "stop", "prompt_eval_count": 3, "eval_count": 3}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, _format, *args):
        pass


@pytest.mark.skipif(not os.getenv("TEST_REDIS_URL") or sys.platform == "win32", reason="requires dedicated Redis DB15 and Linux RQ worker")
def test_real_overview_queue_with_explicit_http_fixture(seeded, monkeypatch):
    url = os.environ["TEST_REDIS_URL"]
    if urlparse(url).path != "/15":
        pytest.fail("TEST_REDIS_URL must use isolated Redis DB15")
    queue = Queue("finsight", connection=Redis.from_url(url), serializer=JSONSerializer)
    queue.empty()
    server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureOllamaHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv("OLLAMA_BASE_URL", f"http://127.0.0.1:{server.server_port}")
        monkeypatch.setenv("TASK_MODE", "rq")
        monkeypatch.setenv("REDIS_URL", url)
        get_settings.cache_clear()
        headers = {"X-Admin-Token": TEST_TOKEN}
        saved = seeded.post("/api/v1/llm/configs", headers=headers, json={"provider": "ollama", "model_id": "test-fixture-only"})
        assert saved.status_code == 200, saved.text
        config = saved.json()
        tested = seeded.post(f"/api/v1/llm/configs/{config['id']}/test", headers=headers, json={"version": config["version"]})
        assert tested.status_code == 200, tested.text
        assert tested.json()["config"]["is_verified"] is True
        activated = seeded.post(f"/api/v1/llm/configs/{config['id']}/activate", headers=headers, json={"version": config["version"]})
        assert activated.status_code == 200 and activated.json()["is_active"] is True
        body = {"config_id": config["id"], "language": "en-US"}
        submitted = seeded.post("/api/v1/companies/320193/overview", headers=headers, json=body)
        assert submitted.status_code == 202, submitted.text
        job_id = submitted.json()["job_id"]
        reused = seeded.post("/api/v1/companies/320193/overview", headers=headers, json=body).json()
        assert reused["reused"] is True and reused["job_id"] == job_id
        assert queue.count == 1
        payload = queue.fetch_job(job_id)
        assert list(payload.args) == [job_id]
        assert b"test-fixture-only" not in payload.data  # Queue carries only an ID, not context or credentials.
        completed = subprocess.run([sys.executable, "-m", "app.workers.run", "--burst"],
                                   cwd=Path(__file__).parents[1], env=os.environ.copy(),
                                   capture_output=True, text=True, timeout=60)
        assert completed.returncode == 0, completed.stderr
        result = seeded.get(f"/api/v1/overview/jobs/{job_id}", headers=headers).json()
        assert result["status"] == "succeeded", result
        report = result["result"]
        assert report["source_kind"] == "synthetic_fixture"
        assert report["provider"] == "ollama"
        assert report["usage"]["total_tokens"] == 6
        assert report["facts"] and report["observations"]
        assert queue.count == 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
