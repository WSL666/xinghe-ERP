"""计费与财务：全平台金豆流水、充值订单、财务总览、企业消费排行。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from deps import require_admin
from store import (
    billing_summary,
    enterprise_consume_ranking,
    list_all_transactions,
    list_recharge_orders,
)

router = APIRouter(prefix="/api/admin/billing", tags=["admin-billing"])


@router.get("/summary")
def api_summary(admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    return {"ok": True, **billing_summary()}


@router.get("/transactions")
def api_transactions(
    user_id: int = Query(0),
    direction: str = Query(""),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    txs, total = list_all_transactions(
        user_id if user_id else None, direction, page, page_size
    )
    return {"ok": True, "transactions": txs, "total": total, "page": page, "page_size": page_size}


@router.get("/orders")
def api_orders(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    orders, total = list_recharge_orders(page, page_size)
    return {"ok": True, "orders": orders, "total": total, "page": page, "page_size": page_size}


@router.get("/ranking")
def api_ranking(
    limit: int = Query(10, ge=1, le=100),
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    return {"ok": True, "ranking": enterprise_consume_ranking(limit)}
