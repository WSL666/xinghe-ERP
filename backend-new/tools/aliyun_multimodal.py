"""阿里云 Qwen 多模态: 只调一次，不重试。"""
from __future__ import annotations

import re
from typing import Any

from core.base import log
from llm.model_type import call_analyze
from tools.tool_result import ToolResult


def analyze(config: dict, prompt: str, image_b64_list: list[str],
            image_count: int) -> ToolResult:
    """config = {"provider", "model", "model_type", "base_url", "api_key"}"""
    import time
    started = time.perf_counter()

    try:
        payload = call_analyze(
            model_type=config["model_type"],
            api_key=config["api_key"],
            base_url=config["base_url"],
            model=config["model"],
            prompt=prompt,
            image_b64_list=image_b64_list,
            timeout=300.0,
        )

        selected_indexes, prompt_items = _validate(payload, image_count)

        elapsed = round(time.perf_counter() - started, 3)
        log(f"多模态完成: selected={selected_indexes}, prompts={len(prompt_items)}, {elapsed}s")
        return ToolResult.success(
            data={
                "payload": payload,
                "selected_indexes": selected_indexes,
                "prompt_items": prompt_items,
            },
            model=config["model"], elapsed=elapsed,
        )
    except Exception as exc:
        elapsed = round(time.perf_counter() - started, 3)
        return ToolResult.error(str(exc), "E007",
                                 model=config["model"], elapsed=elapsed)


def _validate(payload: dict[str, Any], image_count: int) -> tuple[list[int], list[tuple[int, str]]]:
    raw_indexes = payload.get("selected_reference_image_indexes")
    if not isinstance(raw_indexes, list):
        raise ValueError("selected_reference_image_indexes 必须是数组")
    indexes = [int(i) for i in raw_indexes]
    if len(indexes) < 2 or len(indexes) > 3:
        raise ValueError("参考图数量必须是 2-3 个")
    if len(set(indexes)) != len(indexes):
        raise ValueError("参考图索引有重复")
    invalid = [i for i in indexes if i < 1 or i > image_count]
    if invalid:
        raise ValueError(f"参考图索引超出范围 1..{image_count}: {invalid}")

    prompt_items: list[tuple[int, str]] = []
    for key, value in payload.items():
        match = re.fullmatch(r"image_(\d+)", key)
        if not match:
            continue
        prompt_items.append((int(match.group(1)), str(value).strip()))
    prompt_items.sort(key=lambda x: x[0])
    return indexes, prompt_items
