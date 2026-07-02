"""定价配置 CRUD。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from audit_helper import audit
from deps import require_admin
from store import (
    delete_pricing_config,
    list_pricing_configs,
    upsert_pricing_config,
)

router = APIRouter(prefix="/api/admin/pricing", tags=["admin-pricing"])


@router.get("")
def list_pricing(admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    return {"ok": True, "configs": list_pricing_configs()}


@router.post("")
def upsert_pricing(
    payload: dict[str, Any],
    request: Request,
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    platform = str(payload.get("platform", "")).strip()
    step = str(payload.get("step", "")).strip()
    cost_beans = int(payload.get("cost_beans", 0))
    is_active = bool(payload.get("is_active", True))
    if not platform or not step:
        raise HTTPException(status_code=400, detail={"ok": False, "error": "platform 和 step 必填"})
    if cost_beans < 0:
        raise HTTPException(status_code=400, detail={"ok": False, "error": "cost_beans 必须 >= 0"})
    result = upsert_pricing_config(platform, step, cost_beans, is_active)
    audit(request, admin, "upsert_pricing", "pricing", "%s:%s" % (platform, step),
          {"cost_beans": cost_beans, "is_active": is_active})
    return {"ok": True, "config": result}


@router.delete("/{config_id}")
def delete_pricing(
    config_id: int,
    request: Request,
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    if not delete_pricing_config(config_id):
        raise HTTPException(status_code=404, detail={"ok": False, "error": "config not found"})
    audit(request, admin, "delete_pricing", "pricing", config_id)
    return {"ok": True}
