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

import atexit
import fcntl
import logging
import os
import signal
import sys
import time
import tempfile

import mq.redis_queue as pipeline_queue
from mq.orchestrator import worker_handler
from store import close_pool, init_db, list_resumable_imports, cleanup_stale_imports, db_conn, open_pool

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("worker")

# 全局只允许一个 worker 进程消费队列(PID 文件锁)。
# 防止运维事故(手动 python worker.py + systemd 各起一个)导致两个进程抢同一队列。
# PID 文件路径可通过环境变量 WORKER_PID_FILE 覆盖(默认放 /tmp, 普通用户可写)。
_DEFAULT_PID_DIR = os.path.join(tempfile.gettempdir(), "product-pipeline")
_PID_FILE = os.environ.get(
    "WORKER_PID_FILE",
    os.path.join(_DEFAULT_PID_DIR, "worker.pid"),
)
_pid_lock_fd = None


def _acquire_singleton_lock() -> bool:
    """尝试获取 worker 进程级单例锁(文件锁)。

    返回 True=拿到锁(全局唯一 worker),False=已有另一个 worker 在跑。
    锁是 advisory 的(fd 关闭即释放),进程崩溃后系统自动回收 fd,锁也自动释放。
    """
    global _pid_lock_fd
    os.makedirs(os.path.dirname(_PID_FILE), exist_ok=True)
    fd = open(_PID_FILE, "w")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (IOError, OSError):
        fd.close()
        return False
    fd.write(str(os.getpid()))
    fd.flush()
    _pid_lock_fd = fd
    atexit.register(_release_singleton_lock)
    return True


def _release_singleton_lock() -> None:
    global _pid_lock_fd
    if _pid_lock_fd is not None:
        try:
            fcntl.flock(_pid_lock_fd, fcntl.LOCK_UN)
            _pid_lock_fd.close()
        except Exception:
            pass
        _pid_lock_fd = None
        try:
            os.unlink(_PID_FILE)
        except Exception:
            pass


def main() -> None:
    # 单例锁:确保全局只有一个 worker 进程消费队列
    if not _acquire_singleton_lock():
        logger.error("=" * 60)
        logger.error("另一个 worker 进程已在运行(PID 文件锁被占用),本进程退出。")
        logger.error("如需重启: systemctl restart product-pipeline-worker.service")
        logger.error("=" * 60)
        sys.exit(1)

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

    # 崩溃恢复:先清空上次遗留的队列(可能含重复副本)+ active 计数,
    # 再把 DB 中未完成的任务干净地重新入队。避免每重启一次就多堆一批副本。
    try:
        pipeline_queue.reset_queue_and_active()

        # 清理脏数据: 老架构 status 残留 → 统一成 collected
        stale = cleanup_stale_imports()
        if stale:
            logger.info("crash recovery: normalized %d stale status rows → 'collected'", stale)

        # 清理卡在 generating 的残留: worker 刚启动说明上次的 generating 没跑完
        with db_conn() as conn:
            cur = conn.execute(
                "UPDATE imports SET status = 'error', status_msg = '处理中断，请重试', "
                "updated_at = now() WHERE status = 'generating'"
            )
            if cur.rowcount:
                logger.info("crash recovery: reset %d stuck 'generating' → 'error'", cur.rowcount)

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
