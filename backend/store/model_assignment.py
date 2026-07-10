"""用户模型 key 分配: 从 model_keys.json 抽取, 写入 user_model_assignments 表。"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from config import BACKEND_ROOT
from core.base import load_env, log
from store.pool import db_conn, _column_exists

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
                api_key TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMPTZ DEFAULT now(),
                UNIQUE(user_id, task_type)
            )
            """
        )


def _take_key_from_json() -> dict[str, str] | None:
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


def assign_models_to_user(user_id: int, env: dict[str, str] | None = None) -> bool:
    """注册成功后调用: 从 JSON 抽一组 key, 写入 user_model_assignments。"""
    env = env or load_env()
    _ensure_table()

    keys = _take_key_from_json()
    if keys is None:
        log(f"[WARN] model_keys.json 库存为空, 用户 {user_id} 未分配 key")
        _save_unassigned(user_id, env)
        return False

    # 从 .env 读 provider + model, 从 JSON 读 key
    assignments = _build_assignments(env, keys)
    with db_conn() as conn:
        for a in assignments:
            conn.execute(
                """
                INSERT INTO user_model_assignments (user_id, task_type, provider, model, api_key)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (user_id, task_type) DO UPDATE SET
                    provider = EXCLUDED.provider,
                    model = EXCLUDED.model,
                    api_key = EXCLUDED.api_key
                """,
                (user_id, a["task_type"], a["provider"], a["model"], a["api_key"]),
            )
        conn.execute("UPDATE users SET model_assigned = TRUE WHERE id = %s", (user_id,))
    log(f"用户 {user_id} 模型分配完成: {len(assignments)} 个任务")
    return True


def _build_assignments(env: dict[str, str], keys: dict[str, str]) -> list[dict[str, str]]:
    """组装: .env 读 provider+model, JSON 读 key。"""
    result = []
    for task, default_provider_env, key_field in [
        ("title", "TITLE_PROVIDER", "deepseek_key"),
        ("multimodal", "MULTIMODAL_PROVIDER", "aliyun_key"),
        ("image", "IMAGE_PROVIDER", "doubao_key"),
    ]:
        provider = env.get(default_provider_env, "").strip().lower()
        model = env.get(f"{task.upper()}_MODEL", "").strip()
        if not provider or not model:
            continue
        api_key = keys.get(key_field, "")
        result.append({
            "task_type": task,
            "provider": provider,
            "model": model,
            "api_key": api_key,
        })
    return result


def _save_unassigned(user_id: int, env: dict[str, str]) -> None:
    """库存为空时: 用 .env 默认 key 写入 (共享 key)。"""
    assignments = _build_assignments(env, {})
    with db_conn() as conn:
        for a in assignments:
            # 读平台默认 key
            platform_key = env.get(f"{a['provider'].upper()}_API_KEY", "")
            conn.execute(
                """
                INSERT INTO user_model_assignments (user_id, task_type, provider, model, api_key)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (user_id, task_type) DO UPDATE SET
                    provider = EXCLUDED.provider,
                    model = EXCLUDED.model,
                    api_key = EXCLUDED.api_key
                """,
                (user_id, a["task_type"], a["provider"], a["model"], platform_key),
            )
        conn.execute("UPDATE users SET model_assigned = TRUE WHERE id = %s", (user_id,))


def get_user_assignments(user_id: int) -> list[dict[str, Any]]:
    """读用户的模型分配。"""
    _ensure_table()
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT task_type, provider, model FROM user_model_assignments WHERE user_id = %s ORDER BY id",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_user_task_config(user_id: int, task: str, env: dict[str, str] | None = None) -> dict[str, str]:
    """pipeline 用: 读用户分配的 key, 没有就回退 .env。"""
    env = env or load_env()
    _ensure_table()
    with db_conn() as conn:
        row = conn.execute(
            "SELECT provider, model, api_key FROM user_model_assignments WHERE user_id = %s AND task_type = %s",
            (user_id, task),
        ).fetchone()

    if row and row["api_key"]:
        return {
            "provider": row["provider"],
            "model": row["model"],
            "model_type": "openai",
            "base_url": env.get(f"{row['provider'].upper()}_BASE_URL", ""),
            "api_key": row["api_key"],
        }

    # 回退: 用 .env 平台默认 key
    from store.model_config import get_task_config
    return get_task_config(task, env)
