"""OCR 文字提取任务 (预留, 未实现)。

未来接入 OCR 模型时实现: 从图片中提取文字。
可复用 llm/ 层的多模态客户端 (get_vision_client)。
"""
from __future__ import annotations


def extract_text(image_b64: str, **kwargs) -> str:
    raise NotImplementedError("OCR not yet implemented.")
