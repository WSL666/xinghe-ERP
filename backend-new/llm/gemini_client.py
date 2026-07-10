"""Gemini 原生 client (预留，未实现)。"""
from __future__ import annotations

from typing import Any


class GeminiClient:
    def __init__(self, api_key: str, base_url: str):
        self._api_key = api_key
        self._base_url = base_url

    def chat(self, model: str, prompt: str, **kwargs: Any) -> str:
        raise NotImplementedError("Gemini client 尚未实现")

    def analyze(self, model: str, prompt: str, image_b64_list: list[str], **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedError("Gemini 多模态尚未实现")
