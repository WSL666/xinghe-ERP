"""充值/金豆模块。

对外暴露:
  router        → FastAPI 路由(挂 /api/billing/*)
  hold/settle/release → 预扣/结算/释放(pipeline 与 router 调用)

数据结构:
  users.beans         (INTEGER, 默认100)  真实余额(已结算, 前端展示)
  users.frozen_beans  (INTEGER, 默认0)    冻结中(预扣占位)
  可用余额 = beans - frozen_beans

文件:
  router.py     API 路由(查余额/充值/消费记录)
  store.py      金豆读写(hold/settle/release/add/list)
  README.md     模块说明
"""
from .router import router
from .store import (
    add_beans, get_beans, get_available_beans,
    hold_beans, settle_beans, release_beans,
    hold_amount_for, BEANS_FLOOR,
)

__all__ = [
    "router", "add_beans", "get_beans", "get_available_beans",
    "hold_beans", "settle_beans", "release_beans",
    "hold_amount_for", "BEANS_FLOOR",
]
