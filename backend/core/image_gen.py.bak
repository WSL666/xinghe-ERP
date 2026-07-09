from __future__ import annotations

import base64
import time
from typing import Any
import httpx
from openai import APITimeoutError, OpenAI

from core.base import IMAGE_ATTEMPT_TIMEOUT, IMAGE_DOWNLOAD_TIMEOUT, MAX_IMAGE_ATTEMPTS, VIBE_OUTPUT_FORMAT, VIBE_RESPONSE_FORMAT, log
from api_key_pool import get_pool


class ApiKeyError(RuntimeError):
    """携带 HTTP 状态码的 API 调用异常,供 key 池判断是否该换 key。"""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code
from core.images import guess_mime_bytes
from .oss import upload_new_image_to_oss


def build_edit_image(image_bytes_list: list[bytes]):
    """Build the image argument for OpenAI SDK images.edit."""
    files = []
    for i, img_bytes in enumerate(image_bytes_list):
        mime = guess_mime_bytes(img_bytes)
        fname = f"ref_{i + 1}.{mime.split('/')[-1]}"
        files.append((fname, img_bytes, mime))
    if len(files) == 1:
        return files[0]
    return files


def create_vibe_client(api_key: str, base_url: str) -> OpenAI:
    return OpenAI(
        base_url=base_url,
        api_key=api_key,
        default_headers={"User-Agent": "python-httpx/0.28.1"},
    )


def read_result_item_bytes(item: Any, timeout: float) -> bytes:
    if item.b64_json:
        return base64.b64decode(item.b64_json)

    if item.url:
        response = httpx.get(item.url, timeout=timeout)
        if response.is_error:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text[:500]}")
        return response.content

    raise RuntimeError(f"Could not parse image result: {item}")


def is_timeout_error(exc: Exception) -> bool:
    if isinstance(exc, (APITimeoutError, httpx.TimeoutException)):
        return True
    name = type(exc).__name__.lower()
    return "timeout" in name or "timed out" in str(exc).lower()


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
    """用调用方传入的 api_key 生成一张图(自己不再从池取 key)。

    key 轮换逻辑已上移到 _step4_generate:它取一个 key 给所有图共用。
    - 401/403 → 抛 ApiKeyError(告诉上层 key 坏了,要换 key)
    - 超时/空响应 → 同一个 key 内部重试(max_attempts 次)
    """
    started = time.perf_counter()
    last_error = "unknown error"

    for attempt in range(1, max_attempts + 1):
        log(f"{task_name}: attempt {attempt}/{max_attempts} (key=...{api_key[-6:]})")
        client = create_vibe_client(api_key, base_url)
        attempt_started = time.perf_counter()
        try:
            response = client.images.edit(
                image=edit_image,
                prompt=prompt,
                model=model,
                size=size,
                n=1,
                output_format=VIBE_OUTPUT_FORMAT,
                response_format=VIBE_RESPONSE_FORMAT,
                timeout=httpx.Timeout(attempt_timeout, connect=30.0),
            )
        except Exception as exc:
            last_error = str(exc)
            code = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
            # 401/403: key 坏了,抛 ApiKeyError 让上层换 key
            if code in (401, 403):
                raise ApiKeyError(f"{task_name}: key 失效({code})", code) from exc
            # 超时:同一个 key 重试
            if is_timeout_error(exc) and attempt < max_attempts:
                log(f"[WARN] {task_name}: timeout ({code}), retrying with same key...")
                continue
            raise
        elapsed_attempt = time.perf_counter() - attempt_started
        if elapsed_attempt > attempt_timeout:
            last_error = f"single request exceeded {attempt_timeout:.0f}s (took {elapsed_attempt:.0f}s)"
            log(f"[WARN] {task_name}: {last_error}, retrying...")
            if attempt < max_attempts:
                continue
            raise RuntimeError(f"{task_name}: {last_error}")

        data = response.data or []
        if not data:
            last_error = "image response has no data"
            if attempt < max_attempts:
                log(f"[WARN] {task_name}: {last_error}, retrying...")
                continue
            raise RuntimeError(f"{task_name}: {last_error}")

        image_bytes = read_result_item_bytes(data[0], IMAGE_DOWNLOAD_TIMEOUT)
        oss_result = upload_new_image_to_oss(env, image_bytes, task_name)
        elapsed = time.perf_counter() - started
        log(f"[OK] {task_name}: uploaded OSS ({elapsed:.2f}s, {attempt} attempt(s))")
        return oss_result["url"], oss_result, elapsed, attempt

    raise RuntimeError(
        f"{task_name} failed after {max_attempts} attempts: {last_error}"
    )
