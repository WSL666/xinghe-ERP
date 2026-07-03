"""超管专用查询层。

与主应用共享同一个 PostgreSQL（连接池模式与主应用一致），
但这里的查询都是「跨用户/跨企业」的全局聚合视角，且带审计日志写入。
"""
from __future__ import annotations

import json
import logging
import secrets
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from config import get_settings
from deps_hash import hash_password, verify_password

_pool: Optional[ConnectionPool] = None

logger = logging.getLogger("admin.store")


def _configure_connection(conn: psycopg.Connection) -> None:
    conn.row_factory = dict_row


def open_pool() -> None:
    global _pool
    if _pool is not None:
        return
    settings = get_settings()
    logger.info("admin opening database pool (%s)", settings.database_url)
    _pool = ConnectionPool(
        conninfo=settings.database_url,
        min_size=1,
        max_size=8,
        timeout=30,
        max_idle=300,
        configure=_configure_connection,
    )
    # 快速探测，连接失败立即抛真实错误
    with psycopg.connect(settings.database_url, connect_timeout=5) as conn:
        conn.execute("SELECT 1").fetchone()


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def db_conn() -> Iterator[psycopg.Connection]:
    if _pool is None:
        raise RuntimeError("admin pool is not open; call open_pool() at startup")
    conn = _pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _pool.putconn(conn)


def _column_exists(conn: psycopg.Connection, table: str, column: str) -> bool:
    return conn.execute(
        """
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = %s AND column_name = %s
        """,
        (table, column),
    ).fetchone() is not None


def init_db() -> None:
    """建超管专用表 + 给主应用表增量加列。幂等。"""
    with db_conn() as conn:
        # ── 超管账号表 ──
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS platform_admins (
                id BIGSERIAL PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                display_name TEXT NOT NULL DEFAULT '',
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                last_login_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        # ── 审计日志 ──
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS admin_audit_logs (
                id BIGSERIAL PRIMARY KEY,
                admin_id BIGINT REFERENCES platform_admins(id) ON DELETE SET NULL,
                action TEXT NOT NULL DEFAULT '',
                target_type TEXT NOT NULL DEFAULT '',
                target_id TEXT NOT NULL DEFAULT '',
                detail JSONB NOT NULL DEFAULT '{}'::jsonb,
                ip TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_admin ON admin_audit_logs(admin_id, created_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_created ON admin_audit_logs(created_at DESC)"
        )
        # ── 充值订单 ──
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS recharge_orders (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
                enterprise_id BIGINT,
                amount_beans INTEGER NOT NULL,
                amount_cny NUMERIC(10,2) NOT NULL DEFAULT 0,
                pay_method TEXT NOT NULL DEFAULT 'manual',
                status TEXT NOT NULL DEFAULT 'done',
                note TEXT NOT NULL DEFAULT '',
                operator_id BIGINT REFERENCES platform_admins(id) ON DELETE SET NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        # ── 定价配置 ──
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pricing_configs (
                id BIGSERIAL PRIMARY KEY,
                platform TEXT NOT NULL DEFAULT '',
                step TEXT NOT NULL DEFAULT '',
                cost_beans INTEGER NOT NULL DEFAULT 1,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE(platform, step)
            )
            """
        )
        # ── 功能开关 ──
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS feature_flags (
                id BIGSERIAL PRIMARY KEY,
                key TEXT NOT NULL UNIQUE,
                value_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                description TEXT NOT NULL DEFAULT '',
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        # ── 系统公告 ──
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS announcements (
                id BIGSERIAL PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'draft',
                published_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        # ── 给主应用表增量加列（不破坏结构）──
        if not _column_exists(conn, "users", "is_frozen"):
            conn.execute(
                "ALTER TABLE users ADD COLUMN is_frozen BOOLEAN NOT NULL DEFAULT FALSE"
            )
        if not _column_exists(conn, "enterprises", "is_frozen"):
            conn.execute(
                "ALTER TABLE enterprises ADD COLUMN is_frozen BOOLEAN NOT NULL DEFAULT FALSE"
            )
        if not _column_exists(conn, "enterprises", "plan_type"):
            conn.execute(
                "ALTER TABLE enterprises ADD COLUMN plan_type TEXT NOT NULL DEFAULT 'free'"
            )
        if not _column_exists(conn, "users", "is_deleted"):
            conn.execute("ALTER TABLE users ADD COLUMN is_deleted BOOLEAN NOT NULL DEFAULT FALSE")
        if not _column_exists(conn, "users", "deleted_at"):
            conn.execute("ALTER TABLE users ADD COLUMN deleted_at TIMESTAMPTZ")


# ────────────────────────────────────────────
# 超管账号
# ────────────────────────────────────────────

def get_admin_by_username(username: str) -> dict[str, Any] | None:
    with db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM platform_admins WHERE username = %s", (username,)
        ).fetchone()
    return dict(row) if row else None


def get_admin_by_id(admin_id: int) -> dict[str, Any] | None:
    with db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM platform_admins WHERE id = %s", (admin_id,)
        ).fetchone()
    return dict(row) if row else None


def ensure_default_admin() -> None:
    """首次启动自动创建初始超管账号。"""
    settings = get_settings()
    existing = get_admin_by_username(settings.admin_default_username)
    if existing:
        return
    with db_conn() as conn:
        conn.execute(
            """
            INSERT INTO platform_admins (username, password_hash, display_name)
            VALUES (%s, %s, %s)
            """,
            (
                settings.admin_default_username,
                hash_password(settings.admin_default_password),
                "超级管理员",
            ),
        )
    logger.info("created default admin: %s", settings.admin_default_username)


def update_admin_last_login(admin_id: int) -> None:
    with db_conn() as conn:
        conn.execute(
            "UPDATE platform_admins SET last_login_at = now() WHERE id = %s",
            (admin_id,),
        )


def verify_admin_credentials(username: str, password: str) -> dict[str, Any] | None:
    admin = get_admin_by_username(username)
    if not admin or not admin.get("is_active"):
        return None
    if not verify_password(password, admin["password_hash"]):
        return None
    return admin


# ────────────────────────────────────────────
# 审计日志
# ────────────────────────────────────────────

def write_audit(
    admin_id: int,
    action: str,
    target_type: str = "",
    target_id: str = "",
    detail: dict[str, Any] | None = None,
    ip: str = "",
) -> None:
    with db_conn() as conn:
        conn.execute(
            """
            INSERT INTO admin_audit_logs (admin_id, action, target_type, target_id, detail, ip)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                admin_id,
                action,
                target_type,
                str(target_id),
                json.dumps(detail or {}, ensure_ascii=False),
                ip,
            ),
        )


def list_audit_logs(limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    with db_conn() as conn:
        rows = conn.execute(
            """
            SELECT a.*, m.username AS admin_name
            FROM admin_audit_logs a
            LEFT JOIN platform_admins m ON m.id = a.admin_id
            ORDER BY a.created_at DESC
            LIMIT %s OFFSET %s
            """,
            (limit, offset),
        ).fetchall()
    return [dict(r) for r in rows]


def count_audit_logs() -> int:
    with db_conn() as conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM admin_audit_logs").fetchone()
    return int(row["c"]) if row else 0


# ────────────────────────────────────────────
# 驾驶舱统计
# ────────────────────────────────────────────

def dashboard_overview() -> dict[str, Any]:
    """全平台核心指标聚合，一次查询拿全。"""
    with db_conn() as conn:
        users = conn.execute("SELECT COUNT(*) AS c FROM users WHERE is_deleted = FALSE").fetchone()["c"]
        enterprises = conn.execute("SELECT COUNT(*) AS c FROM enterprises").fetchone()["c"]
        today = conn.execute(
            """
            SELECT
                COUNT(*) FILTER (WHERE created_at >= current_date) AS today_imports,
                COUNT(*) FILTER (WHERE status = 'done') AS total_done,
                COUNT(*) FILTER (WHERE status = 'error') AS total_error,
                COUNT(*) FILTER (WHERE status IN ('queued','running','translating','generating','pending')) AS in_progress,
                COUNT(*) AS total_imports,
                COUNT(*) FILTER (WHERE created_at >= current_date AND status = 'done') AS today_done,
                COUNT(*) FILTER (WHERE created_at >= current_date AND status = 'error') AS today_error,
                COUNT(*) FILTER (WHERE created_at >= current_date AND status IN ('queued','running','translating','generating','pending')) AS today_running
            FROM imports
            """
        ).fetchone()
        beans = conn.execute(
            """
            SELECT
                COALESCE(SUM(amount) FILTER (WHERE amount > 0), 0) AS recharge_total,
                COALESCE(SUM(amount) FILTER (WHERE amount < 0), 0) AS consume_total
            FROM bean_transactions
            """
        ).fetchone()
    return {
        "users": int(users),
        "enterprises": int(enterprises),
        "today_imports": int(today["today_imports"]),
        "total_imports": int(today["total_imports"]),
        "total_done": int(today["total_done"]),
        "total_error": int(today["total_error"]),
        "in_progress": int(today["in_progress"]),
        "today_done": int(today["today_done"]),
        "today_error": int(today["today_error"]),
        "today_running": int(today["today_running"]),
        "recharge_beans": int(beans["recharge_total"]),
        "consume_beans": abs(int(beans["consume_total"])),
    }


# ────────────────────────────────────────────
# 用户管理（跨企业全局）
# ────────────────────────────────────────────

def list_users(
    keyword: str = "",
    status: str = "",
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict[str, Any]], int]:
    """全平台用户列表 + 每人统计。"""
    conditions = []
    params: list[Any] = []
    if keyword:
        conditions.append("(u.account ILIKE %s OR u.uid ILIKE %s)")
        params.extend([f"%{keyword}%", f"%{keyword}%"])
    if status == "deleted":
        conditions.append("u.is_deleted = TRUE")
    elif status == "active":
        conditions.append("u.is_deleted = FALSE")
        conditions.append("u.is_active = TRUE")
        conditions.append("u.is_frozen = FALSE")
    elif status == "frozen":
        conditions.append("u.is_frozen = TRUE")
        conditions.append("u.is_deleted = FALSE")
    elif status == "disabled":
        conditions.append("u.is_active = FALSE")
        conditions.append("u.is_deleted = FALSE")
    else:
        conditions.append("u.is_deleted = FALSE")
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    offset = (page - 1) * page_size
    with db_conn() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) AS c FROM users u {where}", params
        ).fetchone()["c"]
        rows = conn.execute(
            f"""
            SELECT u.id, u.account, u.uid, u.display_name, u.beans,
                   u.is_active, u.is_frozen, u.is_deleted, u.role, u.enterprise_id,
                   e.name AS enterprise_name,
                   u.created_at, u.updated_at,
                   COUNT(imp.id) AS import_count,
                   COUNT(imp.id) FILTER (WHERE imp.status = 'done') AS done_count,
                   COALESCE(SUM(bt.amount) FILTER (WHERE bt.amount < 0), 0) AS consumed_beans
            FROM users u
            LEFT JOIN enterprises e ON e.id = u.enterprise_id
            LEFT JOIN imports imp ON imp.user_id = u.id
            LEFT JOIN bean_transactions bt ON bt.user_id = u.id
            {where}
            GROUP BY u.id, e.name
            ORDER BY u.created_at DESC
            LIMIT %s OFFSET %s
            """,
            params + [page_size, offset],
        ).fetchall()
    users = []
    for r in rows:
        d = dict(r)
        for k in ("created_at", "updated_at"):
            if isinstance(d.get(k), datetime):
                d[k] = d[k].strftime("%Y-%m-%d %H:%M:%S")
        users.append(d)
    return users, int(total)


def get_user_detail(user_id: int) -> dict[str, Any] | None:
    with db_conn() as conn:
        row = conn.execute(
            """
            SELECT u.*, e.name AS enterprise_name
            FROM users u
            LEFT JOIN enterprises e ON e.id = u.enterprise_id
            WHERE u.id = %s
            """,
            (user_id,),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        for k in ("created_at", "updated_at", "last_login_at"):
            if isinstance(d.get(k), datetime):
                d[k] = d[k].strftime("%Y-%m-%d %H:%M:%S")
        # 最近任务
        tasks = conn.execute(
            """
            SELECT id, title, status, status_msg, platform, created_at, updated_at
            FROM imports WHERE user_id = %s
            ORDER BY created_at DESC LIMIT 20
            """,
            (user_id,),
        ).fetchall()
        d["recent_tasks"] = [_fmt_task(t) for t in tasks]
        # 金豆流水
        txs = conn.execute(
            """
            SELECT amount, balance_after, reason, created_at, import_id
            FROM bean_transactions WHERE user_id = %s
            ORDER BY created_at DESC LIMIT 20
            """,
            (user_id,),
        ).fetchall()
        d["recent_transactions"] = [_fmt_tx(t) for t in txs]
    return d


def get_user_full_profile(user_id: int, tab: str = "info", page: int = 1, page_size: int = 20) -> dict[str, Any] | None:
    """用户完整档案：基本信息 + 任务/流水/订单全量分页。"""
    with db_conn() as conn:
        row = conn.execute(
            """
            SELECT u.*, e.name AS enterprise_name
            FROM users u
            LEFT JOIN enterprises e ON e.id = u.enterprise_id
            WHERE u.id = %s
            """,
            (user_id,),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        for k in ("created_at", "updated_at", "last_login_at"):
            if isinstance(d.get(k), datetime):
                d[k] = d[k].strftime("%Y-%m-%d %H:%M:%S")

        result: dict[str, Any] = {"user": d}

        offset = (page - 1) * page_size

        if tab == "tasks":
            total = conn.execute(
                "SELECT COUNT(*) AS c FROM imports WHERE user_id = %s", (user_id,)
            ).fetchone()["c"]
            rows = conn.execute(
                """
                SELECT id, title, cn_title, status, status_msg, platform,
                       created_at, started_at, finished_at, user_seq,
                       image_count, sku_count
                FROM imports WHERE user_id = %s
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
                """,
                (user_id, page_size, offset),
            ).fetchall()
            result["tasks"] = [_fmt_task(t) for t in rows]
            result["total"] = int(total)

        elif tab == "transactions":
            total = conn.execute(
                "SELECT COUNT(*) AS c FROM bean_transactions WHERE user_id = %s", (user_id,)
            ).fetchone()["c"]
            rows = conn.execute(
                """
                SELECT amount, balance_after, reason, created_at, import_id
                FROM bean_transactions WHERE user_id = %s
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
                """,
                (user_id, page_size, offset),
            ).fetchall()
            result["transactions"] = [_fmt_tx(t) for t in rows]
            result["total"] = int(total)

        elif tab == "orders":
            total = conn.execute(
                "SELECT COUNT(*) AS c FROM recharge_orders WHERE user_id = %s", (user_id,)
            ).fetchone()["c"]
            rows = conn.execute(
                """
                SELECT r.id, r.amount_beans, r.pay_method, r.note, r.created_at,
                       r.operator_id, pa.username AS operator_name
                FROM recharge_orders r
                LEFT JOIN platform_admins pa ON pa.id = r.operator_id
                WHERE r.user_id = %s
                ORDER BY r.created_at DESC
                LIMIT %s OFFSET %s
                """,
                (user_id, page_size, offset),
            ).fetchall()
            orders = []
            for r in rows:
                od = dict(r)
                if isinstance(od.get("created_at"), datetime):
                    od["created_at"] = od["created_at"].strftime("%Y-%m-%d %H:%M:%S")
                orders.append(od)
            result["orders"] = orders
            result["total"] = int(total)

        else:
            # info tab: 统计汇总
            stats = conn.execute(
                """
                SELECT
                  COUNT(*) AS task_total,
                  COUNT(*) FILTER (WHERE status = 'done') AS task_done,
                  COUNT(*) FILTER (WHERE status = 'error') AS task_error,
                  COALESCE(SUM(CASE WHEN amount < 0 THEN ABS(amount) ELSE 0 END), 0) AS total_consume,
                  COALESCE(SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END), 0) AS total_recharge
                FROM imports imp
                FULL OUTER JOIN bean_transactions bt ON bt.import_id = imp.id AND bt.user_id = %s
                WHERE imp.user_id = %s OR bt.user_id = %s
                """,
                (user_id, user_id, user_id),
            ).fetchone()
            result["stats"] = dict(stats) if stats else {}
            result["total"] = 1

        result["page"] = page
        result["page_size"] = page_size
        return result


def set_user_frozen(user_id: int, frozen: bool) -> bool:
    with db_conn() as conn:
        cur = conn.execute(
            "UPDATE users SET is_frozen = %s, updated_at = now() WHERE id = %s",
            (frozen, user_id),
        )
        return cur.rowcount > 0


def set_user_active(user_id: int, active: bool) -> bool:
    with db_conn() as conn:
        cur = conn.execute(
            "UPDATE users SET is_active = %s, updated_at = now() WHERE id = %s",
            (active, user_id),
        )
        return cur.rowcount > 0


def delete_user(user_id: int) -> dict[str, Any] | None:
    """软删除用户：标记 is_deleted + 禁用登录 + 释放手机号(允许重新注册)。

    所有关联数据(任务/金豆/图片/视频)完整保留，超管可查看历史。
    账号改为 'deleted_{id}_{原账号}' 释放原手机号，允许重新注册。
    """
    with db_conn() as conn:
        row = conn.execute(
            """SELECT id, account, beans, is_deleted FROM users WHERE id = %s""",
            (user_id,),
        ).fetchone()
        if not row or row.get("is_deleted"):
            return None
        info = dict(row)
        orig_account = row["account"]
        conn.execute(
            """UPDATE users SET is_deleted = TRUE, is_active = FALSE,
               account = %s, deleted_at = now(), updated_at = now() WHERE id = %s""",
            (f"deleted_{user_id}_{orig_account}", user_id),
        )
    info["original_account"] = orig_account
    return info


def admin_edit_ai_image(import_id: int, action: str, image_type: str = "", source_url: str = "") -> list[dict[str, Any]] | None:
    """超管对任意任务的 generated_json 做增删改(不限 user_id)。

    action:
      - "promote":  把一张原图 URL 追加为成品图(标记 source=manual_original)
      - "delete":   软删除某张 AI 图(置 deleted=true)
      - "restore":  还原某张已软删的 AI 图(清 deleted)
    返回最新的 generated_json 列表。
    """
    with db_conn() as conn:
        row = conn.execute(
            "SELECT generated_json FROM imports WHERE id = %s FOR UPDATE",
            (import_id,),
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
            "UPDATE imports SET generated_json = %s, updated_at = now() WHERE id = %s",
            (json.dumps(generated, ensure_ascii=False), import_id),
        )
    return generated


def admin_recharge_beans(user_id: int, amount: int, operator_id: int, note: str = "") -> dict[str, Any] | None:
    """超管给用户充值金豆，同时记录订单 + bean_transactions。"""
    if amount <= 0:
        return None
    with db_conn() as conn:
        row = conn.execute(
            "UPDATE users SET beans = beans + %s, updated_at = now() WHERE id = %s RETURNING beans",
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
            (user_id, amount, balance, f"超管充值: {note}" if note else "超管充值"),
        )
        conn.execute(
            """
            INSERT INTO recharge_orders (user_id, amount_beans, pay_method, note, operator_id)
            VALUES (%s, %s, 'manual', %s, %s)
            """,
            (user_id, amount, note, operator_id),
        )
    return {"balance_after": balance}


# ────────────────────────────────────────────
# 企业管理
# ────────────────────────────────────────────

def list_enterprises(keyword: str = "", status: str = "", page: int = 1, page_size: int = 20) -> tuple[list[dict[str, Any]], int]:
    conditions = []
    params: list[Any] = []
    if keyword:
        conditions.append("(e.name ILIKE %s OR e.display_name ILIKE %s)")
        params.extend([f"%{keyword}%", f"%{keyword}%"])
    if status:
        conditions.append("e.status = %s")
        params.append(status)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    offset = (page - 1) * page_size
    with db_conn() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) AS c FROM enterprises e {where}", params
        ).fetchone()["c"]
        rows = conn.execute(
            f"""
            SELECT e.id, e.name, e.display_name, e.status, e.is_frozen, e.plan_type,
                   e.contact_name, e.contact_phone, e.invite_code, e.created_at,
                   COUNT(DISTINCT m.user_id) AS member_count,
                   COUNT(imp.id) AS import_count
            FROM enterprises e
            LEFT JOIN enterprise_members m ON m.enterprise_id = e.id
            LEFT JOIN users u ON u.enterprise_id = e.id
            LEFT JOIN imports imp ON imp.user_id = u.id
            {where}
            GROUP BY e.id
            ORDER BY e.created_at DESC
            LIMIT %s OFFSET %s
            """,
            params + [page_size, offset],
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if isinstance(d.get("created_at"), datetime):
            d["created_at"] = d["created_at"].strftime("%Y-%m-%d %H:%M:%S")
        out.append(d)
    return out, int(total)


def set_enterprise_frozen(enterprise_id: int, frozen: bool) -> bool:
    with db_conn() as conn:
        cur = conn.execute(
            "UPDATE enterprises SET is_frozen = %s, updated_at = now() WHERE id = %s",
            (frozen, enterprise_id),
        )
        return cur.rowcount > 0


def set_enterprise_status(enterprise_id: int, status: str) -> bool:
    with db_conn() as conn:
        cur = conn.execute(
            "UPDATE enterprises SET status = %s, updated_at = now() WHERE id = %s",
            (status, enterprise_id),
        )
        return cur.rowcount > 0


# ────────────────────────────────────────────
# 任务监控（全平台）
# ────────────────────────────────────────────

def list_all_tasks(
    platform: str = "",
    status: str = "",
    keyword: str = "",
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict[str, Any]], int]:
    conditions = []
    params: list[Any] = []
    if platform:
        conditions.append("imp.platform = %s")
        params.append(platform)
    if status:
        statuses = [s.strip() for s in status.split(",") if s.strip()]
        if len(statuses) == 1:
            conditions.append("imp.status = %s")
            params.append(statuses[0])
        else:
            placeholders = ",".join(["%s"] * len(statuses))
            conditions.append(f"imp.status IN ({placeholders})")
            params.extend(statuses)
    if keyword:
        conditions.append("(imp.title ILIKE %s OR imp.goods_id ILIKE %s)")
        params.extend([f"%{keyword}%", f"%{keyword}%"])
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    offset = (page - 1) * page_size
    with db_conn() as conn:
        total = conn.execute(
            f"""
            SELECT COUNT(*) AS c FROM imports imp {where}
            """,
            params,
        ).fetchone()["c"]
        rows = conn.execute(
            f"""
            SELECT imp.id, imp.title, imp.status, imp.status_msg, imp.platform,
                   imp.user_seq, imp.created_at, imp.updated_at,
                   u.account, u.uid, u.display_name,
                   e.name AS enterprise_name
            FROM imports imp
            JOIN users u ON u.id = imp.user_id
            LEFT JOIN enterprises e ON e.id = u.enterprise_id
            {where}
            ORDER BY imp.created_at DESC
            LIMIT %s OFFSET %s
            """,
            params + [page_size, offset],
        ).fetchall()
    return [_fmt_task(r) for r in rows], int(total)


# ────────────────────────────────────────────
# 辅助
# ────────────────────────────────────────────

def _fmt_task(row: dict[str, Any]) -> dict[str, Any]:
    d = dict(row)
    for k in ("created_at", "updated_at", "started_at", "finished_at"):
        if isinstance(d.get(k), datetime):
            d[k] = d[k].strftime("%Y-%m-%d %H:%M:%S")
    return d


def _fmt_tx(row: dict[str, Any]) -> dict[str, Any]:
    d = dict(row)
    if isinstance(d.get("created_at"), datetime):
        d["created_at"] = d["created_at"].strftime("%Y-%m-%d %H:%M:%S")
    return d


# ────────────────────────────────────────────
# 富格式任务查询（与主应用 _row_to_import 同模型，跨用户全局视角）
# ────────────────────────────────────────────

def _row_to_import_rich(row: dict[str, Any]) -> dict[str, Any]:
    """把 imports 行转成前端富表格需要的完整结构（图片/规格/尺寸/状态）。"""
    import json as _json

    def _j(value, fallback):
        if value is None:
            return fallback
        if isinstance(value, (dict, list)):
            return value
        try:
            return _json.loads(value)
        except (TypeError, _json.JSONDecodeError):
            return fallback

    item = dict(row)
    raw = _j(item.get("raw_json"), {})
    item["generated_json"] = _j(item.get("generated_json"), [])
    item["vision_json"] = _j(item.get("vision_json"), {})
    item["step_logs"] = _j(item.get("step_logs"), {})
    item["spec_json"] = _j(item.get("spec_json"), {})
    item["video_json"] = _j(item.get("video_json"), [])
    item["size_json"] = _j(item.get("size_json"), {})
    old_image_urls = raw.get("product", {}).get("oldImageUrls", []) if isinstance(raw, dict) else []
    gallery_images = raw.get("product", {}).get("galleryImages", []) if isinstance(raw, dict) else []
    item["gallery_images"] = old_image_urls or gallery_images
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
    owner_uid = item.pop("owner_uid", None) or ""
    seq = item.get("user_seq") or 0
    item["ref_code"] = f"{owner_uid}{seq}" if owner_uid else str(seq)
    return item


def list_all_tasks_rich(
    platform: str = "",
    status: str = "",
    keyword: str = "",
    account: str = "",
    ref_code: str = "",
    date_from: str = "",
    date_to: str = "",
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict[str, Any]], int]:
    """富格式全平台任务列表（含图片/规格/尺寸，供前端富表格渲染）。"""
    conditions = []
    params: list[Any] = []
    if platform:
        conditions.append("imp.platform = %s")
        params.append(platform)
    if status:
        statuses = [s.strip() for s in status.split(",") if s.strip()]
        if len(statuses) == 1:
            conditions.append("imp.status = %s")
            params.append(statuses[0])
        else:
            placeholders = ",".join(["%s"] * len(statuses))
            conditions.append(f"imp.status IN ({placeholders})")
            params.extend(statuses)
    if keyword:
        conditions.append("(imp.title ILIKE %s OR imp.goods_id ILIKE %s OR imp.cn_title ILIKE %s OR imp.status_msg ILIKE %s)")
        params.extend([f"%{keyword}%", f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"])
    if account:
        conditions.append("(u.account ILIKE %s OR u.uid ILIKE %s OR u.display_name ILIKE %s)")
        params.extend([f"%{account}%", f"%{account}%", f"%{account}%"])
    if ref_code:
        # ref_code = uid + user_seq, 拆分匹配
        conditions.append("(u.uid || imp.user_seq::text = %s)")
        params.append(ref_code)
    if date_from:
        conditions.append("imp.created_at >= %s")
        params.append(date_from)
    if date_to:
        conditions.append("imp.created_at <= %s")
        params.append(date_to + " 23:59:59")
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    offset = (page - 1) * page_size
    with db_conn() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) AS c FROM imports imp JOIN users u ON u.id = imp.user_id {where}", params
        ).fetchone()["c"]
        rows = conn.execute(
            f"""
            SELECT imp.*, u.uid AS owner_uid, u.account, u.display_name,
                   e.name AS enterprise_name,
                   COALESCE(bt.bean_cost, 0) AS bean_cost
            FROM imports imp
            JOIN users u ON u.id = imp.user_id
            LEFT JOIN enterprises e ON e.id = u.enterprise_id
            LEFT JOIN (
                SELECT import_id, COALESCE(SUM(ABS(amount)), 0) AS bean_cost
                FROM bean_transactions WHERE amount < 0 AND import_id IS NOT NULL
                GROUP BY import_id
            ) bt ON bt.import_id = imp.id
            {where}
            ORDER BY imp.created_at DESC
            LIMIT %s OFFSET %s
            """,
            params + [page_size, offset],
        ).fetchall()
    items = []
    for row in rows:
        d = _row_to_import_rich(dict(row))
        d["account"] = row["account"]
        d["display_name"] = row["display_name"]
        d["enterprise_name"] = row["enterprise_name"]
        d["bean_cost"] = int(row["bean_cost"]) if "bean_cost" in row.keys() else 0
        items.append(d)
    return items, int(total)


def get_task_detail(import_id: int) -> dict[str, Any] | None:
    """单个任务的完整详情（富格式，含全部 JSON 字段）。"""
    with db_conn() as conn:
        row = conn.execute(
            """
            SELECT imp.*, u.uid AS owner_uid, u.account, u.display_name,
                   e.name AS enterprise_name,
                   COALESCE(bt.bean_cost, 0) AS bean_cost
            FROM imports imp
            JOIN users u ON u.id = imp.user_id
            LEFT JOIN enterprises e ON e.id = u.enterprise_id
            LEFT JOIN (
                SELECT import_id, COALESCE(SUM(ABS(amount)), 0) AS bean_cost
                FROM bean_transactions WHERE amount < 0 AND import_id IS NOT NULL
                GROUP BY import_id
            ) bt ON bt.import_id = imp.id
            WHERE imp.id = %s
            """,
            (import_id,),
        ).fetchone()
    if not row:
        return None
    d = _row_to_import_rich(dict(row))
    d["account"] = row["account"]
    d["display_name"] = row["display_name"]
    d["enterprise_name"] = row["enterprise_name"]
    d["bean_cost"] = int(row["bean_cost"]) if "bean_cost" in row.keys() else 0
    return d


# ────────────────────────────────────────────
# 企业成员管理
# ────────────────────────────────────────────

def list_enterprise_members(enterprise_id: int) -> list[dict[str, Any]]:
    """某企业全部成员 + 每人任务统计。"""
    with db_conn() as conn:
        rows = conn.execute(
            """
            SELECT u.id, u.account, u.uid, u.display_name, m.role, m.status,
                   m.joined_at, u.beans, u.is_active, u.is_frozen,
                   COUNT(imp.id) AS import_count,
                   COUNT(imp.id) FILTER (WHERE imp.status = 'done') AS success_count,
                   COUNT(imp.id) FILTER (WHERE imp.status = 'error') AS error_count,
                   MAX(imp.updated_at) AS last_active_at
            FROM enterprise_members m
            JOIN users u ON u.id = m.user_id
            LEFT JOIN imports imp ON imp.user_id = u.id
            WHERE m.enterprise_id = %s
            GROUP BY u.id, m.role, m.status, m.joined_at
            ORDER BY m.joined_at
            """,
            (enterprise_id,),
        ).fetchall()
    members = []
    for row in rows:
        d = dict(row)
        for k in ("joined_at", "last_active_at"):
            if isinstance(d.get(k), datetime):
                d[k] = d[k].strftime("%Y-%m-%d %H:%M:%S")
            elif not d.get(k):
                d[k] = ""
        members.append(d)
    return members


def get_enterprise_detail(enterprise_id: int) -> dict[str, Any] | None:
    with db_conn() as conn:
        row = conn.execute(
            """
            SELECT e.*, COUNT(DISTINCT m.user_id) AS member_count
            FROM enterprises e
            LEFT JOIN enterprise_members m ON m.enterprise_id = e.id
            WHERE e.id = %s
            GROUP BY e.id
            """,
            (enterprise_id,),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    if isinstance(d.get("created_at"), datetime):
        d["created_at"] = d["created_at"].strftime("%Y-%m-%d %H:%M:%S")
    return d


# ────────────────────────────────────────────
# 金豆流水与财务统计
# ────────────────────────────────────────────

def list_all_transactions(
    user_id: int | None = None,
    direction: str = "",
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[dict[str, Any]], int]:
    """全平台金豆流水查询。direction: recharge(>0) / consume(<0) / 空=全部。"""
    conditions = []
    params: list[Any] = []
    if user_id:
        conditions.append("t.user_id = %s")
        params.append(user_id)
    if direction == "recharge":
        conditions.append("t.amount > 0")
    elif direction == "consume":
        conditions.append("t.amount < 0")
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    offset = (page - 1) * page_size
    with db_conn() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) AS c FROM bean_transactions t {where}", params
        ).fetchone()["c"]
        rows = conn.execute(
            f"""
            SELECT t.id, t.user_id, t.amount, t.balance_after, t.reason,
                   t.import_id, t.created_at,
                   u.account, u.uid, u.display_name,
                   e.name AS enterprise_name
            FROM bean_transactions t
            JOIN users u ON u.id = t.user_id
            LEFT JOIN enterprises e ON e.id = u.enterprise_id
            {where}
            ORDER BY t.created_at DESC
            LIMIT %s OFFSET %s
            """,
            params + [page_size, offset],
        ).fetchall()
    out = []
    for row in rows:
        d = dict(row)
        if isinstance(d.get("created_at"), datetime):
            d["created_at"] = d["created_at"].strftime("%Y-%m-%d %H:%M:%S")
        out.append(d)
    return out, int(total)


def list_recharge_orders(
    page: int = 1, page_size: int = 50
) -> tuple[list[dict[str, Any]], int]:
    """充值订单列表。"""
    offset = (page - 1) * page_size
    with db_conn() as conn:
        total = conn.execute("SELECT COUNT(*) AS c FROM recharge_orders").fetchone()["c"]
        rows = conn.execute(
            """
            SELECT r.id, r.user_id, r.amount_beans, r.amount_cny, r.pay_method,
                   r.status, r.note, r.created_at,
                   u.account, u.uid, u.display_name,
                   pa.username AS operator_name
            FROM recharge_orders r
            LEFT JOIN users u ON u.id = r.user_id
            LEFT JOIN platform_admins pa ON pa.id = r.operator_id
            ORDER BY r.created_at DESC
            LIMIT %s OFFSET %s
            """,
            (page_size, offset),
        ).fetchall()
    out = []
    for row in rows:
        d = dict(row)
        if isinstance(d.get("created_at"), datetime):
            d["created_at"] = d["created_at"].strftime("%Y-%m-%d %H:%M:%S")
        out.append(d)
    return out, int(total)


def billing_summary() -> dict[str, Any]:
    """财务总览：充值总额、消费总额、净额、订单数、今日数据。"""
    with db_conn() as conn:
        row = conn.execute(
            """
            SELECT
                COALESCE(SUM(amount) FILTER (WHERE amount > 0), 0) AS recharge_beans,
                COALESCE(SUM(amount) FILTER (WHERE amount < 0), 0) AS consume_beans,
                COUNT(*) AS tx_count,
                COUNT(*) FILTER (WHERE amount > 0) AS recharge_count,
                COUNT(*) FILTER (WHERE amount < 0) AS consume_count,
                COALESCE(SUM(amount) FILTER (WHERE amount > 0 AND created_at >= current_date), 0) AS today_recharge,
                COALESCE(SUM(amount) FILTER (WHERE amount < 0 AND created_at >= current_date), 0) AS today_consume
            FROM bean_transactions
            """
        ).fetchone()
        orders = conn.execute(
            """
            SELECT COUNT(*) AS count,
                   COALESCE(SUM(amount_beans), 0) AS total_beans
            FROM recharge_orders
            """
        ).fetchone()
    return {
        "recharge_beans": int(row["recharge_beans"]),
        "consume_beans": abs(int(row["consume_beans"])),
        "net_beans": int(row["recharge_beans"]) + int(row["consume_beans"]),
        "tx_count": int(row["tx_count"]),
        "recharge_count": int(row["recharge_count"]),
        "consume_count": int(row["consume_count"]),
        "today_recharge": int(row["today_recharge"]),
        "today_consume": abs(int(row["today_consume"])),
        "order_count": int(orders["count"]),
        "order_total_beans": int(orders["total_beans"]),
    }


def enterprise_consume_ranking(limit: int = 10) -> list[dict[str, Any]]:
    """企业消费金豆排行（Top N）。"""
    with db_conn() as conn:
        rows = conn.execute(
            """
            SELECT e.id, e.name, e.display_name,
                   COALESCE(SUM(bt.amount) FILTER (WHERE bt.amount < 0), 0) AS consumed,
                   COUNT(DISTINCT u.id) AS user_count
            FROM enterprises e
            LEFT JOIN users u ON u.enterprise_id = e.id
            LEFT JOIN bean_transactions bt ON bt.user_id = u.id
            GROUP BY e.id, e.name, e.display_name
            ORDER BY consumed ASC
            LIMIT %s
            """
            ,
            (limit,),
        ).fetchall()
    out = []
    for row in rows:
        d = dict(row)
        d["consumed"] = abs(int(d["consumed"]))
        out.append(d)
    return out


# ────────────────────────────────────────────
# 错误中心（失败任务聚合分类）
# ────────────────────────────────────────────

def error_summary() -> dict[str, Any]:
    """失败任务统计总览。"""
    with db_conn() as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS total_errors,
                COUNT(*) FILTER (WHERE updated_at >= current_date) AS today_errors,
                COUNT(*) FILTER (WHERE platform = 'temu') AS temu_errors,
                COUNT(*) FILTER (WHERE platform = '1688') AS alibaba_errors,
                COUNT(*) FILTER (WHERE platform = 'ozon') AS ozon_errors,
                COUNT(DISTINCT user_id) AS affected_users
            FROM imports
            WHERE status = 'error'
            """
        ).fetchone()
    return {
        "total_errors": int(row["total_errors"]),
        "today_errors": int(row["today_errors"]),
        "temu_errors": int(row["temu_errors"]),
        "alibaba_errors": int(row["alibaba_errors"]),
        "ozon_errors": int(row["ozon_errors"]),
        "affected_users": int(row["affected_users"]),
    }


def error_breakdown() -> list[dict[str, Any]]:
    """按错误原因分类聚合 Top N（截取 status_msg 关键词归类）。"""
    with db_conn() as conn:
        rows = conn.execute(
            """
            SELECT
                CASE
                    WHEN status_msg ILIKE '%翻译%' OR status_msg ILIKE '%translat%' THEN '翻译失败'
                    WHEN status_msg ILIKE '%视觉%' OR status_msg ILIKE '%vision%' THEN '视觉解析失败'
                    WHEN status_msg ILIKE '%生成%' OR status_msg ILIKE '%generat%' THEN '图片生成失败'
                    WHEN status_msg ILIKE '%超时%' OR status_msg ILIKE '%timeout%' THEN '超时'
                    WHEN status_msg ILIKE '%enqueue%' OR status_msg ILIKE '%队列%' THEN '入队失败'
                    WHEN status_msg ILIKE '%key%' OR status_msg ILIKE '%401%' OR status_msg ILIKE '%403%' THEN 'API Key 异常'
                    WHEN status_msg ILIKE '%oss%' OR status_msg ILIKE '%upload%' THEN '上传失败'
                    WHEN status_msg ILIKE '%连接%' OR status_msg ILIKE '%connect%' THEN '连接失败'
                    ELSE '其他'
                END AS error_type,
                COUNT(*) AS count
            FROM imports
            WHERE status = 'error'
            GROUP BY error_type
            ORDER BY count DESC
            """
        ).fetchall()
    return [{"error_type": r["error_type"], "count": int(r["count"])} for r in rows]


def list_error_tasks(
    platform: str = "",
    keyword: str = "",
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict[str, Any]], int]:
    """失败任务列表（富格式，可按平台/关键词筛选）。"""
    conditions = ["imp.status = 'error'"]
    params: list[Any] = []
    if platform:
        conditions.append("imp.platform = %s")
        params.append(platform)
    if keyword:
        conditions.append("(imp.title ILIKE %s OR imp.status_msg ILIKE %s OR imp.goods_id ILIKE %s)")
        params.extend([f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"])
    where = "WHERE " + " AND ".join(conditions)
    offset = (page - 1) * page_size
    with db_conn() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) AS c FROM imports imp {where}", params
        ).fetchone()["c"]
        rows = conn.execute(
            f"""
            SELECT imp.id, imp.title, imp.cn_title, imp.status_msg, imp.platform,
                   imp.user_seq, imp.created_at, imp.updated_at,
                   u.account, u.uid, u.display_name,
                   e.name AS enterprise_name
            FROM imports imp
            JOIN users u ON u.id = imp.user_id
            LEFT JOIN enterprises e ON e.id = u.enterprise_id
            {where}
            ORDER BY imp.updated_at DESC
            LIMIT %s OFFSET %s
            """,
            params + [page_size, offset],
        ).fetchall()
    out = []
    for row in rows:
        d = _fmt_task(dict(row))
        out.append(d)
    return out, int(total)


def retry_import(import_id: int) -> bool:
    """超管重试失败任务：置为 queued 并重新入队。"""
    with db_conn() as conn:
        row = conn.execute(
            "SELECT user_id, status FROM imports WHERE id = %s", (import_id,)
        ).fetchone()
        if not row:
            return False
        conn.execute(
            "UPDATE imports SET status = 'queued', status_msg = 'retry by admin', updated_at = now() WHERE id = %s",
            (import_id,),
        )
        user_id = int(row["user_id"])
    # 重新入队（跨进程，通过主应用的 enqueue 逻辑）
    try:
        import redis as redis_lib
        from config import get_settings
        r = redis_lib.from_url(get_settings().redis_url, socket_connect_timeout=3)
        member = f"{user_id}:{import_id}"
        r.srem("pipeline:enqueued", member)
        import json as _json
        payload = _json.dumps({"user_id": user_id, "import_id": import_id})
        r.lpush("pipeline:queue", payload)
        r.close()
    except Exception as exc:
        logger.warning("retry enqueue failed for import %s: %s", import_id, exc)
    return True


def batch_retry(import_ids: list[int]) -> int:
    """批量重试，返回成功数。"""
    count = 0
    for import_id in import_ids:
        if retry_import(import_id):
            count += 1
    return count


# ────────────────────────────────────────────
# 定价配置 CRUD
# ────────────────────────────────────────────

def list_pricing_configs() -> list[dict[str, Any]]:
    """所有定价配置。"""
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM pricing_configs ORDER BY platform, step"
        ).fetchall()
    return [dict(r) for r in rows]


def upsert_pricing_config(platform: str, step: str, cost_beans: int, is_active: bool = True) -> dict[str, Any]:
    """新增或更新一条定价配置（UNIQUE(platform, step)）。"""
    with db_conn() as conn:
        row = conn.execute(
            """
            INSERT INTO pricing_configs (platform, step, cost_beans, is_active)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (platform, step) DO UPDATE
            SET cost_beans = EXCLUDED.cost_beans,
                is_active = EXCLUDED.is_active,
                updated_at = now()
            RETURNING *
            """,
            (platform, step, cost_beans, is_active),
        ).fetchone()
    return dict(row)


def delete_pricing_config(config_id: int) -> bool:
    with db_conn() as conn:
        cur = conn.execute("DELETE FROM pricing_configs WHERE id = %s", (config_id,))
        return cur.rowcount > 0


def init_default_pricing() -> None:
    """初始化默认定价（首次启动，仅插入不存在的）。"""
    defaults = [
        ("temu", "translate", 1),
        ("temu", "vision", 2),
        ("temu", "generate", 5),
        ("1688", "translate", 1),
        ("1688", "vision", 2),
        ("1688", "generate", 5),
        ("ozon", "translate", 1),
        ("ozon", "vision", 2),
        ("ozon", "generate", 5),
    ]
    with db_conn() as conn:
        for platform, step, cost in defaults:
            conn.execute(
                """
                INSERT INTO pricing_configs (platform, step, cost_beans, is_active)
                VALUES (%s, %s, %s, TRUE)
                ON CONFLICT (platform, step) DO NOTHING
                """,
                (platform, step, cost),
            )


# ────────────────────────────────────────────
# 财务报表
# ────────────────────────────────────────────

def revenue_daily(days: int = 30) -> list[dict[str, Any]]:
    """近 N 天每日收入/消费汇总。"""
    with db_conn() as conn:
        rows = conn.execute(
            """
            SELECT
                DATE(created_at) AS date,
                COALESCE(SUM(amount) FILTER (WHERE amount > 0), 0) AS recharge,
                ABS(COALESCE(SUM(amount) FILTER (WHERE amount < 0), 0)) AS consume
            FROM bean_transactions
            WHERE created_at >= current_date - %s
            GROUP BY DATE(created_at)
            ORDER BY date DESC
            """,
            (days,),
        ).fetchall()
    return [
        {"date": str(r["date"]), "recharge": int(r["recharge"]), "consume": int(r["consume"])}
        for r in rows
    ]


def user_consume_ranking(limit: int = 20) -> list[dict[str, Any]]:
    """用户消费金豆排行 Top N。"""
    with db_conn() as conn:
        rows = conn.execute(
            """
            SELECT t.user_id, u.account, u.uid, u.display_name,
                   e.name AS enterprise_name,
                   ABS(COALESCE(SUM(t.amount) FILTER (WHERE t.amount < 0), 0)) AS consumed,
                   COUNT(t.id) FILTER (WHERE t.amount < 0) AS tx_count
            FROM bean_transactions t
            JOIN users u ON u.id = t.user_id
            LEFT JOIN enterprises e ON e.id = u.enterprise_id
            GROUP BY t.user_id, u.account, u.uid, u.display_name, e.name
            ORDER BY consumed DESC
            LIMIT %s
            """,
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def monthly_revenue(months: int = 6) -> list[dict[str, Any]]:
    """近 N 个月的月度收入汇总。"""
    with db_conn() as conn:
        rows = conn.execute(
            """
            SELECT
                TO_CHAR(DATE_TRUNC('month', created_at), 'YYYY-MM') AS month,
                COALESCE(SUM(amount) FILTER (WHERE amount > 0), 0) AS recharge,
                ABS(COALESCE(SUM(amount) FILTER (WHERE amount < 0), 0)) AS consume,
                COUNT(*) FILTER (WHERE amount > 0) AS recharge_count,
                COUNT(*) FILTER (WHERE amount < 0) AS consume_count
            FROM bean_transactions
            WHERE created_at >= DATE_TRUNC('month', current_date) - %s * INTERVAL '1 month'
            GROUP BY DATE_TRUNC('month', created_at)
            ORDER BY month DESC
            """,
            (months,),
        ).fetchall()
    return [
        {
            "month": r["month"],
            "recharge": int(r["recharge"]),
            "consume": int(r["consume"]),
            "recharge_count": int(r["recharge_count"]),
            "consume_count": int(r["consume_count"]),
        }
        for r in rows
    ]


# ────────────────────────────────────────────
# AI 模型配置（从 .env 读取，只读）
# ────────────────────────────────────────────

def read_ai_config() -> dict[str, Any]:
    """读取 .env 里的 AI 模型配置（只读展示，不暴露密钥明文）。"""
    from pathlib import Path
    env_path = Path(__file__).resolve().parent.parent.parent / "backend" / ".env"
    config = {}
    if not env_path.exists():
        return {"available": False, "error": ".env not found"}
    env = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip("'").strip('"')

    def mask(v):
        if not v or len(v) <= 8:
            return v
        return v[:4] + "****" + v[-4:]

    return {
        "available": True,
        "models": {
            "chat_model": env.get("CHAT_MODEL", ""),
            "chat_base_url": env.get("OPENAI_CHAT_BASE_URL", ""),
            "image_model": env.get("IMAGE_MODEL", ""),
            "image_size": env.get("IMAGE_SIZE", ""),
            "vibe_base_url": env.get("VIBE_BASE_URL", ""),
        },
        "keys": {
            "chat_api_key": mask(env.get("CHAT_API_KEY", "")),
            "vibe_api_key": mask(env.get("VIBE_API_KEY", "")),
        },
        "oss": {
            "endpoint": env.get("OSS_ENDPOINT", ""),
            "bucket": env.get("OSS_BUCKET", ""),
            "cdn_domain": env.get("OSS_CDN_DOMAIN", ""),
            "use_signed_url": env.get("OSS_USE_SIGNED_URL", ""),
        },
        "pipeline": {
            "max_per_user": env.get("PIPELINE_MAX_PER_USER", ""),
        },
    }


def read_prompts() -> dict[str, Any]:
    """读取各平台 prompt 模板（只读展示）。"""
    from pathlib import Path
    result = {}
    prompts_root = Path(__file__).resolve().parent.parent.parent / "backend" / "platforms" / "temu" / "prompts"
    if not prompts_root.exists():
        return {"available": False}
    for pf in prompts_root.glob("*.py"):
        if pf.name == "__init__.py":
            continue
        try:
            content = pf.read_text(encoding="utf-8")
            # 提取模块级字符串常量（PROMPT / PROMPT_TEMPLATE）
            result[pf.stem] = {
                "filename": pf.name,
                "lines": len(content.splitlines()),
                "preview": content[:500],
            }
        except Exception:
            pass
    return {"available": True, "prompts": result}
