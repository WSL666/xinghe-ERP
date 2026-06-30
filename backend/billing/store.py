"""金豆读写层:余额查询/扣减/增加 + 消费记录表。

扣减用原子 SQL(UPDATE ... WHERE beans >= amount RETURNING),避免超扣。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from store import db_conn


def init_billing_tables() -> None:
    """建金豆消费记录表(幂等)。在应用启动时调一次。"""
    with db_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bean_transactions (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                amount INTEGER NOT NULL,
                balance_after INTEGER NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                import_id BIGINT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_bean_tx_user ON bean_transactions(user_id, created_at DESC)"
        )
        # 老库兼容: 补 import_id 列(CREATE TABLE IF NOT EXISTS 不会给已存在的表加列)
        conn.execute(
            "ALTER TABLE bean_transactions ADD COLUMN IF NOT EXISTS import_id BIGINT"
        )
        # 幂等防重扣: 同一 import_id 的消费记录唯一。
        # 部分唯一索引(仅当 import_id NOT NULL 且 amount < 0),避免充值/import_id=NULL 受限。
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_bean_charge_import "
            "ON bean_transactions (import_id) WHERE import_id IS NOT NULL AND amount < 0"
        )


def get_beans(user_id: int) -> int:
    """查余额。"""
    with db_conn() as conn:
        row = conn.execute("SELECT beans FROM users WHERE id = %s", (user_id,)).fetchone()
    return int(row["beans"]) if row else 0


def charge_beans(user_id: int, amount: int, reason: str = "", import_id: int | None = None) -> dict[str, Any] | None:
    """扣减金豆(原子,允许欠到-10)。成功返回 {balance_after}, 余额不足返回 None。

    幂等: 若带 import_id 且该 import 已扣过费, 直接返回当前余额(不重复扣)。
    这能根治"队列重复 -> 任务重跑 -> 重复扣费"。
    WHERE beans - amount >= -10: 扣完后余额不低于 -10。
    """
    if amount <= 0:
        return None
    BEANS_FLOOR = -10
    with db_conn() as conn:
        # 幂等检查: 同一 import 是否已扣过
        if import_id is not None:
            dup = conn.execute(
                "SELECT 1 FROM bean_transactions WHERE import_id = %s AND amount < 0 LIMIT 1",
                (import_id,),
            ).fetchone()
            if dup:
                row = conn.execute("SELECT beans FROM users WHERE id = %s", (user_id,)).fetchone()
                return {"balance_after": int(row["beans"]) if row else 0, "dedup": True}
        row = conn.execute(
            """
            UPDATE users SET beans = beans - %s, updated_at = now()
            WHERE id = %s AND beans - %s >= %s
            RETURNING beans
            """,
            (amount, user_id, amount, BEANS_FLOOR),
        ).fetchone()
        if not row:
            return None
        balance = int(row["beans"])
        conn.execute(
            """
            INSERT INTO bean_transactions (user_id, amount, balance_after, reason, import_id)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (user_id, -amount, balance, reason or "消费", import_id),
        )
    return {"balance_after": balance}


def add_beans(user_id: int, amount: int, reason: str = "充值") -> dict[str, Any] | None:
    """增加金豆(管理员充值用)。成功返回 {balance_after}。"""
    if amount <= 0:
        return None
    with db_conn() as conn:
        row = conn.execute(
            """
            UPDATE users SET beans = beans + %s, updated_at = now()
            WHERE id = %s
            RETURNING beans
            """,
            (amount, user_id),
        ).fetchone()
        if not row:
            return None
        balance = int(row["beans"])
        conn.execute(
            """
            INSERT INTO bean_transactions (user_id, amount, balance_after, reason)
            VALUES (%s, %s, %s, %s)
            """,
            (user_id, amount, balance, reason),
        )
    return {"balance_after": balance}


def list_transactions(user_id: int, limit: int = 20) -> list[dict[str, Any]]:
    """查消费/充值记录(最近的 limit 条)。"""
    with db_conn() as conn:
        rows = conn.execute(
            """
            SELECT t.amount, t.balance_after, t.reason, t.created_at,
                   t.import_id, i.user_seq, u.uid AS owner_uid
            FROM bean_transactions t
            LEFT JOIN imports i ON i.id = t.import_id
            LEFT JOIN users u ON u.id = t.user_id
            WHERE t.user_id = %s
            ORDER BY t.created_at DESC
            LIMIT %s
            """,
            (user_id, limit),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        seq = d.get("user_seq")
        uid = d.get("owner_uid") or ""
        d["ref_code"] = f"{uid}{seq}" if (uid and seq) else ""
        out.append(d)
    return out
