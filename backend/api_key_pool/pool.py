"""API Key 池:Redis 三状态管理(可用/冷却/失效),带元数据。

数据结构(每个 provider 一组):
  apikeys:{p}:available   ZSET(score=上次使用时间戳)  可用 key 集合
  apikeys:{p}:cooling     ZSET(score=恢复时间戳)       冷却中
  apikeys:{p}:failed      ZSET(score=失效时间戳)       失效(401/403/5xx认证类)
  apikeys:{p}:meta        HASH(key=apikey -> JSON)     每个 key 的元数据:
                              status, added_at, last_used, fail_count,
                              fail_reason, fail_code, fail_at

设计要点:
  - 所有操作走 Redis,多 worker/多进程共享同一份池状态。
  - acquire 用"最久未使用"(LRU)取 key,均匀分摊负载。
  - 401/403/5xx认证类 → 直接 failed;429/超时 → 累计3次进 cooling,5分钟自动恢复。
  - meta 随每次状态变化更新,供管理面板表格展示(添加时间/失败次数/失败原因)。
  - provider 注册表 PROVIDERS 决定有哪些模型池,新增模型只改这里。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

import redis as redis_lib

from config import get_settings

logger = logging.getLogger("api_key_pool")

# provider → 业务名(面板显示用)。新增模型在这里加一行即可。
PROVIDERS = {
    "chat": "多模态模型",
    "vibe": "图片生成模型",
}

_DEFAULT_COOL_SECONDS = 300      # 冷却 5 分钟
_FAIL_THRESHOLD = 3              # 连续失败 N 次进冷却
# 认证类错误码:直接进失效板块
_AUTH_FAIL_CODES = {401, 403}
# 5xx 中属于"key 不可用"类的也直接失效(如 503 服务端明确拒绝该 key 时)
# 注意:真正的服务端临时故障(500/502/503)通常不应判 key 失效,这里保守只认 401/403。
# 如果你的某个 API 用 503 表示"key 无效",把 503 加入这个集合即可。
_HARD_FAIL_CODES = _AUTH_FAIL_CODES

_CLIENT: redis_lib.Redis | None = None
_LOCK = threading.Lock()


def _client() -> redis_lib.Redis:
    global _CLIENT
    if _CLIENT is None:
        with _LOCK:
            if _CLIENT is None:
                _CLIENT = redis_lib.from_url(
                    get_settings().redis_url,
                    decode_responses=True,
                    socket_timeout=10,
                    socket_connect_timeout=5,
                    health_check_interval=30,
                    retry_on_timeout=True,
                )
    return _CLIENT


def _k(provider: str, state: str) -> str:
    return f"apikeys:{provider}:{state}"


def _meta_key(provider: str) -> str:
    return f"apikeys:{provider}:meta"


def _mask(api_key: str) -> str:
    """脱敏:保留首4+末4,中间省略。短 key 原样返回。"""
    if len(api_key) <= 12:
        return api_key
    return api_key[:4] + "****" + api_key[-4:]


def _now_iso() -> str:
    import datetime
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class ApiKeyPool:
    """单个 provider 的 key 池。"""

    def __init__(self, provider: str):
        if provider not in PROVIDERS:
            raise ValueError(f"unknown provider: {provider}")
        self.provider = provider

    def _avail(self) -> str:
        return _k(self.provider, "available")

    def _cool(self) -> str:
        return _k(self.provider, "cooling")

    def _fail(self) -> str:
        return _k(self.provider, "failed")

    def _meta(self) -> str:
        return _meta_key(self.provider)

    # ── 元数据读写 ──
    def _get_meta(self, api_key: str) -> dict[str, Any]:
        raw = _client().hget(self._meta(), api_key)
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return {}

    def _set_meta(self, api_key: str, **fields) -> None:
        meta = self._get_meta(api_key)
        meta.update(fields)
        _client().hset(self._meta(), api_key, json.dumps(meta, ensure_ascii=False))

    # ── 取用 ──
    def acquire(self) -> str | None:
        """取一个可用 key(LRU),自动回收到期冷却。无可用返回 None。"""
        c = _client()
        avail = self._avail()
        for _attempt in range(8):
            try:
                now = time.time()
                # 1) 懒回收:到期的 cooling 回到 available
                recovered = c.zrangebyscore(self._cool(), "-inf", now)
                if recovered:
                    pipe = c.pipeline()
                    for k in recovered:
                        pipe.zadd(avail, {k: now})
                        pipe.zrem(self._cool(), k)
                    pipe.execute()
                    for k in recovered:
                        self._set_meta(k, status="available", last_used=_now_iso())
                # 2) WATCH 乐观锁取最久未使用
                with c.pipeline() as pipe:
                    pipe.watch(avail)
                    members = pipe.zrange(avail, 0, 0, withscores=True)
                    if not members:
                        pipe.unwatch()
                        return None
                    key = members[0][0]
                    pipe.multi()
                    pipe.zadd(avail, {key: now})
                    pipe.execute()
                self._set_meta(key, last_used=_now_iso())
                return key
            except redis_lib.exceptions.WatchError:
                continue
            except Exception as exc:
                logger.error("acquire failed provider=%s: %s", self.provider, exc)
                return None
        return None

    # ── 反馈 ──
    def mark_success(self, api_key: str) -> None:
        """调用成功:清零失败计数。"""
        try:
            self._set_meta(api_key, fail_count=0, fail_reason="", fail_code=None)
        except Exception as exc:
            logger.warning("mark_success failed: %s", exc)

    def mark_failed(self, api_key: str, status_code: int | None, error: str = "") -> str:
        """调用失败反馈。返回 key 进入的状态: 'failed' / 'cooling' / 'retry'。"""
        c = _client()
        reason = f"{status_code or ''} {error}".strip()
        try:
            # 认证类:直接失效
            if status_code in _HARD_FAIL_CODES:
                c.zadd(self._fail(), {api_key: time.time()})
                c.zrem(self._avail(), api_key)
                c.zrem(self._cool(), api_key)
                self._set_meta(api_key, status="failed", fail_reason=reason,
                               fail_code=status_code, fail_at=_now_iso())
                logger.warning("key 失效 provider=%s key=%s code=%s",
                               self.provider, _mask(api_key), status_code)
                return "failed"
            # 临时错误:累计失败次数
            meta = self._get_meta(api_key)
            n = int(meta.get("fail_count", 0)) + 1
            if n >= _FAIL_THRESHOLD:
                recover_at = time.time() + _DEFAULT_COOL_SECONDS
                c.zadd(self._cool(), {api_key: recover_at})
                c.zrem(self._avail(), api_key)
                self._set_meta(api_key, status="cooling", fail_count=n,
                               fail_reason=reason, fail_code=status_code, fail_at=_now_iso())
                logger.info("key 冷却 provider=%s key=%s count=%s",
                            self.provider, _mask(api_key), n)
                return "cooling"
            self._set_meta(api_key, fail_count=n, fail_reason=reason,
                           fail_code=status_code, fail_at=_now_iso())
            return "retry"
        except Exception as exc:
            logger.error("mark_failed error: %s", exc)
            return "cooling"

    # ── 管理(CRUD) ──
    def add(self, api_key: str) -> bool:
        """新增 key 到可用池。已存在(任意状态)则忽略。

        用 pipeline 保证 ZADD + HSET 原子提交:不会出现 key 进了 ZSET 但 meta 没写的情况。
        """
        c = _client()
        try:
            if (c.zscore(self._avail(), api_key) is not None
                    or c.zscore(self._cool(), api_key) is not None
                    or c.zscore(self._fail(), api_key) is not None):
                return False
            meta_json = json.dumps({
                "status": "available",
                "added_at": _now_iso(),
                "last_used": "-",
                "fail_count": 0,
                "fail_reason": "",
                "fail_code": None,
            }, ensure_ascii=False)
            pipe = c.pipeline()
            pipe.zadd(self._avail(), {api_key: time.time()})
            pipe.hset(self._meta(), api_key, meta_json)
            pipe.execute()
            logger.info("key 添加成功 provider=%s key=%s", self.provider, _mask(api_key))
            return True
        except Exception as exc:
            logger.error("add failed provider=%s key=%s: %s", self.provider, _mask(api_key), exc)
            return False

    def remove(self, api_key: str) -> bool:
        """从所有状态删除某 key(含元数据)。"""
        c = _client()
        try:
            removed = (c.zrem(self._avail(), api_key)
                       + c.zrem(self._cool(), api_key)
                       + c.zrem(self._fail(), api_key))
            c.hdel(self._meta(), api_key)
            return removed > 0
        except Exception as exc:
            logger.error("remove failed: %s", exc)
            return False

    def bulk_remove(self, api_keys: list[str]) -> int:
        n = 0
        for k in api_keys:
            if self.remove(k):
                n += 1
        return n

    def update(self, api_key: str, new_status: str = None) -> bool:
        """改状态(面板'改'用):手动移动 key 到指定状态。
        new_status: 'available' / 'failed' / 'cooling'。"""
        c = _client()
        try:
            existed = (c.zscore(self._avail(), api_key) is not None
                       or c.zscore(self._cool(), api_key) is not None
                       or c.zscore(self._fail(), api_key) is not None)
            if not existed:
                return False
            c.zrem(self._avail(), api_key)
            c.zrem(self._cool(), api_key)
            c.zrem(self._fail(), api_key)
            if new_status == "available":
                c.zadd(self._avail(), {api_key: time.time()})
                self._set_meta(api_key, status="available", fail_count=0,
                               fail_reason="", fail_code=None)
            elif new_status == "failed":
                c.zadd(self._fail(), {api_key: time.time()})
                self._set_meta(api_key, status="failed", fail_reason="手动标记失效",
                               fail_code=None, fail_at=_now_iso())
            elif new_status == "cooling":
                recover = time.time() + _DEFAULT_COOL_SECONDS
                c.zadd(self._cool(), {api_key: recover})
                self._set_meta(api_key, status="cooling", fail_reason="手动冷却",
                               fail_at=_now_iso())
            else:
                return False
            return True
        except Exception as exc:
            logger.error("update failed: %s", exc)
            return False

    # 别名:revive = update(available)
    def revive(self, api_key: str) -> bool:
        return self.update(api_key, "available")

    def clear_all(self) -> int:
        c = _client()
        n = 0
        try:
            for key in (self._avail(), self._cool(), self._fail()):
                n += c.zcard(key)
                c.delete(key)
            c.delete(self._meta())
            return n
        except Exception as exc:
            logger.error("clear_all failed: %s", exc)
            return n

    # ── 查询(面板用) ──
    def _row(self, api_key: str, default_status: str) -> dict[str, Any]:
        meta = self._get_meta(api_key)
        return {
            "key": _mask(api_key),
            "full_key": api_key,
            "status": meta.get("status", default_status),
            "added_at": meta.get("added_at", "-"),
            "last_used": meta.get("last_used", "-"),
            "fail_count": meta.get("fail_count", 0),
            "fail_reason": meta.get("fail_reason", ""),
            "fail_code": meta.get("fail_code"),
            "fail_at": meta.get("fail_at", "-"),
        }

    def list_normal(self) -> list[dict[str, Any]]:
        """正常 key(可用 + 冷却),按添加时间倒序。"""
        c = _client()
        out = []
        try:
            for k in c.zrange(self._avail(), 0, -1):
                out.append(self._row(k, "available"))
            for k in c.zrange(self._cool(), 0, -1):
                out.append(self._row(k, "cooling"))
        except Exception as exc:
            logger.error("list_normal failed: %s", exc)
        out.sort(key=lambda r: r.get("added_at", ""), reverse=True)
        return out

    def list_failed(self) -> list[dict[str, Any]]:
        """失效 key,按失效时间倒序。"""
        c = _client()
        out = []
        try:
            for k in c.zrange(self._fail(), 0, -1):
                out.append(self._row(k, "failed"))
        except Exception as exc:
            logger.error("list_failed failed: %s", exc)
        out.sort(key=lambda r: r.get("fail_at", ""), reverse=True)
        return out

    def snapshot(self) -> dict[str, Any]:
        """完整快照:正常列表 + 失效列表 + 计数(面板渲染用)。"""
        normal = self.list_normal()
        failed = self.list_failed()
        return {
            "provider": self.provider,
            "label": PROVIDERS[self.provider],
            "normal": normal,
            "failed": failed,
            "counts": {
                "normal": len(normal),
                "failed": len(failed),
                "available": sum(1 for r in normal if r["status"] == "available"),
                "cooling": sum(1 for r in normal if r["status"] == "cooling"),
            },
        }


_POOLS: dict[str, ApiKeyPool] = {}

# provider → .env 里对应的兜底 key 变量名
# 启动时若池子为空, 自动把这些 key 注入池子(防止 FLUSHDB/Redis 重启后 key 丢失)
_ENV_KEY_MAP = {
    "chat": "CHAT_API_KEY",
    "vibe": "VIBE_API_KEY",
}


def bootstrap_from_env() -> None:
    """启动时调用: 若某 provider 的池子为空, 自动从 .env 注入兜底 key。

    这样即使 Redis 被 FLUSHDB / 重启清空, 重启服务后 key 池会自动恢复,
    不会出现"面板加了 key 但丢了"的情况。
    已有 key 的池子不会被修改(只补空池)。
    """
    try:
        env = {}
        from pathlib import Path as _Path
        env_path = _Path(__file__).resolve().parent.parent / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip("'").strip('"')
        added = 0
        for provider, env_var in _ENV_KEY_MAP.items():
            pool = get_pool(provider)
            # 池子非空就跳过(不覆盖手动添加的 key)
            snap = pool.snapshot()
            if snap["counts"]["normal"] > 0 or snap["counts"]["failed"] > 0:
                continue
            key = env.get(env_var, "").strip()
            if key:
                if pool.add(key):
                    added += 1
                    logger.info("bootstrap: 从 .env 注入 %s 池子 key=%s", provider, _mask(key))
        if added:
            logger.info("bootstrap: 共从 .env 恢复 %d 个 key 到池子", added)
    except Exception as exc:
        logger.warning("bootstrap_from_env failed: %s", exc)


def get_pool(provider: str) -> ApiKeyPool:
    if provider not in _POOLS:
        _POOLS[provider] = ApiKeyPool(provider)
    return _POOLS[provider]


def all_snapshots() -> list[dict[str, Any]]:
    return [get_pool(p).snapshot() for p in PROVIDERS]
