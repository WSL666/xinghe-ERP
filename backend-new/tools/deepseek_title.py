"""DeepSeek 翻译: 只调一次，不重试。失败返回 ToolResult.error。"""
from __future__ import annotations

import json

from core.base import log, parse_json_response
from llm.model_type import call_chat
from tools.tool_result import ToolResult


def translate(config: dict, titles: list[str], prompt_template: str) -> ToolResult:
    """config = {"provider", "model", "model_type", "base_url", "api_key"}"""
    import time
    started = time.perf_counter()
    full_prompt = f"{prompt_template}\n\nInput:\n{json.dumps(titles, ensure_ascii=False)}"

    try:
        raw = call_chat(
            model_type=config["model_type"],
            api_key=config["api_key"],
            base_url=config["base_url"],
            model=config["model"],
            prompt=full_prompt,
            max_tokens=4096,
            timeout=60.0,
        )
        parsed = parse_json_response(raw)
        if not isinstance(parsed, list) or not parsed:
            return ToolResult.error("翻译结果格式错误", "E006",
                                     raw_preview=raw[:200], model=config["model"])

        elapsed = round(time.perf_counter() - started, 3)
        log(f"翻译完成: {len(parsed)} 条, {elapsed}s")
        return ToolResult.success(
            data={"titles": parsed},
            model=config["model"], elapsed=elapsed,
        )
    except Exception as exc:
        elapsed = round(time.perf_counter() - started, 3)
        return ToolResult.error(str(exc), "E007",
                                 model=config["model"], elapsed=elapsed)
