"""Worker idle/reconnection regressions; real Redis tests use isolated DB15 and unique queues."""
import operator
import os
import subprocess
import sys
from pathlib import Path
from time import monotonic, sleep
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from redis import Redis
from redis.connection import UnixDomainSocketConnection
from redis.exceptions import TimeoutError as RedisTimeoutError
from rq import Queue, Worker
from rq.exceptions import DequeueTimeout, StopRequested
from rq.serializers import JSONSerializer

from app.workers.run import build_worker


def test_url_short_timeout_cannot_override_worker_blocking_timeout():
    worker = build_worker("redis://localhost:6379/15?socket_timeout=0.01", worker_ttl=16, prepare_for_work=False)
    assert worker.dequeue_timeout == 1
    assert worker.connection.connection_pool.connection_kwargs["socket_timeout"] == worker.connection_timeout == 11
    assert worker.connection.connection_pool.connection_kwargs["socket_keepalive"] is True
    assert worker.queues[0].connection is worker.connection


def test_unix_socket_pool_creates_connection_without_tcp_keepalive():
    worker = build_worker("unix:///tmp/finsight-worker.sock", worker_ttl=16, prepare_for_work=False)
    connection = worker.connection.connection_pool.make_connection()
    assert isinstance(connection, UnixDomainSocketConnection)
    assert connection.path == "/tmp/finsight-worker.sock"
    assert connection.socket_timeout == 11


def test_dequeue_timeout_reconnects_and_retries_but_signals_propagate(monkeypatch):
    worker = build_worker("redis://localhost:6379/15", worker_ttl=16, prepare_for_work=False)
    calls, delays = [], []
    def dequeue(self, timeout, max_idle_time=None):
        calls.append(timeout)
        if len(calls) == 1:
            raise RedisTimeoutError("private connection details must not be logged")
        return None
    monkeypatch.setattr(Worker, "dequeue_job_and_maintain_ttl", dequeue)
    monkeypatch.setattr("app.workers.run.time.sleep", delays.append)
    assert worker.dequeue_job_and_maintain_ttl(1) is None
    assert calls == [1, 1] and delays == [1]
    def stop(*args, **kwargs):
        raise StopRequested
    monkeypatch.setattr(Worker, "dequeue_job_and_maintain_ttl", stop)
    with pytest.raises(StopRequested):
        worker.dequeue_job_and_maintain_ttl(1)


@pytest.mark.skipif(not os.getenv("TEST_REDIS_URL") or sys.platform == "win32", reason="requires Linux and isolated real Redis DB15")
def test_previously_pinged_short_pool_is_not_reused_and_idle_dequeue_survives():
    url = os.environ["TEST_REDIS_URL"]
    if urlsplit(url).path != "/15":
        pytest.fail("TEST_REDIS_URL must use dedicated Redis DB15")
    original = Redis.from_url(url, socket_timeout=0.05, socket_connect_timeout=1)
    assert original.ping()
    original_connection = original.connection_pool.get_connection()
    assert original_connection.socket_timeout == 0.05
    original.connection_pool.release(original_connection)
    worker = build_worker(connection=original, worker_ttl=16, prepare_for_work=False)
    unique_queue = Queue("finsight-idle-test-" + uuid4().hex, connection=worker.connection)
    try:
        worker_socket = worker.connection.connection_pool.get_connection()
        assert worker_socket.socket_timeout == 11
        worker.connection.connection_pool.release(worker_socket)
        assert worker.connection.connection_pool is not original.connection_pool
        for _ in range(2):
            started = monotonic()
            with pytest.raises(DequeueTimeout):
                Queue.dequeue_any([unique_queue], 1, worker.connection)
            assert monotonic() - started >= 0.8
        assert worker.connection.ping()
        assert original.ping()  # Caller pool remains unchanged and usable.
        assert original.connection_pool.connection_kwargs["socket_timeout"] == 0.05
    finally:
        worker.connection.connection_pool.disconnect()
        original.connection_pool.disconnect()


@pytest.mark.skipif(not os.getenv("TEST_REDIS_URL") or sys.platform == "win32", reason="requires Linux fork worker and real Redis DB15")
def test_worker_process_consumes_job_after_multiple_empty_dequeues():
    url = os.environ["TEST_REDIS_URL"]
    if urlsplit(url).path != "/15":
        pytest.fail("TEST_REDIS_URL must use dedicated Redis DB15")
    connection = Redis.from_url(url, socket_timeout=2)
    queue_name = "finsight-worker-idle-test-" + uuid4().hex
    queue = Queue(queue_name, connection=connection, serializer=JSONSerializer)
    program = """
import os
from rq import Queue
from rq.serializers import JSONSerializer
from app.workers.run import build_worker, ResilientWorker
base = build_worker(os.environ['TEST_REDIS_URL'], worker_ttl=16, prepare_for_work=False)
queue = Queue(os.environ['IDLE_TEST_QUEUE'], connection=base.connection, serializer=JSONSerializer)
worker = ResilientWorker([queue], connection=base.connection, worker_ttl=16, serializer=JSONSerializer)
print('READY', flush=True)
worked = worker.work(max_jobs=1, max_idle_time=8, logging_level='WARNING')
raise SystemExit(0 if worked else 3)
"""
    environment = {**os.environ, "IDLE_TEST_QUEUE": queue_name}
    process = subprocess.Popen([sys.executable, "-c", program], cwd=Path(__file__).parents[1],
                               env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    job = None
    try:
        assert process.stdout.readline().strip() == "READY"
        sleep(2.2)  # More than two complete empty BLMOVE cycles (dequeue_timeout=1).
        assert process.poll() is None
        job = queue.enqueue(operator.add, 20, 22)
        stdout, stderr = process.communicate(timeout=12)
        assert process.returncode == 0, (stdout, stderr)
        assert job.return_value() == 42
        assert queue.count == 0
    finally:
        if process.poll() is None:
            process.terminate()
            process.communicate(timeout=5)
        if job:
            job.delete()
        queue.delete(delete_jobs=True)
        connection.connection_pool.disconnect()
