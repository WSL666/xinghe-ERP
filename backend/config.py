from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv


BACKEND_ROOT = Path(__file__).resolve().parent
APP_ROOT = BACKEND_ROOT.parent
FRONTEND_ROOT = APP_ROOT / "frontend"
ENV_PATH = BACKEND_ROOT / ".env"


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
    database_url: str
    redis_url: str
    secret_key: str
    session_cookie_name: str
    session_max_age_seconds: int
    auto_verify_users: bool
    cors_origins: tuple[str, ...]
    pool_min_size: int
    pool_max_size: int
    pool_timeout: float
    pool_max_idle: int

    @property
    def cookie_secure(self) -> bool:
        return self.app_env.lower() == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_dotenv(ENV_PATH)
    cors_raw = os.getenv("CORS_ORIGINS", "")
    cors_origins = tuple(item.strip() for item in cors_raw.split(",") if item.strip())
    return Settings(
        app_env=os.getenv("APP_ENV", "development"),
        database_url=os.getenv(
            "DATABASE_URL",
            "postgresql://product_pipeline_user:product_pipeline_dev_password@127.0.0.1:5433/product_pipeline",
        ),
        redis_url=os.getenv("REDIS_URL", "redis://127.0.0.1:6380/0"),
        secret_key=os.getenv("APP_SECRET_KEY", "dev-secret-change-me"),
        session_cookie_name=os.getenv("SESSION_COOKIE_NAME", "ppe_session"),
        session_max_age_seconds=_int_env("SESSION_MAX_AGE_SECONDS", 60 * 60 * 24 * 14),
        auto_verify_users=_bool_env("AUTO_VERIFY_USERS", True),
        cors_origins=cors_origins,
        pool_min_size=_int_env("POOL_MIN_SIZE", 2),
        pool_max_size=_int_env("POOL_MAX_SIZE", 20),
        pool_timeout=float(os.getenv("POOL_TIMEOUT", "30") or 30),
        pool_max_idle=_int_env("POOL_MAX_IDLE", 300),
    )
