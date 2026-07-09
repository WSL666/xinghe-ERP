"""API Key 池模块:Redis 三状态管理(可用/冷却/失效)。

对外暴露的公共 API:
  get_pool(provider) -> ApiKeyPool
  PROVIDERS           -> {"chat": "多模态模型", "vibe": "图片生成"}
  all_snapshots()     -> 两池状态快照
  bootstrap_from_env  -> 启动时空池自动从 .env 恢复兜底 key

注意: 旧的 /admin/keys HTML 面板已删除。
      Key 管理(增删改查)已迁移到超级管理员系统(admin-platform)的 AI 资源模块。
"""
from .pool import ApiKeyPool, PROVIDERS, get_pool, all_snapshots

__all__ = ["ApiKeyPool", "PROVIDERS", "get_pool", "all_snapshots"]
