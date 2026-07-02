"""超管鉴权依赖：独立的 Session 体系，与普通用户完全隔离。"""
from __future__ import annotations

import ipaddress
import logging
from typing import Any

from fastapi import Cookie, Depends, HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from config import get_settings
from store import get_admin_by_id

logger = logging.getLogger("admin.deps")


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().admin_secret_key, salt="ppe-admin-session")


def create_admin_token(admin_id: int) -> str:
    return _serializer().dumps({"aid": admin_id})


def load_admin_token(token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    settings = get_settings()
    try:
        data = _serializer().loads(token, max_age=settings.session_max_age_seconds)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(data, dict) or not data.get("aid"):
        return None
    return data


def check_ip_allowed(request: Request) -> None:
    """可选 IP 白名单（ADMIN_ALLOW_CIDR）。空则不限制。"""
    cidr = get_settings().admin_allow_cidr
    if not cidr:
        return
    client_ip = request.client.host if request.client else ""
    try:
        network = ipaddress.ip_network(cidr, strict=False)
        if ipaddress.ip_address(client_ip) not in network:
            raise HTTPException(status_code=403, detail={"ok": False, "error": "IP not allowed"})
    except ValueError:
        pass


async def require_admin(
    request: Request,
    admin_session: str | None = Cookie(default=None, alias=None),
) -> dict[str, Any]:
    """所有 /api/admin/* 接口的鉴权门卫。"""
    check_ip_allowed(request)
    settings = get_settings()
    cookie_name = settings.admin_cookie_name
    token = request.cookies.get(cookie_name) or admin_session
    data = load_admin_token(token)
    if not data:
        raise HTTPException(status_code=401, detail={"ok": False, "error": "not authenticated"})
    admin = get_admin_by_id(int(data["aid"]))
    if not admin or not admin.get("is_active"):
        raise HTTPException(status_code=401, detail={"ok": False, "error": "account disabled"})
    request.state.admin = admin
    return admin


def get_client_ip(request: Request) -> str:
    return request.client.host if request.client else ""
