"""豆包 Seedream 生图: 只调一次，不重试。"""
from __future__ import annotations

from typing import Any

from core.base import log
from core.images import guess_mime_bytes
from core.oss import upload_new_image_to_oss
from llm.model_type import call_generate_one
from tools.tool_result import ToolResult


def build_edit_image(image_bytes_list: list[bytes]):
    files = []
    for i, img_bytes in enumerate(image_bytes_list):
        mime = guess_mime_bytes(img_bytes)
        fname = f"ref_{i + 1}.{mime.split('/')[-1]}"
        files.append((fname, img_bytes, mime))
    return files[0] if len(files) == 1 else files


def generate(config: dict, task_name: str, prompt: str,
             edit_image: Any, size: str = "1024x1024") -> ToolResult:
    """config = {"provider", "model", "model_type", "base_url", "api_key"}"""
    import time
    started = time.perf_counter()

    try:
        image_bytes, meta = call_generate_one(
            model_type=config["model_type"],
            api_key=config["api_key"],
            base_url=config["base_url"],
            model=config["model"],
            prompt=prompt,
            edit_image=edit_image,
            size=size,
            timeout=240.0,
        )

        oss_result = upload_new_image_to_oss(
            _load_env_for_oss(), image_bytes, task_name
        )
        elapsed = round(time.perf_counter() - started, 3)
        log(f"[OK] {task_name}: uploaded OSS ({elapsed}s)")
        return ToolResult.success(
            data={
                "image_type": task_name,
                "generated_image": oss_result["url"],
                "oss_object_key": oss_result.get("object_key", ""),
                "prompt": prompt,
            },
            model=config["model"], elapsed=elapsed,
        )
    except Exception as exc:
        elapsed = round(time.perf_counter() - started, 3)
        return ToolResult.error(str(exc), "E007",
                                 model=config["model"], elapsed=elapsed)


def _load_env_for_oss():
    from core.base import load_env
    return load_env()
