"""API Key 池模块:Redis 三状态管理 + 内网管理面板。

对外暴露的公共 API(向上兼容原有 from api_key_pool import ... 用法):
  get_pool(provider) -> ApiKeyPool
  PROVIDERS           -> {"chat": "视觉解析", "vibe": "图片生成"}
  all_snapshots()     -> 两池状态快照
  router              -> FastAPI 管理面板路由(挂 /admin/keys)

文件结构:
  pool.py    Redis 三状态 key 池核心(可用/冷却/失效, LRU 轮换)
  admin.py   内网管理面板(HTML 页面 + CRUD JSON API)
  README.md  模块说明
"""
from .pool import ApiKeyPool, PROVIDERS, get_pool, all_snapshots
from .admin import router

__all__ = ["ApiKeyPool", "PROVIDERS", "get_pool", "all_snapshots", "router"]
