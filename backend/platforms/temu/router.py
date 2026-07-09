
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

import json
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse


import mq.redis_queue as pipeline_queue
from security import create_session_token, load_session_token
from store import (
    close_pool, delete_import, get_import, get_or_create_dev_user,
    get_raw_import, get_user_by_api_key, get_user_by_id, get_user_by_uid, init_db, mark_imports_exported,
    open_pool, unmark_imports_exported,
    insert_import, list_imports, update_raw_import, update_status, edit_ai_image,
    set_ai_features, update_ai_settings, get_ai_settings,
)

from platforms.temu.adapter import from_db_row, parse_product
from platforms.temu.export import to_xlsx as temu_export_xlsx, to_xlsx_batch as temu_export_xlsx_batch

from mq.orchestrator import run_auto_pipeline

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

    payload = {**payload, "platform": "temu"}
    uid = int(user["id"])
    import_id = insert_import(uid, payload)

    # 采集免费无限: 存库后 status=collected, 不扣费不入队
    update_status(uid, import_id, "collected", "采集完成")

    # 检查用户开了哪些 AI 模块 → 自动入队
    ai_cfg = get_ai_settings(uid)
    features = []
    if ai_cfg.get("ai_title_enabled"):
        features.append("title")
    if ai_cfg.get("ai_images_enabled"):
        features.append("images")

    from billing.store import hold_amount_for as _hold_amt, hold_beans, get_available_beans

    if features:
        hold_amount = _hold_amt(features)
        try:
            avail = get_available_beans(uid)
        except Exception:
            avail = 1
        held = hold_beans(uid, hold_amount, import_id)
        if held:
            set_ai_features(uid, import_id, features)
            run_auto_pipeline(uid, import_id)
        else:
            update_status(uid, import_id, "insufficient", f"金豆不足(需{hold_amount})")

    try:
        avail_after = get_available_beans(uid)
    except Exception:
        avail_after = None
    return _ok(
        import_id=import_id,
        title=product_data.get("title", ""),
        sku_count=len(skus),
        total_images=len((product_data.get("galleryImages", []) or [])[:10]),
        status="collected",
        available=avail_after,
    )


@router.get("/imports")
async def temu_list_imports(platform: str | None = None, request: Request = None,
                            user: dict[str, Any] = Depends(_current_user)) -> dict[str, Any]:
    exported = (request.query_params.get("exported") in {"1", "true", "yes"}) if request else False
    error_box = (request.query_params.get("error") in {"1", "true", "yes"}) if request else False
    insufficient_box = (request.query_params.get("insufficient") in {"1", "true", "yes"}) if request else False
    # 错误箱/余额不足箱 跨平台汇总, 不按 platform 过滤
    pf = None if (error_box or insufficient_box) else platform
    return _ok(imports=list_imports(int(user["id"]), pf, exported, error_box, insufficient_box))


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

    ref_code = 用户uid + 序号。后端按固定长度拆出 uid 和 seq, 定位到唯一一条。
    uid 固定 8 位(见 store.generate_uid), 但其字符集含数字, 不能用正则贪心
    切分末尾数字(会把 uid 末尾的数字误归给 seq, 甚至越权定位到别的用户)。
    """
    ref_code = (ref_code or "").strip()
    if not ref_code:
        raise _err("ref_code 不能为空", 400)
    # uid 固定 8 位, 其后才是序号
    if len(ref_code) <= 8 or not ref_code[8:].isdigit():
        raise _err("ref_code 格式错误", 400)
    uid_part, seq_part = ref_code[:8], int(ref_code[8:])
    target_user = get_user_by_uid(uid_part)
    if not target_user:
        raise _err("用户ID不存在", 404)
    # 归属校验: ref_code 指向的用户必须就是当前登录用户本人, 防止越权读取他人采集详情。
    if int(target_user["id"]) != int(user["id"]):
        raise _err("无权访问该记录", 403)
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
    shop_config = payload.get("shopConfig") or {}

    def _build_item(import_id: int) -> dict | tuple:
        """构建导出条目。只导出 status==done 的, 未完成的返回跳过标记。"""
        row = get_import(uid, import_id) or {}
        status = str(row.get("status", "") or "")
        if status != "done":
            return ("skip", import_id)
        raw_import = get_raw_import(uid, import_id)
        if not raw_import:
            return ("skip", import_id)
        if shop_config:
            raw_import = dict(raw_import)
            raw_import["shopConfig"] = shop_config
            update_raw_import(uid, import_id, raw_import)
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
                      payload: dict[str, Any] | None = Body(default=None),
                      user: dict[str, Any] = Depends(_current_user)) -> StreamingResponse:
    uid = int(user["id"])
    raw_import = get_raw_import(uid, import_id)
    if not raw_import:
        raise _err(f"import {import_id} not found", 404)
    shop_config = ((payload or {}).get("shopConfig")) or {}
    if shop_config:
        raw_import = dict(raw_import)
        raw_import["shopConfig"] = shop_config
        update_raw_import(uid, import_id, raw_import)
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
        _d = datetime.strptime(created_str, '%Y-%m-%d %H:%M:%S')
        link_ts = f"{_d.year}{_d.month}{_d.day}{_d:%H%M}"
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


@router.post("/imports/{import_id}/restore")
async def temu_restore(import_id: int,
                       user: dict[str, Any] = Depends(_current_user)) -> dict[str, Any]:
    """把错误箱里的记录移回收采箱: 仅重置状态为 pending(不自动重跑)。

    用户在采集箱里可再手动点「整体生成」触发流水线。
    """
    uid = int(user["id"])
    if not get_import(uid, import_id):
        raise _err(f"import {import_id} not found", 404)
    update_status(uid, import_id, "collected", "restored")
    return _ok(import_id=import_id, status="collected")


@router.post("/imports/{import_id}/ai-image/promote")
async def temu_ai_image_promote(import_id: int, payload: dict[str, Any],
                                user: dict[str, Any] = Depends(_current_user)) -> dict[str, Any]:
    """把一张原图提升为成品图(追加进 generated_json, 标记 manual_original)。"""
    uid = int(user["id"])
    if not get_import(uid, import_id):
        raise _err(f"import {import_id} not found", 404)
    source_url = str(payload.get("source_url", "") or "").strip()
    if not source_url:
        raise _err("source_url is required", 400)
    generated = edit_ai_image(uid, import_id, "promote", source_url=source_url)
    if generated is None:
        raise _err("promote failed", 400)
    return _ok(import_id=import_id, generated=generated)


@router.post("/imports/{import_id}/ai-image/delete")
async def temu_ai_image_delete(import_id: int, payload: dict[str, Any],
                               user: dict[str, Any] = Depends(_current_user)) -> dict[str, Any]:
    """软删除一张 AI 成品图(置 deleted=true)。"""
    uid = int(user["id"])
    if not get_import(uid, import_id):
        raise _err(f"import {import_id} not found", 404)
    image_type = str(payload.get("image_type", "") or "").strip()
    if not image_type:
        raise _err("image_type is required", 400)
    generated = edit_ai_image(uid, import_id, "delete", image_type=image_type)
    if generated is None:
        raise _err("image not found", 404)
    return _ok(import_id=import_id, generated=generated)


@router.post("/imports/{import_id}/ai-image/restore")
async def temu_ai_image_restore(import_id: int, payload: dict[str, Any],
                                user: dict[str, Any] = Depends(_current_user)) -> dict[str, Any]:
    """还原一张已软删的 AI 成品图(清 deleted)。"""
    uid = int(user["id"])
    if not get_import(uid, import_id):
        raise _err(f"import {import_id} not found", 404)
    image_type = str(payload.get("image_type", "") or "").strip()
    if not image_type:
        raise _err("image_type is required", 400)
    generated = edit_ai_image(uid, import_id, "restore", image_type=image_type)
    if generated is None:
        raise _err("image not found", 404)
    return _ok(import_id=import_id, generated=generated)


@router.post("/imports/{import_id}/ai-run")
async def temu_ai_run(import_id: int,
                      payload: dict[str, Any] = Body(default=None),
                      user: dict[str, Any] = Depends(_current_user)) -> dict[str, Any]:
    """手动触发某条链接的 AI 处理。features: ["title"] / ["images"] / ["title","images"]。"""
    uid = int(user["id"])
    if not get_import(uid, import_id):
        raise _err(f"import {import_id} not found", 404)
    features = (payload or {}).get("features") or []
    if not features:
        raise _err("未选择 AI 功能", 400)

    from billing.store import hold_amount_for, hold_beans, get_available_beans, reset_billing_for_import
    # 重新触发前清除旧计费记录, 让新一轮 hold→settle 正常走
    reset_billing_for_import(uid, import_id)
    hold_amount = hold_amount_for(features)
    avail = get_available_beans(uid)
    held = hold_beans(uid, hold_amount, import_id)
    if not held:
        update_status(uid, import_id, "insufficient", f"金豆不足(需{hold_amount})")
        raise _err(f"金豆不足，需冻结{hold_amount}金豆，当前可用{avail}", 402)

    set_ai_features(uid, import_id, features)
    run_auto_pipeline(uid, import_id)
    try:
        avail_after = get_available_beans(uid)
    except Exception:
        avail_after = None
    return _ok(import_id=import_id, features=features, status="queued", available=avail_after)


@router.get("/ai-settings")
async def temu_ai_settings_get(user: dict[str, Any] = Depends(_current_user)) -> dict[str, Any]:
    """读用户的 AI 开关设置。"""
    return _ok(**get_ai_settings(int(user["id"])))


@router.post("/ai-settings")
async def temu_ai_settings_set(payload: dict[str, Any] = Body(default=None),
                               user: dict[str, Any] = Depends(_current_user)) -> dict[str, Any]:
    """更新用户的 AI 开关设置。payload: {ai_title_enabled: bool, ai_images_enabled: bool}。"""
    uid = int(user["id"])
    title = (payload or {}).get("ai_title_enabled")
    images = (payload or {}).get("ai_images_enabled")
    result = update_ai_settings(uid,
                                title_enabled=bool(title) if title is not None else None,
                                images_enabled=bool(images) if images is not None else None)
    return _ok(**result)


@router.get("/health")
async def temu_health() -> dict[str, Any]:
    return _ok(status="healthy")


def _ensure_plugin_zip() -> Path:
    """确保采集插件 zip 是最新源码打包的(进程级缓存 + 源码变更检测)。

    机制:
      - 进程内存缓存 zip 路径, 命中则直接发文件(下载瞬间完成)。
      - 命中时额外比对"源码目录最新 mtime"与"zip 文件 mtime":
        源码有更新(改了 popup.js/html 等) → 自动重新打包, 无需重启服务。
      - 重启后缓存丢失, 首次下载重新打包(用最新源码)。
    即: 改了插件代码永远下载到最新版, 不用手动打包, 不用重启服务。
    """
    import io
    import zipfile as zf
    from config import APP_ROOT

    collector_dir = APP_ROOT / "collector" / "temu-collector"
    if not collector_dir.is_dir():
        raise _err("采集插件目录不存在", 404)
    zip_path = collector_dir.parent / "temu-collector.zip"

    # 源码最新修改时间(用于检测是否需要重新打包)
    src_mtime = max(
        (f.stat().st_mtime for f in collector_dir.rglob("*") if f.is_file()),
        default=0.0,
    )
    cached = getattr(_ensure_plugin_zip, "_path", None)
    if cached == zip_path and zip_path.is_file():
        # 缓存命中: 仅当源码比 zip 更新时才重新打包
        if zip_path.stat().st_mtime >= src_mtime:
            return cached

    # (重新)打包
    buf = io.BytesIO()
    with zf.ZipFile(buf, "w", zf.ZIP_DEFLATED) as zipf:
        for file_path in collector_dir.rglob("*"):
            if not file_path.is_file():
                continue
            # zip 内用相对路径, 保持扁平结构(Chrome 加载时直接指向文件夹)
            arcname = file_path.relative_to(collector_dir)
            zipf.write(file_path, arcname)
    zip_path.write_bytes(buf.getvalue())
    _ensure_plugin_zip._path = zip_path
    return zip_path


@router.get(
    "/plugin/download",
    response_class=FileResponse,
)
async def temu_plugin_download(user: dict[str, Any] = Depends(_current_user)) -> StreamingResponse:
    """发送采集插件 zip 供用户下载。

    zip 由 _ensure_plugin_zip() 在首次请求时打包一次并缓存为静态文件,
    此后每次下载只是发送该文件, 不再重复打包。既保证下载瞬间完成,
    又无需在部署时手动同步 zip 文件。
    """
    zip_path = _ensure_plugin_zip()
    if not zip_path:
        raise _err("采集插件目录不存在", 404)

    # 压缩包文件名动态带版本号: 通快商品采集助手{version}.zip
    # version 来自 manifest.json, 改插件时记得把 manifest version +0.5
    from config import APP_ROOT as _root
    _manifest_path = _root / "collector" / "temu-collector" / "manifest.json"
    _ver = "1.0"
    try:
        _ver = json.loads(_manifest_path.read_text(encoding="utf-8")).get("version", "1.0")
    except Exception:
        pass
    filename = f"通快商品采集助手{_ver}.zip"
    from urllib.parse import quote
    safe = filename.encode("ascii", "ignore").decode("ascii") or "plugin.zip"
    headers = {
        "Content-Disposition": f'attachment; filename="{safe}"; filename*=UTF-8\'\'{quote(filename)}',
    }
    return FileResponse(
        path=str(zip_path),
        media_type="application/zip",
        headers=headers,
    )
