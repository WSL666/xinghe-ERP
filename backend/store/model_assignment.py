"""用户模型 key 分配: 从 model_keys.json 抽取, 写入 user_model_assignments 表。

JSON 结构 (每组含完整配置, 跟 .env 彻底解耦):
[
  {
    "title": [{"provider":"deepseek","model":"...","base_url":"...","api_key":"..."}],
    "multimodal": [{"provider":"aliyun","model":"...","base_url":"...","api_key":"..."}],
    "image": [{"provider":"doubao","model":"...","base_url":"...","api_key":"..."}]
  }
]
"""
from __future__ import annotations

import json
import threading
from typing import Any

from config import BACKEND_ROOT
from core.base import log
from store.pool import db_conn

_JSON_PATH = BACKEND_ROOT / "model_keys.json"
_lock = threading.Lock()


def _ensure_table() -> None:
    with db_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_model_assignments (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                task_type TEXT NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                base_url TEXT NOT NULL DEFAULT '',
                api_key TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMPTZ DEFAULT now(),
                UNIQUE(user_id, task_type)
            )
            """
        )


def _take_key_from_json() -> dict[str, list[dict]] | None:
    """从 model_keys.json 取第一个, 删掉, 写回。线程安全。"""
    with _lock:
        if not _JSON_PATH.exists():
            return None
        try:
            data = json.loads(_JSON_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError):
            return None
        if not isinstance(data, list) or not data:
            return None
        item = data.pop(0)
        _JSON_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"从 model_keys.json 分配一组 key, 剩余 {len(data)} 组")
        return item


def assign_models_to_user(user_id: int) -> bool:
    """注册成功后调用: 从 JSON 抽一组, 写入 user_model_assignments。"""
    _ensure_table()

    keys = _take_key_from_json()
    if keys is None:
        log(f"[WARN] model_keys.json 库存为空, 用户 {user_id} 未分配 key")
        return False

    with db_conn() as conn:
        for task_type in ("title", "multimodal", "image"):
            models = keys.get(task_type, [])
            if not models:
                continue
            m = models[0]
            conn.execute(
                """
                INSERT INTO user_model_assignments (user_id, task_type, provider, model, base_url, api_key)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (user_id, task_type) DO UPDATE SET
                    provider = EXCLUDED.provider,
                    model = EXCLUDED.model,
                    base_url = EXCLUDED.base_url,
                    api_key = EXCLUDED.api_key
                """,
                (user_id, task_type, m["provider"], m["model"], m["base_url"], m["api_key"]),
            )
        conn.execute("UPDATE users SET model_assigned = TRUE WHERE id = %s", (user_id,))
    log(f"用户 {user_id} 模型分配完成")
    return True


def get_user_assignments(user_id: int) -> list[dict[str, Any]]:
    """读用户的模型分配 (不含 api_key, 供前端展示)。"""
    _ensure_table()
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT task_type, provider, model FROM user_model_assignments WHERE user_id = %s ORDER BY id",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_user_task_config(user_id: int, task: str) -> dict[str, str]:
    """pipeline 用: 读用户分配的完整配置。"""
    _ensure_table()
    with db_conn() as conn:
        row = conn.execute(
            "SELECT provider, model, base_url, api_key FROM user_model_assignments WHERE user_id = %s AND task_type = %s",
            (user_id, task),
        ).fetchone()

    if row and row["api_key"]:
        return {
            "provider": row["provider"],
            "model": row["model"],
            "model_type": "openai",
            "base_url": row["base_url"],
            "api_key": row["api_key"],
        }

    # 回退: 用 .env
    from store.model_config import get_task_config
    return get_task_config(task)
