"""充值/金豆模块。

对外暴露:
  router        → FastAPI 路由(挂 /api/billing/*)
  charge_beans  → 扣减金豆(pipeline 跑任务时调用)

数据结构:
  users.beans (INTEGER, 默认100)  金豆余额, 直接存在 users 表

文件:
  router.py     API 路由(查余额/充值/消费记录)
  store.py      金豆读写(余额查询/扣减/增加/记录)
  README.md     模块说明
"""
from .router import router
from .store import charge_beans, add_beans, get_beans

__all__ = ["router", "charge_beans", "add_beans", "get_beans"]
