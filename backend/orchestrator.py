"""流水线编排入口:入队 + worker handler 分发。

这是队列和平台 pipeline 之间的薄层:
  run_auto_pipeline()  →  入队(平台无关)
  _worker_handler()    →  worker 取出任务后,按 platform 分发

平台逻辑都在 platforms/*/pipeline.py,这里只管调度。
"""
from __future__ import annotations

from typing import Any

import pipeline_queue
from config import ENV_PATH, get_settings
from core.base import load_env, log
from store import get_import, update_status


_env_cache: dict[str, str] = {}

def _load_env() -> dict[str, str]:
    if not _env_cache:
        _env_cache.update(load_env(ENV_PATH))
    return _env_cache


def _one_line_error(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.split("\nTraceback", 1)[0].strip()
    return text.splitlines()[0].strip() if "\n" in text else text


def run_auto_pipeline(user_id: int, import_id: int) -> None:
    """入队:置 queued + push 到 Redis。平台无关。"""
    update_status(user_id, import_id, "queued", "waiting in queue")
    try:
        pipeline_queue.enqueue_pipeline(user_id, import_id)
    except Exception as exc:
        update_status(user_id, import_id, "error", f"enqueue failed: {_one_line_error(exc)}")


def worker_handler(user_id: int, import_id: int) -> None:
    """worker 取出任务后的处理:按 platform 分发到对应 pipeline。

    被 pipeline_queue.start_workers 注册为 handler。
    幂等保护:已处于终态(done/error)的任务直接跳过,不重复执行。
    这能根治"重复入队 -> 已完成任务被重跑 -> status 互相覆盖"的死循环。
    """
    from platforms import dispatch
    import store

    env = _load_env()
    row = get_import(user_id, import_id)
    if not row:
        # 记录已被删除 → 静默跳过(不标记 error, 行已不存在)
        log(f"skip: import={import_id} not found (deleted), abort")
        return

    # 新架构: status 只表示采集状态(collected), AI 状态由 ai_status 跟踪
    ai_status = (row.get("ai_status") or "").strip()
    if ai_status in ("done", "generating"):
        log(f"skip: import={import_id} ai_status={ai_status} (duplicate queue item ignored)")
        return
    if ai_status == "insufficient":
        log(f"skip: import={import_id} insufficient beans, not executed")
        return

    platform = row.get("platform") or "temu"
    log(f">>> dispatch: platform={platform} import={import_id}")
    dispatch.execute(env, platform, user_id, import_id, store)
