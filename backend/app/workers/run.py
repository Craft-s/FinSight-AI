import argparse
import time

from redis import ConnectionPool, Redis
from redis.exceptions import TimeoutError as RedisTimeoutError
from rq import Queue, Worker
from rq.defaults import DEFAULT_WORKER_TTL
from rq.serializers import JSONSerializer

from app.core.config import get_settings


class ResilientWorker(Worker):
    """A transient blocking-read timeout must not leave the queue without a worker."""
    def dequeue_job_and_maintain_ttl(self, timeout, max_idle_time=None):
        while True:
            try:
                return super().dequeue_job_and_maintain_ttl(timeout, max_idle_time)
            except RedisTimeoutError:
                # Only retry dequeuing: never replay a model or SEC task. Redis-py
                # disconnects the failed socket; retire other idle sockets as well.
                # In-use pubsub sockets and StopRequested/signals are not swallowed.
                self.log.warning("Redis dequeue read timed out; reconnecting and continuing to wait.")
                self.connection.connection_pool.disconnect(inuse_connections=False)
                time.sleep(1)


def build_worker(redis_url=None, *, worker_ttl=DEFAULT_WORKER_TTL, connection=None, prepare_for_work=True):
    if worker_ttl < 16:
        raise ValueError("Worker TTL must be at least 16 seconds")
    source = connection or Redis.from_url(redis_url or get_settings().redis_url)
    pool = source.connection_pool
    # URL parameters override Redis.from_url keyword arguments. Clone the parsed
    # pool and set policy after URL parsing, before ANY connection is instantiated.
    # RQ only mutates pool kwargs; a previously PINGed connection otherwise keeps
    # its old short timeout. Do not mutate or close a caller's/shared API pool.
    options = {**pool.connection_kwargs, "socket_timeout": max(1, worker_ttl - 15) + 10,
               "socket_connect_timeout": 5, "socket_keepalive": True}
    worker_connection = Redis(connection_pool=ConnectionPool(connection_class=pool.connection_class,
                                                            max_connections=pool.max_connections, **options))
    queue = Queue("finsight", connection=worker_connection, serializer=JSONSerializer)
    return ResilientWorker([queue], connection=worker_connection, worker_ttl=worker_ttl,
                           serializer=JSONSerializer, prepare_for_work=prepare_for_work)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--burst", action="store_true")
    args = parser.parse_args()
    build_worker().work(burst=args.burst)


if __name__ == "__main__":
    main()
