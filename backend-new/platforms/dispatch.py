"""平台分发器:根据 import 的 platform 字段,调对应平台的 pipeline。

worker 从 Redis 取出任务后,用它分发到 temu/1688/... 各自的 pipeline.execute。

新增平台时,只需在 PIPELINES 注册表加一行,无需改 worker 逻辑。
"""
from __future__ import annotations

from typing import Any, Callable

# platform → 执行函数 的注册表
# 每个 execute 签名: execute(env, user_id, import_id, store) -> None
PIPELINES: dict[str, Callable[..., None]] = {}


def register(platform: str, execute_fn: Callable[..., None]) -> None:
    """注册某平台的 pipeline 执行函数。"""
    PIPELINES[platform] = execute_fn


def get_pipeline(platform: str) -> Callable[..., None] | None:
    """按 platform 名取执行函数,未注册返回 None。"""
    return PIPELINES.get(platform)


def execute(env: dict[str, str], platform: str, user_id: int, import_id: int,
            store: Any) -> bool:
    """分发执行。返回 True=有对应平台,False=未注册平台。

    worker 调用入口:任务从队列取出 → 查 platform → 调对应 pipeline。
    """
    fn = get_pipeline(platform)
    if fn is None:
        store.update_status(
            user_id, import_id, "error",
            f"unknown platform: {platform}"
        )
        return False
    fn(env, user_id, import_id, store)
    return True


# ── 注册当前已实现的平台 ──
from platforms.temu import pipeline as temu_pipeline  # noqa: E402

register("temu", temu_pipeline.execute)
