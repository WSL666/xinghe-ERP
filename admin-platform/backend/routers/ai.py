"""AI 资源管理：Key 池状态 + 模型配置 + Prompt 模板。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from deps import require_admin
from store import read_ai_config, read_prompts

router = APIRouter(prefix="/api/admin/ai", tags=["admin-ai"])


@router.get("/config")
def ai_config(admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    """AI 模型配置（从 .env 读取，密钥脱敏）。"""
    return {"ok": True, **read_ai_config()}


@router.get("/keys")
def key_pool_status(admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    """API Key 池实时状态（整合主应用的 api_key_pool）。"""
    try:
        import sys
        import os
        main_backend = os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend")
        main_backend = os.path.abspath(main_backend)
        if main_backend not in sys.path:
            sys.path.insert(0, main_backend)
        from api_key_pool.pool import all_snapshots
        return {"ok": True, "pools": all_snapshots(), "connected": True}
    except Exception as exc:
        return {"ok": True, "pools": [], "connected": False, "error": str(exc)}


@router.get("/prompts")
def prompts_status(admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    """Prompt 模板列表（只读展示）。"""
    return {"ok": True, **read_prompts()}
