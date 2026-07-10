"""用户模型配置 CRUD。平台配置从 .env 读，任务映射从 .env 读。

.env 结构:
  {PLATFORM}_BASE_URL / {PLATFORM}_API_KEY  → 平台凭证
  TITLE_PROVIDER / TITLE_MODEL             → 翻译用哪个平台哪个模型
  MULTIMODAL_PROVIDER / MULTIMODAL_MODEL   → 多模态
  IMAGE_PROVIDER / IMAGE_MODEL             → 生图
  VIDEO_PROVIDER / VIDEO_MODEL             → 视频

所有国内模型都是 OpenAI 兼容，model_type 默认 "openai"。
"""
from __future__ import annotations

from typing import Any

from core.base import load_env


def get_platform_config(provider: str, env: dict[str, str] | None = None) -> dict[str, str]:
    """根据 provider 名读 {PROVIDER}_BASE_URL + {PROVIDER}_API_KEY。"""
    env = env or load_env()
    prefix = provider.upper()
    base_url = env.get(f"{prefix}_BASE_URL", "").strip()
    api_key = env.get(f"{prefix}_API_KEY", "").strip()
    if not base_url or not api_key:
        raise ValueError(f"平台 {provider} 缺少配置: {prefix}_BASE_URL 或 {prefix}_API_KEY")
    return {"provider": provider, "base_url": base_url, "api_key": api_key}


def get_task_config(task: str, env: dict[str, str] | None = None) -> dict[str, str]:
    """读任务配置: provider + model + model_type + base_url + api_key。

    task: "title" | "multimodal" | "image" | "video"
    """
    env = env or load_env()
    prefix = task.upper()
    provider = env.get(f"{prefix}_PROVIDER", "").strip().lower()
    model = env.get(f"{prefix}_MODEL", "").strip()
    if not provider or not model:
        raise ValueError(f"任务 {task} 缺少配置: {prefix}_PROVIDER 或 {prefix}_MODEL")

    platform = get_platform_config(provider, env)
    return {
        "provider": provider,
        "model": model,
        "model_type": "openai",  # 国内模型都是 OpenAI 兼容
        "base_url": platform["base_url"],
        "api_key": platform["api_key"],
    }


def get_all_task_configs(env: dict[str, str] | None = None) -> dict[str, dict[str, str]]:
    """一次读全部任务配置。"""
    env = env or load_env()
    configs: dict[str, dict[str, str]] = {}
    for task in ("title", "multimodal", "image", "video"):
        try:
            configs[task] = get_task_config(task, env)
        except ValueError:
            pass  # 未配置的任务跳过
    return configs
