"""数据库连接池 + 基础工具函数。"""
from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, Optional
from urllib.parse import urlparse

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from config import get_settings

logger = logging.getLogger("store")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_pool: Optional[ConnectionPool] = None


def _configure_connection(conn: psycopg.Connection) -> None:
    conn.row_factory = dict_row


def _describe_dsn(conninfo: str) -> str:
    """Show host/port/db/user from a DSN without leaking the password."""
    try:
        u = urlparse(conninfo)
        db = (u.path or "/").lstrip("/")
        return f"host={u.hostname}:{u.port} db={db} user={u.username}"
    except Exception:
        return "<unparseable conninfo>"


def _check_connection(conninfo: str) -> None:
    """One explicit connectivity probe right after the pool is created."""
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
    """Probe a column without aborting the transaction."""
    return conn.execute(
        """
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = %s
          AND column_name = %s
        """,
        (table, column),
    ).fetchone() is not None
