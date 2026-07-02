"""运营驾驶舱：全平台核心指标。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from deps import require_admin
from store import dashboard_overview

router = APIRouter(prefix="/api/admin/dashboard", tags=["admin-dashboard"])


@router.get("/overview")
def overview(admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    return {"ok": True, **dashboard_overview()}
