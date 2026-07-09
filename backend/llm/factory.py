"""LLM 工厂: 根据 .env 配置创建对应的 client 实例。

用法:
    from llm.factory import get_text_client, get_multimodal_client, get_image_client
    text = get_text_client(env)
    text.chat("翻译这段话...")
"""
from __future__ import annotations

from typing import Any

from core.base import require_env
from llm.openai_client import OpenAITextClient, OpenAIMultimodalClient, OpenAIImageClient


def get_text_client(env: dict[str, str], **kwargs: Any) -> OpenAITextClient:
    """文本 LLM (翻译/对话)。默认走 DeepSeek (OpenAI 兼容)。"""
    return OpenAITextClient(env, **kwargs)


def get_multimodal_client(env: dict[str, str], api_key: str | None = None) -> OpenAIMultimodalClient:
    """多模态多模态 (图片解析)。api_key 由调用方传入(key 池轮换)。"""
    return OpenAIMultimodalClient(env, api_key=api_key)


def get_image_client(env: dict[str, str], api_key: str, base_url: str, model: str) -> OpenAIImageClient:
    """图片生成。api_key 由调用方传入(key 池轮换)。"""
    return OpenAIImageClient(env, api_key, base_url, model)
