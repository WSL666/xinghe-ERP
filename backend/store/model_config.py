"""任务配置读取 (.env 回退用)。

.env 结构:
  {PLATFORM}_BASE_URL / {PLATFORM}_API_KEY  → 平台凭证
  LLM_PROVIDER / LLM_MODEL                 → 文本 LLM
  MULTIMODAL_PROVIDER / MULTIMODAL_MODEL   → 多模态
  IMAGE_GEN_PROVIDER / IMAGE_GEN_MODEL     → 生图
  VIDEO_GEN_PROVIDER / VIDEO_GEN_MODEL     → 视频
"""
from __future__ import annotations

from typing import Any

from core.base import load_env


def get_platform_config(provider: str, env: dict[str, str] | None = None) -> dict[str, str]:
    env = env or load_env()
    prefix = provider.upper()
    base_url = env.get(f"{prefix}_BASE_URL", "").strip()
    api_key = env.get(f"{prefix}_API_KEY", "").strip()
    if not base_url or not api_key:
        raise ValueError(f"平台 {provider} 缺少配置: {prefix}_BASE_URL 或 {prefix}_API_KEY")
    return {"provider": provider, "base_url": base_url, "api_key": api_key}


# task → .env 前缀映射
_TASK_ENV = {
    "llm": "LLM",
    "multimodal": "MULTIMODAL",
    "image_gen": "IMAGE_GEN",
    "video_gen": "VIDEO_GEN",
}


def get_task_config(task: str, env: dict[str, str] | None = None) -> dict[str, str]:
    env = env or load_env()
    prefix = _TASK_ENV.get(task, task.upper())
    provider = env.get(f"{prefix}_PROVIDER", "").strip().lower()
    model = env.get(f"{prefix}_MODEL", "").strip()
    if not provider or not model:
        raise ValueError(f"任务 {task} 缺少配置: {prefix}_PROVIDER 或 {prefix}_MODEL")

    platform = get_platform_config(provider, env)
    return {
        "provider": provider,
        "model": model,
        "model_type": "openai",
        "base_url": platform["base_url"],
        "api_key": platform["api_key"],
    }


def get_all_task_configs(env: dict[str, str] | None = None) -> dict[str, dict[str, str]]:
    env = env or load_env()
    configs: dict[str, dict[str, str]] = {}
    for task in _TASK_ENV:
        try:
            configs[task] = get_task_config(task, env)
        except ValueError:
            pass
    return configs
