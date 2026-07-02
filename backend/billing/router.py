"""充值/金豆 API 路由(挂 /api/billing/*)。

接口:
  GET  /api/billing/balance        查余额(登录用户)
  GET  /api/billing/transactions   查消费/充值记录
  POST /api/billing/recharge       管理员充值(需管理员, 测试阶段用)
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from config import get_settings
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


def _require_admin(request: Request) -> None:
    """校验管理员身份: 请求头 X-Admin-Token 必须等于配置的 ADMIN_TOKEN。

    未配置 ADMIN_TOKEN(空)时直接拒绝, 避免空 token 绕过。
    用 hmac.compare_digest 做常量时间比较防计时侧信道。
    """
    expected = get_settings().admin_token
    if not expected:
        raise _err("未配置管理员令牌(ADMIN_TOKEN), 充值功能已禁用", 403)
    provided = request.headers.get("x-admin-token", "")
    if not provided:
        raise _err("缺少管理员令牌", 403)
    import hmac as _hmac
    if not _hmac.compare_digest(provided, expected):
        raise _err("管理员令牌无效", 403)


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

    需管理员令牌(请求头 X-Admin-Token == 后端 ADMIN_TOKEN 环境变量)。
    Body: {uid: "xxxx", amount: 100, reason: "充值"}
    """
    _require_admin(request)
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
