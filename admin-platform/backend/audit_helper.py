"""审计日志便捷封装：路由里一行写入。"""
from __future__ import annotations

from typing import Any

from fastapi import Request

from deps import get_client_ip
from store import write_audit


def audit(
    request: Request,
    admin: dict[str, Any],
    action: str,
    target_type: str = "",
    target_id: Any = "",
    detail: dict[str, Any] | None = None,
) -> None:
    write_audit(
        admin_id=int(admin["id"]),
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        detail=detail,
        ip=get_client_ip(request),
    )
