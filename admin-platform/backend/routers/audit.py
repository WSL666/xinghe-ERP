"""安全审计：操作日志查询。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from deps import require_admin
from store import count_audit_logs, list_audit_logs

router = APIRouter(prefix="/api/admin/audit", tags=["admin-audit"])


@router.get("")
def api_list_audit(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    offset = (page - 1) * page_size
    logs = list_audit_logs(page_size, offset)
    total = count_audit_logs()
    return {"ok": True, "logs": logs, "total": total, "page": page, "page_size": page_size}
