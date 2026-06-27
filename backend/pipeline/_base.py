from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import mimetypes
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any
import httpx
from openai import APITimeoutError, OpenAI


# pipeline is a package under backend/; reuse path constants from config so
# the root is computed in exactly one place (avoids duplicate __file__ math).
from config import APP_ROOT, BACKEND_ROOT, ENV_PATH

PIPELINE_ROOT = BACKEND_ROOT
OUTPUT_DIR = BACKEND_ROOT / "output"
PROMPTS_DIR = BACKEND_ROOT / "prompts"
TEMP_DIR = BACKEND_ROOT / "temp"

# image-generation API constants
VIBE_OUTPUT_FORMAT = "png"
VIBE_RESPONSE_FORMAT = "b64_json"

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
MAX_PARALLEL = 10
IMAGE_DOWNLOAD_CONCURRENCY = 8
IMAGE_ATTEMPT_TIMEOUT = 150.0
MAX_IMAGE_ATTEMPTS = 3
IMAGE_DOWNLOAD_TIMEOUT = 60.0
VISION_TIMEOUT = 300.0
VISION_MAX_ATTEMPTS = 3

_print_lock = threading.Lock()


def log(message: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    safe = message.encode("ascii", errors="replace").decode("ascii")
    with _print_lock:
        print(f"[{ts}] {safe}", flush=True)


class PipelineStepError(RuntimeError):
    """Step exception carrying structured detail for the upper layer to write to DB logs."""

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


def load_prompt_module(prompt_file: str) -> str:
    path = PROMPTS_DIR / prompt_file
    if not path.exists():
        raise FileNotFoundError(f"prompt file not found: {path}")
    spec = importlib.util.spec_from_file_location(prompt_file.rstrip(".py"), path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    prompt = getattr(module, "PROMPT", None)
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError(f"define a non-empty PROMPT string in {path}")
    return prompt.strip()


def call_text_llm(env: dict[str, str], prompt_str: str, max_tokens: int = 4096,
                  base_url: str = None, api_key: str = None, model: str = None) -> str:
    """Call a text-only OpenAI-compatible LLM (e.g. DeepSeek) via the SDK."""
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
