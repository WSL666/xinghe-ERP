"""OpenAI 兼容客户端: 支持 GPT/DeepSeek/Qwen/Seedream 等 OpenAI 格式的模型。

一个文件涵盖三种能力:
  - chat():       文本 LLM (翻译/对话)
  - analyze():    多模态多模态 (图片解析)
  - generate():   图片生成 (images.edit)

不同模型只是 model/base_url/api_key 不同, 协议一致, 共用此文件。
"""
from __future__ import annotations

import base64
import time
from typing import Any

import httpx
from openai import APITimeoutError, OpenAI

from core.base import log, parse_json_response, require_env
from core.images import guess_mime_bytes
from llm.base import ApiKeyError


class OpenAITextClient:
    """文本 LLM (OpenAI 兼容: DeepSeek/Qwen/GPT 等)。"""

    def __init__(self, env: dict[str, str], base_url: str | None = None,
                 api_key: str | None = None, model: str | None = None):
        self._api_key = api_key or require_env(env, "step2_api_key")
        self._base_url = (base_url or require_env(env, "step2_base_url")).rstrip("/")
        if self._base_url.endswith("/chat/completions"):
            self._base_url = self._base_url[: -len("/chat/completions")]
        self._model = model or env.get("step2_model", "deepseek-chat")

    def chat(self, prompt: str, max_tokens: int = 4096, **kwargs: Any) -> str:
        log(f"text LLM: model={self._model}, base={self._base_url}")
        client = OpenAI(base_url=self._base_url, api_key=self._api_key)
        resp = client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            timeout=120,
        )
        content = resp.choices[0].message.content
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("LLM API returned empty content")
        return content.strip()


class OpenAIVisionClient:
    """多模态多模态 (OpenAI 兼容: GPT-4o/GPT-5.5 等)。"""

    def __init__(self, env: dict[str, str], api_key: str | None = None):
        self._env = env
        self._api_key = api_key or require_env(env, "CHAT_API_KEY")
        self._base_url = require_env(env, "OPENAI_CHAT_BASE_URL")
        self._model = env.get("CHAT_MODEL", "gpt-5.5")
        if self._base_url.endswith("/chat/completions"):
            self._sdk_base = self._base_url[: -len("/chat/completions")]
        else:
            self._sdk_base = self._base_url.rstrip("/")

    def analyze(self, prompt: str, image_b64_list: list[str], **kwargs: Any) -> dict[str, Any]:
        import traceback as _tb
        log(f"Vision: base={self._sdk_base}, model={self._model}, "
            f"key=...{self._api_key[-8:]}, images={len(image_b64_list)}")

        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for b64_url in image_b64_list:
            content.append({"type": "image_url", "image_url": {"url": b64_url}})
        messages = [{"role": "user", "content": content}]

        client = OpenAI(
            base_url=self._sdk_base,
            api_key=self._api_key,
            default_headers={"User-Agent": "python-httpx/0.28.1"},
        )
        timeout = kwargs.get("timeout", 300.0)
        try:
            stream = client.chat.completions.create(
                model=self._model,
                messages=messages,
                stream=True,
                stream_options={"include_usage": True},
                timeout=httpx.Timeout(timeout, connect=30.0),
            )
            content_parts: list[str] = []
            usage = None
            for chunk in stream:
                if getattr(chunk, "usage", None):
                    usage = chunk.usage
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                delta = getattr(choices[0], "delta", None)
                delta_content = getattr(delta, "content", None) if delta is not None else None
                if delta_content:
                    content_parts.append(delta_content)
        except Exception as exc:
            log(f"Vision API call exception: {exc}")
            log(f"Traceback: {_tb.format_exc()}")
            code = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
            raise ApiKeyError(f"Vision API call failed: {exc}", code) from exc

        raw = "".join(content_parts).strip()
        if not raw:
            raise RuntimeError("Empty vision stream response")
        if usage:
            log(f"Vision usage: {usage}")
        log(f"Vision raw response (first 500 chars): {raw[:500]}")
        return parse_json_response(raw)


class OpenAIImageClient:
    """图片生成 (OpenAI 兼容: gpt-image/Seedream 等)。

    Capability 差异在 generate() 内部处理:
      - gpt-image: 每次生成 1 张
      - Seedream: 可一次生成多张 (n > 1)
    调用方无需关心, 只管调 generate() 拿结果。
    """

    def __init__(self, env: dict[str, str], api_key: str, base_url: str, model: str):
        self._env = env
        self._api_key = api_key
        self._base_url = base_url
        self._model = model

    def _create_client(self) -> OpenAI:
        return OpenAI(
            base_url=self._base_url,
            api_key=self._api_key,
            default_headers={"User-Agent": "python-httpx/0.28.1"},
        )

    @staticmethod
    def build_edit_image(image_bytes_list: list[bytes]):
        """Build the image argument for OpenAI SDK images.edit."""
        files = []
        for i, img_bytes in enumerate(image_bytes_list):
            mime = guess_mime_bytes(img_bytes)
            fname = f"ref_{i + 1}.{mime.split('/')[-1]}"
            files.append((fname, img_bytes, mime))
        return files[0] if len(files) == 1 else files

    @staticmethod
    def read_result_bytes(item: Any, timeout: float) -> bytes:
        if item.b64_json:
            return base64.b64decode(item.b64_json)
        if item.url:
            response = httpx.get(item.url, timeout=timeout)
            if response.is_error:
                raise RuntimeError(f"HTTP {response.status_code}: {response.text[:500]}")
            return response.content
        raise RuntimeError(f"Could not parse image result: {item}")

    @staticmethod
    def is_timeout_error(exc: Exception) -> bool:
        if isinstance(exc, (APITimeoutError, httpx.TimeoutException)):
            return True
        name = type(exc).__name__.lower()
        return "timeout" in name or "timed out" in str(exc).lower()

    def generate_one(
        self,
        prompt: str,
        edit_image: Any,
        size: str = "1024x1024",
        task_name: str = "",
        attempt_timeout: float = 240.0,
    ) -> tuple[bytes, dict[str, Any]]:
        """生成单张图 (内部不重试, 由调用方控制重试)。

        返回 (image_bytes, meta)。
        - 401/403 → 抛 ApiKeyError(告诉上层 key 坏了)
        - 超时 → 抛异常(调用方可重试)
        """
        log(f"{task_name}: calling images.edit (key=...{self._api_key[-6:]})")
        client = self._create_client()
        try:
            response = client.images.edit(
                image=edit_image,
                prompt=prompt,
                model=self._model,
                size=size,
                n=1,
                output_format="png",
                response_format="b64_json",
                timeout=httpx.Timeout(attempt_timeout, connect=30.0),
            )
        except Exception as exc:
            code = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
            if code in (401, 403):
                raise ApiKeyError(f"{task_name}: key 失效({code})", code) from exc
            raise

        data = response.data or []
        if not data:
            raise RuntimeError(f"{task_name}: image response has no data")

        image_bytes = self.read_result_bytes(data[0], 60.0)
        return image_bytes, {"model": self._model, "size": size}
