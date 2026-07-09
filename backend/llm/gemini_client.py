"""Google Gemini 原生格式客户端 (预留, 未实现)。

Gemini 的 API 格式与 OpenAI 不同 (generateContent + parts),
需要独立的 SDK (google-generativeai 包)。当需要接入 Gemini 时实现此类。
"""
from __future__ import annotations

from typing import Any


class GeminiTextClient:
    """预留: Gemini 文本/对话。"""

    def __init__(self, env: dict[str, str], api_key: str | None = None, model: str | None = None):
        self._env = env
        self._api_key = api_key or env.get("GEMINI_API_KEY", "")
        self._model = model or env.get("GEMINI_MODEL", "gemini-2.0-flash")

    def chat(self, prompt: str, max_tokens: int = 4096, **kwargs: Any) -> str:
        raise NotImplementedError("Gemini client not yet implemented. Install 'google-generativeai' and implement.")
