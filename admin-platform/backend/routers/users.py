"""用户管理：全平台 3级用户。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from deps import get_client_ip, require_admin
from store import (
    admin_recharge_beans,
    get_user_detail,
    list_users,
    set_user_active,
    set_user_frozen,
)
from audit_helper import audit

router = APIRouter(prefix="/api/admin/users", tags=["admin-users"])


@router.get("")
def api_list_users(
    keyword: str = Query(""),
    status: str = Query(""),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    users, total = list_users(keyword, status, page, page_size)
    return {"ok": True, "users": users, "total": total, "page": page, "page_size": page_size}


@router.get("/{user_id}")
def api_user_detail(
    user_id: int,
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    detail = get_user_detail(user_id)
    if not detail:
        raise HTTPException(status_code=404, detail={"ok": False, "error": "user not found"})
    return {"ok": True, "user": detail}


@router.post("/{user_id}/freeze")
def api_freeze_user(
    user_id: int,
    request: Request,
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    if not set_user_frozen(user_id, True):
        raise HTTPException(status_code=404, detail={"ok": False, "error": "user not found"})
    audit(request, admin, "freeze_user", "user", user_id)
    return {"ok": True}


@router.post("/{user_id}/unfreeze")
def api_unfreeze_user(
    user_id: int,
    request: Request,
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    if not set_user_frozen(user_id, False):
        raise HTTPException(status_code=404, detail={"ok": False, "error": "user not found"})
    audit(request, admin, "unfreeze_user", "user", user_id)
    return {"ok": True}


@router.post("/{user_id}/disable")
def api_disable_user(
    user_id: int,
    request: Request,
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    if not set_user_active(user_id, False):
        raise HTTPException(status_code=404, detail={"ok": False, "error": "user not found"})
    audit(request, admin, "disable_user", "user", user_id)
    return {"ok": True}


@router.post("/{user_id}/enable")
def api_enable_user(
    user_id: int,
    request: Request,
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    if not set_user_active(user_id, True):
        raise HTTPException(status_code=404, detail={"ok": False, "error": "user not found"})
    audit(request, admin, "enable_user", "user", user_id)
    return {"ok": True}


@router.post("/{user_id}/recharge")
def api_recharge_user(
    user_id: int,
    payload: dict[str, Any],
    request: Request,
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    amount = int(payload.get("amount", 0))
    note = str(payload.get("note", "")).strip()
    if amount <= 0:
        raise HTTPException(status_code=400, detail={"ok": False, "error": "充值金额必须大于0"})
    result = admin_recharge_beans(user_id, amount, int(admin["id"]), note)
    if not result:
        raise HTTPException(status_code=404, detail={"ok": False, "error": "user not found"})
    audit(request, admin, "recharge", "user", user_id, {"amount": amount, "note": note})
    return {"ok": True, **result}
