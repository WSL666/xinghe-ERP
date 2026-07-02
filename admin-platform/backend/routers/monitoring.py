"""监控中心：队列实时状态 + 错误中心。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from audit_helper import audit
from config import get_settings
from deps import require_admin
from store import (
    batch_retry,
    error_breakdown,
    error_summary,
    list_error_tasks,
    retry_import,
)

router = APIRouter(prefix="/api/admin/monitoring", tags=["admin-monitoring"])


@router.get("/queue")
def queue_status(admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    """Redis 队列实时状态：队列深度、在跑任务数、worker 配置。"""
    info = {"queue_depth": 0, "queued_items": [], "active_users": [], "connected": False}
    try:
        import redis as redis_lib
        r = redis_lib.from_url(get_settings().redis_url, socket_connect_timeout=3)
        info["connected"] = True
        # 队列长度
        depth = r.llen("pipeline:queue")
        info["queue_depth"] = int(depth)
        # 队列内容（最多看前 50 条）
        items = r.lrange("pipeline:queue", 0, 49)
        import json as _json
        info["queued_items"] = [_json.loads(it) for it in items]
        # 活跃计数（每用户正在跑的任务数）
        active_keys = r.keys("pipeline:active:*")
        active_users = []
        for key in active_keys:
            val = r.get(key)
            if val and int(val) > 0:
                key_str = key.decode() if isinstance(key, bytes) else key
                user_id = key_str.split(":")[-1]
                active_users.append({"user_id": user_id, "active": int(val)})
        info["active_users"] = active_users
        info["total_active"] = sum(u["active"] for u in active_users)
        r.close()
    except Exception as exc:
        info["error"] = str(exc)
    return {"ok": True, **info}


@router.get("/errors/summary")
def errors_summary_endpoint(admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    return {"ok": True, **error_summary(), "breakdown": error_breakdown()}


@router.get("/errors")
def list_errors(
    platform: str = Query(""),
    keyword: str = Query(""),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    tasks, total = list_error_tasks(platform, keyword, page, page_size)
    return {"ok": True, "tasks": tasks, "total": total, "page": page, "page_size": page_size}


@router.post("/errors/{import_id}/retry")
def retry_one(
    import_id: int,
    request: Request,
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    if not retry_import(import_id):
        raise HTTPException(status_code=404, detail={"ok": False, "error": "task not found"})
    audit(request, admin, "retry_task", "import", import_id)
    return {"ok": True}


@router.post("/errors/batch-retry")
def retry_batch(
    payload: dict[str, Any],
    request: Request,
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    ids = payload.get("import_ids", [])
    if not isinstance(ids, list) or not ids:
        raise HTTPException(status_code=400, detail={"ok": False, "error": "import_ids required"})
    count = batch_retry([int(i) for i in ids])
    audit(request, admin, "batch_retry", "import", ",".join(str(i) for i in ids), {"count": count})
    return {"ok": True, "retried": count}
