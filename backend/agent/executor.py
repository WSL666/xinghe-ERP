"""Agent 执行器 (预留 stub)。

未来实现: 编排 agent 的多步执行循环
(observe → think → act → observe ...), 支持 tool calling。
"""
from __future__ import annotations

from typing import Any


class AgentExecutor:
    """编排 agent 的执行循环。"""

    def __init__(self, agent: Any, max_steps: int = 10):
        self.agent = agent
        self.max_steps = max_steps

    def execute(self, user_input: str) -> str:
        raise NotImplementedError("AgentExecutor.execute() not yet implemented.")
