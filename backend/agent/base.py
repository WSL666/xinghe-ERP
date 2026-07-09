"""Agent 基类 (预留 stub)。

未来实现: 定义 Agent 的生命周期(run/step/observe/act),
支持多轮对话、工具调用、记忆管理。
"""
from __future__ import annotations

from typing import Any


class BaseAgent:
    """Agent 基类: 未来智能体的基础。"""

    def __init__(self, name: str = "", **kwargs: Any):
        self.name = name
        self.tools: dict[str, Any] = {}

    def register_tool(self, name: str, fn: Any) -> None:
        self.tools[name] = fn

    def run(self, user_input: str) -> str:
        raise NotImplementedError("Agent.run() not yet implemented.")
