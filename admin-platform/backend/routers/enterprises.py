"""企业管理：全平台 2级企业 + 成员管理。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from audit_helper import audit
from deps import require_admin
from store import (
    get_enterprise_detail,
    list_enterprise_members,
    list_enterprises,
    set_enterprise_frozen,
    set_enterprise_status,
)

router = APIRouter(prefix="/api/admin/enterprises", tags=["admin-enterprises"])


@router.get("")
def api_list_enterprises(
    keyword: str = Query(""),
    status: str = Query(""),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    items, total = list_enterprises(keyword, status, page, page_size)
    return {"ok": True, "enterprises": items, "total": total, "page": page, "page_size": page_size}


@router.get("/{enterprise_id}")
def api_enterprise_detail(
    enterprise_id: int,
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    detail = get_enterprise_detail(enterprise_id)
    if not detail:
        raise HTTPException(status_code=404, detail={"ok": False, "error": "enterprise not found"})
    return {"ok": True, "enterprise": detail}


@router.get("/{enterprise_id}/members")
def api_enterprise_members(
    enterprise_id: int,
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    members = list_enterprise_members(enterprise_id)
    return {"ok": True, "members": members}


@router.post("/{enterprise_id}/freeze")
def api_freeze(
    enterprise_id: int,
    request: Request,
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    if not set_enterprise_frozen(enterprise_id, True):
        raise HTTPException(status_code=404, detail={"ok": False, "error": "enterprise not found"})
    audit(request, admin, "freeze_enterprise", "enterprise", enterprise_id)
    return {"ok": True}


@router.post("/{enterprise_id}/unfreeze")
def api_unfreeze(
    enterprise_id: int,
    request: Request,
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    if not set_enterprise_frozen(enterprise_id, False):
        raise HTTPException(status_code=404, detail={"ok": False, "error": "enterprise not found"})
    audit(request, admin, "unfreeze_enterprise", "enterprise", enterprise_id)
    return {"ok": True}


@router.post("/{enterprise_id}/status")
def api_set_status(
    enterprise_id: int,
    payload: dict[str, Any],
    request: Request,
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    status = str(payload.get("status", "")).strip()
    if status not in {"approved", "pending", "rejected"}:
        raise HTTPException(status_code=400, detail={"ok": False, "error": "invalid status"})
    if not set_enterprise_status(enterprise_id, status):
        raise HTTPException(status_code=404, detail={"ok": False, "error": "enterprise not found"})
    audit(request, admin, "set_enterprise_status", "enterprise", enterprise_id, {"status": status})
    return {"ok": True}
