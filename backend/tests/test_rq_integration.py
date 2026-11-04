"""Real Redis and Linux worker integration. Never substitutes fakeredis."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

import pytest
from redis import Redis
from rq import Queue
from rq.serializers import JSONSerializer

from app.core.config import get_settings
from tests.conftest import FIXTURES, TEST_TOKEN


@pytest.mark.skipif(not os.getenv("TEST_REDIS_URL") or sys.platform == "win32", reason="requires TEST_REDIS_URL and a real Linux RQ worker")
def test_real_queue_worker_and_snapshot_persistence(api, tmp_path, monkeypatch):
    url = os.environ["TEST_REDIS_URL"]
    if urlparse(url).path != "/15":
        pytest.fail("TEST_REDIS_URL must use isolated Redis database 15")
    connection = Redis.from_url(url, socket_timeout=2)
    assert connection.ping()
    queue = Queue("finsight", connection=connection, serializer=JSONSerializer)
    queue.empty()
    folder = tmp_path / "snapshots" / "0000320193"
    folder.mkdir(parents=True)
    for kind in ("submissions", "companyfacts"):
        shutil.copyfile(FIXTURES / f"aapl_{kind}.json", folder / f"{kind}.json")
    (folder / "manifest.json").write_text(json.dumps({
        "source_kind": "synthetic_fixture", "fetched_at": "2026-01-01T00:00:00Z",
    }))
    monkeypatch.setenv("TASK_MODE", "rq")
    monkeypatch.setenv("REDIS_URL", url)
    monkeypatch.setenv("SNAPSHOT_DIRECTORY", str(folder.parent))
    get_settings.cache_clear()
    response = api.post("/api/v1/companies/320193/sync", json={"force": True}, headers={"X-Admin-Token": TEST_TOKEN})
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    repeated = api.post("/api/v1/companies/320193/sync", headers={"X-Admin-Token": TEST_TOKEN})
    assert repeated.json()["reused"] is True
    assert repeated.json()["job_id"] == job_id
    assert queue.count == 1
    completed = subprocess.run(
        [sys.executable, "-m", "app.workers.run", "--burst"],
        cwd=Path(__file__).parents[1], env=os.environ.copy(), capture_output=True, text=True, timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    result = api.get(f"/api/v1/sync/jobs/{job_id}").json()
    assert result["status"] == "succeeded", result
    assert result["attempts"] == 1
    metrics = api.get("/api/v1/companies/320193/metrics").json()
    assert metrics["source_kind"] == "synthetic_fixture"
    assert any(m["value"] is not None for m in metrics["items"])
    assert queue.count == 0
