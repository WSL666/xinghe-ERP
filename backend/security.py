from __future__ import annotations

import hmac
import hashlib
import re
import secrets
from datetime import datetime, timezone
from typing import Any

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from config import get_settings


PASSWORD_ALGORITHM = "pbkdf2_sha256"
PASSWORD_ITERATIONS = 260_000


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def normalize_login(value: str) -> str:
    return value.strip().lower()


def validate_account(value: str) -> str:
    account = normalize_login(value)
    if not account:
        raise ValueError("account is required")
    if "@" in account:
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", account):
            raise ValueError("invalid email")
        return account
    phone = re.sub(r"\s+", "", account)
    if not re.fullmatch(r"\+?\d{6,20}", phone):
        raise ValueError("account must be a valid phone or email")
    return phone


def hash_password(password: str) -> str:
    if len(password or "") < 6:
        raise ValueError("password must be at least 6 characters")
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        PASSWORD_ITERATIONS,
    ).hex()
    return f"{PASSWORD_ALGORITHM}${PASSWORD_ITERATIONS}${salt}${digest}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, iterations_raw, salt, expected = password_hash.split("$", 3)
        if algorithm != PASSWORD_ALGORITHM:
            return False
        iterations = int(iterations_raw)
    except (ValueError, AttributeError):
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations,
    ).hex()
    return hmac.compare_digest(digest, expected)


def session_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().secret_key, salt="ppe-session")


def create_session_token(user_id: int) -> str:
    return session_serializer().dumps({"uid": user_id})


def load_session_token(token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    settings = get_settings()
    try:
        data = session_serializer().loads(token, max_age=settings.session_max_age_seconds)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(data, dict) or not data.get("uid"):
        return None
    return data


def constant_time_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def create_api_key() -> str:
    return "ppe_" + secrets.token_urlsafe(32)


def hash_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()
