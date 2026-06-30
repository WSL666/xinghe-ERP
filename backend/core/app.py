"""平台无关的公共 HTTP 路由:auth、enterprise、静态页面、health。

这些路由与平台无关,所有平台共享。各平台的业务路由(如 /api/temu/*)
通过 APIRouter 在 main.py 里挂载。

从旧 main.py 抽取,去掉平台特化的 import/export 路由。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from config import FRONTEND_ROOT, get_settings
from security import (
    create_session_token, load_session_token, validate_account, verify_password,
)
from store import (
    create_user, get_user_by_account, get_user_by_id, public_user,
    ensure_user_api_key, create_enterprise_with_owner, get_enterprise_by_id,
    get_enterprise_context_for_user, join_enterprise_by_invite,
    list_enterprise_members, regenerate_invite_code,
    remove_enterprise_member, update_member_role,
)
import sms

router = APIRouter(tags=["common"])


# ── Pydantic 模型 ──
class RegisterPayload(BaseModel):
    account: str = Field(..., min_length=3)
    password: str = Field(..., min_length=6)
    display_name: str = ""
    invite_code: str = ""
    sms_code: str = ""


class SmsSendPayload(BaseModel):
    account: str = Field(..., min_length=3)


class LoginPayload(BaseModel):
    account: str
    password: str


class OnboardPayload(BaseModel):
    enterprise_name: str = Field(..., min_length=2)
    contact_name: str = ""
    contact_phone: str = ""
    account: str = Field(..., min_length=3)
    password: str = Field(..., min_length=6)
    display_name: str = ""


class MemberRolePayload(BaseModel):
    role: str


# ── 工具函数 ──
def api_ok(**payload: Any) -> dict[str, Any]:
    return {"ok": True, **payload}


def api_error(message: str, status_code: int = 400) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"ok": False, "error": message})


def attach_session(response: Response, user_id: int) -> None:
    settings = get_settings()
    response.set_cookie(
        settings.session_cookie_name,
        create_session_token(user_id),
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.session_max_age_seconds,
        path="/",
    )


def clear_session(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(settings.session_cookie_name, path="/")


async def current_user(request: Request) -> dict[str, Any]:
    settings = get_settings()
    token = request.cookies.get(settings.session_cookie_name)
    session_data = load_session_token(token)
    if not session_data:
        raise api_error("not authenticated", 401)
    user = get_user_by_id(int(session_data["uid"]))
    if not user or not user.get("is_active"):
        raise api_error("not authenticated", 401)
    return user


# ── Auth 路由 ──
@router.post("/api/auth/sms/send")
def send_sms_code(payload: SmsSendPayload) -> dict[str, Any]:
    try:
        result = sms.send_code(payload.account)
    except sms.SmsError as exc:
        raise api_error(str(exc), 429)
    return api_ok(**result)


@router.post("/api/auth/register")
def register(payload: RegisterPayload, response: Response) -> dict[str, Any]:
    try:
        account = validate_account(payload.account)
    except ValueError as exc:
        raise api_error(str(exc), 400)
    if get_user_by_account(account):
        raise api_error("该账号已注册，请直接登录或换一个账号", 409)
    if not payload.sms_code:
        raise api_error("请先获取并输入验证码", 400)
    try:
        if not sms.verify_code(account, payload.sms_code):
            raise api_error("验证码错误或已过期", 400)
    except sms.SmsError as exc:
        raise api_error(str(exc), 400)
    try:
        user = create_user(account=account, password=payload.password, display_name=payload.display_name)
    except ValueError as exc:
        raise api_error(str(exc), 400)
    enterprise = None
    if payload.invite_code:
        enterprise = join_enterprise_by_invite(payload.invite_code, int(user["id"]))
        if enterprise is None:
            raise api_error("invalid invite code", 400)
        fresh = get_user_by_id(int(user["id"])) or {}
        user["role"] = fresh.get("role", "member")
        user["enterprise_id"] = fresh.get("enterprise_id")
    attach_session(response, int(user["id"]))
    full = get_user_by_id(int(user["id"])) or {}
    full.update({k: v for k, v in user.items() if k not in full})
    from core.base import log
    log(f"注册成功: account={account} uid={full.get('uid', user['id'])}")
    return api_ok(user=public_user(full), enterprise=enterprise)


@router.post("/api/auth/login")
def login(payload: LoginPayload, response: Response) -> dict[str, Any]:
    account = payload.account.strip().lower()
    user = get_user_by_account(account)
    if not user or not verify_password(payload.password, user["password_hash"]):
        raise api_error("账号或密码错误", 401)
    if not user.get("is_active"):
        raise api_error("账号已被禁用", 403)
    # 老用户迁移: 若没有固定密钥则补发(仅一次)
    if not user.get("api_key"):
        ensure_user_api_key(int(user["id"]))
        user = get_user_by_account(account) or user
    attach_session(response, int(user["id"]))
    from core.base import log
    log(f"登录成功: account={account} uid={user.get('uid', user['id'])}")
    return api_ok(user=public_user(user))


@router.post("/api/auth/logout")
def logout(response: Response) -> dict[str, Any]:
    clear_session(response)
    return api_ok()


@router.get("/api/auth/me")
def auth_me(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    enterprise = get_enterprise_context_for_user(int(user["id"]))
    data = public_user(user)
    if enterprise:
        data["enterprise"] = enterprise
    return api_ok(user=data)


# ── Enterprise 路由 ──
async def require_enterprise_admin(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    ctx = get_enterprise_context_for_user(int(user["id"]))
    if not ctx or ctx.get("role") not in {"owner", "admin"}:
        raise api_error("enterprise admin only", 403)
    user["enterprise"] = ctx
    return user


@router.post("/api/enterprise/onboard")
def api_enterprise_onboard(payload: OnboardPayload, response: Response) -> dict[str, Any]:
    try:
        enterprise, user = create_enterprise_with_owner(
            name=payload.enterprise_name,
            contact_name=payload.contact_name,
            contact_phone=payload.contact_phone,
            account=payload.account,
            password=payload.password,
            display_name=payload.display_name,
        )
    except ValueError as exc:
        raise api_error(str(exc), 400)
    attach_session(response, int(user["id"]))
    return api_ok(enterprise=enterprise, user=public_user(user))


@router.get("/api/enterprise/me")
def api_enterprise_me(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    ctx = get_enterprise_context_for_user(int(user["id"]))
    if not ctx:
        raise api_error("no enterprise", 404)
    return api_ok(enterprise=ctx)


@router.get("/api/enterprise/members")
def api_enterprise_members(user: dict[str, Any] = Depends(require_enterprise_admin)) -> dict[str, Any]:
    enterprise_id = int(user["enterprise"]["id"])
    return api_ok(
        enterprise=get_enterprise_by_id(enterprise_id),
        members=list_enterprise_members(enterprise_id),
    )


@router.post("/api/enterprise/invite/regenerate")
def api_enterprise_regenerate_invite(user: dict[str, Any] = Depends(require_enterprise_admin)) -> dict[str, Any]:
    if user["enterprise"]["role"] != "owner":
        raise api_error("only owner can regenerate invite code", 403)
    code = regenerate_invite_code(int(user["enterprise"]["id"]))
    return api_ok(invite_code=code)


@router.post("/api/enterprise/members/{member_id}/role")
def api_enterprise_member_role(
    member_id: int,
    payload: MemberRolePayload,
    user: dict[str, Any] = Depends(require_enterprise_admin),
) -> dict[str, Any]:
    try:
        updated = update_member_role(int(user["enterprise"]["id"]), member_id, payload.role)
    except ValueError as exc:
        raise api_error(str(exc), 400)
    if not updated:
        raise api_error("member not found", 404)
    return api_ok()


@router.delete("/api/enterprise/members/{member_id}")
def api_enterprise_remove_member(
    member_id: int,
    user: dict[str, Any] = Depends(require_enterprise_admin),
) -> dict[str, Any]:
    if not remove_enterprise_member(int(user["enterprise"]["id"]), member_id):
        raise api_error("member not found", 404)
    return api_ok()


# ── 静态页面路由 ──
@router.get("/", response_class=HTMLResponse)
def index() -> str:
    return (FRONTEND_ROOT / "index.html").read_text(encoding="utf-8")


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard() -> str:
    return (FRONTEND_ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")


@router.get("/enterprise", response_class=HTMLResponse)
def enterprise_page() -> str:
    return (FRONTEND_ROOT / "enterprise.html").read_text(encoding="utf-8")


@router.get("/onboard", response_class=HTMLResponse)
def onboard_page() -> str:
    return (FRONTEND_ROOT / "onboard.html").read_text(encoding="utf-8")


@router.get("/api/health")
def health() -> dict[str, Any]:
    return api_ok(status="healthy")
