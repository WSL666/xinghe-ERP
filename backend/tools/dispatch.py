"""dispatch 路由: 根据 provider + task 动态导入对应 tool 脚本。

优先用用户分配的 key (user_model_assignments), 回退 .env 平台默认 key。
"""
from __future__ import annotations

import importlib
from typing import Any

from core.base import log
from tools.tool_result import ToolResult


def get_tool(task: str, provider: str):
    """动态导入 tools/{provider}_{task} 模块。"""
    module_name = f"tools.{provider}_{task}"
    try:
        return importlib.import_module(module_name)
    except ImportError:
        raise ValueError(f"找不到 tool 脚本: {module_name}")


def _get_config(task: str, env: dict[str, str], user_id: int | None = None) -> dict[str, str]:
    """读配置: 优先用户分配, 回退 .env。"""
    if user_id:
        from store.model_assignment import get_user_task_config
        return get_user_task_config(user_id, task, env)
    from store.model_config import get_task_config
    return get_task_config(task, env)


def run_translate(env: dict[str, str], titles: list[str], prompt_template: str,
                  user_id: int | None = None) -> ToolResult:
    cfg = _get_config("title", env, user_id)
    module = get_tool("title", cfg["provider"])
    log(f"翻译: provider={cfg['provider']}, model={cfg['model']}")
    return module.translate(cfg, titles, prompt_template)


def run_multimodal(env: dict[str, str], prompt: str, image_b64_list: list[str],
                   image_count: int, user_id: int | None = None) -> ToolResult:
    cfg = _get_config("multimodal", env, user_id)
    module = get_tool("multimodal", cfg["provider"])
    log(f"多模态: provider={cfg['provider']}, model={cfg['model']}")
    return module.analyze(cfg, prompt, image_b64_list, image_count)


def run_image_gen(env: dict[str, str], task_name: str, prompt: str,
                  edit_image: Any, size: str = "1024x1024",
                  user_id: int | None = None) -> ToolResult:
    cfg = _get_config("image", env, user_id)
    module = get_tool("image", cfg["provider"])
    log(f"生图: provider={cfg['provider']}, model={cfg['model']}")
    return module.generate(cfg, task_name, prompt, edit_image, size)


def run_video(env: dict[str, str], prompt: str, user_id: int | None = None, **kwargs) -> ToolResult:
    cfg = _get_config("video", env, user_id)
    module = get_tool("video", cfg["provider"])
    log(f"视频: provider={cfg['provider']}, model={cfg['model']}")
    return module.generate(cfg, prompt, **kwargs)
