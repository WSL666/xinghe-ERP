"""独立 worker 进程入口:只消费 Redis 队列,不跑 HTTP。

为什么要独立:
  以前 worker 线程焊在 uvicorn web 进程里(main.py 的 lifespan)。
  问题:
    1. web 重启 = 在跑的任务全丢
    2. 想加并发 worker 只能多起 web 进程(--workers N),白白多复制整个 web
    3. 一台机器到顶了,没法把 worker 单独搬到别的机器

  拆开后:
    web 进程:   只接 HTTP / 入队(轻),重启不影响任务
    worker 进程:只 brpop 队列 + 跑 pipeline(本文件)
    两者通过 Redis 队列解耦,可以各自扩容、各自重启、甚至各在各的机器。

启动:
  python worker.py
  或 systemd: product-pipeline-worker.service

并发数:
  PIPELINE_CONCURRENCY 环境变量(每个 worker 进程的线程数,默认 2)
  PIPELINE_MAX_PER_USER 环境变量(每用户并发上限)
  生产建议: 1 个 worker 进程 + PIPELINE_CONCURRENCY=8~10
"""
from __future__ import annotations

import logging
import os
import signal
import sys
import time

import pipeline_queue
from orchestrator import worker_handler
from store import close_pool, init_db, list_resumable_imports, open_pool

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("worker")


def main() -> None:
    logger.info("=" * 60)
    logger.info("Product Pipeline Worker (standalone)")
    concurrency = max(1, int(os.getenv("PIPELINE_CONCURRENCY", "2")))
    max_per_user = max(1, int(os.getenv("PIPELINE_MAX_PER_USER", "1")))
    logger.info("  PIPELINE_CONCURRENCY = %s (worker 线程数)", concurrency)
    logger.info("  PIPELINE_MAX_PER_USER = %s (每用户并发上限)", max_per_user)
    logger.info("=" * 60)

    # 初始化 DB 池 + 表(和 web 进程一样的初始化)
    open_pool()
    init_db()

    # 崩溃恢复:把上次进程死亡时未完成的任务重新入队
    try:
        resumed = 0
        for row in list_resumable_imports():
            pipeline_queue.enqueue_pipeline(int(row["user_id"]), int(row["id"]))
            resumed += 1
        if resumed:
            logger.info("crash recovery: re-enqueued %d interrupted task(s)", resumed)
    except Exception:
        logger.exception("re-enqueue failed (redis down?)")

    # 启动 worker 线程,消费队列
    pipeline_queue.start_workers(worker_handler, count=concurrency)
    logger.info("worker started with %d thread(s), consuming Redis queue...", concurrency)

    # 主线程优雅退出:等 SIGTERM/SIGINT
    stop = False

    def _handle_signal(signum, _frame):
        nonlocal stop
        logger.info("received signal %s, shutting down...", signum)
        stop = True
        pipeline_queue.stop_workers()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    try:
        while not stop:
            time.sleep(1)
    finally:
        close_pool()
        logger.info("worker exited")


if __name__ == "__main__":
    main()
