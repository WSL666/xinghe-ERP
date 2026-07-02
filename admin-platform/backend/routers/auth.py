"""超管登录 / 登出 / 当前身份。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from config import get_settings
from deps import create_admin_token, get_client_ip, require_admin
from store import update_admin_last_login, verify_admin_credentials, write_audit

router = APIRouter(prefix="/api/admin/auth", tags=["admin-auth"])


class LoginPayload(BaseModel):
    username: str
    password: str


def _ok(**payload: Any) -> dict[str, Any]:
    return {"ok": True, **payload}


@router.post("/login")
def login(payload: LoginPayload, request: Request, response: Response) -> dict[str, Any]:
    admin = verify_admin_credentials(payload.username.strip(), payload.password)
    if not admin:
        raise HTTPException(status_code=401, detail={"ok": False, "error": "用户名或密码错误"})
    update_admin_last_login(int(admin["id"]))
    token = create_admin_token(int(admin["id"]))
    settings = get_settings()
    response.set_cookie(
        key=settings.admin_cookie_name,
        value=token,
        max_age=settings.session_max_age_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    write_audit(
        int(admin["id"]), "login", target_type="self", ip=get_client_ip(request)
    )
    return _ok(admin=_public(admin))


@router.post("/logout")
def logout(response: Response, admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    settings = get_settings()
    response.delete_cookie(settings.admin_cookie_name, path="/")
    return _ok()


@router.get("/me")
def me(admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    return _ok(admin=_public(admin))


def _public(admin: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": admin["id"],
        "username": admin["username"],
        "display_name": admin.get("display_name", ""),
        "last_login_at": str(admin.get("last_login_at") or ""),
    }
