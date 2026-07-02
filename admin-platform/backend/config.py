"""超管后台配置。

共享主应用的 DATABASE_URL（同一个 PostgreSQL），但端口/密钥/cookie 全部独立。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv


BACKEND_ROOT = Path(__file__).resolve().parent
APP_ROOT = BACKEND_ROOT.parent
FRONTEND_ROOT = APP_ROOT / "frontend"
ENV_PATH = Path(os.getenv("ADMIN_ENV_PATH", "")) if os.getenv("ADMIN_ENV_PATH") else None

# 默认读取主应用的 backend/.env（共享 DB 配置）
_MAIN_ENV = BACKEND_ROOT.parent.parent / "backend" / ".env"
if _MAIN_ENV.is_file():
    load_dotenv(_MAIN_ENV)


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    return int(raw)


@dataclass(frozen=True)
class Settings:
    app_env: str
    admin_port: int
    database_url: str
    admin_secret_key: str
    admin_cookie_name: str
    session_max_age_seconds: int
    admin_default_username: str
    admin_default_password: str
    redis_url: str
    admin_allow_cidr: str
    cors_origins: tuple[str, ...]

    @property
    def cookie_secure(self) -> bool:
        return self.app_env.lower() == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    cors_raw = os.getenv("ADMIN_CORS_ORIGINS", "")
    cors_origins = tuple(item.strip() for item in cors_raw.split(",") if item.strip())
    return Settings(
        app_env=os.getenv("APP_ENV", "development"),
        admin_port=_int_env("ADMIN_PORT", 6689),
        database_url=os.getenv(
            "DATABASE_URL",
            "postgresql://product_pipeline_user:product_pipeline_dev_password@127.0.0.1:5433/product_pipeline",
        ),
        admin_secret_key=os.getenv("ADMIN_SECRET_KEY", "admin-dev-change-me"),
        admin_cookie_name=os.getenv("ADMIN_COOKIE_NAME", "ppe_admin_session"),
        session_max_age_seconds=_int_env("ADMIN_SESSION_MAX_AGE", 60 * 60 * 12),
        admin_default_username=os.getenv("ADMIN_DEFAULT_USERNAME", "admin"),
        admin_default_password=os.getenv("ADMIN_DEFAULT_PASSWORD", "admin123"),
        redis_url=os.getenv(
            "REDIS_URL",
            "unix:///var/run/product-pipeline/redis.sock?db=0",
        ),
        admin_allow_cidr=os.getenv("ADMIN_ALLOW_CIDR", ""),
        cors_origins=cors_origins,
    )
