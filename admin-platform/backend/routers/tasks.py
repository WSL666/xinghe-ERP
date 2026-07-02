"""任务监控：全平台任务，富格式（含图片/规格/尺寸，与主应用工作台一致）。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from deps import require_admin
from store import get_task_detail, list_all_tasks_rich

router = APIRouter(prefix="/api/admin/tasks", tags=["admin-tasks"])


@router.get("")
def api_list_tasks(
    platform: str = Query(""),
    status: str = Query(""),
    keyword: str = Query(""),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    tasks, total = list_all_tasks_rich(platform, status, keyword, page, page_size)
    return {"ok": True, "tasks": tasks, "total": total, "page": page, "page_size": page_size}


@router.get("/{import_id}")
def api_task_detail(
    import_id: int,
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    detail = get_task_detail(import_id)
    if not detail:
        raise HTTPException(status_code=404, detail={"ok": False, "error": "task not found"})
    return {"ok": True, "task": detail}
