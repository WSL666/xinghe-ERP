"""LLM 客户端抽象层: 定义统一接口, 各 provider 实现具体调用。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class LLMClient(ABC):
    """文本 LLM (翻译/对话/生成文本)。"""

    @abstractmethod
    def chat(self, prompt: str, max_tokens: int = 4096, **kwargs: Any) -> str:
        ...


class VisionClient(ABC):
    """多模态视觉模型 (图片解析/OCR/视频理解)。"""

    @abstractmethod
    def analyze(self, prompt: str, image_b64_list: list[str], **kwargs: Any) -> dict[str, Any]:
        ...


class ImageGenClient(ABC):
    """图片生成模型。"""

    @abstractmethod
    def generate(self, prompt: str, image_bytes_list: list[bytes],
                 size: str = "1024x1024", task_name: str = "", **kwargs: Any) -> tuple[bytes, dict[str, Any]]:
        ...


class ApiKeyError(RuntimeError):
    """携带 HTTP 状态码的 API 调用异常, 供 key 池判断是否该换 key。"""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code
