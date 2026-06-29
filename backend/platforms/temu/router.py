
"""Temu 平台 HTTP 路由(FastAPI APIRouter)。

挂在 /api/temu/* 前缀下。包含:
  POST /import        采集商品(浏览器插件调用)
  GET  /imports       列表
  GET  /imports/{id}  详情
  DELETE /imports/{id} 删除
  POST /bulk/delete   批量删除
  POST /bulk/export   批量导出(zip)
  POST /imports/{id}/export  单个导出
  POST /imports/{id}/generate 手动重跑流水线

新增 1688 时,写自己的 router.py,挂 /api/1688/*,逻辑类似但可不同。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

import pipeline_queue
from security import create_session_token, load_session_token
from store import (
    close_pool, delete_import, get_import, get_or_create_dev_user,
    get_raw_import, get_user_by_api_key, get_user_by_id, init_db, open_pool,
    insert_import, list_imports, update_status,
)

from platforms.temu.adapter import from_db_row, parse_product
from platforms.temu.export import to_xlsx as temu_export_xlsx

from orchestrator import run_auto_pipeline

router = APIRouter(prefix="/api/temu", tags=["temu"])


def _ok(**payload: Any) -> dict[str, Any]:
    return {"ok": True, **payload}


def _err(message: str, status_code: int = 400) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"ok": False, "error": message})


async def _current_user(request: Request) -> dict[str, Any]:
    settings = __import__("config").get_settings()
    token = request.cookies.get(settings.session_cookie_name)
    session_data = load_session_token(token)
    if not session_data:
        raise _err("not authenticated", 401)
    user = get_user_by_id(int(session_data["uid"]))
    if not user or not user.get("is_active"):
        raise _err("not authenticated", 401)
    return user


async def _plugin_user(request: Request) -> dict[str, Any]:
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise _err("missing plugin api key", 401)
    user = get_user_by_api_key(token.strip())
    if not user:
        raise _err("plugin api key is invalid", 401)
    if not user.get("is_active"):
        raise _err("account disabled", 403)
    return user


@router.post("/import")
async def temu_import(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    """插件采集入口:存库 → 入队。支持 session 登录或 API Key。"""
    user = None
    settings = __import__("config").get_settings()
    token = request.cookies.get(settings.session_cookie_name)
    if token:
        user = await _current_user(request)
    else:
        user = await _plugin_user(request)

    product_data = payload.get("product", {}) or {}
    skus = payload.get("skus")
    if not product_data or not skus:
        raise _err("missing product or skus", 400)
    payload = {**payload, "platform": "temu"}
    import_id = insert_import(int(user["id"]), payload)
    run_auto_pipeline(int(user["id"]), import_id)
    return _ok(
        import_id=import_id,
        title=product_data.get("title", ""),
        sku_count=len(skus),
        total_images=len((product_data.get("galleryImages", []) or [])[:10]),
        status="queued",
    )


@router.get("/imports")
async def temu_list_imports(platform: str | None = None, request: Request = None,
                            user: dict[str, Any] = Depends(_current_user)) -> dict[str, Any]:
    return _ok(imports=list_imports(int(user["id"]), "temu"))


@router.get("/imports/{import_id}")
async def temu_get_import(import_id: int, request: Request,
                          user: dict[str, Any] = Depends(_current_user)) -> dict[str, Any]:
    compact = request.query_params.get("full") not in {"1", "true", "yes"}
    row = get_import(int(user["id"]), import_id, compact=compact)
    if not row:
        raise _err(f"import {import_id} not found", 404)
    return {"ok": True, "import": row}


@router.delete("/imports/{import_id}")
async def temu_delete_import(import_id: int, user: dict[str, Any] = Depends(_current_user)) -> dict[str, Any]:
    if not delete_import(int(user["id"]), import_id):
        raise _err(f"import {import_id} not found", 404)
    return _ok(deleted=import_id)


@router.post("/imports/bulk/delete")
async def temu_bulk_delete(payload: dict[str, Any],
                           user: dict[str, Any] = Depends(_current_user)) -> dict[str, Any]:
    ids = [int(i) for i in payload.get("ids", []) if str(i).strip().lstrip("-").isdigit()]
    if not ids:
        raise _err("no ids provided", 400)
    uid = int(user["id"])
    deleted, missing = 0, []
    for import_id in ids:
        if delete_import(uid, import_id):
            deleted += 1
        else:
            missing.append(import_id)
    return _ok(deleted=deleted, missing=missing)


@router.post("/imports/bulk/export")
async def temu_bulk_export(payload: dict[str, Any],
                           user: dict[str, Any] = Depends(_current_user)) -> StreamingResponse:
    import io
    import zipfile

    ids = [int(i) for i in payload.get("ids", []) if str(i).strip().lstrip("-").isdigit()]
    if not ids:
        raise _err("no ids provided", 400)
    uid = int(user["id"])

    def _build_one(import_id: int) -> bytes | None:
        raw_import = get_raw_import(uid, import_id)
        if not raw_import:
            return None
        row = get_import(uid, import_id) or {}
        cn = row.get("cn_title", "") or raw_import.get("product", {}).get("title", "")
        en = row.get("en_title", "")
        gj = row.get("generated_json", [])
        generated = gj if isinstance(gj, list) else []
        return temu_export_xlsx(raw_import, cn, en, generated)

    if len(ids) == 1:
        import_id = ids[0]
        data = _build_one(import_id)
        if data is None:
            raise _err(f"import {import_id} not found", 404)
        filename = f"final_result_{uid}_{import_id}.xlsx"
        return StreamingResponse(
            iter([data]),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for import_id in ids:
            data = _build_one(import_id)
            if data:
                zf.writestr(f"final_result_{uid}_{import_id}.xlsx", data)
    buf.seek(0)
    return StreamingResponse(
        buf, media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="exports.zip"'},
    )


@router.post("/imports/{import_id}/export")
async def temu_export(import_id: int,
                      user: dict[str, Any] = Depends(_current_user)) -> StreamingResponse:
    uid = int(user["id"])
    raw_import = get_raw_import(uid, import_id)
    if not raw_import:
        raise _err(f"import {import_id} not found", 404)
    row = get_import(uid, import_id) or {}
    cn = row.get("cn_title", "") or raw_import.get("product", {}).get("title", "")
    en = row.get("en_title", "")
    gj = row.get("generated_json", [])
    generated = gj if isinstance(gj, list) else []
    data = temu_export_xlsx(raw_import, cn, en, generated)
    filename = f"final_result_{uid}_{import_id}.xlsx"
    return StreamingResponse(
        iter([data]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/imports/{import_id}/generate")
async def temu_generate(import_id: int,
                        user: dict[str, Any] = Depends(_current_user)) -> dict[str, Any]:
    uid = int(user["id"])
    if not get_import(uid, import_id):
        raise _err(f"import {import_id} not found", 404)
    run_auto_pipeline(uid, import_id)
    return _ok(import_id=import_id, status="queued")


@router.get("/health")
async def temu_health() -> dict[str, Any]:
    return _ok(status="healthy")
