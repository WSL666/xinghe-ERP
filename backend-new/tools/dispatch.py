"""dispatch 路由: 根据 provider + task 动态导入对应 tool 脚本。

用法:
    from tools.dispatch import run_translate
    result = run_translate(env, titles, prompt)  # → ToolResult
"""
from __future__ import annotations

import importlib
from typing import Any

from core.base import log
from store.model_config import get_task_config
from tools.tool_result import ToolResult


def get_tool(task: str, provider: str):
    """动态导入 tools/{provider}_{task} 模块。"""
    module_name = f"tools.{provider}_{task}"
    try:
        return importlib.import_module(module_name)
    except ImportError:
        raise ValueError(f"找不到 tool 脚本: {module_name}")


def run_translate(env: dict[str, str], titles: list[str], prompt_template: str) -> ToolResult:
    cfg = get_task_config("title", env)
    module = get_tool("title", cfg["provider"])
    log(f"翻译: provider={cfg['provider']}, model={cfg['model']}")
    return module.translate(cfg, titles, prompt_template)


def run_multimodal(env: dict[str, str], prompt: str, image_b64_list: list[str],
                   image_count: int) -> ToolResult:
    cfg = get_task_config("multimodal", env)
    module = get_tool("multimodal", cfg["provider"])
    log(f"多模态: provider={cfg['provider']}, model={cfg['model']}")
    return module.analyze(cfg, prompt, image_b64_list, image_count)


def run_image_gen(env: dict[str, str], task_name: str, prompt: str,
                  edit_image: Any, size: str = "1024x1024") -> ToolResult:
    cfg = get_task_config("image", env)
    module = get_tool("image", cfg["provider"])
    log(f"生图: provider={cfg['provider']}, model={cfg['model']}")
    return module.generate(cfg, task_name, prompt, edit_image, size)


def run_video(env: dict[str, str], prompt: str, **kwargs) -> ToolResult:
    cfg = get_task_config("video", env)
    module = get_tool("video", cfg["provider"])
    log(f"视频: provider={cfg['provider']}, model={cfg['model']}")
    return module.generate(cfg, prompt, **kwargs)
