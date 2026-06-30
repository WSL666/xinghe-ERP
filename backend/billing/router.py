"""充值/金豆 API 路由(挂 /api/billing/*)。

接口:
  GET  /api/billing/balance        查余额(登录用户)
  GET  /api/billing/transactions   查消费/充值记录
  POST /api/billing/recharge       管理员充值(需管理员, 测试阶段用)
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from core.app import current_user
from store import get_user_by_uid
from .store import add_beans, get_beans, list_transactions

router = APIRouter(prefix="/api/billing", tags=["billing"])


async def _uid(request: Request) -> int:
    try:
        user = await current_user(request)
        return int(user["id"])
    except Exception:
        raise _err("请先登录", 401)


def _ok(**payload: Any) -> dict[str, Any]:
    return {"ok": True, **payload}


def _err(message: str, code: int = 400) -> HTTPException:
    return HTTPException(status_code=code, detail={"ok": False, "error": message})


@router.get("/balance")
async def get_balance(request: Request) -> dict[str, Any]:
    """查当前用户金豆余额。"""
    uid = await _uid(request)
    return _ok(beans=get_beans(uid))


@router.get("/transactions")
async def get_transactions(request: Request, limit: int = 20) -> dict[str, Any]:
    """查消费/充值记录。"""
    uid = await _uid(request)
    txs = list_transactions(uid, min(limit, 100))
    return _ok(transactions=txs)


@router.post("/recharge")
async def recharge(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    """管理员充值:按 uid 给用户加金豆。

    测试阶段不验权限(任何登录用户可调), 上线前加管理员校验。
    Body: {uid: "xxxx", amount: 100, reason: "充值"}
    """
    uid_str = str(payload.get("uid", "")).strip()
    amount = int(payload.get("amount", 0))
    reason = str(payload.get("reason", "充值")).strip() or "充值"

    if not uid_str:
        raise _err("请输入用户ID")
    if amount <= 0:
        raise _err("充值金额必须大于0")

    uid = await _uid(request)
    target = get_user_by_uid(uid_str)
    if not target:
        raise _err("用户ID不存在")

    result = add_beans(int(target["id"]), amount, reason)
    if not result:
        raise _err("充值失败")
    return _ok(balance_after=result["balance_after"], uid=uid_str)
