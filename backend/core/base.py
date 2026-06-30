from __future__ import annotations

import base64
import hashlib
import json
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from openai import APITimeoutError, OpenAI

from config import BACKEND_ROOT, ENV_PATH

# 根目录(backend_new)
PIPELINE_ROOT = BACKEND_ROOT
ENV_PATH = ENV_PATH

# 图片生成 API 常量
VIBE_OUTPUT_FORMAT = "png"
VIBE_RESPONSE_FORMAT = "b64_json"

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
MAX_PARALLEL = 10
IMAGE_DOWNLOAD_CONCURRENCY = 8
IMAGE_ATTEMPT_TIMEOUT = 200.0  # 单张图超时:正常70~186s,超200s判卡死
MAX_IMAGE_ATTEMPTS = 2
IMAGE_DOWNLOAD_TIMEOUT = 60.0
VISION_TIMEOUT = 300.0
VISION_MAX_ATTEMPTS = 3

# 单条 import 流水线总时长兜底：超过即强制判失败，防止任何步骤卡死导致僵尸任务
PIPELINE_TOTAL_TIMEOUT = 900.0

_print_lock = threading.Lock()


def log(message: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    safe = message.encode("ascii", errors="replace").decode("ascii")
    with _print_lock:
        print(f"[{ts}] {safe}", flush=True)


class PipelineStepError(RuntimeError):
    """携带结构化 detail 的步骤异常,供上层写入 DB 日志。"""

    def __init__(self, message: str, detail: dict[str, Any] | None = None):
        super().__init__(message)
        self.detail = detail or {}


def load_env(path: Path = ENV_PATH) -> dict[str, str]:
    if not path.exists():
        raise FileNotFoundError(f"config file not found: {path}")
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip()
    return env


def require_env(env: dict[str, str], key: str) -> str:
    value = env.get(key, "").strip()
    if not value:
        raise ValueError(f"missing required .env config: {key}")
    return value


def parse_json_response(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        return json.loads(text[start : end + 1])


def call_text_llm(env: dict[str, str], prompt_str: str, max_tokens: int = 4096,
                  base_url: str = None, api_key: str = None, model: str = None) -> str:
    """调用文本 OpenAI 兼容 LLM(如 DeepSeek)。"""
    _api_key = api_key or require_env(env, "step2_api_key")
    _base_url = (base_url or require_env(env, "step2_base_url")).rstrip("/")
    if _base_url.endswith("/chat/completions"):
        _base_url = _base_url[: -len("/chat/completions")]
    _model = model or env.get("step2_model", "deepseek-chat")

    log(f"text LLM: model={_model}, base={_base_url}")
    client = OpenAI(base_url=_base_url, api_key=_api_key)
    resp = client.chat.completions.create(
        model=_model,
        messages=[{"role": "user", "content": prompt_str}],
        max_tokens=max_tokens,
        timeout=120,
    )
    content = resp.choices[0].message.content
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("LLM API returned empty content")
    return content.strip()
