"""导入记录(import) CRUD + AI 状态管理。"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from store.pool import db_conn, _json


def insert_import(user_id: int, payload: dict[str, Any]) -> int:
    product = payload.get("product", {}) or {}
    skus = payload.get("skus", []) or []
    gallery = product.get("galleryImages", []) or []
    spec = payload.get("spec", {}) or {}
    videos = payload.get("videos", []) or []
    size = payload.get("size", {}) or {}
    with db_conn() as conn:
        seq_row = conn.execute(
            "UPDATE users SET import_seq = import_seq + 1 WHERE id = %s RETURNING import_seq",
            (user_id,),
        ).fetchone()
        user_seq = int(seq_row["import_seq"]) if seq_row else 0
        row = conn.execute(
            """
            INSERT INTO imports (
                user_id, goods_id, title, sku_count, image_count, raw_json,
                spec_json, video_json, size_json, platform, user_seq
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                user_id,
                payload.get("goodsId", "") or "",
                product.get("title", "") or "",
                len(skus),
                len(gallery),
                json.dumps(payload, ensure_ascii=False),
                json.dumps(spec, ensure_ascii=False),
                json.dumps(videos, ensure_ascii=False),
                json.dumps(size, ensure_ascii=False),
                (payload.get("platform") or "temu").strip().lower(),
                user_seq,
            ),
        ).fetchone()
    return int(row["id"])


def _row_to_import(row: dict[str, Any], compact: bool = True) -> dict[str, Any]:
    item = dict(row)
    raw = _json(item.get("raw_json"), {})
    item["generated_json"] = _json(item.get("generated_json"), [])
    item["vision_json"] = _json(item.get("vision_json"), {})
    item["step_logs"] = _json(item.get("step_logs"), {})
    item["spec_json"] = _json(item.get("spec_json"), {})
    item["video_json"] = _json(item.get("video_json"), [])
    item["size_json"] = _json(item.get("size_json"), {})
    item["raw_json"] = raw
    old_image_urls = raw.get("product", {}).get("oldImageUrls", []) if isinstance(raw, dict) else []
    gallery_images = raw.get("product", {}).get("galleryImages", []) if isinstance(raw, dict) else []
    item["gallery_images"] = old_image_urls or gallery_images
    if compact:
        item.pop("raw_json", None)
    if isinstance(item.get("created_at"), datetime):
        item["created_at"] = item["created_at"].strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(item.get("updated_at"), datetime):
        item["updated_at"] = item["updated_at"].strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(item.get("started_at"), datetime):
        item["started_at"] = item["started_at"].strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(item.get("finished_at"), datetime):
        item["finished_at"] = item["finished_at"].strftime("%Y-%m-%d %H:%M:%S")
    for key in ("step2_done", "step3_done", "step4_done", "exported"):
        item[key] = 1 if item.get(key) else 0
    item["ai_features"] = _json(item.get("ai_features"), [])
    owner_uid = item.pop("owner_uid", None) or ""
    seq = item.get("user_seq") or 0
    item["ref_code"] = f"{owner_uid}{seq}" if owner_uid else str(seq)
    return item


def list_imports(user_id: int, platform: str | None = None, exported: bool = False,
                error_box: bool = False, insufficient_box: bool = False) -> list[dict[str, Any]]:
    """列出某用户的导入记录。"""
    with db_conn() as conn:
        clauses = ["i.user_id = %s"]
        params: list = [user_id]
        if error_box:
            clauses.append("i.status = 'error'")
        elif insufficient_box:
            clauses.append("i.status = 'insufficient'")
        else:
            clauses.append("i.exported = %s")
            params.append(exported)
            clauses.append("i.status != 'error'")
            clauses.append("i.status != 'insufficient'")
        if platform:
            clauses.append("i.platform = %s")
            params.append(platform)
        where = " AND ".join(clauses)
        rows = conn.execute(
            f"""
            SELECT i.*, u.uid AS owner_uid
            FROM imports i
            JOIN users u ON u.id = i.user_id
            WHERE {where}
            ORDER BY i.id DESC
            """,
            tuple(params),
        ).fetchall()
    return [_row_to_import(dict(row), compact=True) for row in rows]


def mark_imports_exported(user_id: int, import_ids: list[int]) -> int:
    """把记录标记为已导出(归档), 返回实际更新的行数。"""
    if not import_ids:
        return 0
    with db_conn() as conn:
        cur = conn.execute(
            "UPDATE imports SET exported = TRUE, updated_at = now() "
            "WHERE user_id = %s AND id = ANY(%s) "
            "AND status NOT IN ('queued', 'generating')",
            (user_id, import_ids),
        )
    return cur.rowcount


def unmark_imports_exported(user_id: int, import_ids: list[int]) -> int:
    """把记录移回收采箱(取消已导出标记), 返回实际更新的行数。"""
    if not import_ids:
        return 0
    with db_conn() as conn:
        cur = conn.execute(
            "UPDATE imports SET exported = FALSE, updated_at = now() WHERE user_id = %s AND id = ANY(%s)",
            (user_id, import_ids),
        )
    return cur.rowcount


def get_import(user_id: int, import_id: int, compact: bool = False) -> dict[str, Any] | None:
    with db_conn() as conn:
        row = conn.execute(
            """
            SELECT i.*, u.uid AS owner_uid
            FROM imports i JOIN users u ON u.id = i.user_id
            WHERE i.user_id = %s AND i.id = %s
            """,
            (user_id, import_id),
        ).fetchone()
    return _row_to_import(dict(row), compact=compact) if row else None


def get_raw_import(user_id: int, import_id: int) -> dict[str, Any] | None:
    row = get_import(user_id, import_id)
    return row.get("raw_json") if row else None


def update_raw_import(user_id: int, import_id: int, raw_import: dict[str, Any]) -> None:
    with db_conn() as conn:
        conn.execute(
            "UPDATE imports SET raw_json = %s, updated_at = now() WHERE user_id = %s AND id = %s",
            (json.dumps(raw_import, ensure_ascii=False), user_id, import_id),
        )


def update_step2(user_id: int, import_id: int, cn_title: str, en_title: str) -> None:
    with db_conn() as conn:
        conn.execute(
            """
            UPDATE imports
            SET step2_done = TRUE, cn_title = %s, en_title = %s, updated_at = now()
            WHERE user_id = %s AND id = %s
            """,
            (cn_title, en_title, user_id, import_id),
        )


def update_step3_vision(user_id: int, import_id: int, vision_data: dict[str, Any], done: bool = True) -> None:
    with db_conn() as conn:
        conn.execute(
            """
            UPDATE imports
            SET step3_done = %s, vision_json = %s, updated_at = now()
            WHERE user_id = %s AND id = %s
            """,
            (done, json.dumps(vision_data, ensure_ascii=False), user_id, import_id),
        )


def update_step4(user_id: int, import_id: int, generated: list[dict[str, Any]], done: bool = True) -> None:
    with db_conn() as conn:
        conn.execute(
            """
            UPDATE imports
            SET step4_done = %s, generated_json = %s, updated_at = now()
            WHERE user_id = %s AND id = %s
            """,
            (done, json.dumps(generated, ensure_ascii=False), user_id, import_id),
        )


def append_generated_image(user_id: int, import_id: int, image_data: dict[str, Any]) -> None:
    """原子追加一张生成的图片到 generated_json(供前端实时展示)。"""
    one = json.dumps([image_data], ensure_ascii=False)
    with db_conn() as conn:
        conn.execute(
            """
            UPDATE imports
            SET generated_json = generated_json || %s::jsonb, updated_at = now()
            WHERE user_id = %s AND id = %s
            """,
            (one, user_id, import_id),
        )


def edit_ai_image(user_id: int, import_id: int, action: str,
                image_type: str = "", source_url: str = "") -> dict[str, Any] | None:
    """对 generated_json 做原地增删改,返回最新的 generated_json(供前端刷新)。"""
    with db_conn() as conn:
        row = conn.execute(
            "SELECT generated_json FROM imports WHERE user_id = %s AND id = %s FOR UPDATE",
            (user_id, import_id),
        ).fetchone()
        if row is None:
            return None
        generated = row.get("generated_json")
        if isinstance(generated, str):
            generated = json.loads(generated)
        if not isinstance(generated, list):
            generated = []

        if action == "promote":
            if not source_url:
                return None
            generated = [
                g for g in generated
                if not (g.get("source") == "manual_original"
                        and g.get("generated_image") == source_url)
            ]
            new_item = {
                "image_type": f"manual_{int(datetime.now().timestamp() * 1000)}",
                "generated_image": source_url,
                "oss_object_key": "",
                "source": "manual_original",
                "deleted": False,
            }
            generated = generated + [new_item]
        elif action in ("delete", "restore"):
            flag = action == "delete"
            found = False
            for g in generated:
                if g.get("image_type") == image_type:
                    g["deleted"] = flag
                    found = True
                    break
            if not found:
                return None
        else:
            return None

        conn.execute(
            """
            UPDATE imports
            SET generated_json = %s, updated_at = now()
            WHERE user_id = %s AND id = %s
            """,
            (json.dumps(generated, ensure_ascii=False), user_id, import_id),
        )
    return generated


def update_status(user_id: int, import_id: int, status: str, msg: str = "") -> None:
    with db_conn() as conn:
        conn.execute(
            """
            UPDATE imports
            SET status = %s, status_msg = %s, updated_at = now()
            WHERE user_id = %s AND id = %s
            """,
            (status, msg, user_id, import_id),
        )


def set_ai_features(user_id: int, import_id: int, features: list[str]) -> None:
    """记录本条 import 实际跑了哪些 AI 模块。"""
    with db_conn() as conn:
        conn.execute(
            """
            UPDATE imports SET ai_features = %s, updated_at = now()
            WHERE user_id = %s AND id = %s
            """,
            (json.dumps(features), user_id, import_id),
        )


def update_ai_settings(user_id: int, title_enabled: bool | None = None,
                       images_enabled: bool | None = None) -> dict[str, Any]:
    """更新用户的 AI 开关设置, 返回最新值。"""
    with db_conn() as conn:
        if title_enabled is not None:
            conn.execute("UPDATE users SET ai_title_enabled = %s WHERE id = %s", (title_enabled, user_id))
        if images_enabled is not None:
            conn.execute("UPDATE users SET ai_images_enabled = %s WHERE id = %s", (images_enabled, user_id))
        row = conn.execute(
            "SELECT ai_title_enabled, ai_images_enabled FROM users WHERE id = %s", (user_id,)
        ).fetchone()
    return {"ai_title_enabled": bool(row["ai_title_enabled"]) if row else False,
            "ai_images_enabled": bool(row["ai_images_enabled"]) if row else False}


def get_ai_settings(user_id: int) -> dict[str, Any]:
    """读用户的 AI 开关设置。"""
    with db_conn() as conn:
        row = conn.execute(
            "SELECT ai_title_enabled, ai_images_enabled FROM users WHERE id = %s", (user_id,)
        ).fetchone()
    return {"ai_title_enabled": bool(row["ai_title_enabled"]) if row else False,
            "ai_images_enabled": bool(row["ai_images_enabled"]) if row else False}


def update_videos(user_id: int, import_id: int, videos: list[dict[str, Any]]) -> None:
    with db_conn() as conn:
        conn.execute(
            """
            UPDATE imports
            SET video_json = %s, updated_at = now()
            WHERE user_id = %s AND id = %s
            """,
            (json.dumps(videos, ensure_ascii=False), user_id, import_id),
        )


def update_started_at(user_id: int, import_id: int) -> None:
    """记录任务真正开始执行的时刻(worker 抢到线程后调)。"""
    with db_conn() as conn:
        conn.execute(
            """
            UPDATE imports SET started_at = now(), updated_at = now()
            WHERE user_id = %s AND id = %s AND started_at IS NULL
            """,
            (user_id, import_id),
        )


def update_finished_at(user_id: int, import_id: int) -> None:
    with db_conn() as conn:
        conn.execute(
            """
            UPDATE imports
            SET finished_at = now(), updated_at = now()
            WHERE user_id = %s AND id = %s
            """,
            (user_id, import_id),
        )


def _record_step_atomic(
    user_id: int,
    import_id: int,
    step: str,
    status: str,
    input_data: dict[str, Any] | None = None,
    output_data: dict[str, Any] | None = None,
    error: str | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
    label: str | None = None,
) -> None:
    current = {
        "status": status,
        "started_at": started_at,
        "finished_at": finished_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "input": input_data or {},
        "output": output_data or {},
        "error": error or "",
        "label": label or step,
    }
    with db_conn() as conn:
        row = conn.execute(
            "SELECT step_logs FROM imports WHERE user_id = %s AND id = %s FOR UPDATE",
            (user_id, import_id),
        ).fetchone()
        if not row:
            return
        logs = _json(row.get("step_logs"), {})
        if not isinstance(logs, dict):
            logs = {}
        previous = logs.get(step, {}) if isinstance(logs.get(step, dict), dict) else {}
        history = previous.get("history", []) if isinstance(previous.get("history", []), list) else []
        history.append(dict(current))
        current["history"] = history[-20:]
        current["history_count"] = len(history)
        logs[step] = current
        conn.execute(
            "UPDATE imports SET step_logs = %s, updated_at = now() WHERE user_id = %s AND id = %s",
            (json.dumps(logs, ensure_ascii=False), user_id, import_id),
        )


def record_step(
    user_id: int,
    import_id: int,
    step: str,
    status: str,
    input_data: dict[str, Any] | None = None,
    output_data: dict[str, Any] | None = None,
    error: str | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
    label: str | None = None,
) -> None:
    """Append a step record to step_logs atomically (delegates to _record_step_atomic)."""
    _record_step_atomic(
        user_id, import_id, step, status,
        input_data=input_data, output_data=output_data,
        error=error, started_at=started_at, finished_at=finished_at,
        label=label,
    )


def delete_import(user_id: int, import_id: int) -> bool:
    """删除一条导入: 退还未结算冻结金豆 + 清 Redis 队列 + 删 DB。"""
    try:
        from billing.store import get_hold_amount_for_import, release_beans
        hold_amt = get_hold_amount_for_import(user_id, import_id)
        if hold_amt > 0:
            release_beans(user_id, import_id, hold_amt)
    except Exception as e:
        logging.getLogger("store").warning(
            "delete_import: release beans failed user=%s import=%s: %s",
            user_id, import_id, e)

    try:
        import pipeline_queue
        pipeline_queue.remove_from_queue(user_id, import_id)
    except Exception:
        pass

    with db_conn() as conn:
        cur = conn.execute(
            "DELETE FROM imports WHERE user_id = %s AND id = %s",
            (user_id, import_id),
        )
    return cur.rowcount > 0


def cleanup_stale_imports() -> int:
    """启动时清理: 卡在 generating 的(worker重启=没跑完)标记为 error。"""
    with db_conn() as conn:
        cur = conn.execute(
            "UPDATE imports SET status = 'error', status_msg = '处理中断，请重试', updated_at = now() "
            "WHERE status = 'generating'"
        )
        return cur.rowcount


def list_resumable_imports() -> list[dict[str, Any]]:
    """Imports left in a non-terminal status after a crash/restart."""
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT id, user_id FROM imports WHERE status IN ('queued', 'generating') ORDER BY id"
        ).fetchall()
    return [dict(row) for row in rows]


def get_products_for_pipeline(user_id: int, import_id: int) -> list[dict[str, Any]] | None:
    raw = get_raw_import(user_id, import_id)
    if not raw:
        return None
    product_data = raw.get("product", {}) or {}
    gallery = (product_data.get("galleryImages", []) or [])[:10]
    return [{
        "row": 2,
        "chinese_title": product_data.get("title", "") or "",
        "carousel_images": gallery,
        "old_image_urls": (product_data.get("oldImageUrls", []) or [])[:10],
    }]
