"""数据库表结构迁移/初始化。"""
from __future__ import annotations

from store.pool import db_conn, _column_exists


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
                is_verified BOOLEAN NOT NULL DEFAULT FALSE,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
                beans INTEGER NOT NULL DEFAULT 0,
                frozen_beans INTEGER NOT NULL DEFAULT 0,
                import_seq INTEGER NOT NULL DEFAULT 0,
                uid TEXT NOT NULL DEFAULT '',
                role TEXT NOT NULL DEFAULT 'member',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
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
                translate_done BOOLEAN NOT NULL DEFAULT FALSE,
                analyze_done BOOLEAN NOT NULL DEFAULT FALSE,
                generate_done BOOLEAN NOT NULL DEFAULT FALSE,
                cn_title TEXT NOT NULL DEFAULT '',
                en_title TEXT NOT NULL DEFAULT '',
                generated_json JSONB NOT NULL DEFAULT '[]'::jsonb,
                multimodal_json JSONB NOT NULL DEFAULT '{}'::jsonb,
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
        if not _column_exists(conn, "users", "api_key"):
            conn.execute("ALTER TABLE users ADD COLUMN api_key TEXT NOT NULL DEFAULT ''")
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
            ("ai_features", "JSONB NOT NULL DEFAULT '[]'::jsonb"),
            ("ai_status", "TEXT NOT NULL DEFAULT ''"),
            ("ai_status_msg", "TEXT NOT NULL DEFAULT ''"),
        ]:
            if not _column_exists(conn, "imports", col):
                conn.execute(f"ALTER TABLE imports ADD COLUMN {col} {col_def}")

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
            ("ai_title_enabled", "BOOLEAN NOT NULL DEFAULT FALSE"),
            ("ai_images_enabled", "BOOLEAN NOT NULL DEFAULT FALSE"),
        ]:
            if not _column_exists(conn, "users", col):
                conn.execute(f"ALTER TABLE users ADD COLUMN {col} {col_def}")
