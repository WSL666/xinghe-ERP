"""AI 资源管理：Key 池完整管理(读+写) + 模型配置 + Prompt 模板。

整合了主应用旧 /admin/keys 面板的全部能力，迁移到超管系统下，
用 require_admin 鉴权替代旧的 ADMIN_TOKEN。
"""
from __future__ import annotations

import os
import sys
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from audit_helper import audit
from deps import require_admin
from store import read_ai_config, read_prompts

router = APIRouter(prefix="/api/admin/ai", tags=["admin-ai"])

# 主应用 backend 路径（用于 import api_key_pool.pool）
_MAIN_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend"))
if _MAIN_BACKEND not in sys.path:
    sys.path.append(_MAIN_BACKEND)


def _get_pool_funcs():
    """延迟导入主应用的 api_key_pool.pool（Redis 客户端单例由主应用共享）。"""
    try:
        from api_key_pool.pool import PROVIDERS, all_snapshots, get_pool
        return PROVIDERS, all_snapshots, get_pool
    except Exception as exc:
        raise HTTPException(status_code=503, detail={"ok": False, "error": f"Key 池引擎不可用: {exc}"})


# ── 模型配置（只读）──

@router.get("/config")
def ai_config(admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    """AI 模型配置（从 .env 读取，密钥脱敏）。"""
    return {"ok": True, **read_ai_config()}


@router.get("/prompts")
def prompts_status(admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    """Prompt 模板列表（只读展示）。"""
    return {"ok": True, **read_prompts()}


# ── Key 池：读取 ──

@router.get("/keys")
def key_pool_status(
    provider: str = "",
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """API Key 池实时状态。

    不传 provider 返回全部快照; 传 provider 只返回该 provider 的快照。
    """
    try:
        PROVIDERS, all_snapshots, get_pool = _get_pool_funcs()
        if provider and provider in PROVIDERS:
            snapshot = get_pool(provider).snapshot()
            return {"ok": True, "providers": dict(PROVIDERS), "pools": [snapshot], "connected": True}
        return {"ok": True, "providers": dict(PROVIDERS), "pools": all_snapshots(), "connected": True}
    except HTTPException:
        raise
    except Exception as exc:
        return {"ok": True, "providers": {}, "pools": [], "connected": False, "error": str(exc)}


# ── Key 池：写入（迁移自旧 /admin/keys 面板）──

@router.post("/keys/add")
def key_add(
    payload: dict[str, Any],
    request: Request,
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """添加 Key（支持批量：keys 列表，自动去重）。

    Body: {provider: "chat"|"vibe", keys: ["sk-xxx", ...]}
    单个 key 也可以用 {provider, key: "sk-xxx"}。
    """
    PROVIDERS, _, get_pool = _get_pool_funcs()
    provider = str(payload.get("provider", "")).strip()
    if provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail={"ok": False, "error": f"provider 必须是: {list(PROVIDERS)}"})

    keys_raw = payload.get("keys") or []
    if not isinstance(keys_raw, list):
        keys_raw = [keys_raw]
    single = payload.get("key")
    if single:
        keys_raw.append(single)

    keys = []
    for k in keys_raw:
        k = str(k).strip()
        if k:
            keys.append(k)
    if not keys:
        raise HTTPException(status_code=400, detail={"ok": False, "error": "key 不能为空"})

    pool = get_pool(provider)
    added = 0
    duplicate = 0
    for k in keys:
        if pool.add(k):
            added += 1
        else:
            duplicate += 1

    audit(request, admin, "add_api_key", "keypool", provider,
          {"added": added, "duplicate": duplicate})
    return {"ok": True, "added": added, "duplicate": duplicate}


@router.post("/keys/remove")
def key_remove(
    payload: dict[str, Any],
    request: Request,
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """删除单个 Key（传完整 key，非脱敏值）。"""
    PROVIDERS, _, get_pool = _get_pool_funcs()
    provider = str(payload.get("provider", "")).strip()
    if provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail={"ok": False, "error": "provider 无效"})
    key = str(payload.get("key", "")).strip()
    if not key:
        raise HTTPException(status_code=400, detail={"ok": False, "error": "key 不能为空"})

    pool = get_pool(provider)
    ok = pool.remove(key)
    if not ok:
        raise HTTPException(status_code=404, detail={"ok": False, "error": "key 不存在"})
    audit(request, admin, "remove_api_key", "keypool", provider, {"key": key[:8] + "****"})
    return {"ok": True}


@router.post("/keys/bulk-remove")
def key_bulk_remove(
    payload: dict[str, Any],
    request: Request,
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """批量删除（可指定 keys 列表，或 state=failed 删全部失效）。"""
    PROVIDERS, _, get_pool = _get_pool_funcs()
    provider = str(payload.get("provider", "")).strip()
    if provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail={"ok": False, "error": "provider 无效"})
    pool = get_pool(provider)

    keys = payload.get("keys")
    if isinstance(keys, list) and keys:
        n = pool.bulk_remove([str(k).strip() for k in keys if k])
        audit(request, admin, "bulk_remove_api_key", "keypool", provider, {"count": n})
        return {"ok": True, "removed": n}

    state = str(payload.get("state", "")).strip()
    if state == "failed":
        n = pool.bulk_remove([r["full_key"] for r in pool.list_failed()])
        audit(request, admin, "clear_failed_keys", "keypool", provider, {"count": n})
        return {"ok": True, "removed": n}

    raise HTTPException(status_code=400, detail={"ok": False, "error": "需要 keys[] 或 state=failed"})


@router.post("/keys/update")
def key_update(
    payload: dict[str, Any],
    request: Request,
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """改 Key 状态: available / failed / cooling。"""
    PROVIDERS, _, get_pool = _get_pool_funcs()
    provider = str(payload.get("provider", "")).strip()
    if provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail={"ok": False, "error": "provider 无效"})
    key = str(payload.get("key", "")).strip()
    status = str(payload.get("status", "")).strip()
    if not key or status not in ("available", "failed", "cooling"):
        raise HTTPException(status_code=400, detail={"ok": False, "error": "key 和 status(available/failed/cooling) 必填"})

    pool = get_pool(provider)
    ok = pool.update(key, status)
    if not ok:
        raise HTTPException(status_code=404, detail={"ok": False, "error": "key 不存在"})
    audit(request, admin, "update_api_key", "keypool", provider, {"key": key[:8] + "****", "status": status})
    return {"ok": True}
