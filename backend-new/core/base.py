"""基础设施: 日志 + trace_id + 常量 + 工具函数。"""
from __future__ import annotations

import re
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import json

from config import BACKEND_ROOT, ENV_PATH

PIPELINE_ROOT = BACKEND_ROOT
ENV_PATH = ENV_PATH

# 常量
MAX_PARALLEL = 10
IMAGE_DOWNLOAD_CONCURRENCY = 8
IMAGE_DOWNLOAD_TIMEOUT = 60.0
PIPELINE_TOTAL_TIMEOUT = 900.0

_print_lock = threading.Lock()

# trace_id: 每条 pipeline 生一个, 贯穿所有 step
_trace_id: threading.local = threading.local()


def set_trace_id(trace_id: str | None = None) -> str:
    _trace_id.value = trace_id or uuid.uuid4().hex[:12]
    return _trace_id.value


def get_trace_id() -> str:
    return getattr(_trace_id, "value", "")


def clear_trace_id() -> None:
    _trace_id.value = ""


def log(message: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    tid = get_trace_id()
    prefix = f"[{ts}]" + (f"[trace={tid}]" if tid else "")
    safe = message.encode("ascii", errors="replace").decode("ascii")
    with _print_lock:
        print(f"{prefix} {safe}", flush=True)


class PipelineStepError(RuntimeError):
    """携带结构化 detail 的步骤异常。"""

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
