"""API Key 池管理面板 — 独立启动器（测试用，不影响生产）。

为什么要这个文件:
  测试阶段你不想每次改面板都 `systemctl restart product-pipeline.service`(重启整个
  生产后端)。这个脚本单独把面板跑起来,所见即所得——加载的就是 admin.py 的正式面板
  代码(左右布局 + 双表格),只是把真实 Redis 换成内存版,所以:
    ✓ 界面 = 正式界面(一模一样)
    ✓ 数据 = 内存隔离(不碰生产 Redis,加/删的 key 退出即消失)
    ✓ 端口 = 7799(和生产的 6688 完全分开)
    ✓ 不依赖 config.py / 真实数据库,任何机器都能跑

用法:
  cd /root/workspace/wsl-workplace/backend
  python -m api_key_pool.run
  # 浏览器打开: http://127.0.0.1:7799/admin/keys?token=dev
  # Ctrl+C 退出

预置数据(方便你直接看效果,不用手动加):
  - 视觉解析模型: 3 个可用 + 1 个失效(演示失败原因列)
  - 图片生成模型: 2 个可用 + 2 个失效

这个文件只用于测试。生产环境面板由 main.py 自动挂载(无需此文件)。
正式使用时, key 通过面板添加到真实 Redis,生产 worker 直接用。
"""
from __future__ import annotations

import os
import sys
from types import ModuleType


def _bootstrap():
    """注入内存 config + fakeredis,让正式面板代码在隔离环境跑起来。"""
    # 1. 假 config: 让 pool.py/admin.py 的 `import config` 不报错
    class _FakeSettings:
        redis_url = "memory://standalone"
        admin_token = os.environ.get("ADMIN_TOKEN", "dev")  # 测试默认 token=dev

    fake_config = ModuleType("config")
    fake_config.get_settings = lambda: _FakeSettings()
    sys.modules["config"] = fake_config

    # 2. 用 fakeredis 替换 pool 内的真实 Redis 客户端
    import fakeredis
    from api_key_pool import pool as _pool
    _pool._CLIENT = fakeredis.FakeRedis(decode_responses=True)
    _pool._POOLS.clear()


def _seed_demo_data():
    """预置演示数据,打开就能看到两张表都有内容。"""
    from api_key_pool import get_pool
    # 视觉解析: 3 可用 + 1 失效
    chat = get_pool("chat")
    for k in ["sk-chat-aaa111222333444", "sk-chat-bbb555666777888", "sk-chat-ccc999000111222"]:
        chat.add(k)
    chat.add("sk-chat-deadkey-fail-001")
    chat.mark_failed("sk-chat-deadkey-fail-001", 403, "Forbidden")
    # 图片生成: 2 可用 + 2 失效
    vibe = get_pool("vibe")
    for k in ["sk-vibe-xxx111222333444", "sk-vibe-yyy555666777888"]:
        vibe.add(k)
    vibe.add("sk-vibe-deadkey-401-aaa")
    vibe.mark_failed("sk-vibe-deadkey-401-aaa", 401, "Unauthorized")
    vibe.add("sk-vibe-deadkey-503-bbb")
    vibe.mark_failed("sk-vibe-deadkey-503-bbb", 503, "Service Unavailable")


def main():
    _bootstrap()
    _seed_demo_data()

    from fastapi import FastAPI
    from api_key_pool import router  # 正式面板路由(就是 admin.py 的)
    import uvicorn

    app = FastAPI(title="API Key 池面板 (测试)", docs_url=None, redoc_url=None)
    app.include_router(router)

    port = int(os.environ.get("PORT", "7799"))
    token = os.environ.get("ADMIN_TOKEN", "dev")
    print()
    print("=" * 58)
    print("  🔑 API Key 池管理面板 — 独立测试模式")
    print("=" * 58)
    print(f"  浏览器打开:  http://127.0.0.1:{port}/admin/keys?token={token}")
    print()
    print("  • 加载的是正式面板代码(左右布局 + 双表格)")
    print("  • 用内存 Redis, 数据和生产行产完全隔离")
    print(f"  • 已预置演示数据(可直接看两张表效果)")
    print(f"  • 端口 {port}, 和生产 {6688} 分开, 不影响生产")
    print(f"  • Ctrl+C 退出, 不改任何业务代码")
    print("=" * 58)
    print()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
