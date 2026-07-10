"""OSS 上传: 底层 client + 业务封装(合并自 oss_client.py + core/oss.py)。"""
from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import oss2

from core.base import log
from core.images import download_bytes, ensure_jpeg_bytes, guess_mime_bytes, image_suffix_for_mime


# ── 底层 OSS client ──

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
        bucket_name, endpoint, key, cdn_domain)
    return UploadResult(
        ok=True, url=url, object_key=key, bucket=bucket_name,
        folder=folder_id, size=len(data), content_type=final_content_type)


def upload_file_bytes(
    env: dict[str, str],
    data: bytes,
    filename: str,
    content_type: str | None = None,
    object_key: str | None = None,
    folder: str | None = None,
) -> UploadResult:
    """Generic OSS upload for any file type (e.g. videos)."""
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
        bucket_name, endpoint, key, cdn_domain)
    return UploadResult(
        ok=True, url=url, object_key=key, bucket=bucket_name,
        folder=folder_id, size=len(data), content_type=final_content_type)


# ── 业务封装 ──

def upload_image_bytes_to_oss(
    env: dict[str, str],
    img_bytes: bytes,
    filename: str,
    mime: str,
    folder: str,
) -> dict[str, Any]:
    result = upload_image_bytes(env, img_bytes, filename=filename, content_type=mime, folder=folder)
    return {
        "ok": result.ok,
        "url": result.url,
        "object_key": result.object_key,
        "bucket": result.bucket,
        "folder": result.folder,
        "size": result.size,
        "content_type": result.content_type,
    }


def upload_old_image_to_oss(env: dict[str, str], img_bytes: bytes, mime: str, index: int) -> dict[str, Any]:
    suffix = image_suffix_for_mime(mime or guess_mime_bytes(img_bytes))
    filename = f"old_image_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{index}{suffix}"
    return upload_image_bytes_to_oss(env, img_bytes, filename, mime or guess_mime_bytes(img_bytes), folder="1")


def upload_new_image_to_oss(env: dict[str, str], img_bytes: bytes, task_name: str) -> dict[str, Any]:
    jpg_bytes = ensure_jpeg_bytes(img_bytes)
    filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{task_name}.jpg"
    return upload_image_bytes_to_oss(env, jpg_bytes, filename, "image/jpeg", folder="2")


def upload_source_image_bytes_to_oss(
    env: dict[str, str], image_bytes_list: list[bytes | None]
) -> list[str]:
    """把已下载的图片字节上传到 OSS。"""
    old_image_urls: list[str] = []
    index = 0
    for raw_bytes in image_bytes_list:
        if raw_bytes is None:
            continue
        index += 1
        mime = guess_mime_bytes(raw_bytes)
        oss_result = upload_old_image_to_oss(env, raw_bytes, mime, index)
        old_image_urls.append(oss_result["url"])
    return old_image_urls


def video_suffix_for_mime(mime: str) -> str:
    mime = (mime or "").split(";", 1)[0].lower().strip()
    return {
        "video/mp4": ".mp4",
        "video/webm": ".webm",
        "video/quicktime": ".mov",
        "video/x-msvideo": ".avi",
        "video/x-matroska": ".mkv",
    }.get(mime, ".mp4")


def upload_file_bytes_to_oss(
    env: dict[str, str],
    file_bytes: bytes,
    filename: str,
    mime: str,
    folder: str,
) -> dict[str, Any]:
    result = upload_file_bytes(env, file_bytes, filename=filename, content_type=mime, folder=folder)
    return {
        "ok": result.ok,
        "url": result.url,
        "object_key": result.object_key,
        "bucket": result.bucket,
        "folder": result.folder,
        "size": result.size,
        "content_type": result.content_type,
    }


def upload_old_video_to_oss(env: dict[str, str], video_bytes: bytes, mime: str, index: int) -> dict[str, Any]:
    suffix = video_suffix_for_mime(mime)
    filename = f"old_video_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{index}{suffix}"
    return upload_file_bytes_to_oss(env, video_bytes, filename, mime or "video/mp4", folder="3")


def upload_source_videos_to_oss(env: dict[str, str], videos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Download each source video and upload to OSS old_video folder."""
    result: list[dict[str, Any]] = []
    for index, video in enumerate(videos, start=1):
        if isinstance(video, str):
            video = {"url": video, "poster": "", "width": 0, "height": 0}
        entry = {**video}
        url = video.get("url") or ""
        if not url:
            result.append(entry)
            continue
        try:
            raw_bytes, mime = download_bytes(url)
            oss_result = upload_old_video_to_oss(env, raw_bytes, mime, index)
            entry["oss_url"] = oss_result["url"]
        except Exception as exc:  # noqa: BLE001
            log(f"[WARN] video {index} upload failed, kept original url {url}: {str(exc)[:160]}")
            entry["oss_url"] = ""
        result.append(entry)
    return result
