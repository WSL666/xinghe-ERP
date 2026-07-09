from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path

import oss2


# Bucket construction (oss2.Auth + oss2.Bucket) is cheap-ish but rebuilding it
# for every upload in a 10-wide step4 burst is wasteful. Cache one bucket per
# (key_id, endpoint, bucket_name) triple; oss2 clients are thread-safe for PUT.
_bucket_cache: dict[str, oss2.Bucket] = {}


def get_bucket(env: dict[str, str]) -> oss2.Bucket:
    access_key_id = require_env(env, "OSS_ACCESS_KEY_ID")
    access_key_secret = require_env(env, "OSS_ACCESS_KEY_SECRET")
    endpoint = require_env(env, "OSS_ENDPOINT")
    bucket_name = require_env(env, "OSS_BUCKET")
    cache_key = f"{access_key_id}|{endpoint}|{bucket_name}"
    cached = _bucket_cache.get(cache_key)
    if cached is not None:
        return cached
    auth = oss2.Auth(access_key_id, access_key_secret)
    bucket = oss2.Bucket(auth, endpoint, bucket_name)
    _bucket_cache[cache_key] = bucket
    return bucket


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg", ".ico", ".tiff", ".tif"}


@dataclass
class UploadResult:
    ok: bool
    url: str
    object_key: str
    bucket: str
    folder: str
    size: int
    content_type: str


def require_env(env: dict[str, str], key: str) -> str:
    value = env.get(key, "").strip()
    if not value:
        raise ValueError(f"Missing OSS config: {key}")
    return value


def env_bool(env: dict[str, str], key: str, default: bool = False) -> bool:
    raw = env.get(key, str(default)).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def env_int(env: dict[str, str], key: str, default: int) -> int:
    raw = env.get(key, "").strip()
    return int(raw) if raw else default


def normalize_endpoint(endpoint: str) -> str:
    return endpoint.replace("https://", "").replace("http://", "").strip("/")


def build_object_key_from_name(name: str, prefix: str, custom_key: str | None = None) -> str:
    if custom_key:
        return custom_key.lstrip("/")

    prefix = prefix.strip("/")
    source = Path(name)
    suffix = source.suffix.lower() or ".jpg"
    stem = source.stem or "image"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    object_name = f"{stem}_{timestamp}{suffix}"
    return f"{prefix}/{object_name}" if prefix else object_name


def build_public_url(bucket_name: str, endpoint: str, object_key: str, cdn_domain: str = "") -> str:
    if cdn_domain:
        domain = cdn_domain.replace("https://", "").replace("http://", "").strip("/")
        return f"https://{domain}/{object_key}"
    endpoint_host = normalize_endpoint(endpoint)
    return f"https://{bucket_name}.{endpoint_host}/{object_key}"


def resolve_folder_prefix(env: dict[str, str], folder: str | None) -> tuple[str, str]:
    folder_id = (folder or env.get("OSS_DEFAULT_FOLDER", "1")).strip()
    if not folder_id:
        raise ValueError("OSS folder id is required")

    key = f"OSS_FOLDER_{folder_id}"
    prefix = env.get(key, "").strip().strip("/")
    if not prefix:
        raise ValueError(f"Missing OSS config: {key}")
    return folder_id, prefix


def upload_image_bytes(
    env: dict[str, str],
    data: bytes,
    filename: str,
    content_type: str | None = None,
    object_key: str | None = None,
    folder: str | None = None,
) -> UploadResult:
    if not data:
        raise ValueError("image bytes is empty")

    suffix = Path(filename).suffix.lower()
    if suffix not in IMAGE_EXTENSIONS:
        raise ValueError(f"Unsupported image extension: {suffix}")

    folder_id, prefix = resolve_folder_prefix(env, folder)
    cdn_domain = env.get("OSS_CDN_DOMAIN", "")
    use_signed_url = env_bool(env, "OSS_USE_SIGNED_URL", False)
    sign_expires = env_int(env, "OSS_SIGN_EXPIRES", 86400)

    key = build_object_key_from_name(filename, prefix, object_key)
    guessed_type, _ = mimetypes.guess_type(filename)
    final_content_type = content_type or guessed_type or "application/octet-stream"

    bucket = get_bucket(env)
    bucket_name = require_env(env, "OSS_BUCKET")
    endpoint = require_env(env, "OSS_ENDPOINT")
    result = bucket.put_object(key, BytesIO(data), headers={"Content-Type": final_content_type})
    if result.status != 200:
        raise RuntimeError(f"OSS upload failed, HTTP status: {result.status}")

    url = bucket.sign_url("GET", key, sign_expires) if use_signed_url else build_public_url(
        bucket_name,
        endpoint,
        key,
        cdn_domain,
    )

    return UploadResult(
        ok=True,
        url=url,
        object_key=key,
        bucket=bucket_name,
        folder=folder_id,
        size=len(data),
        content_type=final_content_type,
    )


def upload_file_bytes(
    env: dict[str, str],
    data: bytes,
    filename: str,
    content_type: str | None = None,
    object_key: str | None = None,
    folder: str | None = None,
) -> UploadResult:
    """Generic OSS upload for any file type (e.g. videos). No extension allow-list."""
    if not data:
        raise ValueError("file bytes is empty")

    folder_id, prefix = resolve_folder_prefix(env, folder)
    cdn_domain = env.get("OSS_CDN_DOMAIN", "")
    use_signed_url = env_bool(env, "OSS_USE_SIGNED_URL", False)
    sign_expires = env_int(env, "OSS_SIGN_EXPIRES", 86400)

    key = build_object_key_from_name(filename, prefix, object_key)
    guessed_type, _ = mimetypes.guess_type(filename)
    final_content_type = content_type or guessed_type or "application/octet-stream"

    bucket = get_bucket(env)
    bucket_name = require_env(env, "OSS_BUCKET")
    endpoint = require_env(env, "OSS_ENDPOINT")
    result = bucket.put_object(key, BytesIO(data), headers={"Content-Type": final_content_type})
    if result.status != 200:
        raise RuntimeError(f"OSS upload failed, HTTP status: {result.status}")

    url = bucket.sign_url("GET", key, sign_expires) if use_signed_url else build_public_url(
        bucket_name,
        endpoint,
        key,
        cdn_domain,
    )

    return UploadResult(
        ok=True,
        url=url,
        object_key=key,
        bucket=bucket_name,
        folder=folder_id,
        size=len(data),
        content_type=final_content_type,
    )
