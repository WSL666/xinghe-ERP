
"""Temu 平台 HTTP 路由(FastAPI APIRouter)。

挂在 /api/temu/* 前缀下。包含:
  POST /import        采集商品(浏览器插件调用)
  GET  /imports       列表
  GET  /imports/{id}  详情
  DELETE /imports/{id} 删除
  POST /bulk/delete   批量删除
  POST /bulk/export   批量导出(单xlsx,多链接连续)
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
    get_raw_import, get_user_by_api_key, get_user_by_id, init_db, mark_imports_exported,
    open_pool, unmark_imports_exported,
    insert_import, list_imports, update_status,
)

from platforms.temu.adapter import from_db_row, parse_product
from platforms.temu.export import to_xlsx as temu_export_xlsx, to_xlsx_batch as temu_export_xlsx_batch

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



def _content_disposition(filename: str) -> dict:
    """生成兼容中文文件名的 Content-Disposition 头。

    Starlette/HTTP 头只能 latin-1 编码, 中文文件名直接放 filename= 会崩。
    按 RFC 5987 用 filename*=UTF-8''<percent-encoded>, 同时给一个纯 ASCII
    的 filename= 兜底(旧浏览器/工具不认 filename* 时用)。
    """
    from urllib.parse import quote
    safe = filename.encode("ascii", "ignore").decode("ascii") or "export.xlsx"
    if not safe.endswith(".xlsx"):
        safe += ".xlsx"
    return {"Content-Disposition": f'attachment; filename="{safe}"; filename*=UTF-8\'\'{quote(filename)}'}


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

    # 产品属性(pid/vid/templatePid)统一在导出时用最新 attr_db 补全,
    # 入库只存采集端原始 propName/propValue/refPid, 避免存快照导致换库后老数据失效。

    # 金豆余额检查: 允许欠到-10, 余额 <= -10 时拒绝(防止无限欠费)
    try:
        from billing.store import get_beans
        beans = get_beans(int(user["id"]))
        if beans <= -10:
            raise _err("金豆不足，请充值后再试", 402)
    except _err:
        raise
    except Exception:
        pass  # billing 查询失败不阻断主流程

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
    exported = (request.query_params.get("exported") in {"1", "true", "yes"}) if request else False
    return _ok(imports=list_imports(int(user["id"]), platform, exported))


@router.get("/imports/{import_id}")
async def temu_get_import(import_id: int, request: Request,
                          user: dict[str, Any] = Depends(_current_user)) -> dict[str, Any]:
    compact = request.query_params.get("full") not in {"1", "true", "yes"}
    row = get_import(int(user["id"]), import_id, compact=compact)
    if not row:
        raise _err(f"import {import_id} not found", 404)
    return {"ok": True, "import": row}


@router.get("/imports/by-ref/{ref_code}")
async def temu_get_import_by_ref(ref_code: str, request: Request,
                                 user: dict[str, Any] = Depends(_current_user)) -> dict[str, Any]:
    """按 ref_code(如 aB3xK9mP2) 查询单条。

    ref_code = 用户uid + 序号。后端拆出 uid 和 seq, 定位到唯一一条。
    """
    ref_code = (ref_code or "").strip()
    if not ref_code:
        raise _err("ref_code 不能为空", 400)
    # 拆分: uid = 字母部分, seq = 末尾数字部分
    import re
    m = re.match(r"^(.+?)(\d+)$", ref_code)
    if not m:
        raise _err("ref_code 格式错误", 400)
    uid_part, seq_part = m.group(1), int(m.group(2))
    target_user = get_user_by_uid(uid_part)
    if not target_user:
        raise _err("用户ID不存在", 404)
    with __import__("store").db_conn() as conn:
        row = conn.execute(
            """SELECT * FROM imports WHERE user_id = %s AND user_seq = %s""",
            (int(target_user["id"]), seq_part),
        ).fetchone()
    if not row:
        raise _err("未找到该记录", 404)
    compact = request.query_params.get("full") not in {"1", "true", "yes"}
    data = get_import(int(target_user["id"]), int(row["id"]), compact=compact)
    return {"ok": True, "import": data}


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
    """批量导出:所有链接的 SKU 行连续写入同一个 xlsx(不压缩,不插空行)。"""
    ids = [int(i) for i in payload.get("ids", []) if str(i).strip().lstrip("-").isdigit()]
    if not ids:
        raise _err("no ids provided", 400)
    uid = int(user["id"])

    def _build_item(import_id: int) -> dict | tuple:
        """构建导出条目。只导出 status==done 的, 未完成的返回跳过标记。"""
        row = get_import(uid, import_id) or {}
        status = str(row.get("status", "") or "")
        if status != "done":
            return ("skip", import_id)
        raw_import = get_raw_import(uid, import_id)
        if not raw_import:
            return ("skip", import_id)
        cn = row.get("cn_title", "") or raw_import.get("product", {}).get("title", "")
        en = row.get("en_title", "")
        gj = row.get("generated_json", [])
        generated = gj if isinstance(gj, list) else []
        return {"raw_import": raw_import, "cn_title": cn, "en_title": en, "generated": generated}

    items = []
    skipped = []
    for import_id in ids:
        it = _build_item(import_id)
        if not it:
            continue
        if isinstance(it, tuple) and it[0] == "skip":
            skipped.append(import_id)
            continue
        items.append(it)
    if not items:
        raise _err("所选商品均未完成, 无法导出(请等待生图结束后再导)", 400)

    data = temu_export_xlsx_batch(items)
    # 文件名: 导出时间_平台名(条数条), 如 2026711955_TEMU(10条)
    from datetime import datetime
    now = datetime.now()
    ts = f"{now.year}{now.month}{now.day}{now:%H%M}"
    platform = ""
    for it in items:
        pf = (it.get("raw_import") or {}).get("platform") or ""
        if pf:
            platform = pf.upper()
            break
    if not platform:
        platform = "TEMU"
    filename = f"{ts}_{platform}_{len(items)}.xlsx"
    return StreamingResponse(
        iter([data]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=_content_disposition(filename),
    )


@router.post("/imports/bulk/mark-exported")
async def temu_bulk_mark_exported(payload: dict[str, Any],
                                  user: dict[str, Any] = Depends(_current_user)) -> dict[str, Any]:
    """用户在文件保存框点「确定」后才调: 把已完成的标记为已导出(归档)。

    只标记 status=done 的记录, 未完成的不会被归档。
    返回实际归档数, 供前端提示。
    """
    uid = int(user["id"])
    ids = [int(i) for i in payload.get("ids", []) if str(i).strip().lstrip("-").isdigit()]
    if not ids:
        raise _err("no ids provided", 400)
    marked = mark_imports_exported(uid, ids)  # store 层内部只更新 done 的
    return _ok(marked=marked, total=len(ids))


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
    # 归档动作由前端在文件保存确认后单独调 /imports/bulk/mark-exported
    # 文件名: 链接时间_链接id(ref_code), 如 2026711832_xTcarcjb6
    from datetime import datetime
    created_str = str(raw_import.get("createdAt") or "")
    link_ts = ""
    try:
        link_ts = f"{datetime.strptime(created_str, '%Y-%m-%d %H:%M:%S'):%Y%-m%-d%H%M}".replace("%-", "")
    except ValueError:
        pass
    if not link_ts:
        # createdAt 缺失或格式不符时用 DB 记录的 created_at 兜底
        try:
            ca = str(row.get("created_at") or "")
            dt = datetime.strptime(ca, "%Y-%m-%d %H:%M:%S")
            link_ts = f"{dt.year}{dt.month}{dt.day}{dt:%H%M}"
        except ValueError:
            link_ts = created_str.replace("-", "").replace(":", "").replace(" ", "")[:12]
    ref_code = str(row.get("ref_code") or import_id)
    filename = f"{link_ts}_{ref_code}.xlsx"
    return StreamingResponse(
        iter([data]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=_content_disposition(filename),
    )


@router.post("/imports/{import_id}/mark-exported")
async def temu_mark_exported(import_id: int,
                             user: dict[str, Any] = Depends(_current_user)) -> dict[str, Any]:
    """单个导出: 用户文件保存确认后调用, 标记为已导出(只标记 done 的)。"""
    uid = int(user["id"])
    marked = mark_imports_exported(uid, [import_id])
    return _ok(import_id=import_id, marked=marked)


@router.post("/imports/{import_id}/unexport")
async def temu_unexport(import_id: int,
                        user: dict[str, Any] = Depends(_current_user)) -> dict[str, Any]:
    """把已导出(归档)的记录移回收采箱。"""
    uid = int(user["id"])
    if not get_import(uid, import_id):
        raise _err(f"import {import_id} not found", 404)
    unmark_imports_exported(uid, [import_id])
    return _ok(import_id=import_id, exported=False)


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
