"""翻译任务: 调 LLM 做标题翻译。

业务逻辑(prompt 组装 + 结果解析)在这层, 模型调用在 llm/ 层。
"""
from __future__ import annotations

import json
from typing import Any

from core.base import log, parse_json_response
from llm.factory import get_text_client


def translate_titles(env: dict[str, str], titles: list[str], prompt_template: str) -> list[dict[str, Any]]:
    """调 LLM 翻译标题, 返回 [{"cn_title": ..., "en_title": ...}, ...]。"""
    titles_json = json.dumps(titles, ensure_ascii=False)
    full_prompt = f"{prompt_template}\n\nInput:\n{titles_json}"

    client = get_text_client(
        env,
        base_url=env.get("step2_base_url", "").strip() or None,
        api_key=env.get("step2_api_key", "").strip() or None,
        model=env.get("step2_model", "").strip() or None,
    )
    raw_text = client.chat(full_prompt, max_tokens=4096)

    translated = parse_json_response(raw_text)
    if not isinstance(translated, list) or not translated:
        raise ValueError(f"翻译结果非数组: {raw_text[:200]}")
    log(f"翻译完成: {len(translated)} 条")
    return translated
