"""ToolResult: 所有 tool 脚本的统一返回格式。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    status: str  # "success" | "error" | "partial"
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    error_code: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_success(self) -> bool:
        return self.status == "success"

    @classmethod
    def success(cls, data: dict, **metadata: Any) -> "ToolResult":
        return cls(status="success", data=data, metadata=metadata)

    @classmethod
    def error(cls, error: str, error_code: str | None = None, **metadata: Any) -> "ToolResult":
        return cls(status="error", error=error, error_code=error_code, metadata=metadata)

    @classmethod
    def partial(cls, data: dict, error: str, **metadata: Any) -> "ToolResult":
        return cls(status="partial", data=data, error=error, metadata=metadata)
