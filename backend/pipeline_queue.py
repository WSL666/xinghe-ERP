"""Redis-backed pipeline queue with crash recovery and graceful shutdown.

enqueue_pipeline() pushes work onto a Redis list; start_workers() spins up
consumer threads that BRPOP and call the handler. The FastAPI process owns the
workers, so a task that is mid-flight when the process is killed is re-enqueued
on the next startup by main startup recovery (it scans the DB for non-terminal
imports). stop_workers() is wired to the app lifespan so reloads/restarts don't
leave zombie worker threads holding DB connections.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Callable

import redis as redis_lib

from config import get_settings

QUEUE_KEY = "pipeline:queue"

_redis_client: redis_lib.Redis | None = None
_stop_event = threading.Event()
_workers: list[threading.Thread] = []
_active_per_user: dict[int, int] = {}
_active_lock = threading.Lock()
_max_per_user = max(1, int(os.getenv("PIPELINE_MAX_PER_USER", "1")))

logger = logging.getLogger("pipeline.queue")


def _client() -> redis_lib.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis_lib.from_url(get_settings().redis_url, decode_responses=True)
    return _redis_client


def enqueue_pipeline(user_id: int, import_id: int) -> None:
    payload = json.dumps({"user_id": user_id, "import_id": import_id})
    _client().lpush(QUEUE_KEY, payload)


def active_count_for_user(user_id: int) -> int:
    """How many jobs for this user are currently being processed."""
    with _active_lock:
        return _active_per_user.get(user_id, 0)


def start_workers(handler: Callable[[int, int], None], count: int | None = None) -> None:
    _stop_event.clear()
    n = count or max(1, int(os.getenv("PIPELINE_CONCURRENCY", "2")))
    for i in range(n):
        t = threading.Thread(
            target=_worker_loop,
            args=(handler, i),
            daemon=True,
            name=f"pipeline-worker-{i}",
        )
        t.start()
        _workers.append(t)


def stop_workers(timeout: float = 5.0) -> None:
    """Signal worker threads to exit and wait briefly for them to drain."""
    _stop_event.set()
    for t in _workers:
        t.join(timeout=timeout)
    _workers.clear()


def _worker_loop(handler: Callable[[int, int], None], worker_id: int) -> None:
    client = _client()
    while not _stop_event.is_set():
        try:
            # BRPOP blocks up to 15s then returns None; the loop keeps the
            # worker responsive while idle without busy-spinning, and the
            # short timeout lets stop_workers() shut it down promptly.
            item = client.brpop(QUEUE_KEY, timeout=15)
        except Exception as exc:
            logger.warning("worker-%s brpop failed: %s; retrying", worker_id, exc)
            time.sleep(2)
            continue
        if not item:
            continue
        user_id = None
        import_id = None
        try:
            payload = json.loads(item[1])
            user_id = int(payload["user_id"])
            import_id = int(payload["import_id"])
            # Per-user fairness: if this user already has a job running, put
            # this one back at the head of the queue and let other users'
            # jobs go first. This stops one user flooding the queue from
            # monopolizing every worker while others wait.
            with _active_lock:
                running = _active_per_user.get(user_id, 0)
            if running >= _max_per_user:
                _client().rpush(QUEUE_KEY, item[1])
                time.sleep(0.2)
                continue
            with _active_lock:
                _active_per_user[user_id] = _active_per_user.get(user_id, 0) + 1
            logger.info("worker-%s start user=%s import=%s", worker_id, user_id, import_id)
            handler(user_id, import_id)
            logger.info("worker-%s done user=%s import=%s", worker_id, user_id, import_id)
        except Exception as exc:
            # A single bad job must never kill the worker thread, but it must
            # also never vanish silently: log full context so failures are
            # debuggable instead of stranding an import in "generating".
            logger.exception("worker-%s job failed user=%s import=%s: %s", worker_id, user_id, import_id, exc)
            if user_id is not None and import_id is not None:
                try:
                    from store import update_status
                    update_status(user_id, import_id, "error", f"pipeline worker crashed: {exc}")
                except Exception:
                    pass
        finally:
            if user_id is not None:
                with _active_lock:
                    n = _active_per_user.get(user_id, 0) - 1
                    if n <= 0:
                        _active_per_user.pop(user_id, None)
                    else:
                        _active_per_user[user_id] = n
    logger.info("worker-%s exiting (stop signaled)", worker_id)
