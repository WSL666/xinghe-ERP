"""根据 model_type 路由到对应 client。

当前所有国内模型都是 OpenAI 兼容 → model_type="openai"。
未来接入 Claude/Gemini 原生协议时在这里加分支。
"""
from __future__ import annotations

from typing import Any

from core.errors import ErrorCode


def _get_client(model_type: str, api_key: str, base_url: str):
    if model_type == "openai":
        from llm.openai_client import OpenAIClient
        return OpenAIClient(api_key, base_url)
    elif model_type == "claude":
        from llm.claude_client import ClaudeClient
        return ClaudeClient(api_key, base_url)
    elif model_type == "gemini":
        from llm.gemini_client import GeminiClient
        return GeminiClient(api_key, base_url)
    raise ValueError(f"不支持的 model_type: {model_type}")


def call_chat(model_type: str, api_key: str, base_url: str, model: str,
              prompt: str, **kwargs: Any) -> str:
    client = _get_client(model_type, api_key, base_url)
    return client.chat(model, prompt, **kwargs)


def call_analyze(model_type: str, api_key: str, base_url: str, model: str,
                 prompt: str, image_b64_list: list[str], **kwargs: Any) -> dict[str, Any]:
    client = _get_client(model_type, api_key, base_url)
    return client.analyze(model, prompt, image_b64_list, **kwargs)


def call_generate_one(model_type: str, api_key: str, base_url: str, model: str,
                      prompt: str, edit_image: Any, size: str = "1024x1024",
                      **kwargs: Any) -> tuple[bytes, dict[str, Any]]:
    client = _get_client(model_type, api_key, base_url)
    return client.generate_one(model, prompt, edit_image, size, **kwargs)
