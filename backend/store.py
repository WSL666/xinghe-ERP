from __future__ import annotations

import json
import logging
import secrets
import string
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, Optional
from urllib.parse import urlparse

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from config import get_settings
from security import create_api_key, hash_password, normalize_login


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Process-level connection pool. Without it, every store call opened and
# closed a fresh backend connection; under concurrent multi-user load that
# thrashes Postgres and exhausts max_connections. Opened once at app startup
# (main lifespan) and closed at shutdown, so request handlers and pipeline
# worker threads share a bounded set of reusable connections.
_pool: Optional[ConnectionPool] = None


def _configure_connection(conn: psycopg.Connection) -> None:
    conn.row_factory = dict_row


logger = logging.getLogger("store")


def _describe_dsn(conninfo: str) -> str:
    """Show host/port/db/user from a DSN without leaking the password."""
    try:
        u = urlparse(conninfo)
        db = (u.path or "/").lstrip("/")
        return f"host={u.hostname}:{u.port} db={db} user={u.username}"
    except Exception:
        return "<unparseable conninfo>"


def _check_connection(conninfo: str) -> None:
    """One explicit connectivity probe right after the pool is created.

    ConnectionPool is lazy: it builds connections in the background and only
    surfaces a real failure as a bare PoolTimeout ~POOL_TIMEOUT seconds later,
    hiding whether the DB is unreachable, auth failed, or the DB does not
    exist. This probe fails fast with the actual exception so startup logs
    say exactly what went wrong.
    """
    desc = _describe_dsn(conninfo)
    logger.info("数据库连接探测中 (%s)", desc)
    try:
        with psycopg.connect(conninfo, connect_timeout=5) as conn:
            value = conn.execute("SELECT 1").fetchone()[0]
        logger.info("数据库连接成功 (%s, SELECT 1 -> %s)", desc, value)
    except Exception as exc:
        logger.error("数据库连接失败 (%s): %s: %s", desc, type(exc).__name__, exc)
        raise


def open_pool() -> None:
    """Open the global pool. Idempotent; called once at app startup."""
    global _pool
    if _pool is not None:
        return
    settings = get_settings()
    logger.info("opening database connection pool (%s)", _describe_dsn(settings.database_url))
    _pool = ConnectionPool(
        conninfo=settings.database_url,
        min_size=settings.pool_min_size,
        max_size=settings.pool_max_size,
        timeout=settings.pool_timeout,
        max_idle=settings.pool_max_idle,
        configure=_configure_connection,
    )
    # Probe once now: a missing/unreachable Postgres must surface a real
    # error message instead of a bare PoolTimeout 30s later.
    _check_connection(settings.database_url)


def close_pool() -> None:
    """Close the global pool. Called once at app shutdown."""
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def db_conn() -> Iterator[psycopg.Connection]:
    if _pool is None:
        raise RuntimeError("database pool is not open; call open_pool() at startup")
    conn = _pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _pool.putconn(conn)


def _json(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback
def _column_exists(conn: psycopg.Connection, table: str, column: str) -> bool:
    """Probe a column without aborting the transaction.

    The legacy probe (SELECT col ... LIMIT 1 + catching UndefinedColumn then
    rollback) aborts the whole init_db transaction. On a fresh database the
    rollback also undoes every CREATE TABLE issued earlier in the same
    transaction, so the following ALTER TABLE dies with 'relation does not
    exist'. Reading information_schema never errors out, so migrations are
    safe on both fresh and pre-existing databases.
    """
    return conn.execute(
        """
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = %s
          AND column_name = %s
        """,
        (table, column),
    ).fetchone() is not None


def init_db() -> None:
    with db_conn() as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id BIGSERIAL PRIMARY KEY,
                account TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                api_key TEXT NOT NULL DEFAULT '',
                display_name TEXT NOT NULL DEFAULT '',
                is_verified BOOLEAN NOT NULL DEFAULT TRUE,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                enterprise_id BIGINT,
                role TEXT NOT NULL DEFAULT 'member',
                beans INTEGER NOT NULL DEFAULT 100
            )
            """
        )
        # uid 字段(老库兼容: ALTER 加列; 已建好的库此处为空操作)
        conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS uid TEXT")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_uid_key ON users(uid)")
        # 金豆字段(老库兼容; 新用户默认100)
        conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS beans INTEGER NOT NULL DEFAULT 100")
        # import_seq: 每用户自增序号(生成 uid+序号 用)
        conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS import_seq INTEGER NOT NULL DEFAULT 0")
        # user_seq: imports 每用户的序号
        conn.execute("ALTER TABLE imports ADD COLUMN IF NOT EXISTS user_seq INTEGER NOT NULL DEFAULT 0")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS verification_codes (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
                target TEXT NOT NULL,
                purpose TEXT NOT NULL,
                code_hash TEXT NOT NULL,
                expires_at TIMESTAMPTZ NOT NULL,
                consumed_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS imports (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                goods_id TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                sku_count INTEGER NOT NULL DEFAULT 0,
                image_count INTEGER NOT NULL DEFAULT 0,
                raw_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                step2_done BOOLEAN NOT NULL DEFAULT FALSE,
                step3_done BOOLEAN NOT NULL DEFAULT FALSE,
                step4_done BOOLEAN NOT NULL DEFAULT FALSE,
                cn_title TEXT NOT NULL DEFAULT '',
                en_title TEXT NOT NULL DEFAULT '',
                generated_json JSONB NOT NULL DEFAULT '[]'::jsonb,
                vision_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                step_logs JSONB NOT NULL DEFAULT '{}'::jsonb,
                spec_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                video_json JSONB NOT NULL DEFAULT '[]'::jsonb,
                size_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                user_seq INTEGER NOT NULL DEFAULT 0,
                started_at TIMESTAMPTZ,
                finished_at TIMESTAMPTZ,
                status TEXT NOT NULL DEFAULT 'pending',
                status_msg TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                platform TEXT NOT NULL DEFAULT 'temu'
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_imports_user_created ON imports(user_id, created_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_imports_user_status ON imports(user_id, status)")
        # 插件 API 密钥: 明文存储(uid+8位随机), 永久固定
        if not _column_exists(conn, "users", "api_key"):
            conn.execute("ALTER TABLE users ADD COLUMN api_key TEXT NOT NULL DEFAULT ''")
        # 老库迁移: 若还有旧的 hash/preview 列则丢弃(数据不可逆, 老用户登录时重新生成)
        for _legacy_col in ("api_key_hash", "api_key_preview"):
            if _column_exists(conn, "users", _legacy_col):
                try:
                    conn.execute(f"ALTER TABLE users DROP COLUMN IF EXISTS {_legacy_col}")
                except Exception:
                    pass
        for col, col_def in [
            ("spec_json", "JSONB NOT NULL DEFAULT '{}'::jsonb"),
            ("video_json", "JSONB NOT NULL DEFAULT '[]'::jsonb"),
            ("size_json", "JSONB NOT NULL DEFAULT '{}'::jsonb"),
            ("finished_at", "TIMESTAMPTZ"),
            ("started_at", "TIMESTAMPTZ"),
            ("platform", "TEXT NOT NULL DEFAULT 'temu'"),
            ("exported", "BOOLEAN NOT NULL DEFAULT FALSE"),
        ]:
            if not _column_exists(conn, "imports", col):
                conn.execute(f"ALTER TABLE imports ADD COLUMN {col} {col_def}")

        # Enterprises + membership. Multi-tenant layer: an owner onboards a
        # company, members join by invite code. Roles: owner/admin/member.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS enterprises (
                id BIGSERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                display_name TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'approved',
                contact_name TEXT NOT NULL DEFAULT '',
                contact_phone TEXT NOT NULL DEFAULT '',
                invite_code TEXT NOT NULL UNIQUE,
                creator_user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS enterprise_members (
                id BIGSERIAL PRIMARY KEY,
                enterprise_id BIGINT NOT NULL REFERENCES enterprises(id) ON DELETE CASCADE,
                user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                role TEXT NOT NULL DEFAULT 'member',
                status TEXT NOT NULL DEFAULT 'active',
                joined_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE(enterprise_id, user_id)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_em_enterprise ON enterprise_members(enterprise_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_em_user ON enterprise_members(user_id)")
        for col, col_def in [
            ("enterprise_id", "BIGINT"),
            ("role", "TEXT NOT NULL DEFAULT 'member'"),
        ]:
            if not _column_exists(conn, "users", col):
                conn.execute(f"ALTER TABLE users ADD COLUMN {col} {col_def}")


def get_or_create_dev_user() -> dict[str, Any]:
    account = "admin"
    user = get_user_by_account(account)
    if user:
        if not user.get("api_key"):
            ensure_user_api_key(int(user["id"]))
        return get_user_by_account(account) or user
    return create_user(account=account, password="123456", display_name="Admin")


# uid 字符集: 大小写字母+数字, 去掉易混淆字符(0/O/o/I/l/1)
_UID_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz"


def generate_uid() -> str:
    """Generate 8-char uid (no ambiguous chars), deduped."""
    import secrets as _secrets
    with db_conn() as conn:
        for _ in range(50):
            uid = "".join(_secrets.choice(_UID_ALPHABET) for _ in range(8))
            exists = conn.execute("SELECT 1 FROM users WHERE uid = %s", (uid,)).fetchone()
            if not exists:
                return uid
    raise RuntimeError("generate_uid collision after 50 tries")


def create_user(account: str, password: str, display_name: str = "") -> dict[str, Any]:
    normalized = normalize_login(account)
    password_hash = hash_password(password)
    with db_conn() as conn:
        uid = generate_uid()
        api_key = create_api_key(uid)
        row = conn.execute(
            """
            INSERT INTO users (uid, account, password_hash, api_key, display_name, is_verified)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id, uid, account, api_key, display_name,
                     is_verified, is_active, beans, role, created_at
            """,
            (
                uid,
                normalized,
                password_hash,
                api_key,
                display_name or normalized,
                get_settings().auto_verify_users,
            ),
        ).fetchone()
    user = dict(row)
    return user


def get_user_by_account(account: str) -> dict[str, Any] | None:
    with db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE account = %s",
            (normalize_login(account),),
        ).fetchone()
    return dict(row) if row else None


def get_user_by_id(user_id: int) -> dict[str, Any] | None:
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = %s", (user_id,)).fetchone()
    return dict(row) if row else None


def get_user_by_uid(uid: str) -> dict[str, Any] | None:
    """Lookup user by uid (for recharge/beans/support)."""
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE uid = %s", (uid,)).fetchone()
    return dict(row) if row else None


def get_user_by_api_key(api_key: str) -> dict[str, Any] | None:
    if not api_key:
        return None
    with db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE api_key = %s",
            (api_key,),
        ).fetchone()
    return dict(row) if row else None


def ensure_user_api_key(user_id: int) -> str:
    """老用户补发固定密钥(uid+8位随机)。已有则不动。返回密钥明文。"""
    with db_conn() as conn:
        row = conn.execute(
            "SELECT uid, api_key FROM users WHERE id = %s", (user_id,)
        ).fetchone()
        if not row:
            return ""
        existing = row.get("api_key") or ""
        if existing:
            return existing
        uid = row.get("uid") or ""
        api_key = create_api_key(uid)
        conn.execute(
            "UPDATE users SET api_key = %s, updated_at = now() WHERE id = %s",
            (api_key, user_id),
        )
        return api_key


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    data = {
        "id": user["id"],
        "uid": user.get("uid", ""),
        "account": user["account"],
        "display_name": user.get("display_name") or user["account"],
        "is_verified": bool(user.get("is_verified")),
        "api_key": user.get("api_key", "") or "",
        "role": user.get("role", "member"),
        "enterprise_id": user.get("enterprise_id"),
        "beans": int(user.get("beans") or 0),
    }
    return data


def insert_import(user_id: int, payload: dict[str, Any]) -> int:
    product = payload.get("product", {}) or {}
    skus = payload.get("skus", []) or []
    gallery = product.get("galleryImages", []) or []
    spec = payload.get("spec", {}) or {}
    videos = payload.get("videos", []) or []
    size = payload.get("size", {}) or {}
    with db_conn() as conn:
        # 原子拿该用户的自增序号(并发安全)
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
    # ref_code: 用户uid+序号(如 aB3xK9mP1), 供展示和查询用
    owner_uid = item.pop("owner_uid", None) or ""
    seq = item.get("user_seq") or 0
    item["ref_code"] = f"{owner_uid}{seq}" if owner_uid else str(seq)
    return item


def list_imports(user_id: int, platform: str | None = None, exported: bool = False,
                error_box: bool = False) -> list[dict[str, Any]]:
    """列出某用户的导入记录。

    三个互斥的箱子(由调用方组合参数决定):
      - 采集箱(默认): exported=False, error_box=False → 未导出 且 非 error
      - 已导出箱:     exported=True,  error_box=False → 已归档(可能含 error)
      - 错误箱:       error_box=True → 所有 status=error 的(跨平台汇总)
    platform 不为空时额外按平台过滤(错误箱通常不传, 拉全部平台)。
    """
    with db_conn() as conn:
        clauses = ["i.user_id = %s"]
        params: list = [user_id]
        if error_box:
            clauses.append("i.status = 'error'")
        else:
            clauses.append("i.exported = %s")
            params.append(exported)
            # 采集箱 + 已导出箱 都排除 error, error 只出现在错误汇总
            clauses.append("i.status != 'error'")
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
    """把记录标记为已导出(归档), 返回实际更新的行数。

    只归档 status='done' 的记录: 运行中/排队中的不归档, 防止误移走。
    """
    if not import_ids:
        return 0
    with db_conn() as conn:
        cur = conn.execute(
            "UPDATE imports SET exported = TRUE, updated_at = now() "
            "WHERE user_id = %s AND id = ANY(%s) AND status = 'done'",
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
    """原子追加一张生成的图片到 generated_json(供前端实时展示)。

    用 PostgreSQL 的 jsonb || 操作,不需要读-改-写,天然无竞态。
    多个生图线程同时调用也不会覆盖彼此的结果。
    """
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
    """对 generated_json 做原地增删改,返回最新的 generated_json(供前端刷新)。

    action:
      - "promote":  把一张原图 URL 追加为成品图(标记 source=manual_original)
      - "delete":   软删除某张 AI 图(置 deleted=true,数据保留可还原)
      - "restore":  还原某张已软删的 AI 图(清 deleted)
    定位用 image_type(数组内唯一), promote 无需 image_type(新增)。

    并发安全: 在一个事务内 SELECT ... FOR UPDATE 锁行, 读-改-写原子完成,
    避免与 append_generated_image 的 worker 追加互相覆盖。
    """
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
            # 同一张原图不重复 promote: 先清掉所有同 URL 的旧 manual_original 项
            # (避免历史多次 promote/delete 留下多个重复项), 再追加一个新的。
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
    # Read-modify-write in a single transaction with a row lock. The auto
    # pipeline runs step2 and step3 in parallel threads that both record into
    # the same import's step_logs; reading on one connection then UPDATE-ing on
    # another let one thread clobber the other's entry. FOR UPDATE serializes
    # the writes safely.
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
    with db_conn() as conn:
        cur = conn.execute(
            "DELETE FROM imports WHERE user_id = %s AND id = %s",
            (user_id, import_id),
        )
    return cur.rowcount > 0


def list_resumable_imports() -> list[dict[str, Any]]:
    """Imports left in a non-terminal status after a crash/restart.

    Used at startup to re-enqueue jobs that were queued or mid-generation when
    the previous process died, so nothing strands forever.
    """
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


_INVITE_ALPHABET = string.ascii_uppercase + string.digits


def generate_invite_code() -> str:
    """8-char invite code, kept unique by a retry loop in create/regenerate."""
    return "PP-" + "".join(secrets.choice(_INVITE_ALPHABET) for _ in range(6))


def _row_enterprise(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    if isinstance(item.get("created_at"), datetime):
        item["created_at"] = item["created_at"].strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(item.get("updated_at"), datetime):
        item["updated_at"] = item["updated_at"].strftime("%Y-%m-%d %H:%M:%S")
    return item


def _row_member(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    if isinstance(item.get("joined_at"), datetime):
        item["joined_at"] = item["joined_at"].strftime("%Y-%m-%d %H:%M:%S")
    return item


def create_enterprise_with_owner(
    name: str,
    contact_name: str,
    contact_phone: str,
    account: str,
    password: str,
    display_name: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Create an enterprise, its owner account, and the membership link.

    Owner account is created with role=owner and denormalized onto users
    (enterprise_id/role) so /api/auth/me can answer role + enterprise in one
    read without joining. The invite code is retried until unique.
    """
    normalized = normalize_login(account)
    if get_user_by_account(normalized):
        raise ValueError("account already exists")
    normalized_name = (name or "").strip()
    if not normalized_name:
        raise ValueError("enterprise name is required")

    password_hash = hash_password(password)

    with db_conn() as conn:
        invite_code = generate_invite_code()
        for _ in range(5):
            exists = conn.execute(
                "SELECT 1 FROM enterprises WHERE invite_code = %s", (invite_code,)
            ).fetchone()
            if not exists:
                break
            invite_code = generate_invite_code()
        else:
            raise RuntimeError("could not generate a unique invite code")

        ent_row = conn.execute(
            """
            INSERT INTO enterprises (name, display_name, contact_name, contact_phone, invite_code)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING *
            """,
            (normalized_name, display_name or normalized_name, contact_name, contact_phone, invite_code),
        ).fetchone()
        enterprise = _row_enterprise(dict(ent_row))
        enterprise_id = enterprise["id"]

        owner_uid = generate_uid()
        api_key = create_api_key(owner_uid)
        user_row = conn.execute(
            """
            INSERT INTO users (uid, account, password_hash, api_key, display_name, enterprise_id, role)
            VALUES (%s, %s, %s, %s, %s, %s, 'owner')
            RETURNING *
            """,
            (owner_uid, normalized, password_hash, api_key, display_name or normalized, enterprise_id),
        ).fetchone()
        user = dict(user_row)

        conn.execute(
            """
            INSERT INTO enterprise_members (enterprise_id, user_id, role)
            VALUES (%s, %s, 'owner')
            """,
            (enterprise_id, user["id"]),
        )
        conn.execute(
            "UPDATE enterprises SET creator_user_id = %s WHERE id = %s",
            (user["id"], enterprise_id),
        )
    return enterprise, user


def join_enterprise_by_invite(invite_code: str, user_id: int) -> dict[str, Any] | None:
    """Attach an existing user to an enterprise as a member by invite code.

    Updates both the membership table and the denormalized users columns so
    the user's role/enterprise are visible immediately on next /api/auth/me.
    Returns the enterprise row or None if the code is invalid.
    """
    code = (invite_code or "").strip().upper()
    if not code:
        return None
    with db_conn() as conn:
        ent = conn.execute(
            "SELECT * FROM enterprises WHERE invite_code = %s AND status = 'approved'",
            (code,),
        ).fetchone()
        if not ent:
            return None
        enterprise = _row_enterprise(dict(ent))
        existing = conn.execute(
            "SELECT 1 FROM enterprise_members WHERE enterprise_id = %s AND user_id = %s",
            (enterprise["id"], user_id),
        ).fetchone()
        if existing:
            return enterprise
        conn.execute(
            """
            INSERT INTO enterprise_members (enterprise_id, user_id, role)
            VALUES (%s, %s, 'member')
            """,
            (enterprise["id"], user_id),
        )
        conn.execute(
            "UPDATE users SET enterprise_id = %s, role = 'member', updated_at = now() WHERE id = %s",
            (enterprise["id"], user_id),
        )
    return enterprise


def get_enterprise_context_for_user(user_id: int) -> dict[str, Any] | None:
    """Enterprise + role + status for a user, for /api/auth/me routing."""
    with db_conn() as conn:
        row = conn.execute(
            """
            SELECT e.id, e.name, e.display_name, e.status, e.invite_code,
                   m.role, m.status AS member_status
            FROM enterprise_members m
            JOIN enterprises e ON e.id = m.enterprise_id
            WHERE m.user_id = %s AND m.status = 'active'
            ORDER BY m.joined_at DESC
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
    if not row:
        return None
    data = dict(row)
    return {
        "id": data["id"],
        "name": data["name"],
        "display_name": data["display_name"],
        "status": data["status"],
        "invite_code": data["invite_code"],
        "role": data["role"],
        "member_status": data["member_status"],
    }


def list_enterprise_members(enterprise_id: int) -> list[dict[str, Any]]:
    """Member roster with per-user pipeline stats joined from imports."""
    with db_conn() as conn:
        rows = conn.execute(
            """
            SELECT u.id, u.account, u.display_name, m.role, m.status,
                   m.joined_at,
                   COUNT(imp.id) AS import_count,
                   COUNT(imp.id) FILTER (WHERE imp.status = 'done') AS success_count,
                   COUNT(imp.id) FILTER (WHERE imp.status = 'error') AS error_count,
                   MAX(imp.updated_at) AS last_active_at
            FROM enterprise_members m
            JOIN users u ON u.id = m.user_id
            LEFT JOIN imports imp ON imp.user_id = u.id
            WHERE m.enterprise_id = %s
            GROUP BY u.id, u.account, u.display_name, m.role, m.status, m.joined_at
            ORDER BY m.joined_at
            """,
            (enterprise_id,),
        ).fetchall()
    members = []
    for row in rows:
        item = _row_member(dict(row))
        if isinstance(item.get("last_active_at"), datetime):
            item["last_active_at"] = item["last_active_at"].strftime("%Y-%m-%d %H:%M:%S")
        else:
            item["last_active_at"] = ""
        members.append(item)
    return members


def regenerate_invite_code(enterprise_id: int) -> str:
    with db_conn() as conn:
        for _ in range(5):
            code = generate_invite_code()
            clash = conn.execute(
                "SELECT 1 FROM enterprises WHERE invite_code = %s AND id <> %s",
                (code, enterprise_id),
            ).fetchone()
            if not clash:
                conn.execute(
                    "UPDATE enterprises SET invite_code = %s, updated_at = now() WHERE id = %s",
                    (code, enterprise_id),
                )
                return code
    raise RuntimeError("could not generate a unique invite code")


def update_member_role(enterprise_id: int, user_id: int, role: str) -> bool:
    if role not in {"admin", "member"}:
        raise ValueError("role must be admin or member")
    with db_conn() as conn:
        cur = conn.execute(
            """
            UPDATE enterprise_members SET role = %s
            WHERE enterprise_id = %s AND user_id = %s AND role <> 'owner'
            """,
            (role, enterprise_id, user_id),
        )
        if cur.rowcount > 0:
            conn.execute(
                "UPDATE users SET role = %s, updated_at = now() WHERE id = %s",
                (role, user_id),
            )
    return cur.rowcount > 0


def remove_enterprise_member(enterprise_id: int, user_id: int) -> bool:
    with db_conn() as conn:
        cur = conn.execute(
            """
            DELETE FROM enterprise_members
            WHERE enterprise_id = %s AND user_id = %s AND role <> 'owner'
            """,
            (enterprise_id, user_id),
        )
        if cur.rowcount > 0:
            conn.execute(
                "UPDATE users SET enterprise_id = NULL, role = 'member', updated_at = now() WHERE id = %s",
                (user_id,),
            )
    return cur.rowcount > 0


def get_enterprise_by_id(enterprise_id: int) -> dict[str, Any] | None:
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM enterprises WHERE id = %s", (enterprise_id,)).fetchone()
    return _row_enterprise(dict(row)) if row else None
