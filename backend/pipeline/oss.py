from __future__ import annotations

from datetime import datetime
from typing import Any

from ._base import log
from .images import download_bytes, ensure_jpeg_bytes, guess_mime_bytes, image_suffix_for_mime


def upload_image_bytes_to_oss(
    env: dict[str, str],
    img_bytes: bytes,
    filename: str,
    mime: str,
    folder: str,
) -> dict[str, Any]:
    from oss_client import upload_image_bytes

    result = upload_image_bytes(
        env,
        img_bytes,
        filename=filename,
        content_type=mime,
        folder=folder,
    )
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


def upload_source_image_urls_to_oss(env: dict[str, str], urls: list[str]) -> list[str]:
    old_image_urls: list[str] = []
    for index, url in enumerate(urls[:10], start=1):
        raw_bytes, mime = download_bytes(url)
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
    from oss_client import upload_file_bytes

    result = upload_file_bytes(
        env,
        file_bytes,
        filename=filename,
        content_type=mime,
        folder=folder,
    )
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
    """Download each source video and upload to OSS old_video folder.

    Videos are display-only (independent of AI image generation). Failures for a
    single video are logged and skipped so a bad URL never breaks the pipeline.
    Returns the videos with an added `oss_url` field.
    """
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
        except Exception as exc:  # noqa: BLE001 - best effort, display-only
            log(f"[WARN] video {index} upload failed, kept original url {url}: {str(exc)[:160]}")
            entry["oss_url"] = ""
        result.append(entry)
    return result
