"""用户 + 企业 CRUD。"""
from __future__ import annotations

import secrets
import string
from datetime import datetime
from typing import Any

from config import get_settings
from security import create_api_key, hash_password, normalize_login
from store.pool import db_conn


_UID_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz"
_INVITE_ALPHABET = string.ascii_uppercase + string.digits


def generate_uid() -> str:
    """Generate 8-char uid (no ambiguous chars), deduped."""
    import secrets as _secrets
    with db_conn() as conn:
        for _ in range(50):
            uid = "".join(_secrets.choice(_UID_ALPHABET) for _ in range(8))
            exists = conn.execute("SELECT 1 FROM users WHERE uid = %s", (uid,)).fetchone()
            if not exists:
                return uid
    raise RuntimeError("generate_uid collision after 50 tries")


def get_or_create_dev_user() -> dict[str, Any]:
    account = "admin"
    user = get_user_by_account(account)
    if user:
        if not user.get("api_key"):
            ensure_user_api_key(int(user["id"]))
        return get_user_by_account(account) or user
    return create_user(account=account, password="123456", display_name="Admin")


def create_user(account: str, password: str, display_name: str = "") -> dict[str, Any]:
    normalized = normalize_login(account)
    password_hash = hash_password(password)
    with db_conn() as conn:
        uid = generate_uid()
        api_key = create_api_key(uid)
        row = conn.execute(
            """
            INSERT INTO users (uid, account, password_hash, api_key, display_name, is_verified)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id, uid, account, api_key, display_name,
                     is_verified, is_active, beans, role, created_at
            """,
            (
                uid,
                normalized,
                password_hash,
                api_key,
                display_name or normalized,
                get_settings().auto_verify_users,
            ),
        ).fetchone()
    user = dict(row)
    return user


def get_user_by_account(account: str) -> dict[str, Any] | None:
    with db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE account = %s AND COALESCE(is_deleted, FALSE) = FALSE",
            (normalize_login(account),),
        ).fetchone()
    return dict(row) if row else None


def get_user_by_id(user_id: int) -> dict[str, Any] | None:
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = %s", (user_id,)).fetchone()
    return dict(row) if row else None


def get_user_by_uid(uid: str) -> dict[str, Any] | None:
    """Lookup user by uid (for recharge/beans/support)."""
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE uid = %s", (uid,)).fetchone()
    return dict(row) if row else None


def get_user_by_api_key(api_key: str) -> dict[str, Any] | None:
    if not api_key:
        return None
    with db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE api_key = %s",
            (api_key,),
        ).fetchone()
    return dict(row) if row else None


def ensure_user_api_key(user_id: int) -> str:
    """老用户补发固定密钥(uid+8位随机)。已有则不动。返回密钥明文。"""
    with db_conn() as conn:
        row = conn.execute(
            "SELECT uid, api_key FROM users WHERE id = %s", (user_id,)
        ).fetchone()
        if not row:
            return ""
        existing = row.get("api_key") or ""
        if existing:
            return existing
        uid = row.get("uid") or ""
        api_key = create_api_key(uid)
        conn.execute(
            "UPDATE users SET api_key = %s, updated_at = now() WHERE id = %s",
            (api_key, user_id),
        )
        return api_key


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    data = {
        "id": user["id"],
        "uid": user.get("uid", ""),
        "account": user["account"],
        "display_name": user.get("display_name") or user["account"],
        "is_verified": bool(user.get("is_verified")),
        "api_key": user.get("api_key", "") or "",
        "role": user.get("role", "member"),
        "enterprise_id": user.get("enterprise_id"),
        "beans": int(user.get("beans") or 0),
    }
    return data


# ── 企业相关 ──


def generate_invite_code() -> str:
    """8-char invite code, kept unique by a retry loop in create/regenerate."""
    return "PP-" + "".join(secrets.choice(_INVITE_ALPHABET) for _ in range(6))


def _row_enterprise(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    if isinstance(item.get("created_at"), datetime):
        item["created_at"] = item["created_at"].strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(item.get("updated_at"), datetime):
        item["updated_at"] = item["updated_at"].strftime("%Y-%m-%d %H:%M:%S")
    return item


def _row_member(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    if isinstance(item.get("joined_at"), datetime):
        item["joined_at"] = item["joined_at"].strftime("%Y-%m-%d %H:%M:%S")
    return item


def create_enterprise_with_owner(
    name: str,
    contact_name: str,
    contact_phone: str,
    account: str,
    password: str,
    display_name: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Create an enterprise, its owner account, and the membership link."""
    normalized = normalize_login(account)
    if get_user_by_account(normalized):
        raise ValueError("account already exists")
    normalized_name = (name or "").strip()
    if not normalized_name:
        raise ValueError("enterprise name is required")

    password_hash = hash_password(password)

    with db_conn() as conn:
        invite_code = generate_invite_code()
        for _ in range(5):
            exists = conn.execute(
                "SELECT 1 FROM enterprises WHERE invite_code = %s", (invite_code,)
            ).fetchone()
            if not exists:
                break
            invite_code = generate_invite_code()
        else:
            raise RuntimeError("could not generate a unique invite code")

        ent_row = conn.execute(
            """
            INSERT INTO enterprises (name, display_name, contact_name, contact_phone, invite_code)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING *
            """,
            (normalized_name, display_name or normalized_name, contact_name, contact_phone, invite_code),
        ).fetchone()
        enterprise = _row_enterprise(dict(ent_row))
        enterprise_id = enterprise["id"]

        owner_uid = generate_uid()
        api_key = create_api_key(owner_uid)
        user_row = conn.execute(
            """
            INSERT INTO users (uid, account, password_hash, api_key, display_name, enterprise_id, role)
            VALUES (%s, %s, %s, %s, %s, %s, 'owner')
            RETURNING *
            """,
            (owner_uid, normalized, password_hash, api_key, display_name or normalized, enterprise_id),
        ).fetchone()
        user = dict(user_row)

        conn.execute(
            """
            INSERT INTO enterprise_members (enterprise_id, user_id, role)
            VALUES (%s, %s, 'owner')
            """,
            (enterprise_id, user["id"]),
        )
        conn.execute(
            "UPDATE enterprises SET creator_user_id = %s WHERE id = %s",
            (user["id"], enterprise_id),
        )
    return enterprise, user


def join_enterprise_by_invite(invite_code: str, user_id: int) -> dict[str, Any] | None:
    """Attach an existing user to an enterprise as a member by invite code."""
    code = (invite_code or "").strip().upper()
    if not code:
        return None
    with db_conn() as conn:
        ent = conn.execute(
            "SELECT * FROM enterprises WHERE invite_code = %s AND status = 'approved'",
            (code,),
        ).fetchone()
        if not ent:
            return None
        enterprise = _row_enterprise(dict(ent))
        existing = conn.execute(
            "SELECT 1 FROM enterprise_members WHERE enterprise_id = %s AND user_id = %s",
            (enterprise["id"], user_id),
        ).fetchone()
        if existing:
            return enterprise
        conn.execute(
            """
            INSERT INTO enterprise_members (enterprise_id, user_id, role)
            VALUES (%s, %s, 'member')
            """,
            (enterprise["id"], user_id),
        )
        conn.execute(
            "UPDATE users SET enterprise_id = %s, role = 'member', updated_at = now() WHERE id = %s",
            (enterprise["id"], user_id),
        )
    return enterprise


def get_enterprise_context_for_user(user_id: int) -> dict[str, Any] | None:
    """Enterprise + role + status for a user, for /api/auth/me routing."""
    with db_conn() as conn:
        row = conn.execute(
            """
            SELECT e.id, e.name, e.display_name, e.status, e.invite_code,
                   m.role, m.status AS member_status
            FROM enterprise_members m
            JOIN enterprises e ON e.id = m.enterprise_id
            WHERE m.user_id = %s AND m.status = 'active'
            ORDER BY m.joined_at DESC
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
    if not row:
        return None
    data = dict(row)
    return {
        "id": data["id"],
        "name": data["name"],
        "display_name": data["display_name"],
        "status": data["status"],
        "invite_code": data["invite_code"],
        "role": data["role"],
        "member_status": data["member_status"],
    }


def list_enterprise_members(enterprise_id: int) -> list[dict[str, Any]]:
    """Member roster with per-user pipeline stats joined from imports."""
    with db_conn() as conn:
        rows = conn.execute(
            """
            SELECT u.id, u.account, u.display_name, m.role, m.status,
                   m.joined_at,
                   COUNT(imp.id) AS import_count,
                   COUNT(imp.id) FILTER (WHERE imp.status = 'done') AS success_count,
                   COUNT(imp.id) FILTER (WHERE imp.status = 'error') AS error_count,
                   MAX(imp.updated_at) AS last_active_at
            FROM enterprise_members m
            JOIN users u ON u.id = m.user_id
            LEFT JOIN imports imp ON imp.user_id = u.id
            WHERE m.enterprise_id = %s
            GROUP BY u.id, u.account, u.display_name, m.role, m.status, m.joined_at
            ORDER BY m.joined_at
            """,
            (enterprise_id,),
        ).fetchall()
    members = []
    for row in rows:
        item = _row_member(dict(row))
        if isinstance(item.get("last_active_at"), datetime):
            item["last_active_at"] = item["last_active_at"].strftime("%Y-%m-%d %H:%M:%S")
        else:
            item["last_active_at"] = ""
        members.append(item)
    return members


def regenerate_invite_code(enterprise_id: int) -> str:
    with db_conn() as conn:
        for _ in range(5):
            code = generate_invite_code()
            clash = conn.execute(
                "SELECT 1 FROM enterprises WHERE invite_code = %s AND id <> %s",
                (code, enterprise_id),
            ).fetchone()
            if not clash:
                conn.execute(
                    "UPDATE enterprises SET invite_code = %s, updated_at = now() WHERE id = %s",
                    (code, enterprise_id),
                )
                return code
    raise RuntimeError("could not generate a unique invite code")


def update_member_role(enterprise_id: int, user_id: int, role: str) -> bool:
    if role not in {"admin", "member"}:
        raise ValueError("role must be admin or member")
    with db_conn() as conn:
        cur = conn.execute(
            """
            UPDATE enterprise_members SET role = %s
            WHERE enterprise_id = %s AND user_id = %s AND role <> 'owner'
            """,
            (role, enterprise_id, user_id),
        )
        if cur.rowcount > 0:
            conn.execute(
                "UPDATE users SET role = %s, updated_at = now() WHERE id = %s",
                (role, user_id),
            )
    return cur.rowcount > 0


def remove_enterprise_member(enterprise_id: int, user_id: int) -> bool:
    with db_conn() as conn:
        cur = conn.execute(
            """
            DELETE FROM enterprise_members
            WHERE enterprise_id = %s AND user_id = %s AND role <> 'owner'
            """,
            (enterprise_id, user_id),
        )
        if cur.rowcount > 0:
            conn.execute(
                "UPDATE users SET enterprise_id = NULL, role = 'member', updated_at = now() WHERE id = %s",
                (user_id,),
            )
    return cur.rowcount > 0


def get_enterprise_by_id(enterprise_id: int) -> dict[str, Any] | None:
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM enterprises WHERE id = %s", (enterprise_id,)).fetchone()
    return _row_enterprise(dict(row)) if row else None
