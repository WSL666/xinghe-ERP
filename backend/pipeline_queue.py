"""Redis 队列 + worker 线程(平台无关)。

worker 取出任务后,不直接跑业务,而是调 platforms.dispatch.execute() 分发
到对应平台。这样队列层永远不用改,加平台只动 platforms/。

设计要点(相对旧版本):
  - 并发计数从旧的"进程内存 dict"搬到 Redis 原子计数 + TTL,
    多 worker 进程共享,进程崩溃/重启不再泄漏计数(不再出现"任务反复回队、
    永不执行"的僵尸现象)。
  - "检查并发上限 + 占座"用 Lua 脚本原子完成,消除 check-then-incr 竞态。
  - 取出任务 → 计数+1 → 执行 → finally 计数-1,任何路径都保证成对释放。
  - brpop 失败时重试并尝试重建连接,不让单个 worker 永久退出。
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
# 每用户并发占用计数 key: pipeline:active:{user_id} = 当前正在运行的任务数
_ACTIVE_KEY = "pipeline:active:{user_id}"
# 计数 TTL: 防止进程崩溃后计数永久残留。一个任务正常最长 ~15 分钟(PIPELINE_TOTAL_TIMEOUT),
# 留足余量设 1 小时;只要还有任务在跑会持续 RENEXPIRE 续期,不会误删活跃计数。
_ACTIVE_TTL_SECONDS = 3600

# 重建 client 的锁(避免多线程并发重建)
_redis_client: redis_lib.Redis | None = None
_redis_lock = threading.Lock()
_stop_event = threading.Event()
_workers: list[threading.Thread] = []
_max_per_user = max(1, int(os.getenv("PIPELINE_MAX_PER_USER", "1")))

logger = logging.getLogger("pipeline.queue")

# Lua: 原子"检查并发上限并占座"。
# KEYS[1] = pipeline:active:{user_id}
# ARGV[1] = max_per_user, ARGV[2] = ttl_seconds
# 返回 1 = 占座成功(已 +1), 0 = 已达上限(未改动)
_ACQUIRE_SCRIPT = """
local cur = tonumber(redis.call('GET', KEYS[1]) or '0')
if cur >= tonumber(ARGV[1]) then
  return 0
end
local n = redis.call('INCR', KEYS[1])
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]))
return 1
"""

# Lua: 原子"释放一座"(DECR, 但不会降到负数)。
# KEYS[1] = pipeline:active:{user_id}
# 返回释放后的值(>=0)
_RELEASE_SCRIPT = """
local cur = tonumber(redis.call('GET', KEYS[1]) or '0')
if cur <= 1 then
  redis.call('DEL', KEYS[1])
  return 0
end
return redis.call('DECR', KEYS[1])
"""

_acquire_sha: str | None = None
_release_sha: str | None = None


def _build_client() -> redis_lib.Redis:
    return redis_lib.from_url(
        get_settings().redis_url,
        decode_responses=True,
        socket_timeout=30,
        socket_connect_timeout=5,
        health_check_interval=30,
        retry_on_timeout=True,
    )


def _client() -> redis_lib.Redis:
    """获取(并惰性创建)进程级 Redis 客户端。"""
    global _redis_client
    if _redis_client is None:
        with _redis_lock:
            if _redis_client is None:
                _redis_client = _build_client()
    return _redis_client


def _reset_client() -> None:
    """丢弃当前 client,下次 _client() 会重建。用于连接异常后恢复。"""
    global _redis_client, _acquire_sha, _release_sha
    with _redis_lock:
        if _redis_client is not None:
            try:
                _redis_client.close()
            except Exception:
                pass
        _redis_client = None
        # client 变了,缓存的 EVALSHA 也失效
        _acquire_sha = None
        _release_sha = None


def _eval(client: redis_lib.Redis, script: str, sha_var: str, keys: list[str], args: list) -> tuple[object, str]:
    """EVALSHA 优先,失败(如脚本被 FLUSH/NOSCRIPT)回退到 EVAL,并返回可复用的 sha。"""
    sha_holder = {"_acquire_sha": _acquire_sha, "_release_sha": _release_sha}
    # 调用方传 sha_var 决定用哪个缓存槽
    cached_sha = _acquire_sha if sha_var == "_acquire_sha" else _release_sha
    try:
        if cached_sha:
            return client.evalsha(cached_sha, len(keys), *keys, *args), cached_sha
    except redis_lib.exceptions.NoScriptError:
        pass
    sha = client.script_load(script)
    return client.evalsha(sha, len(keys), *keys, *args), sha


def _acquire_slot(user_id: int) -> bool:
    """原子占座:成功返回 True,已达上限返回 False。"""
    global _acquire_sha
    client = _client()
    key = _ACTIVE_KEY.format(user_id=user_id)
    result, sha = _eval(client, _ACQUIRE_SCRIPT, "_acquire_sha", [key], [_max_per_user, _ACTIVE_TTL_SECONDS])
    _acquire_sha = sha
    return bool(int(result))


def _release_slot(user_id: int) -> None:
    """释放一座(幂等,不会降到负数)。任何异常都吞掉,避免释放失败影响主流程。"""
    global _release_sha
    try:
        client = _client()
        key = _ACTIVE_KEY.format(user_id=user_id)
        _, sha = _eval(client, _RELEASE_SCRIPT, "_release_sha", [key], [])
        _release_sha = sha
    except Exception as exc:
        logger.warning("release_slot failed user=%s: %s (TTL will reclaim)", user_id, exc)


def enqueue_pipeline(user_id: int, import_id: int) -> None:
    payload = json.dumps({"user_id": user_id, "import_id": import_id})
    try:
        _client().lpush(QUEUE_KEY, payload)
    except Exception as exc:
        logger.error("enqueue failed user=%s import=%s: %s", user_id, import_id, exc)
        raise


def active_count_for_user(user_id: int) -> int:
    """查询某用户当前正在运行的任务数(读 Redis,多进程一致)。"""
    try:
        val = _client().get(_ACTIVE_KEY.format(user_id=user_id))
        return int(val) if val else 0
    except Exception:
        return 0


def start_workers(handler: Callable[[int, int], None], count: int | None = None) -> None:
    _stop_event.clear()
    n = count or max(1, int(os.getenv("PIPELINE_CONCURRENCY", "2")))
    for i in range(n):
        t = threading.Thread(target=_worker_loop, args=(handler, i), daemon=True, name=f"pipeline-worker-{i}")
        t.start()
        _workers.append(t)


def stop_workers(timeout: float = 5.0) -> None:
    _stop_event.set()
    for t in _workers:
        t.join(timeout=timeout)
    _workers.clear()


def _worker_loop(handler: Callable[[int, int], None], worker_id: int) -> None:
    while not _stop_event.is_set():
        client = _client()
        try:
            item = client.brpop(QUEUE_KEY, timeout=15)
        except Exception as exc:
            logger.warning("worker-%s brpop failed: %s; resetting connection", worker_id, exc)
            _reset_client()
            time.sleep(2)
            continue
        if not item:
            continue

        raw_payload = item[1]
        user_id: int | None = None
        import_id: int | None = None
        acquired = False
        try:
            payload = json.loads(raw_payload)
            user_id = int(payload["user_id"])
            import_id = int(payload["import_id"])

            if not _acquire_slot(user_id):
                # 该用户已达并发上限,原样塞回队尾,短暂退避避免空转
                try:
                    _client().rpush(QUEUE_KEY, raw_payload)
                except Exception:
                    # 回队失败:重试一次(重建连接)
                    _reset_client()
                    try:
                        _client().rpush(QUEUE_KEY, raw_payload)
                    except Exception as exc:
                        logger.error("worker-%s requeue failed user=%s import=%s: %s",
                                     worker_id, user_id, import_id, exc)
                time.sleep(0.2)
                continue

            acquired = True
            logger.info("worker-%s start user=%s import=%s", worker_id, user_id, import_id)
            handler(user_id, import_id)
            logger.info("worker-%s done user=%s import=%s", worker_id, user_id, import_id)
        except Exception as exc:
            logger.exception("worker-%s job failed user=%s import=%s: %s", worker_id, user_id, import_id, exc)
            if user_id is not None and import_id is not None:
                try:
                    from store import update_status
                    update_status(user_id, import_id, "error", f"pipeline worker crashed: {exc}")
                except Exception:
                    pass
        finally:
            if acquired and user_id is not None:
                _release_slot(user_id)
    logger.info("worker-%s exiting (stop signaled)", worker_id)
