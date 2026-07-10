"""Agent 记忆管理 (预留 stub)。

未来实现:
  - 短期记忆: Redis (当前对话上下文)
  - 长期记忆: 数据库 (历史对话/用户偏好)
  - 向量检索: pgvector (语义搜索历史)
"""
from __future__ import annotations

from typing import Any


class AgentMemory:
    """Agent 记忆: 短期(Redis) + 长期(DB)。"""

    def __init__(self, session_id: str = ""):
        self.session_id = session_id

    def add(self, role: str, content: str) -> None:
        raise NotImplementedError("AgentMemory.add() not yet implemented.")

    def get_recent(self, limit: int = 10) -> list[dict[str, Any]]:
        raise NotImplementedError("AgentMemory.get_recent() not yet implemented.")

    def clear(self) -> None:
        raise NotImplementedError("AgentMemory.clear() not yet implemented.")
