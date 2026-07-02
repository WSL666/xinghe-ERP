"""任务监控：全平台任务，富格式（含图片/规格/尺寸，与主应用工作台一致）。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from deps import require_admin
from store import admin_edit_ai_image, get_task_detail, list_all_tasks_rich

router = APIRouter(prefix="/api/admin/tasks", tags=["admin-tasks"])


@router.get("")
def api_list_tasks(
    platform: str = Query(""),
    status: str = Query(""),
    keyword: str = Query(""),
    account: str = Query(""),
    ref_code: str = Query(""),
    date_from: str = Query(""),
    date_to: str = Query(""),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    tasks, total = list_all_tasks_rich(
        platform, status, keyword, account, ref_code, date_from, date_to, page, page_size
    )
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


@router.post("/{import_id}/ai-image/promote")
def api_ai_promote(
    import_id: int,
    payload: dict[str, Any],
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    source_url = str(payload.get("source_url", "") or "").strip()
    if not source_url:
        raise HTTPException(status_code=400, detail={"ok": False, "error": "source_url is required"})
    generated = admin_edit_ai_image(import_id, "promote", source_url=source_url)
    if generated is None:
        raise HTTPException(status_code=404, detail={"ok": False, "error": "task not found"})
    return {"ok": True, "import_id": import_id, "generated": generated}


@router.post("/{import_id}/ai-image/delete")
def api_ai_delete(
    import_id: int,
    payload: dict[str, Any],
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    image_type = str(payload.get("image_type", "") or "").strip()
    if not image_type:
        raise HTTPException(status_code=400, detail={"ok": False, "error": "image_type is required"})
    generated = admin_edit_ai_image(import_id, "delete", image_type=image_type)
    if generated is None:
        raise HTTPException(status_code=404, detail={"ok": False, "error": "image not found"})
    return {"ok": True, "import_id": import_id, "generated": generated}


@router.post("/{import_id}/ai-image/restore")
def api_ai_restore(
    import_id: int,
    payload: dict[str, Any],
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    image_type = str(payload.get("image_type", "") or "").strip()
    if not image_type:
        raise HTTPException(status_code=400, detail={"ok": False, "error": "image_type is required"})
    generated = admin_edit_ai_image(import_id, "restore", image_type=image_type)
    if generated is None:
        raise HTTPException(status_code=404, detail={"ok": False, "error": "image not found"})
    return {"ok": True, "import_id": import_id, "generated": generated}
