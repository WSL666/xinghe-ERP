"""Anthropic Claude 原生格式客户端 (预留, 未实现)。

Claude 的 API 格式与 OpenAI 不同 (messages 结构 + content blocks),
需要独立的 SDK (anthropic 包)。当需要接入 Claude 时实现此类。
"""
from __future__ import annotations

from typing import Any


class ClaudeTextClient:
    """预留: Claude 文本/对话。"""

    def __init__(self, env: dict[str, str], api_key: str | None = None, model: str | None = None):
        self._env = env
        self._api_key = api_key or env.get("CLAUDE_API_KEY", "")
        self._model = model or env.get("CLAUDE_MODEL", "claude-sonnet-4-20250514")

    def chat(self, prompt: str, max_tokens: int = 4096, **kwargs: Any) -> str:
        raise NotImplementedError("Claude client not yet implemented. Install 'anthropic' and implement.")
