"""OpenAI 兼容 client: 只管建 client + 裸调用，零重试。

所有 OpenAI 兼容平台(DeepSeek/Qwen/硅基流动/豆包)共用此文件。
不同平台只是 api_key/base_url/model 不同。
"""
from __future__ import annotations

import base64
from typing import Any

import httpx
from openai import OpenAI

from core.base import log, parse_json_response


class OpenAIClient:
    """OpenAI 兼容 client (DeepSeek/Qwen/硅基流动等)。"""

    def __init__(self, api_key: str, base_url: str):
        self._client = OpenAI(
            base_url=base_url,
            api_key=api_key,
            default_headers={"User-Agent": "product-pipeline/2.0"},
        )

    def chat(self, model: str, prompt: str, max_tokens: int = 4096,
             timeout: float = 120.0, **kwargs: Any) -> str:
        """文本对话: 发请求 → 返回文本。失败抛异常，不重试。"""
        resp = self._client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            timeout=timeout,
        )
        content = resp.choices[0].message.content
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("LLM 返回空内容")
        return content.strip()

    def analyze(self, model: str, prompt: str, image_b64_list: list[str],
                timeout: float = 300.0, **kwargs: Any) -> dict[str, Any]:
        """多模态分析: 发请求 → 返回解析后的 dict。失败抛异常，不重试。"""
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for b64_url in image_b64_list:
            content.append({"type": "image_url", "image_url": {"url": b64_url}})

        log(f"多模态: model={model}, images={len(image_b64_list)}")
        stream = self._client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": content}],
            stream=True,
            stream_options={"include_usage": True},
            timeout=httpx.Timeout(timeout, connect=30.0),
        )
        parts: list[str] = []
        for chunk in stream:
            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue
            delta = getattr(choices[0], "delta", None)
            delta_content = getattr(delta, "content", None) if delta else None
            if delta_content:
                parts.append(delta_content)

        raw = "".join(parts).strip()
        if not raw:
            raise RuntimeError("多模态返回空内容")
        log(f"多模态原始返回(前300字): {raw[:300]}")
        return parse_json_response(raw)

    def generate_one(self, model: str, prompt: str, edit_image: Any,
                     size: str = "1024x1024", timeout: float = 240.0,
                     **kwargs: Any) -> tuple[bytes, dict[str, Any]]:
        """图片生成: images.edit → 返回 (图片字节, 元信息)。失败抛异常，不重试。"""
        resp = self._client.images.edit(
            model=model,
            image=edit_image,
            prompt=prompt,
            size=size,
            n=1,
            timeout=httpx.Timeout(timeout, connect=30.0),
        )
        data = resp.data or []
        if not data:
            raise RuntimeError("生图返回空结果")

        item = data[0]
        if item.b64_json:
            return base64.b64decode(item.b64_json), {"model": model, "size": size}
        if item.url:
            r = httpx.get(item.url, timeout=60.0)
            if r.is_error:
                raise RuntimeError(f"下载图片失败: HTTP {r.status_code}")
            return r.content, {"model": model, "size": size}
        raise RuntimeError("无法解析生图结果")
