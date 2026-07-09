"""图片生成任务: 调图片生成模型 + 上传 OSS。

单张图生成 + 重试逻辑在这层, key 池轮换(多轮换 key 重试)在 pipeline 编排层。
"""
from __future__ import annotations

import time
from typing import Any

from core.base import (
    IMAGE_ATTEMPT_TIMEOUT,
    IMAGE_DOWNLOAD_TIMEOUT,
    MAX_IMAGE_ATTEMPTS,
    log,
    require_env,
)
from core.images import guess_mime_bytes
from core.oss import upload_new_image_to_oss
from llm.base import ApiKeyError
from llm.factory import get_image_client


def build_edit_image(image_bytes_list: list[bytes]):
    """Build the image argument for OpenAI SDK images.edit."""
    files = []
    for i, img_bytes in enumerate(image_bytes_list):
        mime = guess_mime_bytes(img_bytes)
        fname = f"ref_{i + 1}.{mime.split('/')[-1]}"
        files.append((fname, img_bytes, mime))
    return files[0] if len(files) == 1 else files


def generate_one_image(
    env: dict[str, str],
    task_name: str,
    prompt: str,
    api_key: str,
    base_url: str,
    edit_image: Any,
    size: str,
    model: str,
    attempt_timeout: float = IMAGE_ATTEMPT_TIMEOUT,
    max_attempts: int = MAX_IMAGE_ATTEMPTS,
) -> tuple[str, dict[str, Any], float, int]:
    """用调用方传入的 api_key 生成一张图。

    返回 (oss_url, oss_result, elapsed, attempts)。
    - 401/403 → 抛 ApiKeyError(告诉上层 key 坏了, 要换 key)
    - 超时/空响应 → 同一个 key 内部重试(max_attempts 次)
    """
    client = get_image_client(env, api_key, base_url, model)
    started = time.perf_counter()
    last_error = "unknown error"

    for attempt in range(1, max_attempts + 1):
        log(f"{task_name}: attempt {attempt}/{max_attempts} (key=...{api_key[-6:]})")
        attempt_started = time.perf_counter()
        try:
            image_bytes, meta = client.generate_one(
                prompt=prompt,
                edit_image=edit_image,
                size=size,
                task_name=task_name,
                attempt_timeout=attempt_timeout,
            )
        except ApiKeyError:
            raise
        except Exception as exc:
            last_error = str(exc)
            if client.is_timeout_error(exc) and attempt < max_attempts:
                log(f"[WARN] {task_name}: timeout, retrying with same key...")
                continue
            raise
        elapsed_attempt = time.perf_counter() - attempt_started
        if elapsed_attempt > attempt_timeout:
            last_error = f"single request exceeded {attempt_timeout:.0f}s (took {elapsed_attempt:.0f}s)"
            log(f"[WARN] {task_name}: {last_error}, retrying...")
            if attempt < max_attempts:
                continue
            raise RuntimeError(f"{task_name}: {last_error}")

        oss_result = upload_new_image_to_oss(env, image_bytes, task_name)
        elapsed = time.perf_counter() - started
        log(f"[OK] {task_name}: uploaded OSS ({elapsed:.2f}s, {attempt} attempt(s))")
        return oss_result["url"], oss_result, elapsed, attempt

    raise RuntimeError(f"{task_name} failed after {max_attempts} attempts: {last_error}")
