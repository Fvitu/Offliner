"""
Cross-platform RQ worker for Offliner.

Starts a worker that processes download jobs enqueued by the Flask app.
On Windows ``rq.SimpleWorker`` is used (no ``fork()`` required).

Usage (called automatically by services.py):
    python rq_worker.py [redis_url]
"""

from __future__ import annotations

import logging
import os
import platform
import sys
from logging.handlers import RotatingFileHandler

# Ensure the application root is on sys.path so that ``logic`` etc.
# can be imported by RQ when it deserialises job arguments.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)


def _setup_logging() -> None:
    """Configure file-based logging for the worker process."""
    log_dir = os.path.join(BASE_DIR, "logs")
    os.makedirs(log_dir, exist_ok=True)

    handler = RotatingFileHandler(
        os.path.join(log_dir, "worker.log"),
        maxBytes=5_242_880,  # 5 MB
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def main() -> None:
    _setup_logging()
    logger = logging.getLogger("rq_worker")

    redis_url = (
        sys.argv[1]
        if len(sys.argv) > 1
        else os.getenv("REDIS_URL", "redis://localhost:6379/0")
    )

    import redis
    from rq import Queue, SimpleWorker, Worker

    conn = redis.Redis.from_url(redis_url)
    queues = [Queue(connection=conn)]

    is_windows = platform.system() == "Windows"
    worker_cls = SimpleWorker if is_windows else Worker
    worker = worker_cls(queues, connection=conn)

    logger.info(
        "RQ worker ready (%s) — Redis at %s",
        worker_cls.__name__,
        redis_url,
    )

    worker.work(with_scheduler=False)


if __name__ == "__main__":
    main()
